"""Shared config for scraper.py and bot.py: .env holds the token, config.toml the rest."""

import json
import os
import tomllib

DEFAULTS = {
    "guild": 0, "channel": 0, "scrape_channel": 0, "owner": 0,
    "data_file": "messages.jsonl", "state_file": "chat_state.json",
    "media_file": "media.jsonl", "url_tokens": True, "skip_bots": True,
    "skip_empty": True, "edit_scan": 200,
    "chat_log": "chats.jsonl", "dm_log": "dms.jsonl",
    "outgoing_mentions": "block", "retrieval": True, "retrieval_threshold": 0.6,
    "retrieval_topn": 5, "retrieval_kinds": ["gif", "image", "link"],
    "corpus_dedupe": True, "markov_test_output": True,
}


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def load_config(path="config.toml"):
    cfg = dict(DEFAULTS)
    if not os.path.exists(path):
        print(f"[config] {path} not found, using built-in defaults")
        return cfg
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    for key, value in raw.items():
        cfg.update(value if isinstance(value, dict) else {key: value})
    return cfg


def read_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


load_dotenv()
CFG = load_config()
