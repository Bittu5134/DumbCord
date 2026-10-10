"""
scraper.py - download Discord channel history into a lean, bot-ready dataset.

Deps:  pip install "discord.py>=2.3"

Usage:
    python scraper.py                 # asks how many messages to save (Enter = all)
    python scraper.py --limit 5000    # save at most 5000 more messages this run
    python scraper.py --all           # no limit
    python scraper.py --watch         # keep running + capture live messages after backfill
    python scraper.py --no-check-edits

Behaviour:
    * First run walks the channel backwards, oldest run resumes where it left off.
    * Every run first catches up on messages posted since the last run.
    * Edited messages are appended again (same id); consumers keep the last record.
    * Messages authored by the bot itself (and, by default, any bot) are never saved.
    * Messages with no text are skipped (controlled by SKIP_EMPTY).
    * Stored content is VERBATIM - mentions, roles and @everyone/@here are kept
      exactly as typed. The only change is pasted URLs becoming <url:HASH> tokens
      (URL_TOKENS), which is what the retrieval index in bot.py matches on.
      No ping neutralisation happens here; bot.py does that at reply time.
    * Progress lives in chat_state.json, data in messages.jsonl (.env for config).

Config (.env / environment):
    TOKEN       discord bot token                       (required)
    GUILD       guild id to scrape                      (required)
    CHANNEL     text channel id to scrape               (required)
    DATA_FILE   output jsonl                            (default messages.jsonl)
    STATE_FILE  scraper state json                      (default chat_state.json)
    SKIP_BOTS   true|false - skip all bots, not just self (default true)
    SKIP_EMPTY  true|false - skip messages without text   (default true)
    EDIT_SCAN   how many recent messages to recheck for edits (default 200)
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import sys
from datetime import datetime, timezone

import discord

# ------------------------------------------------------------------ config ---

def load_dotenv(path=".env"):
    """Minimal .env loader (no external dependency)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))


load_dotenv()


def env_bool(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD") or 0)
CHANNEL_ID = int(os.getenv("CHANNEL") or 0)
DATA_FILE = os.getenv("DATA_FILE", "messages.jsonl")
STATE_FILE = os.getenv("STATE_FILE", "chat_state.json")
SKIP_BOTS = env_bool("SKIP_BOTS", True)
SKIP_EMPTY = env_bool("SKIP_EMPTY", True)
EDIT_SCAN = int(os.getenv("EDIT_SCAN") or 200)

# --- URL tokenisation ---
# Stored content is otherwise verbatim: mentions, roles and @everyone/@here are
# kept exactly as written. All ping neutralisation happens at reply time (bot.py).
MEDIA_FILE = os.getenv("MEDIA_FILE", "media.jsonl")
URL_TOKENS = env_bool("URL_TOKENS", True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------ lean records ---

URL_RE = re.compile(r"https?://[^\s<>\"'`]+")


def _kind(url):
    low = url.lower()
    if any(h in low for h in ("klipy.com", "tenor.com", "giphy.com")):
        return "gif"
    if any(e in low for e in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm")):
        return "image"
    return "link"


def _slug(url):
    seg = url.split("?")[0].rstrip("/").split("/")[-1]
    words = [w for w in re.split(r"[-_.]+", seg) if len(w) > 2 and not w.isdigit()]
    return "-".join(words[:6])


_TRAILING_PUNCT = ".,;:!?\"'"
_CLOSERS = (("(", ")"), ("[", "]"), ("{", "}"))


def clean_url(url):
    """Strip punctuation that chat text glues onto a link, e.g. markdown (url)"""
    url = url.rstrip(_TRAILING_PUNCT)
    for opener, closer in _CLOSERS:
        while url.endswith(closer) and url.count(opener) < url.count(closer):
            url = url[:-1]
    return url


class MediaStore:
    """Every URL gets a stable, whitespace-free <url:HASH> token."""

    def __init__(self, path=MEDIA_FILE):
        self.path = path
        self.by_url = {}
        self.by_token = {}
        self.pending = []
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tok, url = rec.get("token"), rec.get("url")
                if tok and url:
                    self.by_token[tok] = url
                    self.by_url[url] = tok
        print(f"[media] {len(self.by_token)} url token(s) known")

    def token(self, url):
        tok = self.by_url.get(url)
        if tok:
            return tok
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
        tok = f"<url:{digest}>"
        while tok in self.by_token and self.by_token[tok] != url:
            digest = hashlib.sha1((digest + url).encode("utf-8")).hexdigest()[:10]
            tok = f"<url:{digest}>"
        self.by_url[url] = tok
        self.by_token[tok] = url
        host = re.sub(r"^https?://(?:www\.)?", "", url).split("/")[0]
        self.pending.append({
            "token": tok, "url": url, "host": host,
            "slug": _slug(url), "kind": _kind(url), "added_at": now_iso(),
        })
        return tok

    def flush(self):
        if not self.pending:
            return
        with open(self.path, "a", encoding="utf-8") as fh:
            for rec in self.pending:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.pending.clear()


def tokenise_urls(content, media):
    """Replace pasted links with stable <url:HASH> tokens - nothing else.

    Mentions, roles and @everyone/@here are stored exactly as typed. All ping
    neutralisation happens at reply time in bot.py.
    """
    if not content or not URL_TOKENS:
        return content
    return URL_RE.sub(lambda m: media.token(clean_url(m.group(0))), content)


def lean_record(message: discord.Message, media) -> dict:
    """Only the fields the reply bot / trainer actually needs."""
    record = {
        "id": message.id,
        "author_id": message.author.id,
        "author": message.author.display_name or message.author.name,
        "content": tokenise_urls(message.content, media),
        "created_at": message.created_at.isoformat(),
    }
    if message.author.bot:
        record["bot"] = True
    if message.reference is not None and message.reference.message_id:
        record["reply_to"] = message.reference.message_id
    if message.edited_at:
        record["edited_at"] = message.edited_at.isoformat()
    return record


class Scraper:
    def __init__(self, limit, check_edits=True):
        self.limit = limit                 
        self.check_edits = check_edits
        self.self_id = None                
        self.state = self._load_state()
        self.seen = set()                 
        self.edited = {}                   
        self.data_handle = None
        self.media = MediaStore()
        self._load_index()

    # ---- state -------------------------------------------------------------

    def _default_state(self):
        return {
            "guild_id": GUILD_ID,
            "channel_id": CHANNEL_ID,
            "oldest_message_id": None,
            "oldest_message_timestamp": None,
            "newest_message_id": None,
            "newest_message_timestamp": None,
            "total_messages_saved": 0,
            "reached_beginning": False,
            "last_updated": None,
        }

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as fh:
                    state = json.load(fh)
                base = self._default_state()
                base.update(state)
                return base
            except Exception as exc:
                print(f"[warn] could not read {STATE_FILE}: {exc}")
        return self._default_state()

    def save_state(self):
        self.state["last_updated"] = now_iso()
        tmp = f"{STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2)
        os.replace(tmp, STATE_FILE)

    # ---- disk index --------------------------------------------------------

    def _load_index(self):
        """One pass over the dataset: collect ids + edited timestamps."""
        if not os.path.exists(DATA_FILE):
            return
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = rec.get("id")
                if rid is None:
                    continue
                self.seen.add(int(rid))
                if rec.get("edited_at"):
                    self.edited[int(rid)] = rec["edited_at"]
        self.state["total_messages_saved"] = len(self.seen)
        print(f"[init] {len(self.seen)} messages already on disk")
        if self.edited:
            print(f"[init] {len(self.edited)} messages have known edits")

    def open(self):
        self.data_handle = open(DATA_FILE, "a", encoding="utf-8")

    def flush(self):
        if self.data_handle:
            self.data_handle.flush()
        self.media.flush()
        self.save_state()

    def close(self):
        if self.data_handle and not self.data_handle.closed:
            self.data_handle.flush()
            self.data_handle.close()
        self.media.flush()
        self.save_state()

    # ---- filtering ---------------------------------------------------------

    def wanted(self, message: discord.Message) -> bool:
        """Should this message be stored as training data?"""
        if self.self_id and message.author.id == self.self_id:
            return False
        if SKIP_BOTS and message.author.bot:
            return False
        if SKIP_EMPTY and not (message.content or "").strip():
            return False
        return True

    def write(self, message: discord.Message, force=False) -> bool:
        """Append a lean record. force=True allows re-writing edited messages."""
        if not force:
            if message.id in self.seen or not self.wanted(message):
                return False
        self.data_handle.write(
            json.dumps(lean_record(message, self.media), ensure_ascii=False) + "\n"
        )
        if not force:
            self.seen.add(message.id)
        if message.edited_at:
            self.edited[message.id] = message.edited_at.isoformat()
        self.state["total_messages_saved"] = len(self.seen)
        return True

    def track_position(self, message: discord.Message, older: bool):
        if older:
            self.state["oldest_message_id"] = message.id
            self.state["oldest_message_timestamp"] = message.created_at.isoformat()
        else:
            self.state["newest_message_id"] = message.id
            self.state["newest_message_timestamp"] = message.created_at.isoformat()

    # ---- phases ------------------------------------------------------------

    async def catch_up(self, channel):
        """Fetch everything posted since the last run."""
        newest = self.state.get("newest_message_id")
        if not newest:
            return 0
        print(f"[catch-up] checking messages newer than {newest}")
        saved = 0
        async for message in channel.history(
            limit=None, after=discord.Object(id=newest), oldest_first=True
        ):
            if self.write(message):
                saved += 1
            self.track_position(message, older=False)
            if saved and saved % 50 == 0:
                self.flush()
                print(f"[catch-up] +{saved}")
        self.flush()
        print(f"[catch-up] {saved} new message(s) saved")
        return saved

    async def backfill(self, channel):
        """Walk backwards through history, resuming from the oldest saved id."""
        if self.state.get("reached_beginning"):
            print("[backfill] channel already fully downloaded")
            return 0
        oldest = self.state.get("oldest_message_id")
        before = discord.Object(id=oldest) if oldest else None
        print("[backfill] resuming from "
              f"{self.state.get('oldest_message_timestamp') or 'latest message'}")

        saved = 0
        stopped_by_limit = False
        async for message in channel.history(limit=None, before=before, oldest_first=False):
            if self.state.get("newest_message_id") is None:
                self.track_position(message, older=False)
            if self.write(message):
                saved += 1
            self.track_position(message, older=True)

            if saved and saved % 100 == 0:
                self.flush()
                print(f"[backfill] +{saved} this run | total {len(self.seen)} | "
                      f"{message.created_at.strftime('%Y-%m-%d')}")

            if self.limit and saved >= self.limit:
                stopped_by_limit = True
                break

        self.flush()
        if stopped_by_limit:
            print(f"[backfill] limit of {self.limit} reached, rerun to continue")
        else:
            self.state["reached_beginning"] = True
            self.flush()
            print("[backfill] reached the beginning of the channel")
        return saved

    async def check_recent_edits(self, channel):
        """Re-read the most recent messages and append changed ones."""
        if not self.check_edits or EDIT_SCAN <= 0:
            return 0
        updated = 0
        async for message in channel.history(limit=EDIT_SCAN):
            if not message.edited_at or not self.wanted(message):
                continue
            if self.edited.get(message.id) == message.edited_at.isoformat():
                continue
            if self.write(message, force=True):
                updated += 1
        if updated:
            self.flush()
            print(f"[edits] {updated} edited message(s) updated")
        return updated

    # ---- live --------------------------------------------------------------

    async def on_live_message(self, message):
        if message.channel.id != CHANNEL_ID or message.author.id == self.self_id:
            return
        if self.write(message):
            self.track_position(message, older=False)
            self.flush()
            print(f"[live] saved {message.id}")


# ------------------------------------------------------------------ client ---

intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True

client = discord.Client(intents=intents)
scraper = None          # built in main()
watch_mode = False


@client.event
async def on_ready():
    global scraper
    scraper.self_id = client.user.id
    print(f"[login] {client.user} ({client.user.id})")

    guild = client.get_guild(GUILD_ID) or await client.fetch_guild(GUILD_ID)
    if guild is None:
        print(f"[error] guild {GUILD_ID} not found - is the bot invited?")
        await client.close()
        return

    channel = guild.get_channel(CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(CHANNEL_ID)
        except discord.NotFound:
            print(f"[error] channel {CHANNEL_ID} not found in '{guild.name}'")
            await client.close()
            return

    scraper.open()
    print(f"[target] #{channel.name} in '{guild.name}'")

    try:
        await scraper.catch_up(channel)
        await scraper.backfill(channel)
        await scraper.check_recent_edits(channel)
    except asyncio.CancelledError:
        print("[stop] interrupted, progress saved")
    except discord.Forbidden:
        print("[error] missing 'Read Message History' permission")
    finally:
        scraper.flush()
        print(f"[done] {len(scraper.seen)} messages in {DATA_FILE}")

    if not watch_mode:
        await client.close()


@client.event
async def on_message(message):
    if watch_mode and scraper and scraper.self_id:
        await scraper.on_live_message(message)


@client.event
async def on_message_edit(_before, after):
    if watch_mode and scraper and scraper.self_id:
        if after.channel.id == CHANNEL_ID and scraper.wanted(after):
            if scraper.write(after, force=True):
                scraper.flush()
                print(f"[live] updated {after.id}")


# ------------------------------------------------------------------- entry ---

def ask_limit():
    try:
        raw = input("How many messages to save this run? "
                    "[Enter = as many as possible, or a number]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if not raw or raw.lower() in ("all", "0", "-1"):
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        print("[warn] not a number, using unlimited")
        return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Lean Discord channel scraper.")
    parser.add_argument("--limit", type=int, default=None,
                        help="max messages to save from the backlog this run (0 = all)")
    parser.add_argument("--all", action="store_true", help="no limit")
    parser.add_argument("--watch", action="store_true", help="stay online and capture new messages")
    parser.add_argument("--no-check-edits", action="store_true", help="skip the edit re-scan")
    parser.add_argument("--channel", type=int, help="override CHANNEL")
    parser.add_argument("--guild", type=int, help="override GUILD")
    return parser.parse_args()


def main():
    global scraper, watch_mode, GUILD_ID, CHANNEL_ID

    args = parse_args()
    if args.guild:
        GUILD_ID = args.guild
    if args.channel:
        CHANNEL_ID = args.channel

    if not TOKEN:
        print("[error] TOKEN missing (.env or environment)")
        return 1
    if not GUILD_ID or not CHANNEL_ID:
        print("[error] GUILD and CHANNEL must be set in .env")
        return 1

    if args.all:
        limit = 0
    elif args.limit is not None:
        limit = max(0, args.limit)
    else:
        limit = ask_limit()

    watch_mode = args.watch
    scraper = Scraper(limit=limit, check_edits=not args.no_check_edits)

    def shutdown(_signum, _frame):
        print("\n[stop] flushing...")
        if scraper:
            scraper.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        client.run(TOKEN)
    finally:
        if scraper:
            scraper.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
