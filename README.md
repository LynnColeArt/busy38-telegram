# Busy38 Telegram Integration

Telegram integration toolkit for Busy38, providing agent-internal tools for messaging, chat operations, and transcript search via the Telegram Bot API.

## Overview

This plugin mirrors the Discord integration functionality for Telegram, enabling:
- Message sending and receiving
- Chat history search and transcripts
- Group/channel management
- Polling and interactive features

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
