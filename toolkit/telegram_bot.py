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
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timedelta, timezone

# Telegram Bot API
from telegram import Update, Message, Chat
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler as TelegramMessageHandler,
    filters
)

logger = logging.getLogger(__name__)


class Busy38TelegramBot:
    """
    Main Telegram bot runtime for Busy38.
    
    Ingests all channel traffic and decides when to respond.
    Supports subscribe/follow controls for channels.
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
        
        # State
        self.application: Optional[Application] = None
        self.message_callbacks: list[Callable] = []
        self.subscribed_chats: set[str] = set()
        self.recent_events: list[datetime] = []
        self._running = False
        self._transcript = None
        self._sess_store = None
        self._sess_cache: Dict[str, str] = {}
        
        logger.info("Busy38TelegramBot initialized")

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
        
        # Check if we're subscribed to this chat
        if chat_id not in self.subscribed_chats:
            logger.debug(f"Ignoring message from unsubscribed chat: {chat_id}")
            return
        
        # Anti-spam check
        if self._is_spam(chat_id):
            logger.warning(f"Spam detected from chat {chat_id}, skipping")
            return
        
        # Process message
        await self._process_message(message, context)
    
    async def _process_message(self, message: Message, context: ContextTypes.DEFAULT_TYPE):
        """Process a valid message through all registered callbacks."""
        chat_id = str(message.chat_id)
        
        # Build message data
        msg_data = {
            'id': str(message.message_id),
            'chat_id': chat_id,
            'text': message.text or '',
            'from_user': {
                'id': str(message.from_user.id) if message.from_user else None,
                'username': message.from_user.username if message.from_user else None,
                'first_name': message.from_user.first_name if message.from_user else None,
            },
            'timestamp': message.date.isoformat(),
            'is_reply': message.reply_to_message is not None,
            'reply_to_message_id': str(message.reply_to_message.message_id) if message.reply_to_message else None,
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
                tl.log_message(
                    chat_id=chat_id,
                    message_id=str(message.message_id),
                    timestamp=ts,
                    content=message.text or "",
                    metadata={
                        "transport": "telegram",
                        "chat_id": chat_id,
                        "message_id": str(message.message_id),
                        "author_id": str(author.id) if author else None,
                        "author_username": getattr(author, "username", None) if author else None,
                        "author_first_name": getattr(author, "first_name", None) if author else None,
                        "is_bot": bool(getattr(author, "is_bot", False)) if author else False,
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
        for callback in self.message_callbacks:
            try:
                await callback(msg_data, context)
            except Exception as e:
                logger.error(f"Error in message callback: {e}")
        
        # Send acknowledgment if no-response mode
        if self.no_response_reactions:
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
    
    def _is_spam(self, chat_id: str) -> bool:
        """
        Check if recent event volume indicates spam.
        
        Implements rate limiting to prevent flooding.
        """
        now = datetime.now()
        
        # Clean old events outside the window
        window_start = now - timedelta(seconds=self.follow_spam_window_sec)
        self.recent_events = [t for t in self.recent_events if t > window_start]
        
        # Add current event
        self.recent_events.append(now)
        
        # Check if over threshold
        if len(self.recent_events) > self.follow_spam_max_events:
            logger.warning(f"Spam threshold exceeded: {len(self.recent_events)} events in {self.follow_spam_window_sec}s")
            return True
        
        return False
    
    def subscribe_chat(self, chat_id: str):
        """Subscribe to messages from a specific chat."""
        self.subscribed_chats.add(str(chat_id))
        logger.info(f"Subscribed to chat: {chat_id}")
    
    def unsubscribe_chat(self, chat_id: str):
        """Unsubscribe from a chat."""
        self.subscribed_chats.discard(str(chat_id))
        logger.info(f"Unsubscribed from chat: {chat_id}")
    
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
