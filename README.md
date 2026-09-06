# Telegram Migrator

Command-line tool to back up Telegram chats and channels, or move messages from one chat to another.

Two commands:

- `dump` — save messages and media from a chat to local files.
- `transfer` — forward or copy messages from one chat to another.

It runs on your own user account (not a bot) using [Telethon](https://github.com/LonamiWebs/Telethon).

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/warl0ckwraith/telegram-migrator/actions/workflows/ci.yml/badge.svg)](https://github.com/warl0ckwraith/telegram-migrator/actions/workflows/ci.yml)

## Requirements

- Python 3.8 or newer
- A Telegram API ID and hash (see below)

## Install

```bash
pip install -e .
```

This adds a `telegram-migrator` command. You can also run it without installing:

```bash
python src/main.py --help
```

## API credentials

Get an API ID and hash from https://my.telegram.org/apps. Then do one of:

```bash
export TG_API_ID=your_api_id
export TG_API_HASH=your_api_hash
```

or copy `.env.example` to `.env` and fill it in, or pass `--api-id` and `--api-hash` on the command line.

The first run asks for your phone number and a login code.

## Quick start

```bash
# Save a channel to ./backup
telegram-migrator dump -c @channelname -o ./backup

# Copy every message from one chat to another
telegram-migrator transfer -c @source --dest @target -o ./transfer-run

# Send a single message by its link
telegram-migrator transfer -c "https://t.me/c/123456789/42" --dest @target -o ./one
```

Both commands save their progress. Run the same command again and it continues from where it stopped.

## dump

```bash
telegram-migrator dump -c <source> -o <output_dir> [options]
```

| Option | What it does |
| --- | --- |
| `--media-types <list>` | Comma list: `photos,videos,documents,audio,voice,video_notes,all` |
| `--no-media` | Save messages only, skip downloads |
| `--limit <n>` | Stop after n messages |
| `--date-from <YYYY-MM-DD>` | Only messages on or after this date |
| `--date-to <YYYY-MM-DD>` | Only messages on or before this date |
| `--skip-service-messages` | Skip joins, leaves, and similar |
| `--select <selector>` | Pick specific messages (can be used more than once) |
| `--accurate-size` | Scan everything for an exact size estimate (slower) |
| `--retry-failed` | Retry downloads that failed on an earlier run |

`--select` accepts any of:

- `id:<n>` — one message
- `range:<start-end>` — a range of message IDs
- `link:<https://t.me/...>` — a message link
- a plain number, or a plain message link

Examples:

```bash
# Messages only, no media
telegram-migrator dump -c @channel -o ./backup --no-media

# One year, photos and videos only
telegram-migrator dump -c @channel -o ./backup \
  --media-types photos,videos --date-from 2024-01-01 --date-to 2024-12-31

# Specific messages
telegram-migrator dump -c @channel -o ./backup --select id:120 --select range:200-220
```

## transfer

```bash
telegram-migrator transfer -c <source> --dest <destination> -o <output_dir> [options]
```

| Option | What it does |
| --- | --- |
| `--mode forward\|copy` | `forward` keeps the "forwarded from" label. `copy` sends a clean copy |
| `--profile safe\|balanced\|fast` | How fast to send. `safe` is slowest and least likely to hit limits |
| `--skip-known-bad` | Skip message IDs that already failed before |
| `--db <path>` | Where to keep transfer state (default: `<output_dir>/.transfer_state.sqlite3`) |

If `-c` is a chat (`@name`, `title:...`, `id:...`, `invite:...`), transfer goes through the whole history.
If `-c` is a message link, transfer sends only that one message.

```bash
# Whole history
telegram-migrator transfer -c "title:Source Chat" --dest "id:-1001234567890" \
  --mode forward --profile balanced -o ./transfer-full

# One message
telegram-migrator transfer -c "https://t.me/c/123456789/42" --dest @target -o ./one
```

## Naming a chat

For `-c` and `--dest` you can use:

- `@username` or just `username`
- `https://t.me/<username>`
- `title:<exact chat title>`
- `id:<numeric id>`
- `invite:<t.me/+hash>` or `invite:<t.me/joinchat/hash>`

Message links:

- `https://t.me/c/<chat_id>/<message_id>`
- `https://t.me/<username>/<message_id>`

## Options on both commands

- `--session <path>` — session file location (default: `~/.telegram-migrator.session`)
- `--api-id <id>` / `--api-hash <hash>`
- `--proxy <socks5://...|http://...>` — needs `pip install pysocks`
- `--debug` — print full error traces

## Output

```text
output_dir/
├── metadata.json
├── summary.json
├── messages.jsonl
├── media/
│   ├── photos/
│   ├── videos/
│   ├── documents/
│   ├── audio/
│   ├── voice/
│   └── video_notes/
└── .dump_state.db
```

## Common problems

- **`API credentials required`** — set `TG_API_ID` and `TG_API_HASH`, or pass `--api-id` and `--api-hash`.
- **`Channel/user not found`** — check the name and make sure your account can see that chat.
- **`FloodWait`** — Telegram is rate-limiting you. The tool waits and carries on by itself. Use a slower `--profile` if it keeps happening.
- **`Proxy support requires PySocks`** — run `pip install pysocks`.

## Legal

Use this only for content you have the right to keep. You are responsible for following Telegram's Terms of Service, copyright law, other people's privacy, and any data protection rules that apply to you. Get permission before you archive content that isn't yours. The author is not responsible for how you use this tool.

## Keep your session file safe

The `.session` file lets anyone log in as you. Don't share it and don't commit it.

## License

MIT. See [LICENSE](LICENSE).
