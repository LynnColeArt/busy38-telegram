# Busy38 Telegram Integration

Telegram integration toolkit for Busy38, providing agent-internal tools for messaging, chat operations, and transcript search via the Telegram Bot API.

## Overview

This plugin mirrors the Discord integration functionality for Telegram, enabling:
- Message sending and receiving
- Chat history search and transcripts
- Group/channel management
- Polling and interactive features
- Moderator context reset via `/clear` (summary + pin)
- Optional Busy hook-driven status narration while tools run

Roadmap note: full internal rebrand Phase 2 is deferred until after closed-beta hardening is complete.

## AI-Generated / Automated Contributions

Automated code generation and AI-assisted submissions are welcome.

For production code, placeholders are not acceptable.

- Unit tests may use mocks and stubs.
- Runtime and shipping code must be functional and test-backed.
- New functionality must include unit tests (or updates to existing tests) that cover the behavior.

Before submitting generated changes, verify:

- No temporary placeholders in functional code paths (`TODO`, `FIXME`, `NotImplementedError`).
- Mocked/stubbed logic is used only in tests.
- New behavior has tests/integration checks and explicit error handling.
- Critical flows are covered for regressions in permissions, moderation, and relay paths.
- All relevant tests pass before merge.

## Structure

```
busy-38-telegram/
├── manifest.json      # Plugin metadata
├── tool_spec.yaml     # Tool definitions
├── toolkit/           # Implementation
│   └── ...
├── LICENSE            # GPL-3.0
└── README.md          # This file
```

## Namespaces

### tlog - Transcript Operations
- `search` - Search chat history with context
- `around` - Get context around specific messages

### tchat - Chat Messaging
- `send` - Send messages to chats
- `read` - Read recent messages from Busy38 local chat logs
- `edit` - Edit sent messages
- `delete` - Delete messages
- `poll` - Create polls
- `pin` / `unpin` - Pin management
- `get_info` - Chat information
- `get_members` - Member list

### tgroup - Group Management
- `invite` - Invite users
- `ban` / `unban` - User moderation
- `set_permissions` - Permission management

## License

GPL-3.0-only - See LICENSE file for details.

## Integration

Part of the Busy38 vendor toolkit ecosystem.

## Runtime Commands

- `/subscribe` / `/unsubscribe` / `/follow on|off` / `/subs`
- `/clear [hours]` (admin-only in groups): summarize local context and pin the summary
