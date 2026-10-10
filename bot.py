"""
bot.py - replies to DMs, @mentions and messages in one configured channel.

Deps:  pip install "discord.py>=2.3" markovify

Run:   python bot.py

Replies are generated with a Markov chain trained on messages.jsonl (the file
produced by scraper.py). Messages authored by a bot are ignored while training.

Config (.env / environment):
    TOKEN       discord bot token                          (required)
    GUILD       guild id the @mention trigger applies to   (required)
    CHANNEL     channel id the bot always replies in       (required)
    OWNER       your user id, allowed to use !reload       (optional)
    DATA_FILE   training corpus                            (default messages.jsonl)
    CHAT_LOG    log of channel/guild replies               (default chats.jsonl)
    DM_LOG      log of DM replies                          (default dms.jsonl)
"""

import json
import os
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


load_dotenv()

TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD") or 0)
CHANNEL_ID = int(os.getenv("CHANNEL") or 0)
OWNER_ID = int(os.getenv("OWNER") or 0)
DATA_FILE = os.getenv("DATA_FILE", "messages.jsonl")
CHAT_LOG = os.getenv("CHAT_LOG", "chats.jsonl")
DM_LOG = os.getenv("DM_LOG", "dms.jsonl")

FALLBACKS = [
    "I need more data to ramble about that.",
    "Hmm, say that again?",
    "I'm not sure how to reply to that!",
]

# --------------------------------------------------------------- the model ---

model = None
corpus_size = 0


def load_model(bot_id=None):
    """Train a Markov chain on the dataset, ignoring bot-authored messages."""
    global model, corpus_size
    if not os.path.exists(DATA_FILE):
        print(f"[model] {DATA_FILE} not found - run scraper.py first")
        model, corpus_size = None, 0
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
            if rid is None:
                continue
            latest[rid] = rec

    corpus = []
    for rec in latest.values():
        if rec.get("bot"):
            continue
        if bot_id and rec.get("author_id") == bot_id:
            continue
        content = rec.get("content")
        if isinstance(content, str) and content.strip():
            corpus.append(content.strip())

    corpus_size = len(corpus)
    if not corpus:
        print(f"[model] {DATA_FILE} has no usable messages")
        model = None
        return

    model = markovify.NewlineText("\n".join(corpus), state_size=2)
    print(f"[model] trained on {corpus_size} messages")


def generate_reply(prompt, bot_id=None):
    if model is None:
        return "I don't have a dataset to reply from yet."
    prompt = " ".join(w for w in prompt.split() if not w.startswith("<@"))
    for word in prompt.split():
        try:
            reply = model.make_sentence_with_start(word, strict=False)
        except Exception:
            reply = None
        if reply:
            return reply
    return model.make_sentence() or FALLBACKS[hash(prompt) % len(FALLBACKS)]


# --------------------------------------------------------------------- log ---

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
    load_model(client.user.id)
    print(f"[login] {client.user} ({client.user.id})")
    print(f"[ready] DMs = yes | channel = {CHANNEL_ID} | guild for @mentions = {GUILD_ID}")


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
        load_model(client.user.id)
        await message.reply(f"Reloaded: {corpus_size} messages.")
        return

    if not should_reply(message):
        return

    reply = generate_reply(message.content or "", client.user.id)
    if not reply:
        return

    await message.reply(reply)
    is_dm = isinstance(message.channel, discord.DMChannel)
    log_reply(DM_LOG if is_dm else CHAT_LOG, message, reply)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("[error] TOKEN missing (.env or environment)")
    if not GUILD_ID or not CHANNEL_ID:
        raise SystemExit("[error] GUILD and CHANNEL must be set in .env")
    client.run(TOKEN)
