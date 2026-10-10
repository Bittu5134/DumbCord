# ChatCord

**A Discord bot that talks like your server.**
*Scrapes a channel's history, learns from it, and replies in the same voice.*

`@mention it` · `DM it` · or just talk in the configured channel

## How it works

Two scripts, one dataset:

| file | role |
|---|---|
| `scraper.py` | backfills + live-watches a channel into `messages.jsonl` |
| `bot.py` | answers DMs, `@mentions` and one channel, using `messages.jsonl` |

The channel it **learns from** (`discord.scrape_channel`) and the channel it **replies in**
(`discord.channel`) are separate settings — they can be the same channel or two different ones.

Stored content is **verbatim** — mentions, roles and `@everyone` are kept exactly as typed.
The only transformation is pasted URLs becoming `<url:HASH>` tokens, mapped in `media.jsonl`;
that is what the retrieval index matches on. Messages authored by a bot (including this bot)
and messages with no text are never stored.

### Two reply paths

1. **Retrieval** — someone said X, someone replied with a paste. Matching learned
   `text → url` pairs (plus slug keywords like `monica-crying`) sends that GIF/link back.
   Discord auto-embeds a bare URL, so no upload is needed.
2. **Markov** — a word-level chain (`state_size = 2`) trained on the corpus, deduplicated by
   default. Any `<url:...>` token it emits is resolved back to a real URL.

Ping neutralisation happens **at reply time, never in the dataset**: `@everyone`/`@here` are
deleted outright, and `AllowedMentions` blocks users/roles/everyone according to
`bot.outgoing_mentions`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install "discord.py>=2.3" markovify
```

Two config files, two jobs:

**`.env`** — the one secret, nothing else (gitignored):

```ini
DISCORD_TOKEN=...
```

**`config.toml`** — everything else (tracked, safe to commit):

```toml
[discord]
guild          = 123    # server id
channel        = 456    # where the bot replies
scrape_channel = 789    # where it learns from (falls back to channel)
owner          = 0      # user id allowed to run !dumb_reload (0 = DM only)
```

Enable **Message Content Intent** for the bot in the Discord developer portal, or it will
receive empty message text.

## Usage

```bash
python scraper.py                 # asks how many messages to save (Enter = as many as possible)
python scraper.py --limit 5000    # cap the backlog this run (new messages are never capped)
python scraper.py --all           # no cap
python scraper.py --watch         # stay online and capture new messages after backfilling
python scraper.py --no-check-edits
python scraper.py --channel <id> --guild <id>
```

Reruns resume from `chat_state.json`: each run first catches up on messages posted since the
last run, then continues walking backwards through history. Progress is flushed periodically
and on Ctrl-C, so interrupting is safe.

```bash
python bot.py
```

## Config

All settings live in `config.toml`. Sections are flattened on load (so nesting is purely for
readability), omitted keys fall back to the defaults below, and only the token plus the two
channel ids are actually required.

### `[discord]`

| key | default | used by | meaning |
|---|---|---|---|
| `guild` | — *required* | both | server id |
| `channel` | — *required* | bot | channel the bot replies in (scraper fallback) |
| `scrape_channel` | `channel` | scraper | channel to train from |
| `owner` | `0` | bot | user id allowed to run `!dumb_reload` (`0` = DM only) |

### `[scraper]`

| key | default | meaning |
|---|---|---|
| `data_file` | `messages.jsonl` | training corpus |
| `state_file` | `chat_state.json` | resume state |
| `media_file` | `media.jsonl` | `<url:HASH>` → url map |
| `url_tokens` | `true` | tokenise pasted URLs |
| `skip_bots` | `true` | skip every bot author, not just itself |
| `skip_empty` | `true` | skip messages with no text |
| `edit_scan` | `200` | recent messages rechecked for edits |

### `[bot]`

| key | default | meaning |
|---|---|---|
| `chat_log` / `dm_log` | `chats.jsonl` / `dms.jsonl` | reply logs |
| `outgoing_mentions` | `block` | see below |
| `retrieval` | `true` | try the url-retrieval path first |
| `retrieval_threshold` | `0.6` | query coverage needed to send a link |
| `retrieval_topn` | `5` | pick at random among the N best matches (`1` = always the best) |
| `retrieval_kinds` | `["gif", "image", "link"]` | which paste kinds may be sent |
| `corpus_dedupe` | `true` | train on unique lines only |
| `markov_test_output` | `true` | markovify's verbatim-copy filter |

### `outgoing_mentions`

| mode | users | roles | everyone | notes |
|---|---|---|---|---|
| `block` | ✗ | ✗ | ✗ | default |
| `silent` | ✗ | ✗ | ✗ | also marks the message silent in Discord |
| `allow_users` | ✓ | ✗ | ✗ | |
| `allow_all` | ✓ | ✓ | ✗ | `@everyone`/`@here` are always stripped regardless |

## Reloading

`!dumb_reload` retrains from `data_file` without a restart. With `owner` set it is owner-only;
with `owner = 0` it is only accepted over DM. Restarting the bot does the same thing.

## Data files

Gitignored and regenerated by the scripts: `messages.jsonl`, `media.jsonl`, `chat_state.json`,
`chats.jsonl`, `dms.jsonl` — plus `.env` (the token) and `.venv/`. **`config.toml` is tracked**,
so the repo stays runnable while the secret stays out of git.

Log entries look like:
`{ts, author_id, author, guild_id, channel_id, message, reply}`

## Built with

[discord.py](https://github.com/Rapptz/discord.py) · [Markovify](https://github.com/jsvine/markovify)
