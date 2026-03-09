# Telegram Clock In

Versao em portugues: [README.md](README.md)

Telegram bot to track work punches (`entrada`, `almoco`, `entrada_2`, and `saida`) in a group and generate a monthly timesheet automatically.

## Features

- User punch tracking in a Telegram group
- Batch punch registration with optional targets (example: `/entrada me coworker`)
- Simple commands:
  - `/help`
  - `/entrada`
  - `/almoco`
  - `/entrada_2`
  - `/saida`
  - `/status` (your latest punch)
  - `/clear [date] [users]` (deletes punches for today or for a specific date)
  - `/corrigir <type> <HH:MM> [users]` (manual correction for today)
  - `/corrigir <h1> <h2> <h3> <h4> [users]` (block correction for today)
  - `/corrigir <date> <type> <HH:MM> [users]` (manual correction for another day)
  - `/corrigir <date> <h1> <h2> <h3> <h4> [users]` (block correction for another day)
  - `/mes` (generate previous month spreadsheet on demand)
  - `/mes_atual` (generate current month partial spreadsheet)
  - `/mes_png` (generate previous month PNG table, one per user)
  - `/mes_png_atual` (generate current month PNG table, one per user)
  - `/chat_id` (show current group chat ID)
- Automatic monthly `.xlsx` report generation
- Automatic alert at 20:00 when there are pending punches
- Manual `.png` report generation per user to share in the group
- Local SQLite storage (simple and portable)

## Requirements

- Python 3.10+

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Add the bot to the group where punches will be tracked.
3. Get the group `chat_id` (negative value, usually starts with `-100...`).
4. Copy the example environment file:

```bash
cp .env.example .env
```

5. Edit `.env`:

```env
BOT_TOKEN=...
TARGET_CHAT_ID=-100...
TIMEZONE=America/Sao_Paulo
# Optional:
# FIXED_USERS=me=11111111|Your Name;coworker=22222222|Coworker Name
```

If you want strict alias mapping (recommended), configure `FIXED_USERS`.
Then command arguments such as `me` and `coworker` always resolve to the same user IDs.

## Running Locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

Or with Makefile:

```bash
make setup
make run
```

## How Monthly Closing Works

- The bot schedules execution on day **1 of each month at 08:00** (configured timezone).
- It generates the **previous month** spreadsheet and sends it to the configured group.
- It sends a daily alert at **20:00** if there are pending punches.

## Project Structure

- `src/main.py`: Telegram handlers, punch rules, and scheduling
- `src/storage.py`: SQLite access layer
- `src/report.py`: `.xlsx` and `.png` report generation
- `data/MM_YYYY.db`: automatically created monthly databases (example: `03_2026.db`)
- `reports/`: generated reports
- `.github/workflows/ci.yml`: automatic validation on GitHub Actions
- `CONTRIBUTING.md`: contribution guidelines
- `SECURITY.md`: security and vulnerability reporting policy
- `Makefile`: shortcuts for setup, run, and checks

## Safe GitHub Publishing

- The following patterns are already blocked in `.gitignore`:
  - `.env` and `.env.*` (except `.env.example`)
  - `data/`, `reports/`, `*.db`, `*.sqlite`, `*.sqlite3`
  - `venv/`, `.venv/`, caches, and local IDE files
- Before first push, run:

```bash
make check
git status
```

## Notes

- If someone breaks the sequence (for example, forgets `/saida`), the day is marked as pending in reports.
- Supported sequences: `entrada -> almoco`, `entrada_2 -> saida`, and `entrada -> almoco -> entrada_2 -> saida`.
- Commands `/entrada`, `/almoco`, `/entrada_2`, `/saida`, `/clear`, and `/corrigir` accept optional user targets by known name in the group (or `me`).
- The bot validates commands only in `TARGET_CHAT_ID`.
- Punch records are partitioned by month into separate `.db` files.
- To generate a manual spreadsheet, use `/mes`.
- To generate a partial current-month spreadsheet, use `/mes_atual`.
- To generate per-user table images, use `/mes_png`.
- To generate partial current-month per-user images, use `/mes_png_atual`.
- To delete punches, use `/clear [YYYY-MM-DD|DD/MM/YYYY] [users]`.
- Today example: `/clear me coworker`.
- Specific date example: `/clear 02/03/2026 gustavo caio`.
- To add manual correction for today, use `/corrigir <entrada|almoco|entrada_2|saida> <HH:MM> [users]`.
- To add manual correction for another date, use `/corrigir <YYYY-MM-DD|DD/MM/YYYY> <entrada|almoco|entrada_2|saida> <HH:MM> [users]`.
- Block correction format: `entrada almoco entrada_2 saida` (use `-` to skip a slot).
- Example: `/corrigir 02/03/2026 09:00 13:00 14:00 18:30 gustavo caio`.
- Partial example: `/corrigir 02/03/2026 09:00 13:00 - - gustavo`.
- To check the current group ID configured, use `/chat_id`.
