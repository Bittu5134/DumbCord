"""Backfill + live-watch a Discord channel into a lean, bot-ready JSONL dataset."""

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

from config import CFG, read_jsonl

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(CFG["guild"] or 0)
CHANNEL_ID = int(CFG["channel"] or 0)
SCRAPE_CHANNEL_ID = int(CFG["scrape_channel"] or 0) or CHANNEL_ID
DATA_FILE = str(CFG["data_file"] or "messages.jsonl")
STATE_FILE = str(CFG["state_file"] or "chat_state.json")
MEDIA_FILE = str(CFG["media_file"] or "media.jsonl")
URL_TOKENS = bool(CFG["url_tokens"])
SKIP_BOTS = bool(CFG["skip_bots"])
SKIP_EMPTY = bool(CFG["skip_empty"])
EDIT_SCAN = int(CFG["edit_scan"] or 200)

URL_RE = re.compile(r"https?://[^\s<>\"'`]+")
TRAILING_PUNCT = ".,;:!?\"'"
CLOSERS = (("(", ")"), ("[", "]"), ("{", "}"))


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def url_kind(url):
    low = url.lower()
    if any(h in low for h in ("klipy.com", "tenor.com", "giphy.com")):
        return "gif"
    if any(e in low for e in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm")):
        return "image"
    return "link"


def url_slug(url):
    seg = url.split("?")[0].rstrip("/").split("/")[-1]
    words = [w for w in re.split(r"[-_.]+", seg) if len(w) > 2 and not w.isdigit()]
    return "-".join(words[:6])


def clean_url(url):
    url = url.rstrip(TRAILING_PUNCT)
    for opener, closer in CLOSERS:
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
        for rec in read_jsonl(self.path):
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
            "slug": url_slug(url), "kind": url_kind(url), "added_at": now_iso(),
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
    if not content or not URL_TOKENS:
        return content
    return URL_RE.sub(lambda m: media.token(clean_url(m.group(0))), content)


def lean_record(message: discord.Message, media) -> dict:
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

    def _default_state(self):
        return {
            "guild_id": GUILD_ID,
            "channel_id": SCRAPE_CHANNEL_ID,
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
                with open(STATE_FILE, encoding="utf-8") as fh:
                    state = json.load(fh)
            except Exception as exc:
                print(f"[warn] could not read {STATE_FILE}: {exc}")
                return self._default_state()
            if state.get("channel_id") not in (None, SCRAPE_CHANNEL_ID):
                archive = f"{STATE_FILE}.{state.get('channel_id')}.bak"
                os.replace(STATE_FILE, archive)
                print(f"[state] scrape channel changed "
                      f"{state.get('channel_id')} -> {SCRAPE_CHANNEL_ID}; "
                      f"archived the previous state to {archive}")
                return self._default_state()
            base = self._default_state()
            base.update(state)
            return base
        return self._default_state()

    def save_state(self):
        self.state["last_updated"] = now_iso()
        tmp = f"{STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2)
        os.replace(tmp, STATE_FILE)

    def _load_index(self):
        for rec in read_jsonl(DATA_FILE):
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
        self.flush()
        if self.data_handle and not self.data_handle.closed:
            self.data_handle.close()

    def wanted(self, message: discord.Message) -> bool:
        if self.self_id and message.author.id == self.self_id:
            return False
        if SKIP_BOTS and message.author.bot:
            return False
        return not (SKIP_EMPTY and not (message.content or "").strip())

    def write(self, message: discord.Message, force=False) -> bool:
        if not force and (message.id in self.seen or not self.wanted(message)):
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

    async def catch_up(self, channel):
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

    async def on_live_message(self, message):
        if message.channel.id != SCRAPE_CHANNEL_ID or message.author.id == self.self_id:
            return
        if self.write(message):
            self.track_position(message, older=False)
            self.flush()
            print(f"[live] saved {message.id}")


intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True

client = discord.Client(intents=intents)
scraper = None
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

    channel = guild.get_channel(SCRAPE_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(SCRAPE_CHANNEL_ID)
        except discord.NotFound:
            print(f"[error] channel {SCRAPE_CHANNEL_ID} not found in '{guild.name}'")
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
        if after.channel.id == SCRAPE_CHANNEL_ID and scraper.wanted(after):
            if scraper.write(after, force=True):
                scraper.flush()
                print(f"[live] updated {after.id}")


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
    parser.add_argument("--channel", type=int, help="override the scrape channel id")
    parser.add_argument("--guild", type=int, help="override the guild id")
    return parser.parse_args()


def main():
    global scraper, watch_mode, GUILD_ID, CHANNEL_ID, SCRAPE_CHANNEL_ID

    args = parse_args()
    if args.guild:
        GUILD_ID = args.guild
    if args.channel:
        SCRAPE_CHANNEL_ID = args.channel
    if not SCRAPE_CHANNEL_ID:
        SCRAPE_CHANNEL_ID = CHANNEL_ID

    if not TOKEN:
        print("[error] DISCORD_TOKEN missing - put it in .env")
        return 1
    if not GUILD_ID or not SCRAPE_CHANNEL_ID:
        print("[error] guild and scrape_channel (or channel) must be set in config.toml")
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
