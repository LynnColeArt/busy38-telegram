#!/usr/bin/env python3
"""
TranscriptLogger - Chat history and transcript operations

Implements tlog namespace:
- Search chat history
- Get context around messages
- Integration with Busy38's DuckDB chat_entries
"""

import logging
import re
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TranscriptLogger:
    """
    Manages Telegram chat transcripts and search operations.
    
    Mirrors Discord's dlog namespace for consistency.
    """
    
    def __init__(self, bot):
        """
        Initialize with a Bot instance.
        
        Args:
            bot: Initialized telegram.Bot instance
        """
        self.bot = bot
        self._local_cache: Dict[str, List[Dict]] = {}  # Simple in-memory cache
        logger.debug("TranscriptLogger initialized")
    
    # =================================================================
    # Search Operations (tlog namespace)
    # =================================================================
    
    async def search(
        self,
        chat_id: str,
        query: str,
        max_age_hours: int = 24,
        max_messages: int = 5000,
        context: int = 80,
        case_sensitive: bool = False,
        regex: bool = False,
        max_results: int = 20,
    ) -> Dict[str, Any]:
        """
        Search chat history with snippet windows around matches.
        
        Implements tlog:search tool.
        
        Args:
            chat_id: Telegram chat ID
            query: Search query string
            max_age_hours: Only search messages newer than this (0 = all)
            max_messages: Max messages to scan
            context: Character context window for snippets
            case_sensitive: Case-sensitive search
            regex: Treat query as regex pattern
            max_results: Maximum results to return
        
        Returns:
            Search results with snippets and metadata
        """
        try:
            logger.info(f"Searching chat {chat_id} for: {query}")
            
            # Fetch recent messages from Telegram
            # Note: Telegram Bot API limits history access (usually ~1000 messages)
            messages = await self._fetch_messages(chat_id, limit=min(max_messages, 1000))
            
            # Filter by age if specified
            if max_age_hours > 0:
                cutoff = datetime.now() - timedelta(hours=max_age_hours)
                messages = [
                    m for m in messages
                    if datetime.fromisoformat(m['timestamp']) > cutoff
                ]
            
            # Compile search pattern
            flags = 0 if case_sensitive else re.IGNORECASE
            if regex:
                pattern = re.compile(query, flags)
            else:
                pattern = re.compile(re.escape(query), flags)
            
            # Search messages
            results = []
            for msg in messages:
                text = msg.get('text', '')
                if not text:
                    continue
                
                # Find matches
                for match in pattern.finditer(text):
                    # Extract snippet with context
                    start = max(0, match.start() - context)
                    end = min(len(text), match.end() + context)
                    snippet = text[start:end]
                    
                    # Add ellipsis if truncated
                    if start > 0:
                        snippet = '...' + snippet
                    if end < len(text):
                        snippet = snippet + '...'
                    
                    results.append({
                        'id': f"telegram:{msg['id']}",
                        'timestamp': msg['timestamp'],
                        'chat_id': chat_id,
                        'snippets': [snippet],
                        'metadata': {
                            'author_id': msg.get('from_user', {}).get('id'),
                            'author_username': msg.get('from_user', {}).get('username'),
                            'match_start': match.start(),
                            'match_end': match.end(),
                        }
                    })
                    
                    if len(results) >= max_results:
                        break
                
                if len(results) >= max_results:
                    break
            
            logger.info(f"Search found {len(results)} matches")
            
            return {
                'success': True,
                'query': query,
                'results': results,
                'total_scanned': len(messages),
            }
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def get_context(
        self,
        chat_id: str,
        message_id: str,
        before: int = 8,
        after: int = 8
    ) -> Dict[str, Any]:
        """
        Get surrounding messages for context around a specific message.
        
        Implements tlog:around tool.
        
        Args:
            chat_id: Chat ID
            message_id: Target message ID
            before: Number of messages before target
            after: Number of messages after target
        """
        try:
            logger.info(f"Getting context around message {message_id} in chat {chat_id}")
            
            # Fetch messages around the target
            # Telegram doesn't have a direct "get messages around" API
            # So we fetch recent history and find our target
            messages = await self._fetch_messages(chat_id, limit=100)
            
            # Find target message index
            target_idx = None
            for i, msg in enumerate(messages):
                if str(msg['id']) == str(message_id):
                    target_idx = i
                    break
            
            if target_idx is None:
                return {
                    'success': False,
                    'error': f"Message {message_id} not found in recent history",
                }
            
            # Extract context window
            start_idx = max(0, target_idx - before)
            end_idx = min(len(messages), target_idx + after + 1)
            
            context_messages = messages[start_idx:end_idx]
            
            logger.info(f"Retrieved {len(context_messages)} messages for context")
            
            return {
                'success': True,
                'target_message_id': message_id,
                'messages': context_messages,
                'has_more_before': start_idx > 0,
                'has_more_after': end_idx < len(messages),
            }
            
        except Exception as e:
            logger.error(f"Failed to get context: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def _fetch_messages(
        self,
        chat_id: str,
        limit: int = 100,
        offset_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch messages from Telegram chat.
        
        Note: Bot API has limitations on message history access.
        For full history, would need to use MTProto API (user account).
        """
        messages = []
        
        try:
            # Use get_updates or fetch from cache if available
            # For now, this is a simplified implementation
            # In production, this would integrate with persistent storage
            
            # Check local cache
            if chat_id in self._local_cache:
                messages = self._local_cache[chat_id][-limit:]
            
            logger.debug(f"Fetched {len(messages)} messages from cache for chat {chat_id}")
            
        except Exception as e:
            logger.error(f"Failed to fetch messages: {e}")
        
        return messages
    
    def cache_message(self, chat_id: str, message: Dict[str, Any]):
        """
        Cache a message for search/context operations.
        
        Called by message handler to build local search index.
        """
        if chat_id not in self._local_cache:
            self._local_cache[chat_id] = []
        
        self._local_cache[chat_id].append(message)
        
        # Limit cache size (keep last 10k messages per chat)
        if len(self._local_cache[chat_id]) > 10000:
            self._local_cache[chat_id] = self._local_cache[chat_id][-10000:]
    
    # =================================================================
    # Busy38 Integration
    # =================================================================
    
    async def log_to_busy38(
        self,
        chat_id: str,
        message: Dict[str, Any]
    ) -> bool:
        """
        Log message to Busy38's chat_entries table (DuckDB).
        
        This enables cross-platform search and analytics.
        
        Args:
            chat_id: Chat ID
            message: Normalized message data
            
        Returns:
            True if logged successfully
        """
        try:
            # This would integrate with Busy38's logging system
            # For now, we cache locally and log intent
            
            self.cache_message(chat_id, message)
            
            # TODO: Integrate with Busy38's DuckDB
            # project_id format: "telegram:<chat_id>"
            # table: chat_entries
            # columns: id, timestamp, project_id, content, author_id, metadata
            
            logger.debug(f"Logged message {message['id']} to Busy38 (chat {chat_id})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to log to Busy38: {e}")
            return False
    
    async def export_transcript(
        self,
        chat_id: str,
        format: str = 'json',
        since: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Export chat transcript in various formats.
        
        Utility for backups and analysis.
        
        Args:
            chat_id: Chat ID
            format: 'json', 'txt', or 'markdown'
            since: Start date for export
        """
        try:
            messages = self._local_cache.get(chat_id, [])
            
            if since:
                messages = [
                    m for m in messages
                    if datetime.fromisoformat(m['timestamp']) >= since
                ]
            
            if format == 'json':
                content = messages
            elif format == 'txt':
                lines = []
                for m in messages:
                    ts = m['timestamp'][:19]  # Truncate to seconds
                    author = m.get('from_user', {}).get('username', 'Unknown')
                    text = m.get('text', '')
                    lines.append(f"[{ts}] {author}: {text}")
                content = '\n'.join(lines)
            elif format == 'markdown':
                lines = [f"# Chat Transcript\n\nChat ID: {chat_id}\n"]
                for m in messages:
                    ts = m['timestamp'][:19]
                    author = m.get('from_user', {}).get('username', 'Unknown')
                    text = m.get('text', '')
                    lines.append(f"**[{ts}] {author}:**\n{text}\n")
                content = '\n'.join(lines)
            else:
                return {
                    'success': False,
                    'error': f"Unknown format: {format}",
                }
            
            logger.info(f"Exported {len(messages)} messages as {format}")
            
            return {
                'success': True,
                'format': format,
                'message_count': len(messages),
                'content': content,
            }
            
        except Exception as e:
            logger.error(f"Export failed: {e}")
            return {
                'success': False,
                'error': str(e),
            }