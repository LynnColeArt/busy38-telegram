#!/usr/bin/env python3
"""
ChatManager - High-level chat operations for Telegram

Provides:
- Message sending/editing/deletion
- Chat information retrieval
- Member management
- Polling and pinning
"""

import logging
from typing import Optional, List, Dict, Any
from telegram import (
    Bot,
    Message,
    Chat,
    ChatMember,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup
)
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


class ChatManager:
    """
    Manages Telegram chat operations.
    
    Wraps the python-telegram-bot library for Busy38 integration.
    """
    
    def __init__(self, bot: Bot):
        """
        Initialize with a Bot instance.
        
        Args:
            bot: Initialized telegram.Bot instance
        """
        self.bot = bot
        logger.debug("ChatManager initialized")
    
    # =================================================================
    # Message Operations (tchat namespace)
    # =================================================================
    
    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_to: Optional[str] = None,
        parse_mode: Optional[str] = None,
        silent: bool = False,
        keyboard: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Send a message to a chat.
        
        Implements tchat:send tool.
        
        Args:
            chat_id: Chat ID or @username
            text: Message text (supports Markdown if parse_mode set)
            reply_to: Message ID to reply to
            parse_mode: 'Markdown', 'HTML', or None
            silent: Send without notification sound
            keyboard: Inline or reply keyboard markup
        
        Returns:
            dict with success, message_id, timestamp
        """
        try:
            # Build send parameters
            params = {
                'chat_id': chat_id,
                'text': text,
                'disable_notification': silent,
            }
            
            if reply_to:
                params['reply_to_message_id'] = int(reply_to)
            
            if parse_mode:
                params['parse_mode'] = parse_mode
            
            if keyboard:
                if isinstance(keyboard, InlineKeyboardMarkup):
                    params['reply_markup'] = keyboard
                elif isinstance(keyboard, ReplyKeyboardMarkup):
                    params['reply_markup'] = keyboard
            
            # Send message
            message: Message = await self.bot.send_message(**params)
            
            logger.info(f"Sent message {message.message_id} to chat {chat_id}")
            
            return {
                'success': True,
                'message_id': str(message.message_id),
                'timestamp': message.date.isoformat(),
                'chat_id': str(message.chat_id),
            }
            
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        parse_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Edit a previously sent message.
        
        Implements tchat:edit tool.
        """
        try:
            message: Message = await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=text,
                parse_mode=parse_mode,
            )
            
            logger.info(f"Edited message {message_id} in chat {chat_id}")
            
            return {
                'success': True,
                'message_id': str(message.message_id),
                'timestamp': message.date.isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Failed to edit message: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def delete_message(
        self,
        chat_id: str,
        message_id: str
    ) -> Dict[str, Any]:
        """
        Delete a message from a chat.
        
        Implements tchat:delete tool.
        """
        try:
            await self.bot.delete_message(
                chat_id=chat_id,
                message_id=int(message_id),
            )
            
            logger.info(f"Deleted message {message_id} from chat {chat_id}")
            
            return {
                'success': True,
            }
            
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def send_poll(
        self,
        chat_id: str,
        question: str,
        options: List[str],
        is_anonymous: bool = True,
        allows_multiple_answers: bool = False,
        reply_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a poll to a chat.
        
        Implements tchat:poll tool.
        """
        try:
            # python-telegram-bot returns a Message for send_poll; poll details live on message.poll
            message: Message = await self.bot.send_poll(
                chat_id=chat_id,
                question=question,
                options=options,
                is_anonymous=is_anonymous,
                allows_multiple_answers=allows_multiple_answers,
                reply_to_message_id=int(reply_to) if reply_to else None,
            )
            
            poll_id = None
            try:
                if getattr(message, "poll", None) is not None:
                    poll_id = message.poll.id
            except Exception:
                poll_id = None

            logger.info(f"Sent poll {poll_id or '<unknown>'} to chat {chat_id}")
            
            return {
                'success': True,
                'poll_id': poll_id,
                'message_id': str(message.message_id),
            }
            
        except Exception as e:
            logger.error(f"Failed to send poll: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def pin_message(
        self,
        chat_id: str,
        message_id: str,
        silent: bool = False
    ) -> Dict[str, Any]:
        """
        Pin a message in a chat.
        
        Implements tchat:pin tool.
        """
        try:
            await self.bot.pin_chat_message(
                chat_id=chat_id,
                message_id=int(message_id),
                disable_notification=silent,
            )
            
            logger.info(f"Pinned message {message_id} in chat {chat_id}")
            
            return {
                'success': True,
            }
            
        except Exception as e:
            logger.error(f"Failed to pin message: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def unpin_message(
        self,
        chat_id: str,
        message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Unpin a message (or all messages) in a chat.
        
        Implements tchat:unpin tool.
        """
        try:
            if message_id:
                # Unpin specific message
                await self.bot.unpin_chat_message(
                    chat_id=chat_id,
                    message_id=int(message_id),
                )
                logger.info(f"Unpinned message {message_id} in chat {chat_id}")
            else:
                # Unpin all messages
                await self.bot.unpin_all_chat_messages(chat_id=chat_id)
                logger.info(f"Unpinned all messages in chat {chat_id}")
            
            return {
                'success': True,
            }
            
        except Exception as e:
            logger.error(f"Failed to unpin message: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    # =================================================================
    # Chat Information (tchat:get_info)
    # =================================================================
    
    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """
        Get information about a chat.
        
        Implements tchat:get_info tool.
        """
        try:
            chat: Chat = await self.bot.get_chat(chat_id)
            
            result = {
                'success': True,
                'chat': {
                    'id': str(chat.id),
                    'title': chat.title or chat.username or f"Chat {chat.id}",
                    'type': chat.type,
                    'member_count': None,  # Will fill below
                }
            }
            
            # Try to get member count for groups/channels
            if chat.type in ['group', 'supergroup', 'channel']:
                try:
                    count = await self.bot.get_chat_member_count(chat_id)
                    result['chat']['member_count'] = count
                except:
                    pass
            
            logger.info(f"Retrieved info for chat {chat_id}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to get chat info: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def get_chat_members(
        self,
        chat_id: str,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Get members of a chat (requires admin rights).
        
        Implements tchat:get_members tool.
        """
        try:
            members: List[ChatMember] = []
            
            # Get administrators (always available)
            admins = await self.bot.get_chat_administrators(chat_id)
            members.extend(admins)
            
            # For small groups, we can get all members via recent messages
            # For large groups/channels, this is limited
            # Note: Telegram Bot API doesn't provide a direct "get all members" endpoint
            
            result_members = []
            for member in members[:limit]:
                user = member.user
                result_members.append({
                    'user_id': str(user.id),
                    'username': user.username,
                    'first_name': user.first_name,
                    'status': member.status,
                })
            
            logger.info(f"Retrieved {len(result_members)} members from chat {chat_id}")
            
            return {
                'success': True,
                'members': result_members,
            }
            
        except Exception as e:
            logger.error(f"Failed to get chat members: {e}")
            return {
                'success': False,
                'error': str(e),
            }
