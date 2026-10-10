# DumbCord — Detailed Usage

Everything the short [README](README.md) leaves out. DumbCord learns from your server's own
chat history and becomes its unfiltered mirror — no LLM, no API key, no filter layer.

1. **`scraper.py`** downloads a channel's full history into a lean JSONL dataset, then can
   stay online to keep capturing new messages.
2. **`bot.py`** learns from that dataset and replies to DMs, `@mentions` and every message
   in one configured channel.

The channel it **learns from** (`discord.scrape_channel`) and the channel it **replies in**
(`discord.channel`) are separate settings — point them at the same channel for a bot that
chats where it learned, or at two different channels to keep the training ground quiet.

## Contents

- [Quickstart](#quickstart)
- [How it works](#how-it-works)
- [Discord developer portal setup](#discord-developer-portal-setup)
- [Configuration](#configuration)
- [Usage](#usage)
- [Data files](#data-files)
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

## How it works

```
channel history ──▶ scraper.py ──▶ messages.jsonl   (the corpus, verbatim)
                                   media.jsonl      (<url:HASH> → url map)
                                   chat_state.json  (resume point)

messages.jsonl ──▶ bot.py ──▶ retrieval index  (learned "someone said X → paste" pairs)
                              Markov chain     (word-level, state_size = 2)
                     └─▶ replies to DMs / @mentions / the configured channel
```

### The dataset

Stored content is **verbatim** — mentions, roles and `@everyone`/`@here` are kept exactly as
typed. The only transformation is pasted URLs becoming `<url:HASH>` tokens (mapped back to
real URLs in `media.jsonl`), which keeps the corpus free of long noisy links and gives the
retrieval index something stable to match on.

Messages authored by a bot (including this bot) and messages with no text are never stored.
Edited messages are stored again with the same id; consumers keep the last record. Reruns are
cheap: `chat_state.json` remembers how far the scraper got.

### Two reply paths

1. **Retrieval** — someone said X and someone replied with a paste. The bot matches your
   message against learned `text → url` pairs by word overlap and sends that GIF/image/link
   back. One of the top `retrieval_topn` matches is picked at random, so the same input
   doesn't always get the same paste.
2. **Markov** — a word-level chain (`state_size = 2`) trained on the corpus, deduplicated by
   default (`corpus_dedupe`) because chat corpora repeat themselves heavily. Any `<url:...>`
   token it emits is resolved back to the real URL.

Retrieval is tried first; if it finds nothing, the Markov path answers. Expired CDN links
(URLs carrying an `ex=` signature) are never sent.

### Ping safety

The corpus is verbatim, so the bot *can* generate real mentions. Every ping is neutralised
at reply time, never in the dataset:

- `@everyone` / `@here` are deleted outright in **every** mode.
- Users, roles and everyone-else pings are governed by `bot.outgoing_mentions` via discord.py's
  `AllowedMentions` (`block` by default).

## Discord developer portal setup

1. Create an application at <https://discord.com/developers/applications>.
2. **Bot** tab → *Reset Token* → paste it into `.env` as `DISCORD_TOKEN`.
3. Still on the **Bot** tab, enable **Message Content Intent** under
   *Privileged Gateway Intents*. Without it Discord delivers empty message text and the bot
   has nothing to learn from or reply to.
4. **OAuth2 → URL Generator**: scope `bot`, permissions at least
   *View Channels*, *Read Message History*, *Send Messages*. Open the generated URL and
   invite the bot to your server.
5. Turn on **Developer Mode** in Discord (*Settings → Advanced*), then right-click the
   server, channel and (optionally) your own user → *Copy ID* to fill in `config.toml`.

## Configuration

All settings live in `config.toml`. Sections are flattened on load (nesting is purely for
readability), omitted keys fall back to the defaults below, and only `guild` + `channel` are
actually required. `.env` holds the one secret:

```bash
cp .env.example .env      # then put DISCORD_TOKEN=... in it
```

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
| `retrieval_threshold` | `0.6` | 0..1 query coverage needed to send a link |
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

## Usage

### Scraping

```bash
python scraper.py                 # asks how many messages to save (Enter = as many as possible)
python scraper.py --limit 5000    # cap the backlog this run
python scraper.py --all           # no cap
python scraper.py --watch         # stay online and capture new messages after backfilling
python scraper.py --no-check-edits
python scraper.py --channel <id> --guild <id>   # override ids from config.toml
```

Reruns resume from `chat_state.json`: each run first catches up on messages posted since the
last run, then continues walking backwards through history. Progress is flushed every 100
saved messages and on Ctrl-C, so interrupting is always safe. A run finishes with a quick
re-scan of the last `edit_scan` messages to pick up edits.

### Running the bot

```bash
python bot.py
```

On login the bot loads `media.jsonl`, trains on `messages.jsonl`, and then replies to:

| trigger | where |
|---|---|
| any direct message | DMs |
| `@DumbCord ...` | any channel in `guild` |
| any message | the `channel` |

### Reloading

`!dumb_reload` retrains from `data_file` without a restart — run it after re-scraping. With
`owner` set it is owner-only; with `owner = 0` it is only accepted over DM. Restarting the
bot does the same thing.

## Data files

Everything below is gitignored and regenerated by the scripts.

| file | written by | contents |
|---|---|---|
| `messages.jsonl` | scraper | one message per line, newest record wins on duplicate ids |
| `media.jsonl` | scraper | `<url:HASH>` token → url map (one line per new URL) |
| `chat_state.json` | scraper | resume point: oldest/newest ids, totals, `reached_beginning` |
| `chats.jsonl` / `dms.jsonl` | bot | one log line per reply |
| `.env` | you | the bot token — never committed |
| `.venv/` | venv | local environment |

A `messages.jsonl` record:

```json
{"id": 123, "author_id": 456, "author": "name", "content": "text with <url:0abc123def>",
 "created_at": "2024-01-01T00:00:00+00:00", "reply_to": 100, "edited_at": "...", "bot": true}
```

A `media.jsonl` record:

```json
{"token": "<url:0abc123def>", "url": "https://...", "host": "tenor.com",
 "slug": "monica-crying", "kind": "gif", "added_at": "2024-01-01T00:00:00+00:00"}
```

A reply log entry:

```json
{"ts": "...", "author_id": 456, "author": "name", "guild_id": 1, "channel_id": 2,
 "message": "what the bot was replying to", "reply": "what it sent"}
```

## Project structure

```
config.py       shared config loading (.env + config.toml) and JSONL reading
scraper.py      backfill + live-watch a channel into the dataset
bot.py          the reply bot (retrieval + Markov)
config.toml     all settings except the token
.env.example    template for .env (which holds DISCORD_TOKEN)
requirements.txt
```

## Troubleshooting

| symptom | fix |
|---|---|
| `[error] DISCORD_TOKEN missing` | `cp .env.example .env` and put your token in it |
| replies are empty / the bot ignores text | Message Content Intent is off (see [setup step 3](#discord-developer-portal-setup)) |
| `[error] guild ... not found` | the bot isn't in the server, or the id is wrong |
| `[error] missing 'Read Message History' permission` | grant *Read Message History* in the channel |
| the bot never sends GIFs/links | no reply-to pairs in the corpus yet, or `retrieval_threshold` is too high |
| links go dead after a while | Discord CDN links expire; those (`ex=`) are skipped on purpose |
| replies feel repetitive | lower `retrieval_topn`, keep `corpus_dedupe = true`, or scrape more history |
| changes to `messages.jsonl` don't show | run `!dumb_reload` or restart the bot |
