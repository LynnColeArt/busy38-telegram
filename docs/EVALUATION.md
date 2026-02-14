# busy38-telegram Evaluation (Busy38 Vendor Standards)

Status as of commit at evaluation time: `master` synced on 2026-02-14.

## What’s Good

- Repo structure matches vendor expectations: `manifest.json`, `tool_spec.yaml`, `toolkit/`, docs, GPL-3.0-only.
- Clear intent: mirror Discord plugin semantics (recency bias, broad search then drill into context).
- Cognitive Desktop framing (think vs speak) is consistent with Busy’s direction.

## Critical Gaps Found

- Busy vendor integration was missing:
  - No `Toolkit` class for Busy’s `PluginManager` to instantiate.
  - No namespace registration (`register_namespace(...)`) so `tlog:*`, `tchat:*`, `tgroup:*` were not actually exposed.
- Transcript search was not backed by Busy chat logs:
  - `TranscriptLogger` used only an in-memory cache; `_fetch_messages` did not fetch from Telegram or DuckDB.
  - That made `tlog:*` essentially non-functional outside the current process lifetime.
- Docs/spec mismatches:
  - README/API reference claimed cheatcodes existed and transcript search worked against chat logs, but code did not register namespaces or persist logs.
- Dependency packaging missing:
  - `python-telegram-bot` was used but not declared anywhere.

## Patch Applied In This Checkout

This evaluation pass also adds the missing Busy vendor glue so the plugin can be vendored cleanly:

- `toolkit/__init__.py` now defines `Toolkit` and registers:
  - `tlog` (local DuckDB transcript search)
  - `tchat`, `tgroup` (Telegram Bot API operations; lazy imports)
- Added `toolkit/telegram_transcript.py`:
  - DuckDB `chat_entries` persistence (same schema as Discord plugin)
  - Pattern search with snippet windows (`search`)
  - Context drilldown (`context_around`)
  - Local “read recent messages” (`recent_messages`)
- Updated `toolkit/telegram_bot.py`:
  - Logs ingested messages into DuckDB so the agent is not “isolated”
  - Emoji reaction ack is best-effort with version-dependent fallbacks
- Fixed obvious Telegram API mismatch:
  - `GroupManager.invite_user` now always creates an invite link (bots typically can’t directly add users by id)
  - `ChatManager.send_poll` now treats the return as a `Message` (poll id extracted from `message.poll`)
- Added `requirements.txt` with `python-telegram-bot` and `duckdb`

## Remaining Work (To Reach “Full Citizen”)

- Service integration:
  - Busy’s `busy service ...` currently uses core-defined service definitions.
  - If Telegram ingestion should auto-run like Discord, we need either:
    - core support for plugin-defined services, or
    - a new `telegram` ServiceDefinition in Busy that starts the vendored runtime.
- Auth/security:
  - Token should come from SquidKeys (and/or local hardware-gated auth) instead of plain env vars.
- Attachments:
  - Add `tchat:send_file` / `tchat:send_media` or extend `tchat:send` with attachment specs (including base64 payload support).
- Platform limits:
  - Telegram bots can’t fetch arbitrary historical messages; “read/search” should remain local-first (DuckDB) fed by ingestion.

