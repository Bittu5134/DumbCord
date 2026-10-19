<div align="center">

<img src="./assets/logo.png" alt="DumbCord mascot" width="270">

# DumbCord

**A really dumb Discord bot that learns from your server chats!**

Built on Markov chains and hot-takes. No AI. No LLM. No API. Just unfiltered chaos.

</div>


## How It Works

- A scraper script first downloads the server history to train the bot.
- The actual bot is then run, which loads its message history and uses retrieval and Markov chains to generate text replies and GIF responses.
- [Read more on my blog](https://bittu.dev/blog/a-very-dumb-markov-chatbot/)

## Quickstart

You can self-train and host the bot, or try it out on my own Discord server:

[![Join my Discord server](https://img.shields.io/badge/Discord-Join%20server-5865F2?logo=discord&logoColor=white)](https://bittu.dev/discord/)

```bash
# Installation
git clone https://github.com/Bittu5134/DumbCord.git && cd DumbCord
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configuration
cp .env.example .env          # paste your DISCORD_TOKEN

# Scrape history and run
python scraper.py             # download messages
python bot.py                 # start listening and replying
```

> [!IMPORTANT]
> Change the server and channel IDs in `config.toml` to point to your desired channel.

> [!NOTE]
> Requires **Message Content Intent** enabled in your [Discord Developer Portal](USAGE.md#discord-developer-portal-setup).

### Interacting with the Bot

- `@DumbCord <message>` anywhere in the server.
- Direct-message the bot.
- Chat directly in the configured channel.
- Use `!dumb_reload` after scraping new messages to retrain on the fly.

### [Documentation →](USAGE.md)

<br>

## Screenshots

<div align="center">

<img src="./assets/showcase.webp" alt="DumbCord in action — a grid of real replies from the bot" width="813">

<sub>Real replies from the [server](https://bittu.dev/discord/) · names pixelated for privacy</sub>


</div>

## Built Using

[discord.py](https://github.com/Rapptz/discord.py) · [Markovify](https://github.com/jsvine/markovify)

<br>
