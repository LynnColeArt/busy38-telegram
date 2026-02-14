#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
Runtime glue for the Busy38 Telegram transport.

This mirrors the Discord plugin's pattern:
- a global "controller" pointer (the bot runtime instance)
- a global bot pointer (python-telegram-bot Application/Bot access)
- an "active context" (current chat) for hook handlers to post narration
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Dict, Optional

_runtime_bot: Any = None
_runtime_controller: Any = None

_active_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "busy38_telegram_active_context", default={}
)


def _set_telegram_runtime_bot(bot: Any) -> None:
    global _runtime_bot
    _runtime_bot = bot


def _set_telegram_runtime_controller(ctrl: Any) -> None:
    global _runtime_controller
    _runtime_controller = ctrl


def get_bot() -> Any:
    return _runtime_bot


def get_controller() -> Any:
    return _runtime_controller


def get_active_context() -> Dict[str, Any]:
    try:
        return dict(_active_context.get() or {})
    except Exception:
        return {}


@contextmanager
def bind_active_context(ctx: Dict[str, Any]):
    token = _active_context.set(dict(ctx or {}))
    try:
        yield
    finally:
        try:
            _active_context.reset(token)
        except Exception:
            pass


async def run_auto_clear_cycle(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Wrapper used by heartbeat hook jobs. Delegates to the transport controller.
    """
    ctrl = get_controller()
    fn = getattr(ctrl, "run_auto_clear_cycle", None)
    if not fn:
        return {"success": False, "skipped": "telegram_controller_missing_auto_clear"}
    return await fn(trigger="heartbeat", payload=payload or {})


__all__ = [
    "_set_telegram_runtime_bot",
    "_set_telegram_runtime_controller",
    "get_bot",
    "get_controller",
    "get_active_context",
    "bind_active_context",
    "run_auto_clear_cycle",
]

