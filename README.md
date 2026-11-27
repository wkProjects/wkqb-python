# wkQB bot

The chat bot of [wkqb.de](https://wkqb.de) for Webkicks chats. The web interface (wkqb-web) starts it as a
Kubernetes pod and serves its configuration.

The bot is being rewritten with asyncio in `src/wkqb/`. Until that is finished, the image still runs the old bot
(`app.py` and the other modules in the repository root).

## Development

Needs [uv](https://docs.astral.sh/uv/) and Python 3.14.

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run mypy
```

The tests use a fake Webkicks server (`tests/fake_webkicks.py`), never the real chat.

## Tracer

`wkqb-tracer` records everything a chat account sees, to learn how Webkicks behaves (message format,
keep-alives, the nightly maintenance). It logs in like the bot, writes every HTTP exchange and every chunk of the
chat stream to `traces/<account>/<date>.jsonl`, and logs in again whenever the stream ends or anything fails. It only
gives up if the very first login fails. Passwords (from 6 characters) and session ids are removed from the traces.

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
