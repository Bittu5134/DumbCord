"""Reply bot: answers DMs, @mentions and one configured channel from a scraped corpus."""

import json
import os
import random
import re
from collections import Counter
from datetime import datetime, timezone

import discord
import markovify

from config import CFG, read_jsonl

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(CFG["guild"] or 0)
CHANNEL_ID = int(CFG["channel"] or 0)
OWNER_ID = int(CFG["owner"] or 0)
DATA_FILE = str(CFG["data_file"] or "messages.jsonl")
MEDIA_FILE = str(CFG["media_file"] or "media.jsonl")
CHAT_LOG = str(CFG["chat_log"] or "chats.jsonl")
DM_LOG = str(CFG["dm_log"] or "dms.jsonl")

OUTGOING_MENTIONS = str(CFG["outgoing_mentions"] or "block").strip().lower()
RETRIEVAL = bool(CFG["retrieval"])
RETRIEVAL_THRESHOLD = float(CFG["retrieval_threshold"] or 0.6)
RETRIEVAL_TOPN = max(1, int(CFG["retrieval_topn"] or 5))
MARKOV_TEST_OUTPUT = bool(CFG["markov_test_output"])
CORPUS_DEDUPE = bool(CFG["corpus_dedupe"])

_kinds = CFG["retrieval_kinds"]
if isinstance(_kinds, str):
    _kinds = _kinds.split(",")
RETRIEVAL_KINDS = {str(k).strip().lower() for k in _kinds if str(k).strip()}

TOKEN_RE = re.compile(r"<url:[0-9a-f]+>")
MENTION_RE = re.compile(r"<@[!&]?\d+>")
RAW_URL_RE = re.compile(r"https?://\S+")
EVERYONE_RE = re.compile(r"@(?:everyone|here)\b", re.IGNORECASE)
NONWORD_RE = re.compile(r"[^a-z0-9']+")
SQUASH_RE = re.compile(r"\s{2,}")

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

model = None
corpus_size = 0
media = {}
media_kind = {}
keyword_index = {}
pair_index = []


def is_sendable(url, kind):
    return kind in RETRIEVAL_KINDS and "ex=" not in url


def load_media():
    global media, media_kind, keyword_index
    media, media_kind, keyword_index = {}, {}, {}
    if not os.path.exists(MEDIA_FILE):
        print(f"[media] {MEDIA_FILE} not found - url tokens stay unresolved")
        return
    for rec in read_jsonl(MEDIA_FILE):
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
    text = TOKEN_RE.sub(" ", text or "")
    text = MENTION_RE.sub(" ", text)
    text = RAW_URL_RE.sub(" ", text)
    return {w for w in NONWORD_RE.sub(" ", text.lower()).split()
            if len(w) > 2 and w not in STOPWORDS}


def load_model(bot_id=None):
    global model, corpus_size, pair_index
    if not os.path.exists(DATA_FILE):
        print(f"[model] {DATA_FILE} not found - run scraper.py first")
        model, corpus_size, pair_index = None, 0, []
        return

    latest = {}
    for rec in read_jsonl(DATA_FILE):
        rid = rec.get("id")
        if rid is not None:
            latest[rid] = rec

    corpus, seen_lines, pairs, unique_urls = [], set(), [], set()
    dropped = 0
    for rec in latest.values():
        if rec.get("bot"):
            continue
        if bot_id and rec.get("author_id") == bot_id:
            continue
        content = rec.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content.strip()

        if CORPUS_DEDUPE and content in seen_lines:
            dropped += 1
        else:
            seen_lines.add(content)
            corpus.append(content)

        urls = [media[t] for t in TOKEN_RE.findall(content)
                if t in media and is_sendable(media[t], media_kind.get(t, "link"))]
        if urls:
            unique_urls.update(urls)
            parent = latest.get(rec.get("reply_to")) if rec.get("reply_to") else None
            if parent and isinstance(parent.get("content"), str) and parent["content"].strip():
                parent_words = wordset(parent["content"])
                if parent_words:
                    pairs.extend((parent_words, u) for u in urls)

    corpus_size = len(corpus)
    pair_index = pairs
    if not corpus:
        print(f"[model] {DATA_FILE} has no usable messages")
        model = None
        return
    model = markovify.NewlineText("\n".join(corpus), state_size=2)
    extra = f" ({dropped} duplicate line(s) skipped)" if dropped else ""
    print(f"[model] trained on {corpus_size} lines{extra} | "
          f"{len(pairs)} retrieval pair(s), {len(unique_urls)} unique url(s)")


def generate_reply(prompt):
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
    q = wordset(prompt)
    if not q:
        return None

    scores = {}
    for words, url in pair_index:
        shared = len(q & words)
        if shared < 2:
            continue
        score = shared / len(q)
        if score >= RETRIEVAL_THRESHOLD and score > scores.get(url, 0.0):
            scores[url] = score
    if scores:
        top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:RETRIEVAL_TOPN]
        return random.choice(top)[0]

    hits = Counter(url for word in q for url in keyword_index.get(word, ()))
    if hits:
        best_url, n = hits.most_common(1)[0]
        if n >= 3:
            return best_url
    return None


def resolve_tokens(text):
    text = TOKEN_RE.sub(lambda m: media.get(m.group(0), ""), text or "")
    return SQUASH_RE.sub(" ", text).strip()


def sanitize_outgoing(text):
    text = EVERYONE_RE.sub("", text or "")
    return SQUASH_RE.sub(" ", text).strip()


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


def log_reply(message: discord.Message, reply: str):
    path = DM_LOG if isinstance(message.channel, discord.DMChannel) else CHAT_LOG
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
    if message.author.bot:
        return False
    if isinstance(message.channel, discord.DMChannel):
        return True
    matched = message.channel.id == CHANNEL_ID or client.user in message.mentions
    return matched and message.guild is not None and message.guild.id == GUILD_ID


@client.event
async def on_message(message: discord.Message):
    content = message.content or ""
    if content.strip().startswith("!dumb_reload"):
        if OWNER_ID and message.author.id != OWNER_ID:
            return
        if not OWNER_ID and not isinstance(message.channel, discord.DMChannel):
            return
        load_media()
        load_model(client.user.id)
        await message.reply(f"Reloaded: {corpus_size} lines, {len(media)} url tokens.")
        return

    if not should_reply(message):
        return

    reply = retrieve(content) if RETRIEVAL else None
    if not reply:
        reply = resolve_tokens(generate_reply(content))
    reply = sanitize_outgoing(reply)
    if not reply:
        return

    await send_reply(message, reply)
    log_reply(message, reply)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("[error] DISCORD_TOKEN missing - put it in .env")
    if not GUILD_ID or not CHANNEL_ID:
        raise SystemExit("[error] guild and channel must be set in config.toml")
    client.run(TOKEN)
