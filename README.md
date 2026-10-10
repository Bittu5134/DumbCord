# ChatCord

**A Discord bot that talks like your server.**  
*Scrapes your channel history, learns from it, and replies in the same voice.*

```yaml
!reload  ·  @mention it  ·  or just DM it
```

## How It Works

- `scraper.py` backfills and live-watches a channel into `messages.jsonl`.
- Replies are generated with a Markov chain trained on your corpus; an LSTM (`text.py` → `generate.py`) is included for fun.
- The bot answers @mentions, DMs, and everything in one configured channel.

## Run Locally

```bash
pip install "discord.py>=2.3" markovify
python scraper.py        # collect the corpus
python bot.py            # let it loose
```

<sub>Configurable via `TOKEN` · `GUILD` · `CHANNEL` in `.env`</sub>

<br>

## Built Using

[discord.py](https://github.com/Rapptz/discord.py) · [Markovify](https://github.com/jsvine/markovify) · [PyTorch](https://github.com/pytorch/pytorch)
