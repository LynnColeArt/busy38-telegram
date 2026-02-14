#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
busy-38-telegram vendor plugin toolkit.

This repo is intended to be vendored into Busy38's `vendor/` directory.

Key requirement: Busy38 discovers toolkits by importing `toolkit/__init__.py`
and (optionally) instantiating a `Toolkit` class.

We keep imports lazy so that:
- `tlog:*` (local transcript search) can work with just DuckDB available.
- `tchat:*`/`tgroup:*` require `python-telegram-bot`, but missing that
  dependency doesn't prevent Busy from starting.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from core.cheatcodes.registry import register_namespace

logger = logging.getLogger(__name__)
_heartbeat_hook_registered = False
_status_hook_registered = False


def _truthy(raw: str) -> bool:
    v = (raw or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def _changelog_dir() -> str:
    return os.getenv("BUSY38_CHATLOG_DIR", "./data/memory")


def _schedule_coro(coro) -> None:
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except Exception:
        return


def _status_activity_for_cheatcode(namespace: str, action: str, attributes: Dict[str, Any]) -> Optional[str]:
    ns = str(namespace or "").strip().lower()
    act = str(action or "").strip().lower()

    if ns == "rw4":
        if act == "read_file":
            return "opening a file"
        if act == "read_range":
            return "reading a file section"
        if act == "write_file":
            return "writing a file"
        if act == "list":
            return "checking the workspace"
        if act == "shell":
            return "running a command"
        if act == "git_status":
            return "checking git status"
        if act == "git_diff":
            return "reviewing changes"
        if act == "git_commit":
            return "committing changes"
        if act == "lsp_diagnostics":
            return "checking diagnostics"

    if ns == "tlog":
        if act == "search":
            return "searching the chat history"
        if act == "around":
            return "reviewing recent context"

    if ns == "tchat":
        if act == "send":
            return "posting an update"
        if act == "edit":
            return "updating a message"
        if act == "delete":
            return "cleaning up a message"
        if act == "pin":
            return "pinning a note"
        if act == "unpin":
            return "unpinning a note"

    return None


def _maybe_register_heartbeat_jobs() -> None:
    """
    Register heartbeat hook callback that installs telegram auto-clear jobs.

    This remains plugin-local and only activates if heartbeat hooks are available.
    """
    global _heartbeat_hook_registered
    if _heartbeat_hook_registered:
        return
    _heartbeat_hook_registered = True

    try:
        from core.hooks import on_heartbeat_register_jobs
    except Exception:
        logger.debug("Heartbeat hooks unavailable; skipping telegram auto-clear hook registration")
        return

    from .telegram_runtime import run_auto_clear_cycle

    @on_heartbeat_register_jobs(priority=20)
    def _register_telegram_jobs(manager, context=None):
        if not _truthy(os.getenv("TELEGRAM_AUTO_CLEAR_ENABLE", "0")):
            return
        interval = max(60, int(os.getenv("TELEGRAM_AUTO_CLEAR_INTERVAL_SEC", "900")))
        manager.register_job(
            name="telegram_auto_clear",
            interval_seconds=interval,
            source="plugin:busy38-telegram",
            run_immediately=False,
            callback=run_auto_clear_cycle,
            metadata={
                "window_hours": int(os.getenv("TELEGRAM_CLEAR_WINDOW_HOURS", "72")),
                "min_gap_sec": int(os.getenv("TELEGRAM_AUTO_CLEAR_MIN_GAP_SEC", "21600")),
            },
        )


def _maybe_register_status_hooks() -> None:
    """
    Register hook handlers that can narrate tool/cheatcode progress in Telegram.
    """
    global _status_hook_registered
    if _status_hook_registered:
        return
    _status_hook_registered = True

    try:
        from core.hooks import on_pre_cheatcode_execute, on_post_agent_execute
    except Exception:
        logger.debug("Cheatcode hooks unavailable; skipping telegram status hook registration")
        return

    from .telegram_runtime import get_controller, get_active_context

    @on_pre_cheatcode_execute(priority=40)
    def _telegram_status_on_cheatcode(namespace: str, action: str, attributes: Dict[str, Any], context=None):
        ctrl = get_controller()
        if ctrl is None:
            return
        ctx = get_active_context() or {}
        chat_id = ctx.get("chat_id")
        if not chat_id:
            return

        activity = _status_activity_for_cheatcode(namespace, action, attributes or {})
        if not activity:
            return

        _schedule_coro(ctrl.post_status(chat_id=str(chat_id), activity=activity))

    @on_post_agent_execute(priority=80)
    def _telegram_status_on_agent_finish(agent, context=None):
        ctrl = get_controller()
        if ctrl is None:
            return
        ctx = get_active_context() or {}
        chat_id = ctx.get("chat_id")
        if not chat_id:
            return
        _schedule_coro(ctrl.clear_status(chat_id=str(chat_id)))


@dataclass
class _TelegramLogHandler:
    data_dir: str

    def __post_init__(self) -> None:
        from .telegram_transcript import TelegramTranscriptLogger

        self._logger = TelegramTranscriptLogger(data_dir=self.data_dir)

    def execute(self, action: str, **kwargs: Any) -> Any:
        action_map = {
            "search": self._search,
            "around": self._around,
        }
        fn = action_map.get((action or "").strip().lower())
        if not fn:
            return {"success": False, "error": f"Unknown action: {action}"}
        return fn(**kwargs)

    def _search(
        self,
        query: str,
        chat_id: Optional[str] = None,
        project_id: Optional[str] = None,
        max_age_hours: int = 24,
        max_messages: int = 5000,
        context: int = 80,
        case_sensitive: bool = False,
        regex: bool = False,
        snippets_per_message: int = 2,
        max_results: int = 20,
        **_: Any,
    ) -> Dict[str, Any]:
        since = None
        if max_age_hours and int(max_age_hours) > 0:
            since = datetime.now(timezone.utc) - timedelta(hours=int(max_age_hours))

        pid = (project_id or "").strip() or None
        if pid is None and chat_id:
            # chat_id is the raw Telegram chat id. Busy38 project ids for this plugin
            # are always `telegram:<chat_id>`.
            pid = f"telegram:{chat_id}"
        results = self._logger.search(
            query=str(query or ""),
            project_id=pid,
            since=since,
            max_messages=int(max_messages),
            context=int(context),
            case_sensitive=bool(case_sensitive),
            regex=bool(regex),
            snippets_per_message=int(snippets_per_message),
            max_results=int(max_results),
        )
        return {"success": True, "results": results}

    def _around(
        self,
        message_id: str,
        chat_id: Optional[str] = None,
        before: int = 8,
        after: int = 8,
        **_: Any,
    ) -> Dict[str, Any]:
        mid = str(message_id or "").strip()
        if not mid:
            return {"success": False, "error": "message_id is required"}

        # Telegram message ids are only unique per chat. Prefer the fully-qualified
        # id that we store in chat_entries: telegram:<chat_id>:<message_id>.
        if mid.startswith("telegram:"):
            source_id = mid
        else:
            if not chat_id:
                return {"success": False, "error": "chat_id is required when message_id is not prefixed"}
            source_id = f"telegram:{str(chat_id)}:{mid}"

        rows = self._logger.context_around(source_id=source_id, before=int(before), after=int(after))
        return {"success": True, "rows": rows}


class _TelegramChatHandler:
    def _session_store(self):
        """
        Best-effort Busy38 session store.

        This repo can be used standalone; when not vendored into Busy core, this
        import may fail and should be treated as a no-op.
        """
        try:
            from core.session import SessionStore  # type: ignore

            return SessionStore()
        except Exception:
            return None

    def _session_log_outbound(self, *, chat_id: str, text: str, metadata: Dict[str, Any]) -> None:
        st = self._session_store()
        if st is None:
            return
        surface_id = f"telegram:{str(chat_id)}"
        try:
            sid = st.get_bound_session(surface_id=surface_id)
            if not sid and os.getenv("TELEGRAM_SESSION_AUTO_BIND", "1").strip().lower() not in ("0", "false", "no", "off"):
                sid = st.create_session(
                    title=f"Telegram {chat_id}",
                    workspace_root="",
                    writer_id=f"telegram:{os.getpid()}",
                    metadata={"transport": "telegram", "surface_id": surface_id, **(metadata or {})},
                )
                st.bind_surface(surface_id=surface_id, session_id=sid)
            if not sid:
                return
            st.append_event(
                session_id=sid,
                writer_id=f"telegram:{os.getpid()}",
                type="chat.message",
                payload={
                    "transport": "telegram",
                    "surface_id": surface_id,
                    "role": "assistant",
                    "text": str(text or ""),
                    "metadata": metadata or {},
                },
            )
        except Exception:
            return

    async def execute(self, action: str, **kwargs: Any) -> Any:
        act = (action or "").strip().lower()
        action_map = {
            "send": self._send,
            "edit": self._edit,
            "delete": self._delete,
            "poll": self._poll,
            "pin": self._pin,
            "unpin": self._unpin,
            "get_info": self._get_info,
            "get_members": self._get_members,
            "read": self._read,  # local read from chat_entries
        }
        fn = action_map.get(act)
        if not fn:
            return {"success": False, "error": f"Unknown action: {action}"}
        return await fn(**kwargs)

    def _require_token(self) -> str:
        tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not tok:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN (set env var or wire SquidKeys)")
        return tok

    async def _get_bot(self):
        # Lazy import so the plugin can load even if telegram deps aren't installed.
        from telegram import Bot

        return Bot(token=self._require_token())

    async def _send(self, chat_id: str, text: str, reply_to: Optional[str] = None, silent: bool = False, **kwargs: Any):
        from .chat_manager import ChatManager

        bot = await self._get_bot()
        mgr = ChatManager(bot)
        res = await mgr.send_message(
            chat_id=str(chat_id),
            text=str(text or ""),
            reply_to=str(reply_to) if reply_to else None,
            parse_mode=kwargs.get("parse_mode"),
            silent=bool(silent),
        )
        if res and res.get("success"):
            self._session_log_outbound(
                chat_id=str(chat_id),
                text=str(text or ""),
                metadata={
                    "action": "tchat:send",
                    "message_id": res.get("message_id"),
                    "timestamp": res.get("timestamp"),
                },
            )
        return res

    async def _edit(self, chat_id: str, message_id: str, text: str, **kwargs: Any):
        from .chat_manager import ChatManager

        bot = await self._get_bot()
        mgr = ChatManager(bot)
        res = await mgr.edit_message(
            chat_id=str(chat_id),
            message_id=str(message_id),
            text=str(text or ""),
            parse_mode=kwargs.get("parse_mode"),
        )
        if res and res.get("success"):
            self._session_log_outbound(
                chat_id=str(chat_id),
                text=str(text or ""),
                metadata={
                    "action": "tchat:edit",
                    "message_id": str(message_id),
                },
            )
        return res

    async def _delete(self, chat_id: str, message_id: str, **_: Any):
        from .chat_manager import ChatManager

        bot = await self._get_bot()
        mgr = ChatManager(bot)
        return await mgr.delete_message(chat_id=str(chat_id), message_id=str(message_id))

    async def _poll(self, chat_id: str, question: str, options: Any, **kwargs: Any):
        from .chat_manager import ChatManager

        bot = await self._get_bot()
        mgr = ChatManager(bot)
        # tool_spec.yaml says options is an array; API_REFERENCE shows stringified example.
        # We accept either.
        opts = options
        if isinstance(options, str):
            # Comma-separated fallback.
            opts = [s.strip() for s in options.split(",") if s.strip()]
        return await mgr.send_poll(
            chat_id=str(chat_id),
            question=str(question or ""),
            options=list(opts or []),
            is_anonymous=bool(kwargs.get("is_anonymous", True)),
            allows_multiple_answers=bool(kwargs.get("allows_multiple_answers", False)),
            reply_to=str(kwargs.get("reply_to")) if kwargs.get("reply_to") else None,
        )

    async def _pin(self, chat_id: str, message_id: str, silent: bool = False, **_: Any):
        from .chat_manager import ChatManager

        bot = await self._get_bot()
        mgr = ChatManager(bot)
        return await mgr.pin_message(chat_id=str(chat_id), message_id=str(message_id), silent=bool(silent))

    async def _unpin(self, chat_id: str, message_id: Optional[str] = None, **_: Any):
        from .chat_manager import ChatManager

        bot = await self._get_bot()
        mgr = ChatManager(bot)
        return await mgr.unpin_message(chat_id=str(chat_id), message_id=str(message_id) if message_id else None)

    async def _get_info(self, chat_id: str, **_: Any):
        from .chat_manager import ChatManager

        bot = await self._get_bot()
        mgr = ChatManager(bot)
        return await mgr.get_chat_info(chat_id=str(chat_id))

    async def _get_members(self, chat_id: str, limit: int = 100, **_: Any):
        from .chat_manager import ChatManager

        bot = await self._get_bot()
        mgr = ChatManager(bot)
        return await mgr.get_chat_members(chat_id=str(chat_id), limit=int(limit or 100))

    async def _read(self, chat_id: str, limit: int = 50, max_age_hours: int = 24, **_: Any):
        from .telegram_transcript import TelegramTranscriptLogger

        lg = TelegramTranscriptLogger(data_dir=_changelog_dir())
        since = None
        if max_age_hours and int(max_age_hours) > 0:
            since = datetime.now(timezone.utc) - timedelta(hours=int(max_age_hours))
        rows = lg.recent_messages(project_id=f"telegram:{str(chat_id)}", since=since, limit=int(limit or 50))
        # Return in a tool_spec.yaml-friendly shape.
        msgs = []
        for r in rows:
            meta = r.get("metadata") or {}
            author = meta.get("author_username") or meta.get("author_first_name") or meta.get("author_id") or ""
            msgs.append(
                {
                    "id": str(r.get("id") or ""),
                    "timestamp": str(r.get("timestamp") or ""),
                    "from": str(author),
                    "text": str(r.get("content") or ""),
                }
            )
        return {"success": True, "messages": msgs}


class _TelegramGroupHandler:
    async def execute(self, action: str, **kwargs: Any) -> Any:
        act = (action or "").strip().lower()
        action_map = {
            "invite": self._invite,
            "ban": self._ban,
            "unban": self._unban,
            "set_permissions": self._set_permissions,
        }
        fn = action_map.get(act)
        if not fn:
            return {"success": False, "error": f"Unknown action: {action}"}
        return await fn(**kwargs)

    async def _get_bot(self):
        from telegram import Bot

        tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not tok:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN (set env var or wire SquidKeys)")
        return Bot(token=tok)

    async def _invite(self, chat_id: str, user_id: str, **_: Any):
        from .group_manager import GroupManager

        bot = await self._get_bot()
        mgr = GroupManager(bot)
        return await mgr.invite_user(chat_id=str(chat_id), user_id=str(user_id))

    async def _ban(self, chat_id: str, user_id: str, until_date: Optional[int] = None, revoke_messages: bool = False, **_: Any):
        from .group_manager import GroupManager

        bot = await self._get_bot()
        mgr = GroupManager(bot)
        return await mgr.ban_user(
            chat_id=str(chat_id),
            user_id=str(user_id),
            until_date=int(until_date) if until_date is not None else None,
            revoke_messages=bool(revoke_messages),
        )

    async def _unban(self, chat_id: str, user_id: str, **_: Any):
        from .group_manager import GroupManager

        bot = await self._get_bot()
        mgr = GroupManager(bot)
        return await mgr.unban_user(chat_id=str(chat_id), user_id=str(user_id))

    async def _set_permissions(self, chat_id: str, user_id: str, **kwargs: Any):
        from .group_manager import GroupManager

        bot = await self._get_bot()
        mgr = GroupManager(bot)
        return await mgr.set_permissions(chat_id=str(chat_id), user_id=str(user_id), **kwargs)


class Toolkit:
    """Vendor plugin entry point (auto-instantiated by PluginManager)."""

    def __init__(self):
        # Optional integrations that depend on Busy core hook system.
        _maybe_register_heartbeat_jobs()
        _maybe_register_status_hooks()

        # Transcript tools are local-first and should be safe to register even if
        # telegram deps aren't installed (they only need duckdb).
        try:
            register_namespace("tlog", _TelegramLogHandler(data_dir=_changelog_dir()))
        except Exception as exc:
            logger.warning("busy-38-telegram: failed registering tlog: %s", exc)

        # Bot API tools require python-telegram-bot. Register them only if enabled.
        if _truthy(os.getenv("BUSY38_TELEGRAM_ENABLE_CHAT", "1")):
            try:
                register_namespace("tchat", _TelegramChatHandler())
                register_namespace("tgroup", _TelegramGroupHandler())
            except Exception as exc:
                logger.warning("busy-38-telegram: failed registering tchat/tgroup: %s", exc)


__all__ = ["Toolkit"]
