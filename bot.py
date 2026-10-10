"""
bot.py - replies to DMs, @mentions and messages in one configured channel.

Deps:  pip install "discord.py>=2.3" markovify

Run:   python bot.py

Two reply paths:
  1. retrieval  - a learned "someone said X, someone posted this paste" match
                  (built from reply_to links + <url:...> tokens) sends the URL
  2. markov     - a Markov chain trained on the text, with any <url:...> tokens
                  it emits resolved back to the real URL

Bot-authored messages are never trained on.

The corpus is stored verbatim (only pasted URLs are <url:...> tokens), so the bot
CAN generate real mentions - including @everyone and role mentions. Every ping is
therefore neutralised at reply time by OUTGOING_MENTIONS + sanitize_outgoing().

Config (.env / environment):
    TOKEN               discord bot token                       (required)
    GUILD               guild id the @mention trigger applies to (required)
    CHANNEL             channel id the bot always replies in     (required)
    OWNER               your user id, allowed to use !reload     (optional)
    DATA_FILE           training corpus            (default messages.jsonl)
    MEDIA_FILE          url token map              (default media.jsonl)
    CHAT_LOG / DM_LOG   reply logs                 (default chats.jsonl / dms.jsonl)

    OUTGOING_MENTIONS   block | silent | allow_users | allow_all   (default block)
                        note: @everyone/@here are ALWAYS stripped from replies
    RETRIEVAL           true|false  try url retrieval first        (default true)
    RETRIEVAL_THRESHOLD 0..1 minimum query coverage to send a link (default 0.6)
    MARKOV_TEST_OUTPUT  true|false  markovify's verbatim-copy filter (default true)
"""

import json
import os
import random
import re
from datetime import datetime, timezone

import discord
import markovify

# ------------------------------------------------------------------ config ---

def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def env_bool(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


load_dotenv()

TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD") or 0)
CHANNEL_ID = int(os.getenv("CHANNEL") or 0)
OWNER_ID = int(os.getenv("OWNER") or 0)
DATA_FILE = os.getenv("DATA_FILE", "messages.jsonl")
MEDIA_FILE = os.getenv("MEDIA_FILE", "media.jsonl")
CHAT_LOG = os.getenv("CHAT_LOG", "chats.jsonl")
DM_LOG = os.getenv("DM_LOG", "dms.jsonl")

OUTGOING_MENTIONS = os.getenv("OUTGOING_MENTIONS", "block").strip().lower()
RETRIEVAL = env_bool("RETRIEVAL", True)
RETRIEVAL_THRESHOLD = float(os.getenv("RETRIEVAL_THRESHOLD") or 0.6)
MARKOV_TEST_OUTPUT = env_bool("MARKOV_TEST_OUTPUT", True)
# which media kinds may be sent by the retrieval path (media.jsonl "kind")
RETRIEVAL_KINDS = {k.strip().lower() for k in
                   (os.getenv("RETRIEVAL_KINDS") or "gif,image,link").split(",") if k.strip()}

TOKEN_RE = re.compile(r"<url:[0-9a-f]+>")
MENTION_RE = re.compile(r"<@[!&]?\d+>")
RAW_URL_RE = re.compile(r"https?://\S+")
EVERYONE_RE = re.compile(r"@(everyone|here)\b", re.IGNORECASE)
NONWORD_RE = re.compile(r"[^a-z0-9']+")

STOPWORDS = {
    "the", "and", "you", "your", "yours", "for", "with", "that", "this", "these", "those",
    "was", "were", "are", "is", "am", "be", "been", "being", "have", "has", "had", "does",
    "did", "but", "not", "its", "it's", "from", "they", "them", "their", "there", "here",
    "what", "when", "where", "which", "who", "why", "how", "all", "any", "can", "cant",
    "just", "like", "about", "into", "out", "get", "got", "one", "two", "too", "now",
    "then", "than", "some", "she", "her", "him", "his", "our", "ours", "will", "would",
    "should", "could", "dont", "didnt", "im", "ive", "id", "youre", "youve", "lol", "lmao",
}

FALLBACKS = [
    "I need more data to ramble about that.",
    "Hmm, say that again?",
    "I'm not sure how to reply to that!",
]

# ----------------------------------------------------------------- state ------

model = None
corpus_size = 0
media = {}            # "<url:HASH>" -> url
media_kind = {}       # "<url:HASH>" -> gif|image|link
keyword_index = {}    # slug word -> [url]
pair_index = []       # (word set of the message being replied to, url)
gif_urls = []         # every url token seen, for fallback picking


def is_sendable(url, kind="link"):
    """Skip links that would be dead or wrong to post as a reply."""
    if kind not in RETRIEVAL_KINDS:
        return False
    if "ex=" in url:                     # signed CDN url: already expired
        return False
    return True


def load_media():
    """Load the token -> url map written by scraper.py."""
    global media, media_kind, keyword_index
    media, media_kind, keyword_index = {}, {}, {}
    if not os.path.exists(MEDIA_FILE):
        print(f"[media] {MEDIA_FILE} not found - url tokens stay unresolved")
        return
    with open(MEDIA_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tok, url = rec.get("token"), rec.get("url")
            if not tok or not url:
                continue
            media[tok] = url
            media_kind[tok] = rec.get("kind") or "link"
            if not is_sendable(url, media_kind[tok]):
                continue
            for word in (rec.get("slug") or "").split("-"):
                if len(word) > 2 and word not in STOPWORDS:
                    keyword_index.setdefault(word, []).append(url)
    print(f"[media] {len(media)} url token(s), {len(keyword_index)} keyword(s), "
          f"{sum(1 for u in media.values() if 'ex=' in u)} expiring link(s) skipped")


def wordset(text):
    """Plain matching words, with tokens/mentions/urls removed."""
    text = TOKEN_RE.sub(" ", text or "")
    text = MENTION_RE.sub(" ", text)
    text = RAW_URL_RE.sub(" ", text)
    return {w for w in NONWORD_RE.sub(" ", text.lower()).split()
            if len(w) > 2 and w not in STOPWORDS}


def load_model(bot_id=None):
    """Train the Markov chain and build the retrieval index in one pass."""
    global model, corpus_size, pair_index, gif_urls
    if not os.path.exists(DATA_FILE):
        print(f"[model] {DATA_FILE} not found - run scraper.py first")
        model, corpus_size, pair_index, gif_urls = None, 0, [], []
        return

    latest = {}          # dedupe by id, last record wins
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
            if rid is not None:
                latest[rid] = rec

    corpus = []
    pairs = []
    gifs = []
    for rec in latest.values():
        if rec.get("bot"):
            continue
        if bot_id and rec.get("author_id") == bot_id:
            continue
        content = rec.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content.strip()
        corpus.append(content)

        urls = [media[t] for t in TOKEN_RE.findall(content)
                if t in media and is_sendable(media[t], media_kind.get(t, "link"))]
        if urls:
            gifs.extend(urls)
            parent = latest.get(rec.get("reply_to")) if rec.get("reply_to") else None
            if parent and isinstance(parent.get("content"), str) and parent["content"].strip():
                parent_words = wordset(parent["content"])
                if parent_words:
                    pairs.extend((parent_words, u) for u in urls)

    corpus_size = len(corpus)
    pair_index, gif_urls = pairs, gifs
    if not corpus:
        print(f"[model] {DATA_FILE} has no usable messages")
        model = None
        return
    model = markovify.NewlineText("\n".join(corpus), state_size=2)
    print(f"[model] trained on {corpus_size} messages | "
          f"{len(pairs)} retrieval pair(s), {len(set(gifs))} unique url(s)")


def generate_reply(prompt, bot_id=None):
    """A Markov reply; may still contain <url:...> tokens."""
    if model is None:
        return "I don't have a dataset to reply from yet."
    opts = {"test_output": MARKOV_TEST_OUTPUT, "tries": 20}
    prompt = " ".join(w for w in (prompt or "").split() if not w.startswith("<@"))
    for word in prompt.split():
        try:
            reply = model.make_sentence_with_start(word, strict=False, **opts)
        except Exception:
            reply = None
        if reply:
            return reply
    return model.make_sentence(**opts) or FALLBACKS[hash(prompt) % len(FALLBACKS)]


def retrieve(prompt):
    """Find a url someone actually posted in reply to a similar message."""
    q = wordset(prompt)
    if not q:
        return None
    best, best_score = None, 0.0
    for words, url in pair_index:
        shared = len(q & words)
        if shared < 2:
            continue
        score = shared / len(q)
        if score > best_score:
            best, best_score = url, score
    if best and best_score >= RETRIEVAL_THRESHOLD:
        return best

    hits = {}
    for word in q:
        for url in keyword_index.get(word, ()):
            hits[url] = hits.get(url, 0) + 1
    if hits:
        url, n = max(hits.items(), key=lambda kv: kv[1])
        if n >= 3:
            return url
    return None


# ------------------------------------------------------------ outgoing text ---

def resolve_tokens(text):
    """Replace <url:HASH> with the real url; drop unknown tokens."""
    if not text:
        return text
    text = TOKEN_RE.sub(lambda m: media.get(m.group(0), ""), text)
    return re.sub(r"\s{2,}", " ", text).strip()


def sanitize_outgoing(text):
    """@everyone / @here are removed outright - they must never appear.

    This applies in every mode, including allow_all: the reply text can never
    contain them, so no setting can turn them back on.
    """
    if not text:
        return text
    text = EVERYONE_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def allowed_mentions():
    if OUTGOING_MENTIONS == "allow_all":
        return discord.AllowedMentions(everyone=True, roles=True, users=True)
    if OUTGOING_MENTIONS == "allow_users":
        return discord.AllowedMentions(everyone=False, roles=False, users=True)
    return discord.AllowedMentions(everyone=False, roles=False, users=[])


SILENT = OUTGOING_MENTIONS == "silent"
PING_REPLY_AUTHOR = OUTGOING_MENTIONS in ("allow_users", "allow_all")


async def send_reply(message: discord.Message, text: str):
    await message.reply(
        text,
        allowed_mentions=allowed_mentions(),
        silent=SILENT,
        mention_author=PING_REPLY_AUTHOR,
    )


def log_reply(path, message: discord.Message, reply: str):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "author_id": message.author.id,
        "author": message.author.display_name or message.author.name,
        "guild_id": message.guild.id if message.guild else None,
        "channel_id": message.channel.id,
        "message": message.content,
        "reply": reply,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ client ---

intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    load_media()
    load_model(client.user.id)
    print(f"[login] {client.user} ({client.user.id})")
    print(f"[ready] DMs = yes | channel = {CHANNEL_ID} | @mention guild = {GUILD_ID} | "
          f"mentions = {OUTGOING_MENTIONS} | retrieval = {RETRIEVAL}")


def should_reply(message: discord.Message) -> bool:
    if message.author == client.user or message.author.bot:
        return False
    if isinstance(message.channel, discord.DMChannel):
        return True
    if message.channel.id == CHANNEL_ID:
        return message.guild is not None and message.guild.id == GUILD_ID
    if client.user in message.mentions:
        return message.guild is not None and message.guild.id == GUILD_ID
    return False


@client.event
async def on_message(message: discord.Message):
    if message.content.strip().startswith("!reload"):
        if OWNER_ID and message.author.id != OWNER_ID:
            return
        load_media()
        load_model(client.user.id)
        await message.reply(f"Reloaded: {corpus_size} messages, {len(media)} url tokens.")
        return

    if not should_reply(message):
        return

    content = message.content or ""

    if RETRIEVAL:
        url = retrieve(content)
        if url:
            await send_reply(message, sanitize_outgoing(url))
            log_reply(DM_LOG if isinstance(message.channel, discord.DMChannel) else CHAT_LOG,
                      message, url)
            return

    reply = sanitize_outgoing(resolve_tokens(generate_reply(content, client.user.id)))
    if not reply:
        return

    await send_reply(message, reply)
    log_reply(DM_LOG if isinstance(message.channel, discord.DMChannel) else CHAT_LOG,
              message, reply)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("[error] TOKEN missing (.env or environment)")
    if not GUILD_ID or not CHANNEL_ID:
        raise SystemExit("[error] GUILD and CHANNEL must be set in .env")
    client.run(TOKEN)
