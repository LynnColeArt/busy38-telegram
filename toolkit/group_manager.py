#!/usr/bin/env python3
"""
GroupManager - Telegram group and channel management

Implements tgroup namespace:
- Member invites
- Ban/unban operations
- Permission management
"""

import logging
from typing import Optional, Dict, Any
from telegram import ChatPermissions, ChatMember

logger = logging.getLogger(__name__)


class GroupManager:
    """
    Manages Telegram group and channel operations.
    
    Requires appropriate admin permissions in the target chat.
    """
    
    def __init__(self, bot):
        """
        Initialize with a Bot instance.
        
        Args:
            bot: Initialized telegram.Bot instance
        """
        self.bot = bot
        logger.debug("GroupManager initialized")
    
    # =================================================================
    # Member Management (tgroup namespace)
    # =================================================================
    
    async def invite_user(
        self,
        chat_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        Invite a user to a group/channel.
        
        Implements tgroup:invite tool.
        
        Note: For public groups, this generates an invite link.
        For private groups, user must be added directly.
        """
        try:
            # Bots typically cannot "add user by id" into a group/channel without
            # the user joining. The durable approach is to create an invite link.
            invite = await self.bot.create_chat_invite_link(
                chat_id=chat_id,
                member_limit=1,  # Single-use
            )
            
            logger.info(f"Created invite link for user {user_id} to chat {chat_id}")
            return {
                'success': True,
                'method': 'invite_link',
                'invite_link': invite.invite_link,
                'note': 'Send this link to the user',
            }
                
        except Exception as e:
            logger.error(f"Failed to invite user: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def ban_user(
        self,
        chat_id: str,
        user_id: str,
        until_date: Optional[int] = None,
        revoke_messages: bool = False
    ) -> Dict[str, Any]:
        """
        Ban a user from a group/channel.
        
        Implements tgroup:ban tool.
        
        Args:
            chat_id: Chat ID
            user_id: User ID to ban
            until_date: Unix timestamp when ban expires (None = permanent)
            revoke_messages: Delete all messages from user
        """
        try:
            await self.bot.ban_chat_member(
                chat_id=chat_id,
                user_id=int(user_id),
                until_date=until_date,
                revoke_messages=revoke_messages,
            )
            
            ban_type = "temporary" if until_date else "permanent"
            logger.info(f"Banned user {user_id} from chat {chat_id} ({ban_type})")
            
            return {
                'success': True,
                'ban_type': ban_type,
                'until_date': until_date,
            }
            
        except Exception as e:
            logger.error(f"Failed to ban user: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def unban_user(
        self,
        chat_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        Unban a user from a group/channel.
        
        Implements tgroup:unban tool.
        """
        try:
            await self.bot.unban_chat_member(
                chat_id=chat_id,
                user_id=int(user_id),
            )
            
            logger.info(f"Unbanned user {user_id} from chat {chat_id}")
            
            return {
                'success': True,
            }
            
        except Exception as e:
            logger.error(f"Failed to unban user: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def set_permissions(
        self,
        chat_id: str,
        user_id: str,
        can_send_messages: Optional[bool] = None,
        can_send_media: Optional[bool] = None,
        can_send_polls: Optional[bool] = None,
        can_send_other_messages: Optional[bool] = None,
        can_add_web_page_previews: Optional[bool] = None,
        can_change_info: Optional[bool] = None,
        can_invite_users: Optional[bool] = None,
        can_pin_messages: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Set user permissions in a group/channel.
        
        Implements tgroup:set_permissions tool.
        
        Args:
            chat_id: Chat ID
            user_id: User ID
            Various permission booleans (None = don't change)
        """
        try:
            # Build permissions object
            permissions = ChatPermissions(
                can_send_messages=can_send_messages,
                can_send_media_messages=can_send_media,
                can_send_polls=can_send_polls,
                can_send_other_messages=can_send_other_messages,
                can_add_web_page_previews=can_add_web_page_previews,
                can_change_info=can_change_info,
                can_invite_users=can_invite_users,
                can_pin_messages=can_pin_messages,
            )
            
            await self.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=int(user_id),
                permissions=permissions,
            )
            
            logger.info(f"Updated permissions for user {user_id} in chat {chat_id}")
            
            return {
                'success': True,
                'permissions_set': {
                    k: v for k, v in {
                        'can_send_messages': can_send_messages,
                        'can_send_media': can_send_media,
                        'can_send_polls': can_send_polls,
                        'can_invite_users': can_invite_users,
                        'can_pin_messages': can_pin_messages,
                    }.items() if v is not None
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to set permissions: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def promote_member(
        self,
        chat_id: str,
        user_id: str,
        is_anonymous: bool = False,
        can_manage_chat: bool = False,
        can_delete_messages: bool = False,
        can_manage_video_chats: bool = False,
        can_restrict_members: bool = False,
        can_promote_members: bool = False,
        can_change_info: bool = False,
        can_invite_users: bool = False,
        can_post_messages: bool = False,
        can_edit_messages: bool = False,
        can_pin_messages: bool = False,
    ) -> Dict[str, Any]:
        """
        Promote a member to administrator.
        
        Additional tgroup feature for admin management.
        """
        try:
            await self.bot.promote_chat_member(
                chat_id=chat_id,
                user_id=int(user_id),
                is_anonymous=is_anonymous,
                can_manage_chat=can_manage_chat,
                can_delete_messages=can_delete_messages,
                can_manage_video_chats=can_manage_video_chats,
                can_restrict_members=can_restrict_members,
                can_promote_members=can_promote_members,
                can_change_info=can_change_info,
                can_invite_users=can_invite_users,
                can_post_messages=can_post_messages,
                can_edit_messages=can_edit_messages,
                can_pin_messages=can_pin_messages,
            )
            
            logger.info(f"Promoted user {user_id} to admin in chat {chat_id}")
            
            return {
                'success': True,
                'admin_rights': {
                    'is_anonymous': is_anonymous,
                    'can_manage_chat': can_manage_chat,
                    'can_delete_messages': can_delete_messages,
                    'can_restrict_members': can_restrict_members,
                    'can_promote_members': can_promote_members,
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to promote member: {e}")
            return {
                'success': False,
                'error': str(e),
            }
    
    async def get_member_info(
        self,
        chat_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        Get detailed information about a chat member.
        
        Additional utility for member inspection.
        """
        try:
            member: ChatMember = await self.bot.get_chat_member(
                chat_id=chat_id,
                user_id=int(user_id)
            )
            
            result = {
                'success': True,
                'member': {
                    'user_id': str(member.user.id),
                    'username': member.user.username,
                    'first_name': member.user.first_name,
                    'status': member.status,
                    'joined_date': member.joined_date.isoformat() if member.joined_date else None,
                }
            }
            
            # Add admin-specific info if applicable
            if member.status in ['administrator', 'creator']:
                result['member']['admin_rights'] = {
                    'is_anonymous': getattr(member, 'is_anonymous', False),
                    'can_manage_chat': getattr(member, 'can_manage_chat', False),
                    'can_delete_messages': getattr(member, 'can_delete_messages', False),
                    'can_restrict_members': getattr(member, 'can_restrict_members', False),
                    'can_promote_members': getattr(member, 'can_promote_members', False),
                }
            
            logger.info(f"Retrieved member info for user {user_id} in chat {chat_id}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to get member info: {e}")
            return {
                'success': False,
                'error': str(e),
            }
