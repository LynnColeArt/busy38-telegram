#!/usr/bin/env python3
"""
MessageHandler - Process and route incoming Telegram messages

Connects the bot runtime to Busy38's message processing pipeline.
Handles:
- Message parsing and normalization
- Routing to appropriate handlers
- Integration with chat logs
"""

import logging
from typing import Dict, Any, Optional, Callable
from datetime import datetime
import re

logger = logging.getLogger(__name__)


class MessageHandler:
    """
    Handles incoming Telegram messages and routes them to Busy38.
    
    COGNITIVE DESKTOP PRINCIPLE:
    - Agents THINK by default (internal processing)
    - Agents SPEAK explicitly (only when output is intentional)
    - This separates cognition from communication
    
    Similar to Discord's message ingestion pipeline.
    """
    
    def __init__(self, bot_username: Optional[str] = None):
        """Initialize the message handler."""
        self.processors: list[Callable] = []
        self.command_handlers: dict[str, Callable] = {}
        self.thinking_mode = True  # Default: think, don't speak
        self.bot_username = (bot_username or "").lstrip("@").lower() or None
        logger.debug("MessageHandler initialized (thinking mode)")
    
    async def process_message(self, msg_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process an incoming message.
        
        Args:
            msg_data: Normalized message data from telegram_bot.py
        
        Returns:
            Processing result with metadata
        """
        logger.debug(f"Processing message {msg_data.get('id')} from chat {msg_data.get('chat_id')}")
        
        # Add processing metadata
        result = {
            'message': msg_data,
            'processed_at': datetime.now().isoformat(),
            'handlers_triggered': [],
            'commands_found': [],
        }
        
        # Check for commands
        if msg_data.get('text', '').startswith('/'):
            command = self._extract_command(msg_data['text'])
            result['commands_found'].append(command)
            
            # Handle command if registered
            if command in self.command_handlers:
                try:
                    await self.command_handlers[command](msg_data)
                    result['handlers_triggered'].append(f"command:{command}")
                except Exception as e:
                    logger.error(f"Command handler error for {command}: {e}")
        
        # Run through all processors
        for processor in self.processors:
            try:
                await processor(msg_data)
                result['handlers_triggered'].append(processor.__name__)
            except Exception as e:
                logger.error(f"Processor error: {e}")
        
        return result
    
    def _extract_command(self, text: str) -> str:
        """Extract command from message text (/command@bot args -> command)."""
        # Remove leading slash
        text = text[1:]
        # Get command name (before any @ or space)
        command = text.split()[0].split('@')[0]
        return command.lower()
    
    def on_message(self, processor: Callable):
        """Register a message processor."""
        self.processors.append(processor)
        logger.debug(f"Registered message processor: {processor.__name__}")
        return processor
    
    def on_command(self, command: str):
        """Decorator to register a command handler."""
        def decorator(handler: Callable):
            self.command_handlers[command.lower()] = handler
            logger.debug(f"Registered command handler: {command}")
            return handler
        return decorator
    
    def normalize_message(self, telegram_message: Any) -> Dict[str, Any]:
        """
        Convert Telegram message object to normalized dict.
        
        Args:
            telegram_message: telegram.Message object
        
        Returns:
            Normalized message dictionary
        """
        msg = telegram_message
        
        normalized = {
            'id': str(msg.message_id),
            'chat_id': str(msg.chat_id),
            'text': msg.text or msg.caption or '',
            'from_user': {
                'id': str(msg.from_user.id) if msg.from_user else None,
                'username': msg.from_user.username if msg.from_user else None,
                'first_name': msg.from_user.first_name if msg.from_user else None,
                'last_name': msg.from_user.last_name if msg.from_user else None,
                'is_bot': msg.from_user.is_bot if msg.from_user else False,
            },
            'timestamp': msg.date.isoformat() if msg.date else datetime.now().isoformat(),
            'is_reply': msg.reply_to_message is not None,
            'reply_to_message_id': str(msg.reply_to_message.message_id) if msg.reply_to_message else None,
            'message_type': self._get_message_type(msg),
            'entities': [],
        }
        
        # Extract entities (mentions, hashtags, etc.)
        if msg.entities:
            for entity in msg.entities:
                normalized['entities'].append({
                    'type': entity.type,
                    'offset': entity.offset,
                    'length': entity.length,
                })
        
        return normalized
    
    def _get_message_type(self, msg: Any) -> str:
        """Determine message type from Telegram message object."""
        if msg.text:
            return 'text'
        elif msg.photo:
            return 'photo'
        elif msg.video:
            return 'video'
        elif msg.audio:
            return 'audio'
        elif msg.document:
            return 'document'
        elif msg.voice:
            return 'voice'
        elif msg.poll:
            return 'poll'
        elif msg.location:
            return 'location'
        elif msg.contact:
            return 'contact'
        else:
            return 'unknown'
    
    async def log_to_busy38(self, msg_data: Dict[str, Any]):
        """
        Log message to Busy38's chat_entries table.
        
        This integrates with Busy38's logging system.
        """
        try:
            # Runtime integration is implemented in busy-bridge/Telegram transport.
            # Keep this as a thin, explicit boundary until runtime logging is
            # enabled for this message handler path.
            logger.debug("Message logged for Busy38 processing: %s", msg_data.get("id"))
            
        except Exception as e:
            logger.error(f"Failed to log message to Busy38: {e}")

    def set_bot_username(self, username: str | None) -> None:
        self.bot_username = (username or "").lstrip("@").lower() or None

    def _extract_mentions(self, text: str, entities: Optional[list[dict[str, Any]]] = None) -> set[str]:
        mentions: set[str] = set()
        if text:
            for match in re.finditer(r"@([A-Za-z0-9_]{1,32})(?=$|[^A-Za-z0-9_])", text):
                mentions.add(match.group(1).lower())
        if entities:
            for entity in entities:
                if not isinstance(entity, dict) or entity.get("type") != "mention":
                    continue
                try:
                    offset = int(entity.get("offset", -1))
                    length = int(entity.get("length", 0))
                except (TypeError, ValueError):
                    continue
                if offset < 0 or length <= 0 or not text:
                    continue
                token = str(text[offset : offset + length])
                if token.startswith("@") and len(token) > 1:
                    mentions.add(token[1:].lower())
        return mentions
    
    def should_speak(self, msg_data: Dict[str, Any]) -> bool:
        """
        COGNITIVE DESKTOP: Determine if we should SPEAK (respond) to this message.
        
        Default is THINK (process internally) unless explicitly triggered to SPEAK.
        
        Triggers for SPEAKING:
        - Direct command (/command)
        - Reply to our message
        - Explicit mention of bot
        - Urgent/high-priority content
        
        Otherwise: THINK (process, log, but don't respond)
        """
        text = msg_data.get('text', '')
        
        # SPEAK: Direct commands require response
        if text.startswith('/'):
            return True
        
        # SPEAK: Replies to us require response
        if msg_data.get('is_reply'):
            return True
        
        # SPEAK: Explicit mentions require response
        mention_targets = self._extract_mentions(
            text=text,
            entities=msg_data.get("entities"),
        )
        if self.bot_username:
            if self.bot_username in mention_targets:
                return True
        elif mention_targets:
            return True
        
        # THINK: Everything else gets processed but not responded to
        # (logged, analyzed, but no chat output)
        return False
    
    async def think(self, msg_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        COGNITIVE DESKTOP: THINK about a message (internal processing).
        
        This is the default mode - process the message internally without
        sending any response to the chat. Cognition happens invisibly.
        
        Args:
            msg_data: The incoming message
            
        Returns:
            Thinking result (internal, not sent to chat)
        """
        logger.debug(f"THINKING about message {msg_data.get('id')}")
        
        # Internal processing:
        # - Analyze intent
        # - Update context/memory
        # - Log to chat_entries
        # - Update cognitive state
        
        result = {
            'action': 'think',
            'message_id': msg_data.get('id'),
            'thoughts': [],
            'should_speak': False,
        }
        
        # Log to Busy38 (internal, not visible to user)
        await self.log_to_busy38(msg_data)
        
        return result
    
    async def speak(self, chat_id: str, text: str, context: Any = None) -> Dict[str, Any]:
        """
        COGNITIVE DESKTOP: SPEAK into the chat (explicit communication).
        
        This is intentional output - the result of thinking that needs
        to be shared with the user. Explicit, not automatic.
        
        Args:
            chat_id: Chat to speak in
            text: Message to send
            context: Bot context for sending
            
        Returns:
            Speech result
        """
        logger.info(f"SPEAKING in chat {chat_id}: {text[:50]}...")
        
        # This would use ChatManager to actually send
        # For now, log the intent
        
        result = {
            'action': 'speak',
            'chat_id': chat_id,
            'text': text,
            'sent': False,  # Would be True after actual send
        }
        
        return result
