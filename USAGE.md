# DumbCord — Usage Guide

DumbCord learns from your server's own chat history and becomes its unfiltered
mirror — no LLM, no API key, no filter layer. Scrape your server, run the bot,
and it talks like your server talks.

## Contents

- [Quickstart](#quickstart)
- [Discord developer portal setup](#discord-developer-portal-setup)
- [Configuration](#configuration)
- [Using the bot](#using-the-bot)
- [Troubleshooting](#troubleshooting)

## Quickstart

```bash
# 1. Clone & set up environment
git clone https://github.com/Bittu5134/DumbCord.git && cd DumbCord
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure credentials & target channels
cp .env.example .env          # paste your DISCORD_TOKEN
# adjust server & channel IDs in config.toml if needed

# 3. Scrape history & run
python scraper.py             # download messages (press Enter for max history)
python bot.py                 # start listening and replying
```

> **Note:** Requires **Python 3.11+** and **Message Content Intent** enabled in your
> [Discord Developer Portal](#discord-developer-portal-setup).

## Discord developer portal setup

1. Create an application at <https://discord.com/developers/applications>.
2. **Bot** tab → *Reset Token* → paste it into `.env` as `DISCORD_TOKEN`.
3. Still on the **Bot** tab, enable **Message Content Intent** under
   *Privileged Gateway Intents*. Without it Discord delivers empty message text
   and the bot has nothing to learn from or reply to.
4. **OAuth2 → URL Generator**: scope `bot`, permissions at least
   *View Channels*, *Read Message History*, *Send Messages*. Open the generated
   URL and invite the bot to your server.
5. Turn on **Developer Mode** in Discord (*Settings → Advanced*), then
   right-click the server and channel → *Copy ID* to fill in `config.toml`.

## Configuration

All settings live in `config.toml`; the token lives in `.env`. Only two keys
are actually required:

```toml
[discord]
guild   = 1234567890123456789   # your server id
channel = 9876543210987654321   # channel the bot chats in
```

Optional extras:

| key | default | meaning |
|---|---|---|
| `scrape_channel` | `channel` | train from a different channel than it chats in |
| `owner` | `0` | user id allowed to run `!dumb_reload` (`0` = DM only) |

   everything else comes with default preset.

## Using the bot

### 1. Scrape your server

```bash
python scraper.py               # asks how many messages to save (Enter = as many as possible)
python scraper.py --watch       # stay online and keep capturing new messages after backfilling
```

Rerun it any time to catch up on newer messages — it resumes where it left
off, and interrupting with Ctrl-C is always safe.

### 2. Run the bot

```bash
python bot.py
```

### 3. Talk to it

- `@DumbCord <message>` anywhere in the server
- Direct message the bot
- Just chat in the configured channel — it replies to every message there
- Run `!dumb_reload` after scraping new messages to retrain without a restart

The bot answers in one of two ways: it either sends back a GIF, image or link
that someone in your server once replied with to something similar, or it
generates a sentence from your server's own words. Either way — it's your
server talking back to you.

### Data files

The scripts generate a few data files (`messages.jsonl`, `chat_state.json`,
`media.jsonl`, reply logs) next to the code. They are gitignored and
regenerated automatically — you never need to touch them.

## Troubleshooting

| symptom | fix |
|---|---|
| `[error] DISCORD_TOKEN missing` | `cp .env.example .env` and put your token in it |
| replies are empty / the bot ignores text | Message Content Intent is off (see [setup step 3](#discord-developer-portal-setup)) |
| `[error] guild ... not found` | the bot isn't in the server, or the id is wrong |
| `[error] missing 'Read Message History' permission` | grant *Read Message History* in the channel |
| the bot never sends GIFs/links | it needs reply history first — scrape more messages |
| replies feel repetitive | scrape more history so it has more to draw from |
| scraped messages don't show up | run `!dumb_reload` or restart the bot |
