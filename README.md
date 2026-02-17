# Telegram Migrator

A robust CLI tool for archiving Telegram channels/chats with full media support

Can be used for two jobs:
- `dump`: archive Telegram chats/channels to local files.
- `transfer`: forward/copy messages from one Telegram chat to another.

## Install

```bash
pip install -e .
```

## Help

```bash
telegram-migrator --help
telegram-migrator dump --help
telegram-migrator transfer --help
```

You can also run the module form:

```bash
python src/main.py --help
```

## Credentials

Get Telegram API credentials from https://my.telegram.org/apps and set:

```bash
export TG_API_ID=your_api_id
export TG_API_HASH=your_api_hash
```

Or use `.env` from `.env.example`.

## Quick Start

```bash
# Archive a channel
telegram-migrator dump -c @channelname -o ./backup

# Transfer full history from source chat to destination chat
telegram-migrator transfer -c @source --dest @target -o ./transfer-run

# Transfer one specific message by link
telegram-migrator transfer \
  -c "https://t.me/c/123456789/42" \
  --dest @target \
  -o ./transfer-one
```

## Commands

### dump

Archive messages/media from a source chat.

```bash
telegram-migrator dump -c <source> -o <output_dir> [options]
```

Supported options:
- `--media-types photos,videos,documents,audio,voice,video_notes,all`
- `--no-media`
- `--limit <n>`
- `--date-from <YYYY-MM-DD>`
- `--date-to <YYYY-MM-DD>`
- `--skip-service-messages`
- `--select <selector>` (repeatable)
- `--accurate-size`
- `--retry-failed`

Selector syntax for `--select`:
- `id:<n>`
- `range:<start-end>`
- `link:<https://t.me/...>`
- direct numeric IDs
- direct Telegram message links

Examples:

```bash
# Messages only
telegram-migrator dump -c @channel -o ./backup --no-media

# Date window + media filter
telegram-migrator dump -c @channel -o ./backup \
  --media-types photos,videos \
  --date-from 2024-01-01 \
  --date-to 2024-12-31

# Specific messages
telegram-migrator dump -c @channel -o ./backup \
  --select id:120 \
  --select range:200-220
```

Resume behavior: rerun the same `dump` command; processed state is reused automatically.

### transfer

Transfer messages from source to destination (forward/copy).

```bash
telegram-migrator transfer -c <source> --dest <destination> -o <output_dir> [options]
```

Supported options:
- `--mode forward|copy`
- `--profile safe|balanced|fast`
- `--skip-known-bad`
- `--db <path_to_sqlite_db>`

Behavior:
- If `-c` is a chat spec (`@name`, `title:...`, `id:...`, `invite:...`), transfer processes history with resume state.
- If `-c` is a **Telegram message link**, transfer sends **only that message**.

Examples:

```bash
# Full transfer
telegram-migrator transfer \
  -c "title:Source Chat" \
  --dest "id:-1001234567890" \
  --mode forward \
  --profile balanced \
  -o ./transfer-full

# Single-message transfer from link
telegram-migrator transfer \
  -c "https://t.me/c/123456789/42" \
  --dest @target \
  --mode forward \
  -o ./transfer-single
```

## Source / Destination Formats

Accepted chat specs:
- `@username`
- `username`
- `https://t.me/<username>`
- `title:<exact dialog title>`
- `id:<numeric id>`
- `invite:<t.me/+hash>` or `invite:<t.me/joinchat/hash>`

Message link formats:
- `https://t.me/c/<internal_chat_id>/<message_id>`
- `https://t.me/<username>/<message_id>`

## Global Runtime Options

Available on both subcommands:
- `--session <path>`
- `--api-id <id>`
- `--api-hash <hash>`
- `--proxy <socks5://...|http://...>`
- `--debug`

If you use `--proxy`, ensure PySocks is available:

```bash
pip install pysocks
```

## Output

`dump` output:

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

`transfer` state DB default:
- `<output_dir>/.transfer_state.sqlite3`

## Troubleshooting

- `API credentials required`:
  - Set `TG_API_ID` and `TG_API_HASH`, or pass `--api-id/--api-hash`.
- `Channel/user not found`:
  - Verify source/destination spec and account access.
- `FloodWait`:
  - Normal Telegram rate limiting; tool backs off and resumes.
- `Proxy support requires PySocks`:
  - Install `pysocks`.

## Security Note

- Keep your `.session` file private.
