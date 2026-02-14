# busy38-telegram API Reference

This plugin exposes Busy38 Telegram runtime behavior plus agent-facing cheatcodes.

## Runtime: `Busy38TelegramBot`

Implementation path: `toolkit/telegram_bot.py`

Key behavior:
- Ingests all channel traffic and decides when to respond.
- Supports subscribe controls for chats (persisted to local JSON by default).
- Applies 24h recency bias by default for context/search.
- Supports silent acknowledgements via emoji reactions.
- Uses anti-spam guardrails for high-traffic channels.
- Implements **Cognitive Desktop** pattern: think by default, speak explicitly.

Core env controls:
- `TELEGRAM_CONTEXT_MAX_AGE_SEC` (default `86400`)
- `TELEGRAM_STATE_PATH` (default `./data/telegram_state.json`)
- `TELEGRAM_SUBSCRIBE_REQUIRE_ADMIN` (default `1`)
- `TELEGRAM_FOLLOW_SPAM_WINDOW_SEC` (default `30`)
- `TELEGRAM_FOLLOW_SPAM_MAX_EVENTS` (default `12`)
- `TELEGRAM_FOLLOW_SPAM_COOLDOWN_SEC` (default `45`)
- `TELEGRAM_ATTACHMENT_INCLUDE_META` (default `1`)
- `TELEGRAM_CLEAR_WINDOW_HOURS` (default `72`)
- `TELEGRAM_CLEAR_MAX_MESSAGES` (default `1200`)
- `TELEGRAM_AUTO_CLEAR_ENABLE` (default `0`)
- `TELEGRAM_AUTO_CLEAR_INTERVAL_SEC` (default `900`)
- `TELEGRAM_AUTO_CLEAR_MIN_GAP_SEC` (default `21600`)
- `TELEGRAM_CLEAR_STATE_PATH` (default `./data/telegram_clear_state.json`)
- `TELEGRAM_STATUS_ENABLE` (default `0`)
- `TELEGRAM_STATUS_MODE` (`edit` or `message`, default `edit`)
- `TELEGRAM_STATUS_STYLE` (`implicit` or `explicit`, default `implicit`)
- `TELEGRAM_STATUS_DELAY_SEC` (default `1.5`)
- `TELEGRAM_STATUS_MIN_INTERVAL_SEC` (default `2.5`)
- `TELEGRAM_STATUS_DELETE_ON_FINISH` (default `1`)

## Moderator Commands

### `/clear [hours]`

Summarize the last N hours (default 72) using Busy38 local transcripts and pin the summary message.

Notes:
- Requires admin in group/supergroup/channel chats.
- Reads `chat_entries` only (local-first), it does not fetch Telegram back-history.
 

## Namespace: `tlog`

Transcript tools backed by Busy38 local chat entries (DuckDB `chat_entries`).

### `tlog:search`

Broad search with snippet windows around matches. Defaults to recency (last 24h).

Example:
```text
[tlog:search query="deploy failed" chat_id="-1001234567890" max_age_hours=24 /]
```

Parameters:
- `query` (string, required): literal unless `regex=true`
- `chat_id` (string, optional): Telegram chat id (raw, e.g. `-100123...`; omit to search all telegram logs)
- `project_id` (string, optional): Busy38 project id for Telegram logs (`telegram:<chat_id>`). Overrides `chat_id` if provided.
- `max_age_hours` (int, default 24): 0 disables age filter
- `max_messages` (int, default 5000): max messages scanned
- `context` (int, default 80): context window in characters
- `case_sensitive` (bool, default false)
- `regex` (bool, default false)
- `snippets_per_message` (int, default 2): max snippets extracted per matching message
- `max_results` (int, default 20)

Returns:
- `{success: true, results: [...]}` where each result includes:
  - `id`: `telegram:<chat_id>:<message_id>`
  - `timestamp`
  - `project_id` (`telegram:<chat_id>`)
  - `snippets`: list of context strings
  - `metadata`: author info, match positions

### `tlog:around`

Fetches surrounding messages for context.

Example:
```text
[tlog:around chat_id="-1001234567890" message_id="12345" before=8 after=8 /]
```

Parameters:
- `message_id` (string, required): either a raw Telegram message id (unique per chat) or fully-qualified `telegram:<chat_id>:<message_id>`
- `chat_id` (string, required if `message_id` is not fully-qualified): raw Telegram chat id
- `before` (int, default 8)
- `after` (int, default 8)

Returns:
- `{success: true, rows: [...]}` with context rows (chronological window)

## Namespace: `tchat`

Chat messaging operations via Telegram Bot API.

### `tchat:send`

Send a message to a chat:
```text
[tchat:send chat_id="-1001234567890" text="Hello team!" /]
```

Parameters:
- `chat_id` (string, required): Chat ID or @channelusername
- `text` (string, required): Message text (Markdown supported)
- `reply_to` (string, optional): Message ID to reply to
- `parse_mode` (string, optional): 'Markdown', 'HTML', or null
- `silent` (bool, default false): Send without notification

### `tchat:edit`

Edit a previously sent message:
```text
[tchat:edit chat_id="-1001234567890" message_id="123" text="Updated text" /]
```

### `tchat:delete`

Delete a message:
```text
[tchat:delete chat_id="-1001234567890" message_id="123" /]
```

### `tchat:poll`

Create a poll:
```text
[tchat:poll chat_id="-1001234567890" question="Lunch?" options="["Pizza", "Sushi"]" /]
```

### `tchat:pin`

Pin a message:
```text
[tchat:pin chat_id="-1001234567890" message_id="123" /]
```

### `tchat:unpin`

Unpin a message (or all messages):
```text
[tchat:unpin chat_id="-1001234567890" message_id="123" /]
[tchat:unpin chat_id="-1001234567890" /]  # Unpin all
```

### `tchat:get_info`

Get chat information:
```text
[tchat:get_info chat_id="-1001234567890" /]
```

### `tchat:get_members`

Get chat members (requires admin rights):
```text
[tchat:get_members chat_id="-1001234567890" limit=100 /]
```

### `tchat:read`

Read recent messages from Busy38 local chat logs (DuckDB `chat_entries`) for a Telegram chat.

This does not fetch history from the Telegram API (Telegram bots generally cannot read arbitrary back-history).

```text
[tchat:read chat_id="-1001234567890" limit=50 max_age_hours=24 /]
```

## Namespace: `tgroup`

Group and channel management operations. Requires admin privileges.

### `tgroup:invite`

Invite a user to a group:
```text
[tgroup:invite chat_id="-1001234567890" user_id="123456789" /]
```

### `tgroup:ban`

Ban a user:
```text
[tgroup:ban chat_id="-1001234567890" user_id="123456789" /]
[tgroup:ban chat_id="-1001234567890" user_id="123456789" until_date=1700000000 /]  # Temporary
```

### `tgroup:unban`

Unban a user:
```text
[tgroup:unban chat_id="-1001234567890" user_id="123456789" /]
```

### `tgroup:set_permissions`

Set user permissions:
```text
[tgroup:set_permissions 
  chat_id="-1001234567890" 
  user_id="123456789"
  can_send_messages=false
  can_send_media=false
/]
```

## Security Notes

- `tlog:*` reads local DuckDB chat logs only; it does not fetch from Telegram API for search
- `tchat:*` and `tgroup:*` use Telegram Bot API and require appropriate bot permissions
- Bot must be admin to use `tgroup:*` operations
- Cognitive Desktop pattern ensures agent thinks before speaking

## Cognitive Desktop Pattern

This plugin implements the Cognitive Desktop principle:

1. **THINK** (default): All messages are processed internally
   - Logged to chat_entries
   - Analyzed for intent
   - Context updated
   - No external output

2. **SPEAK** (explicit): Only respond when triggered
   - Direct commands (/command)
   - Replies to bot messages
   - Explicit @mentions
   - Wake words (busy38:, squidder:)

This separation ensures the agent processes all information but only communicates intentionally.
