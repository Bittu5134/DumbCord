<div align="center">

# ChatCord

**A Discord bot that learns from your chats — and becomes your server.**

Show it your server's chat history and it starts talking like all of you — sending the
same GIFs and links your members actually use, and saying the things you all say.

No LLM. No API key. No filter. Everything stays on your machine, so the bot is as
unfiltered as your server is. A true mirror.

<sub>`@mention` it · DM it · or just talk in the configured channel — [detailed usage →](USAGE.md)</sub>

<br>

</div>

## How It Works

- **`scraper.py`** downloads a channel's full message history into a small JSONL dataset.
  Links people paste get tokenised into `<url:HASH>` placeholders, with a side map that
  resolves them back to real URLs later.
- **`bot.py`** trains on that dataset and answers two ways:
  - **Retrieval** — it remembers "someone said X and someone replied with this paste", so
    when your message looks like X, it sends that exact GIF/image/link back.
  - **Markov** — if nothing matches, it generates a fresh message from a word-level chain
    built from your server's history.

Point the learning channel and the reply channel at the same place, or keep them separate.

## Quickstart

```bash
git clone <this repo> && cd ChatCord
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your bot token into .env
python scraper.py             # build the corpus  (Enter = as much as possible)
python bot.py                 # start replying
```

That's the whole setup — `config.toml` already has the server/channel ids filled in.
Needs **Python 3.11+** and a bot with **Message Content Intent** enabled
([portal setup](USAGE.md#discord-developer-portal-setup)). Re-run the scraper anytime to
refresh what the bot knows; [`!dumb_reload`](USAGE.md#reloading) retrains without a restart.

<sub>[Full configuration](USAGE.md#configuration) · [Troubleshooting](USAGE.md#troubleshooting)</sub>

<br>

## Built Using

[discord.py](https://github.com/Rapptz/discord.py) · [Markovify](https://github.com/jsvine/markovify)

<br>
