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
import json
from pathlib import Path
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
    
    def __init__(self, token: Optional[str] = None, orchestrator: Any = None):
        """
        Initialize the Telegram bot.
        
        Args:
            token: Bot token from @BotFather. If not provided, reads from env.
        """
        self.token = token or os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.token:
            raise ValueError("Telegram bot token required. Set TELEGRAM_BOT_TOKEN env var.")
        
        # Ensure core namespaces (including sess:*) are present in this transport process (best-effort).
        try:
            from core.cheatcodes.setup import register_core_namespaces  # type: ignore

            register_core_namespaces()
        except Exception:
            pass

        # Create or use provided orchestrator (best-effort; repo can run standalone).
        self.orchestrator = orchestrator
        self._owns_orchestrator = False
        if self.orchestrator is None:
            try:
                from core.orchestration.integration import Busy38Orchestrator, OrchestratorConfig  # type: ignore

                self.orchestrator = Busy38Orchestrator(config=OrchestratorConfig(max_iterations=10))
                self._owns_orchestrator = True
            except Exception:
                self.orchestrator = None
                self._owns_orchestrator = False

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

        # Clear/summarize controls (Telegram equivalent of Discord clear flow).
        self._clear_marker = "[busy38:auto-clear]"
        self._clear_window_hours = max(1, int(os.getenv("TELEGRAM_CLEAR_WINDOW_HOURS", "72")))
        self._clear_max_messages = max(50, int(os.getenv("TELEGRAM_CLEAR_MAX_MESSAGES", "1200")))
        self._auto_clear_min_gap_sec = max(60, int(os.getenv("TELEGRAM_AUTO_CLEAR_MIN_GAP_SEC", "21600")))
        self._clear_state_file = Path(os.getenv("TELEGRAM_CLEAR_STATE_PATH", "./data/telegram_clear_state.json"))
        self._last_clear_by_chat: Dict[str, float] = {}
        self._load_clear_state()

        # Optional status narration (low-noise, action-style updates while working).
        self._status_enable = os.getenv("TELEGRAM_STATUS_ENABLE", "0").strip().lower() not in ("", "0", "false", "no", "off")
        self._status_mode = (os.getenv("TELEGRAM_STATUS_MODE", "edit") or "edit").strip().lower()
        self._status_style = (os.getenv("TELEGRAM_STATUS_STYLE", "implicit") or "implicit").strip().lower()
        self._status_delay_sec = max(0.0, float(os.getenv("TELEGRAM_STATUS_DELAY_SEC", "1.5")))
        self._status_min_interval_sec = max(0.2, float(os.getenv("TELEGRAM_STATUS_MIN_INTERVAL_SEC", "2.5")))
        self._status_delete_on_finish = os.getenv("TELEGRAM_STATUS_DELETE_ON_FINISH", "1").strip().lower() not in (
            "",
            "0",
            "false",
            "no",
            "off",
        )
        self._status_msg_id_by_chat: Dict[str, int] = {}
        self._status_last_update_unix: Dict[str, float] = {}
        self._bot_me: Any = None
        
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

        # Expose runtime pointers for hook handlers (best-effort).
        try:
            from .telegram_runtime import _set_telegram_runtime_controller

            _set_telegram_runtime_controller(self)
        except Exception:
            pass

        self._load_state()
        
        logger.info("Busy38TelegramBot initialized")

    def _load_clear_state(self) -> None:
        data = None
        if self._clear_state_file.exists():
            try:
                data = json.loads(self._clear_state_file.read_text(encoding="utf-8"))
            except Exception:
                data = None
        if isinstance(data, dict):
            self._last_clear_by_chat = {str(k): float(v) for k, v in data.items()}
        else:
            self._last_clear_by_chat = {}

    def _save_clear_state(self) -> None:
        try:
            self._clear_state_file.parent.mkdir(parents=True, exist_ok=True)
            self._clear_state_file.write_text(
                json.dumps(self._last_clear_by_chat, indent=2, sort_keys=True, ensure_ascii=True),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("Failed to save telegram clear state: %s", exc)

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
            self.application.add_handler(CommandHandler("clear", self._cmd_clear))

            self.application.add_handler(
                TelegramMessageHandler(
                    filters.ALL,
                    self._handle_telegram_message
                )
            )
            
            # Initialize
            await self.application.initialize()

            # Expose bot instance for hook handlers (best-effort).
            try:
                from .telegram_runtime import _set_telegram_runtime_bot, _set_telegram_runtime_controller

                _set_telegram_runtime_controller(self)
                _set_telegram_runtime_bot(self.application.bot)
            except Exception:
                pass
            
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
            if self._owns_orchestrator and self.orchestrator is not None:
                try:
                    await self.orchestrator.start()
                except Exception:
                    pass
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
            if self._owns_orchestrator and self.orchestrator is not None:
                try:
                    await self.orchestrator.stop()
                except Exception:
                    pass
            logger.info("Telegram bot stopped")

    async def _get_me(self):
        if self._bot_me is not None:
            return self._bot_me
        if not self.application:
            return None
        try:
            self._bot_me = await self.application.bot.get_me()
        except Exception:
            self._bot_me = None
        return self._bot_me

    def _status_actor(self) -> str:
        try:
            me = self._bot_me
            if me is not None:
                return getattr(me, "first_name", None) or getattr(me, "username", None) or "Busy38"
        except Exception:
            pass
        return "Busy38"

    def _format_status_line(self, activity: str) -> str:
        a = (activity or "").strip()
        if not a:
            a = "working"
        style = self._status_style
        if style not in ("implicit", "explicit"):
            style = "implicit"
        if style == "explicit":
            actor = self._status_actor()
            if a.lower().startswith(("is ", "are ")):
                return f"{actor} {a}..."
            return f"{actor} is {a}..."
        if a.lower().startswith(("is ", "are ")):
            return f"{a}..."
        return f"is {a}..."

    async def post_status(self, *, chat_id: str, activity: str, force: bool = False) -> None:
        if not self._status_enable:
            return
        if self._status_mode in ("off", "none", "0"):
            return
        if not self.application:
            return

        now = time.time()
        last = float(self._status_last_update_unix.get(str(chat_id), 0.0))
        if (not force) and (now - last) < float(self._status_min_interval_sec):
            return
        self._status_last_update_unix[str(chat_id)] = now

        await self._get_me()
        text = self._format_status_line(activity)
        mid = self._status_msg_id_by_chat.get(str(chat_id))
        try:
            if self._status_mode == "message" or not mid:
                msg = await self.application.bot.send_message(chat_id=str(chat_id), text=text)
                self._status_msg_id_by_chat[str(chat_id)] = int(getattr(msg, "message_id", 0) or 0)
                return
            await self.application.bot.edit_message_text(chat_id=str(chat_id), message_id=int(mid), text=text)
        except Exception:
            try:
                self._status_msg_id_by_chat.pop(str(chat_id), None)
            except Exception:
                pass

    async def clear_status(self, *, chat_id: str) -> None:
        if not self._status_enable:
            return
        if not self.application:
            return
        mid = self._status_msg_id_by_chat.pop(str(chat_id), None)
        if not mid:
            return
        try:
            if self._status_delete_on_finish:
                await self.application.bot.delete_message(chat_id=str(chat_id), message_id=int(mid))
            else:
                await self._get_me()
                await self.application.bot.edit_message_text(
                    chat_id=str(chat_id), message_id=int(mid), text=self._format_status_line("done")
                )
        except Exception:
            return

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
        
        # Send to all registered callbacks. Bind an active context so hook handlers
        # (cheatcodes) can narrate status and auto-clear can identify the chat.
        responded = False
        try:
            from .telegram_runtime import bind_active_context
        except Exception:
            bind_active_context = None

        cm = bind_active_context({"chat_id": chat_id, "trigger_message_id": str(message.message_id)}) if bind_active_context else None
        if cm:
            cm.__enter__()
        try:
            for callback in self.message_callbacks:
                try:
                    res = await callback(msg_data, context)
                    if isinstance(res, dict) and bool(res.get("spoke") or res.get("responded") or res.get("sent")):
                        responded = True
                    elif res is True:
                        responded = True
                except Exception as e:
                    logger.error(f"Error in message callback: {e}")
        finally:
            try:
                if cm:
                    cm.__exit__(None, None, None)
            except Exception:
                pass
        
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

    @staticmethod
    def _fallback_summary_from_rows(rows: list[dict[str, Any]], *, max_lines: int = 20) -> str:
        if not rows:
            return "No transcript messages were found in the selected window."
        lines = []
        for r in rows[-max_lines:]:
            meta = r.get("metadata") or {}
            who = meta.get("author_username") or meta.get("author_first_name") or meta.get("author_id") or "unknown"
            content = str(r.get("content") or "").replace("\n", " ").strip()
            if len(content) > 180:
                content = content[:180] + "..."
            lines.append(f"- {who}: {content}")
        return "Recent highlights:\n" + "\n".join(lines)

    async def _summarize_rows_for_agents(self, *, rows: list[dict[str, Any]], chat_name: str, window_hours: int) -> str:
        if not rows:
            return "No transcript messages were found in the selected window."
        clipped = rows[-min(len(rows), 240):]
        history_lines = []
        for r in clipped:
            meta = r.get("metadata") or {}
            who = meta.get("author_username") or meta.get("author_first_name") or meta.get("author_id") or "unknown"
            content = str(r.get("content") or "").replace("\n", " ").strip()
            if len(content) > 280:
                content = content[:280] + "..."
            history_lines.append(f"{who}: {content}")
        history = "\n".join(history_lines)

        sys_prompt = (
            "You are Busy38 summarizing Telegram chat context for agent continuity.\n"
            "Return concise markdown with these sections:\n"
            "1) Snapshot\n2) Decisions\n3) Active Threads\n4) Open Tasks\n5) Risks/Blockers.\n"
            "Do not use tool tags or mission tags."
        )
        user_task = (
            f"Chat: {chat_name}\n"
            f"Window: last {window_hours} hours\n"
            f"Messages analyzed: {len(rows)}\n\n"
            f"History:\n{history}"
        )

        try:
            orch = self.orchestrator
            if orch is None:
                return self._fallback_summary_from_rows(rows)
            if hasattr(orch, "_run_loop"):
                out = await orch._run_loop(
                    task=user_task,
                    context=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_task},
                    ],
                    allow_delegation_tags=False,
                    owner_label="telegram_summary",
                    max_iterations=2,
                )
            else:
                out = await orch.run_agent_loop(task=user_task)
            out = str(out or "").strip()
            if not out:
                return self._fallback_summary_from_rows(rows)
            return out
        except Exception:
            return self._fallback_summary_from_rows(rows)

    async def _clear_chat_context(self, *, chat_id: str, initiated_by: str, window_hours: Optional[int] = None, force: bool = False) -> Dict[str, Any]:
        if not self.application:
            return {"success": False, "error": "bot_unavailable"}

        hours = max(1, int(window_hours or self._clear_window_hours))
        now_unix = time.time()
        key_id = str(chat_id)
        last = float(self._last_clear_by_chat.get(key_id, 0.0))
        age = now_unix - last
        if (not force) and age < float(self._auto_clear_min_gap_sec):
            return {"success": True, "skipped": "cooldown", "seconds_until_next": int(self._auto_clear_min_gap_sec - age)}

        project_id = f"telegram:{str(chat_id)}"
        rows: list[dict[str, Any]] = []
        try:
            tl = self._transcript_logger()
            if tl is not None:
                rows = tl.recent_messages(project_id=project_id, max_age_hours=hours, limit=self._clear_max_messages)
        except Exception:
            rows = []

        chat_name = str(chat_id)
        try:
            ch = await self.application.bot.get_chat(chat_id=str(chat_id))
            chat_name = getattr(ch, "title", None) or getattr(ch, "username", None) or str(chat_id)
        except Exception:
            pass

        summary = await self._summarize_rows_for_agents(rows=rows, chat_name=chat_name, window_hours=hours)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        content = (
            f"{self._clear_marker}\n"
            f"Context Summary ({hours}h) - {chat_name}\n"
            f"Generated by Busy38 ({initiated_by}) at {ts}\n\n"
            f"{summary}"
        )
        # Telegram message limit is ~4096 chars; keep headroom.
        if len(content) > 3900:
            content = content[:3880] + "\n..."

        msg = await self.application.bot.send_message(chat_id=str(chat_id), text=content)

        # Replace older pinned summary (best-effort).
        pinned = False
        try:
            me = await self._get_me()
            ch = await self.application.bot.get_chat(chat_id=str(chat_id))
            pinned_msg = getattr(ch, "pinned_message", None)
            if pinned_msg is not None and me is not None:
                author = getattr(pinned_msg, "from_user", None)
                if author is not None and getattr(author, "id", None) == getattr(me, "id", None):
                    ptxt = str(getattr(pinned_msg, "text", "") or "")
                    if ptxt.startswith(self._clear_marker):
                        try:
                            await self.application.bot.unpin_chat_message(chat_id=str(chat_id), message_id=int(pinned_msg.message_id))
                        except Exception:
                            pass
            await self.application.bot.pin_chat_message(chat_id=str(chat_id), message_id=int(msg.message_id), disable_notification=True)
            pinned = True
        except Exception:
            pinned = False

        self._last_clear_by_chat[key_id] = now_unix
        self._save_clear_state()

        return {
            "success": True,
            "chat_id": str(chat_id),
            "project_id": project_id,
            "messages_seen": len(rows),
            "summary_message_id": int(getattr(msg, "message_id", 0) or 0),
            "pinned": pinned,
            "hours": hours,
        }

    async def _cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.effective_message:
            return
        chat_id = str(update.effective_chat.id)
        user_id = getattr(update.effective_user, "id", None)
        if update.effective_chat.type in ("group", "supergroup", "channel"):
            if not await self._user_is_admin(chat_id=chat_id, user_id=user_id, context=context):
                await update.effective_message.reply_text("Denied: admin required to clear/summarize context.")
                return
        hours = self._clear_window_hours
        try:
            if context.args:
                hours = int(str(context.args[0]).strip())
        except Exception:
            hours = self._clear_window_hours
        res = await self._clear_chat_context(chat_id=chat_id, initiated_by="manual_clear", window_hours=hours, force=True)
        if res.get("skipped"):
            await update.effective_message.reply_text(f"Skipped: {res.get('skipped')} ({res.get('seconds_until_next')}s until next).")
            return
        if not res.get("success"):
            await update.effective_message.reply_text(f"Error: {res.get('error')}")
            return
        await update.effective_message.reply_text(
            "✓ Context summarized and pinned.\n"
            f"- hours: {res.get('hours')}\n"
            f"- summary message id: {res.get('summary_message_id')}\n"
            f"- pinned: {res.get('pinned')}"
        )

    async def run_auto_clear_cycle(self, *, trigger: str = "heartbeat", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Run one auto-clear sweep for subscribed chats.
        """
        if not self.application:
            return {"success": True, "skipped": "telegram_not_ready", "trigger": trigger}

        processed = 0
        cleared = 0
        skipped = 0
        errors: list[str] = []
        for key, cfg in self._state_store().list_subscriptions():
            if not cfg.subscribed:
                continue
            processed += 1
            try:
                res = await self._clear_chat_context(chat_id=str(key.chat_id), initiated_by=trigger, force=False)
                if res.get("skipped"):
                    skipped += 1
                elif res.get("success"):
                    cleared += 1
            except Exception as exc:
                errors.append(f"{key.chat_id}:{exc}")

        return {
            "success": True,
            "trigger": trigger,
            "processed": processed,
            "cleared": cleared,
            "skipped": skipped,
            "errors": errors,
            "payload": payload or {},
        }
    
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
