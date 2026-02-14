#!/usr/bin/env python3
"""
Busy38TelegramBot - Core runtime for Telegram integration

Handles:
- Bot initialization and authentication
- Message ingestion from Telegram
- Webhook/polling setup
- Message routing to handlers
- Anti-spam guardrails
- Acknowledgment controls
"""

import os
import logging
import asyncio
import time
from collections import defaultdict, deque
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timedelta, timezone

# Telegram Bot API
from telegram import Update, Message, Chat
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler as TelegramMessageHandler,
    filters
)

logger = logging.getLogger(__name__)


class Busy38TelegramBot:
    """
    Main Telegram bot runtime for Busy38.
    
    Ingests all channel traffic and decides when to respond.
    Supports subscribe controls for chats (persisted to local JSON by default).
    """
    
    def __init__(self, token: Optional[str] = None):
        """
        Initialize the Telegram bot.
        
        Args:
            token: Bot token from @BotFather. If not provided, reads from env.
        """
        self.token = token or os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.token:
            raise ValueError("Telegram bot token required. Set TELEGRAM_BOT_TOKEN env var.")
        
        # Configuration
        self.context_max_age_sec = int(os.getenv('TELEGRAM_CONTEXT_MAX_AGE_SEC', '86400'))
        self.no_response_reactions = os.getenv('TELEGRAM_NO_RESPONSE_REACTIONS', 'true').lower() == 'true'
        self.no_response_emojis = os.getenv('TELEGRAM_NO_RESPONSE_EMOJIS', '👍,👀,✅').split(',')
        self.follow_spam_window_sec = int(os.getenv('TELEGRAM_FOLLOW_SPAM_WINDOW_SEC', '30'))
        self.follow_spam_max_events = int(os.getenv('TELEGRAM_FOLLOW_SPAM_MAX_EVENTS', '12'))
        self.follow_spam_cooldown_sec = int(os.getenv('TELEGRAM_FOLLOW_SPAM_COOLDOWN_SEC', '45'))
        self.state_path = os.getenv("TELEGRAM_STATE_PATH", "./data/telegram_state.json")
        self.subscribe_require_admin = os.getenv("TELEGRAM_SUBSCRIBE_REQUIRE_ADMIN", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        
        # State
        self.application: Optional[Application] = None
        self.message_callbacks: list[Callable] = []
        self._state = None
        self._follow_recent_events: Dict[str, deque[float]] = defaultdict(deque)
        self._follow_cooldown_until: Dict[str, float] = {}
        self._running = False
        self._transcript = None
        self._sess_store = None
        self._sess_cache: Dict[str, str] = {}

        self._load_state()
        
        logger.info("Busy38TelegramBot initialized")

    def _state_store(self):
        if self._state is not None:
            return self._state
        from .telegram_state import TelegramStateStore

        self._state = TelegramStateStore(default_history_limit=80)
        return self._state

    def _load_state(self) -> None:
        try:
            st = self._state_store()
            st.load_from_path(self.state_path)
        except Exception as exc:
            logger.debug("Failed to load telegram state (%s): %s", self.state_path, exc)

    def _save_state(self) -> None:
        try:
            st = self._state_store()
            st.save_to_path(self.state_path)
        except Exception as exc:
            logger.debug("Failed to save telegram state (%s): %s", self.state_path, exc)

    def _transcript_logger(self):
        """
        Lazy init transcript logger (DuckDB chat_entries).

        This keeps the runtime usable even if duckdb isn't installed; the bot can
        still run, but transcript search won't be available.
        """
        if self._transcript is not None:
            return self._transcript
        try:
            from .telegram_transcript import TelegramTranscriptLogger

            data_dir = os.getenv("BUSY38_CHATLOG_DIR", "./data/memory")
            self._transcript = TelegramTranscriptLogger(data_dir=data_dir)
        except Exception as exc:
            logger.warning("TelegramTranscriptLogger unavailable: %s", exc)
            self._transcript = None
        return self._transcript

    def _session_store(self):
        """
        Best-effort Busy38 session event store (sess:*).

        This plugin repo can run standalone, so we import lazily and no-op if
        Busy core isn't present.
        """
        if self._sess_store is not None:
            return self._sess_store
        if os.getenv("TELEGRAM_SESSION_LOG_ENABLE", "1").strip().lower() in ("0", "false", "no", "off"):
            self._sess_store = None
            return None
        try:
            from core.session import SessionStore  # type: ignore

            self._sess_store = SessionStore()
        except Exception:
            self._sess_store = None
        return self._sess_store
    
    async def initialize(self) -> bool:
        """
        Initialize the bot application.
        
        Returns:
            True if successful, False otherwise.
        """
        try:
            logger.info("Initializing Telegram bot application...")
            
            # Build application
            self.application = (
                ApplicationBuilder()
                .token(self.token)
                .build()
            )
            
            # Add message handler
            self.application.add_handler(CommandHandler("busy38", self._cmd_help))
            self.application.add_handler(CommandHandler("subscribe", self._cmd_subscribe))
            self.application.add_handler(CommandHandler("unsubscribe", self._cmd_unsubscribe))
            self.application.add_handler(CommandHandler("follow", self._cmd_follow))
            self.application.add_handler(CommandHandler("subs", self._cmd_subs))

            self.application.add_handler(
                TelegramMessageHandler(
                    filters.ALL,
                    self._handle_telegram_message
                )
            )
            
            # Initialize
            await self.application.initialize()
            
            logger.info("Telegram bot initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")
            return False

    async def _user_is_admin(self, *, chat_id: str, user_id: Optional[int], context: ContextTypes.DEFAULT_TYPE) -> bool:
        if user_id is None:
            return False
        try:
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=int(user_id))
            status = getattr(member, "status", None)
            return str(status) in ("creator", "administrator")
        except Exception:
            return False

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.effective_message:
            return
        await update.effective_message.reply_text(
            "Busy38 Telegram commands:\n"
            "/subscribe - subscribe this chat for context tracking\n"
            "/unsubscribe - unsubscribe this chat\n"
            "/follow on|off - toggle follow-mode (more proactive triggers)\n"
            "/subs - list subscribed chats (from bot's local state)\n"
        )

    async def _cmd_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.effective_message:
            return
        chat_id = str(update.effective_chat.id)
        user_id = getattr(update.effective_user, "id", None)
        if self.subscribe_require_admin and update.effective_chat.type in ("group", "supergroup", "channel"):
            if not await self._user_is_admin(chat_id=chat_id, user_id=user_id, context=context):
                await update.effective_message.reply_text("Denied: admin required to subscribe this chat.")
                return
        from .telegram_state import ChatKey

        key = ChatKey(chat_id=chat_id)
        self._state_store().set_subscribed(key, True)
        cfg = self._state_store().chat_config(key)
        self._save_state()
        await update.effective_message.reply_text(f"✓ Subscribed this chat (follow_mode={cfg.follow_mode})")

    async def _cmd_unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.effective_message:
            return
        chat_id = str(update.effective_chat.id)
        user_id = getattr(update.effective_user, "id", None)
        if self.subscribe_require_admin and update.effective_chat.type in ("group", "supergroup", "channel"):
            if not await self._user_is_admin(chat_id=chat_id, user_id=user_id, context=context):
                await update.effective_message.reply_text("Denied: admin required to unsubscribe this chat.")
                return
        from .telegram_state import ChatKey

        key = ChatKey(chat_id=chat_id)
        self._state_store().set_subscribed(key, False)
        self._state_store().set_follow_mode(key, False)
        self._save_state()
        await update.effective_message.reply_text("✓ Unsubscribed this chat (follow_mode=off)")

    async def _cmd_follow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.effective_message:
            return
        chat_id = str(update.effective_chat.id)
        user_id = getattr(update.effective_user, "id", None)
        if self.subscribe_require_admin and update.effective_chat.type in ("group", "supergroup", "channel"):
            if not await self._user_is_admin(chat_id=chat_id, user_id=user_id, context=context):
                await update.effective_message.reply_text("Denied: admin required to toggle follow-mode.")
                return
        mode = "on"
        try:
            if context.args:
                mode = str(context.args[0]).strip().lower()
        except Exception:
            mode = "on"
        if mode not in ("on", "off"):
            await update.effective_message.reply_text("Usage: /follow on  OR  /follow off")
            return
        from .telegram_state import ChatKey

        key = ChatKey(chat_id=chat_id)
        self._state_store().set_subscribed(key, True)
        self._state_store().set_follow_mode(key, mode == "on")
        self._save_state()
        await update.effective_message.reply_text(f"✓ follow_mode={mode} (subscribed=True)")

    async def _cmd_subs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        subs = self._state_store().list_subscriptions()
        if not subs:
            await update.effective_message.reply_text("No subscribed chats.")
            return
        lines = []
        for key, cfg in subs[:40]:
            lines.append(f"- `{key.chat_id}` follow_mode={cfg.follow_mode} history_limit={cfg.history_limit}")
        await update.effective_message.reply_text("Subscribed chats:\n" + "\n".join(lines), parse_mode="Markdown")
    
    async def start(self) -> bool:
        """
        Start the bot (polling mode).
        
        Returns:
            True if started successfully.
        """
        if not self.application:
            if not await self.initialize():
                return False
        
        try:
            logger.info("Starting Telegram bot (polling mode)...")
            await self.application.start()
            await self.application.updater.start_polling()
            self._running = True
            logger.info("Telegram bot started and polling for messages")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Telegram bot: {e}")
            return False
    
    async def stop(self):
        """Stop the bot gracefully."""
        if self.application:
            logger.info("Stopping Telegram bot...")
            await self.application.updater.stop()
            await self.application.stop()
            self._running = False
            logger.info("Telegram bot stopped")
    
    async def _handle_telegram_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle incoming Telegram messages.
        
        This is the main entry point for all received messages.
        """
        if not update.message:
            return
        
        message = update.message
        chat_id = str(message.chat_id)

        # Subscription gating: ingest only subscribed chats (matches Discord transport behavior).
        from .telegram_state import ChatKey

        key = ChatKey(chat_id=chat_id)
        cfg = self._state_store().chat_config(key)
        if not cfg.subscribed:
            logger.debug("Ignoring message from unsubscribed chat: %s", chat_id)
            return

        is_command = bool((message.text or "").startswith("/"))
        is_follow_trigger = bool(cfg.subscribed and cfg.follow_mode and (not is_command))

        # Anti-spam check applies only to follow-mode triggers (mention/reply/commands shouldn't be dropped).
        if is_follow_trigger and (not self._follow_guardrail_allows(chat_id)):
            logger.warning("Follow-mode spam guardrail tripped for chat %s; skipping", chat_id)
            return
        
        # Process message
        await self._process_message(message, context)
    
    async def _process_message(self, message: Message, context: ContextTypes.DEFAULT_TYPE):
        """Process a valid message through all registered callbacks."""
        chat_id = str(message.chat_id)

        from .telegram_state import ChatKey

        cfg = self._state_store().chat_config(ChatKey(chat_id=chat_id))
        bot_username = ""
        try:
            bot_username = str(getattr(context.bot, "username", "") or "").strip()
        except Exception:
            bot_username = ""

        text = message.text or ""
        caption = message.caption or ""
        combined_text = text or caption or ""

        trigger = "ingest"
        if combined_text.startswith("/"):
            trigger = "command"
        elif message.reply_to_message and getattr(getattr(message.reply_to_message, "from_user", None), "is_bot", False):
            trigger = "reply"
        elif bot_username and (f"@{bot_username}".lower() in combined_text.lower()):
            trigger = "mention"
        elif cfg.follow_mode:
            trigger = "follow"
        
        # Build message data
        msg_data = {
            'id': str(message.message_id),
            'chat_id': chat_id,
            'text': combined_text,
            'from_user': {
                'id': str(message.from_user.id) if message.from_user else None,
                'username': message.from_user.username if message.from_user else None,
                'first_name': message.from_user.first_name if message.from_user else None,
            },
            'timestamp': message.date.isoformat(),
            'is_reply': message.reply_to_message is not None,
            'reply_to_message_id': str(message.reply_to_message.message_id) if message.reply_to_message else None,
            'trigger': trigger,
            'subscribed': bool(cfg.subscribed),
            'follow_mode': bool(cfg.follow_mode),
        }

        # Persist to Busy38 chat_entries (local-first) for pattern search + boards.
        try:
            tl = self._transcript_logger()
            if tl is not None:
                ts = message.date
                if ts is None:
                    ts = datetime.now(timezone.utc)
                elif ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

                author = message.from_user
                attachments: list[dict[str, Any]] = []
                if getattr(message, "photo", None):
                    # take largest photo size
                    try:
                        ph = list(message.photo)[-1]
                        attachments.append(
                            {
                                "type": "photo",
                                "file_id": getattr(ph, "file_id", None),
                                "file_unique_id": getattr(ph, "file_unique_id", None),
                                "file_size": getattr(ph, "file_size", None),
                                "width": getattr(ph, "width", None),
                                "height": getattr(ph, "height", None),
                            }
                        )
                    except Exception:
                        pass
                doc = getattr(message, "document", None)
                if doc is not None:
                    attachments.append(
                        {
                            "type": "document",
                            "file_id": getattr(doc, "file_id", None),
                            "file_unique_id": getattr(doc, "file_unique_id", None),
                            "file_name": getattr(doc, "file_name", None),
                            "mime_type": getattr(doc, "mime_type", None),
                            "file_size": getattr(doc, "file_size", None),
                        }
                    )
                content = combined_text or ""
                if attachments and os.getenv("TELEGRAM_ATTACHMENT_INCLUDE_META", "1").strip().lower() not in ("0", "false", "no", "off"):
                    # Similar to Discord: keep it stable and short.
                    parts = []
                    for att in attachments[:4]:
                        name = att.get("file_name") or att.get("type") or "file"
                        size = att.get("file_size")
                        if isinstance(size, int):
                            parts.append(f"{name} ({size}B)")
                        else:
                            parts.append(str(name))
                    if len(attachments) > 4:
                        parts.append(f"+{len(attachments) - 4} more")
                    suffix = " [attachments: " + ", ".join(parts) + "]"
                    content = (content + suffix).strip() if content else suffix.strip()
                tl.log_message(
                    chat_id=chat_id,
                    message_id=str(message.message_id),
                    timestamp=ts,
                    content=content,
                    metadata={
                        "transport": "telegram",
                        "chat_id": chat_id,
                        "message_id": str(message.message_id),
                        "author_id": str(author.id) if author else None,
                        "author_username": getattr(author, "username", None) if author else None,
                        "author_first_name": getattr(author, "first_name", None) if author else None,
                        "is_bot": bool(getattr(author, "is_bot", False)) if author else False,
                        "trigger": trigger,
                        "follow_mode": bool(cfg.follow_mode),
                        "attachments": attachments,
                    },
                    participants=[int(author.id)] if author and getattr(author, "id", None) is not None else None,
                )
        except Exception as e:
            logger.debug(f"Transcript log failed: {e}")

        # Persist to Busy38 session event stream (best-effort, when vendored into Busy).
        try:
            st = self._session_store()
            if st is not None:
                surface_id = f"telegram:{chat_id}"
                sid = self._sess_cache.get(surface_id) or st.get_bound_session(surface_id=surface_id)
                if not sid and os.getenv("TELEGRAM_SESSION_AUTO_BIND", "1").strip().lower() not in ("0", "false", "no", "off"):
                    sid = st.create_session(
                        title=f"Telegram {chat_id}",
                        workspace_root="",
                        writer_id=f"telegram:{os.getpid()}",
                        metadata={"transport": "telegram", "surface_id": surface_id, "chat_id": chat_id},
                    )
                    st.bind_surface(surface_id=surface_id, session_id=sid)
                if sid:
                    self._sess_cache[surface_id] = sid
                    st.append_event(
                        session_id=sid,
                        writer_id=f"telegram:{os.getpid()}",
                        type="chat.message",
                        payload={
                            "transport": "telegram",
                            "surface_id": surface_id,
                            "role": "user",
                            "text": message.text or "",
                            "metadata": {
                                "chat_id": chat_id,
                                "message_id": str(message.message_id),
                                "author_id": str(message.from_user.id) if message.from_user else None,
                                "author_username": getattr(message.from_user, "username", None) if message.from_user else None,
                            },
                        },
                    )
        except Exception:
            pass
        
        logger.debug(f"Processing message {msg_data['id']} from chat {chat_id}")
        
        # Send to all registered callbacks
        responded = False
        for callback in self.message_callbacks:
            try:
                res = await callback(msg_data, context)
                if isinstance(res, dict) and bool(res.get("spoke") or res.get("responded") or res.get("sent")):
                    responded = True
                elif res is True:
                    responded = True
            except Exception as e:
                logger.error(f"Error in message callback: {e}")
        
        # Send acknowledgment if no-response mode
        if self.no_response_reactions and (not responded) and trigger != "command":
            await self._send_acknowledgment(message, context)
    
    async def _send_acknowledgment(self, message: Message, context: ContextTypes.DEFAULT_TYPE):
        """Send a silent acknowledgment reaction (emoji)."""
        try:
            # React with first emoji in list
            emoji = self.no_response_emojis[0] if self.no_response_emojis else '👍'
            # Telegram reaction support is version-dependent. Try the most direct method,
            # then fall back to bot API if available.
            try:
                await message.set_reaction(emoji)  # type: ignore[attr-defined]
            except Exception:
                try:
                    await context.bot.set_message_reaction(  # type: ignore[attr-defined]
                        chat_id=message.chat_id,
                        message_id=message.message_id,
                        reaction=[{"type": "emoji", "emoji": emoji}],
                    )
                except Exception:
                    raise
            logger.debug(f"Sent acknowledgment {emoji} to message {message.message_id}")
        except Exception as e:
            logger.debug(f"Could not send acknowledgment: {e}")
    
    def _follow_guardrail_allows(self, chat_id: str) -> bool:
        """
        Return True when follow-mode invocation is allowed for this chat.

        Similar to Discord follow-mode guardrails:
        - rolling window of recent follow triggers
        - cooldown after burst threshold
        """
        if self.follow_spam_max_events <= 0:
            return True
        now = time.time()
        cooldown_until = float(self._follow_cooldown_until.get(chat_id, 0.0))
        if now < cooldown_until:
            return False
        q = self._follow_recent_events[chat_id]
        while q and (now - float(q[0])) > float(self.follow_spam_window_sec):
            q.popleft()
        q.append(now)
        if len(q) > int(self.follow_spam_max_events):
            self._follow_cooldown_until[chat_id] = now + float(self.follow_spam_cooldown_sec)
            return False
        return True
    
    def subscribe_chat(self, chat_id: str):
        """Subscribe to messages from a specific chat."""
        from .telegram_state import ChatKey

        key = ChatKey(chat_id=str(chat_id))
        self._state_store().set_subscribed(key, True)
        self._save_state()
        logger.info("Subscribed to chat: %s", chat_id)
    
    def unsubscribe_chat(self, chat_id: str):
        """Unsubscribe from a chat."""
        from .telegram_state import ChatKey

        key = ChatKey(chat_id=str(chat_id))
        self._state_store().set_subscribed(key, False)
        self._state_store().set_follow_mode(key, False)
        self._save_state()
        logger.info("Unsubscribed from chat: %s", chat_id)
    
    def on_message(self, callback: Callable):
        """Register a callback for incoming messages."""
        self.message_callbacks.append(callback)
        logger.debug(f"Registered message callback: {callback.__name__}")
    
    def is_running(self) -> bool:
        """Check if bot is currently running."""
        return self._running


# Singleton instance
_bot_instance: Optional[Busy38TelegramBot] = None


def get_bot(token: Optional[str] = None) -> Busy38TelegramBot:
    """Get or create the singleton bot instance."""
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = Busy38TelegramBot(token)
    return _bot_instance
