# AGENTS.md

This file defines repository-specific instructions for coding agents working on
WhoopMania Bot.

## Mission

Maintain a reliable Python Telegram bot for race registration, ticket sales,
YooKassa payments, waitlist progression, and race administration.

Prioritize payment correctness and preservation of participant data over
convenience. Never make a change that can assign one user's payment to another
user's slot.

## Repository Map

- `bot.py`: runtime entry point, router registration, and background tasks.
- `background_tasks.py`: expiration of unpaid slot reservations.
- `config.py`: `.env` loading and shared runtime settings.
- `database/db.py`: connections to the race DB and shared payment DB.
- `database/init_db.py`: idempotent schema initialization.
- `handlers/start.py`: subscription check and `/start`.
- `handlers/registration.py`: profile and consent flow.
- `handlers/sales.py`: reservation, payment link, pass form, and cancellation.
- `handlers/payments_watcher.py`: successful payment reconciliation.
- `handlers/waitlist.py`: assignment of free slots to waiting users.
- `handlers/admin.py`: admin commands and reports.
- `payments/service.py`: YooKassa payment creation.
- `tests/`: isolated `unittest` coverage using temporary SQLite databases.

## Runtime Model

There are two SQLite databases:

1. `database/race.db` belongs to this project and stores users, races, slots,
   entries, and test-payment markers.
2. `PAYMENT_DB_PATH` points to a shared payment DB. Another service may update
   provider statuses in this file.

Do not merge these databases or silently change the default payment DB path.
Never copy a live payment DB into Git.

Profiles and race participation are deliberately separate:

- `users` is the persistent Telegram profile.
- `race_entries` contains one participation row per `(race_id, telegram_id)`.
- `race_slots.user_id` must agree with the active `race_entries.telegram_id`.

The legacy `users.status` field is not the source of truth for participation.

## Critical Invariants

Preserve all of these:

1. At most one active `sales_open` race is selected by user-facing flows.
2. A free slot has `status='free'` and `user_id IS NULL`.
3. A reserved or paid slot belongs to exactly one Telegram user.
4. Payment confirmation must compare the payment user with
   `race_slots.user_id`.
5. Slot and `race_entries` status changes must happen in the same transaction.
6. A late payment must never confirm a slot reassigned to another user.
7. Ambiguous payments go to manual review; they are not silently discarded.
8. Expired reservations free the slot and progress the waitlist.
9. Waitlist order is scoped by `race_id`.
10. Test payments must not move a race out of `draft` or notify normal users.
11. `/delete_draft` must never delete a race containing participants or
    occupied slots.

Use `BEGIN IMMEDIATE` or conditional updates for slot assignment paths that can
race with another process.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Required `.env` values:

```text
BOT_TOKEN
RACE_CHANNEL_ID
ADMIN_CHAT_ID
ADMIN_IDS
```

Payment flows also require:

```text
YOOKASSA_SHOP_ID
YOOKASSA_SECRET_KEY
PAYMENT_DB_PATH
```

Optional proxy configuration:

```text
TELEGRAM_PROXY_URL
```

or:

```text
TELEGRAM_PROXY_TYPE
TELEGRAM_PROXY_HOST
TELEGRAM_PROXY_PORT
TELEGRAM_PROXY_USERNAME
TELEGRAM_PROXY_PASSWORD
```

Never print secret values during diagnostics. Reporting whether a variable is
set is sufficient.

## Schema Changes

Run schema initialization from the documented directory:

```bash
cd database
../.venv/bin/python init_db.py
```

Schema changes must be idempotent. Use `CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`, or an explicit migration that is safe against an
existing production DB.

When changing schema:

1. update `database/init_db.py`;
2. update temporary schemas in affected tests;
3. test a fresh DB;
4. test the migration against a copy of an existing DB;
5. include the server migration command in the PR description.

Do not run destructive migrations against the live DB without an explicit user
request and a verified backup.

## Testing

Run before every commit:

```bash
python -m unittest discover -s tests -v
python -m compileall -q bot.py background_tasks.py config.py database handlers payments tests
python -m pip check
git diff --check
```

For DB work also run:

```bash
sqlite3 database/race.db "PRAGMA integrity_check;"
```

Tests must use temporary databases. Never point tests at the live race or
payment DB.

Add regression coverage for:

- new command parsing and authorization;
- slot and entry state transitions;
- normal, late, duplicate, and test payment reconciliation;
- waitlist ordering;
- cleanup and rollback paths;
- Telegram message routing when privacy matters.

## Coding Conventions

- Support the Python version used by the deployment and existing tests.
- Use 4-space indentation and `snake_case`.
- Keep router modules feature-focused and expose `router = Router()`.
- Use the existing `get_connection()` helpers.
- Prefer parameterized SQL and structured queries.
- Keep DB transactions free of network `await` calls.
- Perform blocking HTTP work outside the event loop with
  `asyncio.to_thread`.
- Catch specific exceptions where practical and log failures with context.
- Keep Russian user-facing copy and HTML parse mode consistent.
- Escape DB/user values with `html.escape` before inserting them into Telegram
  HTML messages.
- Use `Command(...)` for Telegram commands instead of prefix string matching.
- Reject unexpected command arguments before changing state.

## Admin Command Expectations

`/admin` is the canonical in-bot command reference. Update its text whenever an
admin command is added, removed, or renamed.

High-impact commands:

- `/open_sales` sends a broadcast and must never be used for testing.
- `/test_payment` must operate only on a draft and only for the invoking admin.
- `/reset_test_entry` must only clear the invoking admin's marked test entry.
- `/delete_draft` must refuse non-empty or non-draft races.
- `/add_user` must not create a second slot for an existing participant.

## Payment Changes

The current payment creation contract is known to work. Preserve:

- YooKassa metadata containing the local payment ID;
- `target_type='race_slot'` for race and isolated test payments;
- `target_id` equal to the race slot ID;
- separate ownership validation in `payments_watcher`.

Do not assume a successful provider payment still owns its original slot.
Always verify current race DB state.

Do not change provider credentials, the payment DB, price defaults, callback
semantics, or refund behavior without explicit confirmation.

## Server Operations

Only access or modify the server when the user explicitly requests it.

For diagnostics:

- inspect service status before restarting;
- mask `.env` values;
- use `getMe` rather than starting a second polling process;
- check the deployed commit and migration state;
- inspect only relevant logs and paths.

For replacing a live DB:

1. identify all writer processes;
2. run `PRAGMA integrity_check`;
3. create a timestamped backup;
4. upload to a temporary name;
5. preserve permissions;
6. atomically rename;
7. run `PRAGMA integrity_check` again.

Do not overwrite the shared payment DB unless the user explicitly identifies
that exact file.

## Git and Pull Requests

- Work from the latest `main`.
- Use `agent/<short-description>` for agent branches.
- Keep commits focused and imperative.
- Stage explicit paths; do not include `.env`, `*.db`, logs, caches, or editor
  files.
- Do not discard unrelated user changes.
- PR descriptions must include:
  - behavioral summary;
  - affected bot flows;
  - DB and `.env` changes;
  - automated checks;
  - manual verification;
  - deployment and migration steps.

Default to a draft PR unless the user explicitly asks for a ready PR.

