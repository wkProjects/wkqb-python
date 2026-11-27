# wkQB bot

The chat bot of [wkqb.de](https://wkqb.de) for Webkicks chats. The web interface (wkqb-web) starts it as a
Kubernetes pod and serves its configuration.

The bot (`src/wkqb/`, asyncio) runs as `wkqb` in the image `ghcr.io/wkprojects/wkqb:<branch>`; every push builds
the image of its branch, and bots use `main` unless their `b_branch` in wkqb-web's database says otherwise.

## Development

Needs [uv](https://docs.astral.sh/uv/) and Python 3.14.

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run mypy
```

The tests use a fake Webkicks server (`tests/fake_webkicks.py`), never the real chat.

Run the new bot locally with a settings file instead of the web interface:

```sh
set -a; . ./bot.env; set +a     # WK_CHATURL, WK_USERNAME, WK_PASSWORD
WKQB_CONFIG_FILE=config.json uv run wkqb
```

It logs in again by itself when the chat session ends (e.g. the nightly maintenance) and only exits on `!quit`,
SIGTERM/SIGINT or when its account logs in elsewhere (exit code 0), or with 1 after 30 minutes without a working
session. The Webkicks protocol as far as known is in `docs/webkicks-protocol.md`.

## Tracer

`wkqb-tracer` records everything a chat account sees, to learn how Webkicks behaves (message format,
keep-alives, the nightly maintenance). It logs in like the bot, writes every HTTP exchange and every chunk of the
chat stream to `traces/<account>/<date>.jsonl`, and logs in again whenever the stream ends or anything fails. It only
stops on its own if the very first login fails, or if the account logs in elsewhere (Webkicks allows one session per
account, so logging in again would throw the other session out). Passwords (from 6 characters), session ids and stream
tokens are removed from the traces.

Only use it with test accounts, in a chat whose users know that it is recorded.

```sh
cat > bot-a.env <<'EOF'
WK_CHATURL=https://server1.webkicks.de/mychat/
WK_USERNAME=TestA
WK_PASSWORD=...
EOF

uv run wkqb-tracer --env-file bot-a.env run                  # until Ctrl+C (logs out)
uv run wkqb-tracer --env-file bot-a.env send "Hallo Welt"     # while "run" is running
```
