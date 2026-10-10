<div align="center">

# DumbCord

**A Discord bot that learns from your server chats — and becomes you!**

No LLM. No API key. No filter. Everything stays on your machine, so the bot is as
unfiltered as your server is. A true mirror.

<sub>`@mention` it · DM it · or just talk in the configured channel — [detailed usage →](USAGE.md)</sub>

<br>

</div>

## How It Works

- A scraper script first downloads the server history to train the bot
- The actual bot is then run, which loads this message history and uses retrieval and Markov methods to reply to users with text and GIFs.

## Quickstart

This is the shortest path to get the bot running locally.

1. Clone the project and enter the folder.
2. Create a virtual environment and install the Python dependencies.
3. Copy the environment example file and add your Discord bot token.
4. Run the scraper to download and build the training corpus from your server history.
5. Start the bot so it can answer in the configured Discord channel, DM, or when mentioned.

```bash
git clone https://github.com/Bittu5134/DumbCord.git && cd DumbCord
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # paste your bot token into .env
python scraper.py             # downloads chat history and builds the knowledge base
python bot.py                 # starts the bot and begins replying
```

`config.toml` is already set up with the default server/channel IDs, so you can get started quickly without extra edits. If you want to change which server or channel it listens to, see the configuration guide.

This project requires **Python 3.11+** and a Discord bot with **Message Content Intent** enabled in the developer portal ([portal setup](USAGE.md#discord-developer-portal-setup)).

After the initial scrape, run `python scraper.py` again anytime you want to refresh what the bot has learned. You can also use [`!dumb_reload`](USAGE.md#reloading) to retrain the bot without restarting it.

<sub>[Full configuration](USAGE.md#configuration) · [Troubleshooting](USAGE.md#troubleshooting)</sub>

### Basic Usage

Once the bot is online, you can:

- Mention it with `@DumbCord your message`
- Send it a direct message
- Talk in the configured channel

<br>

## Built Using

[discord.py](https://github.com/Rapptz/discord.py) · [Markovify](https://github.com/jsvine/markovify)

<br>
