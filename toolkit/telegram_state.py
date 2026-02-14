#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
Telegram state utilities for Busy38.

Matches the Discord plugin's core runtime affordances:
- per-chat subscription state
- follow-mode toggle (more proactive response triggers, still rate-limited)

This plugin repo is intended to be vendored, so we keep this pure-python and
persist to a local JSON file by default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ChatKey:
    chat_id: str

    def as_str(self) -> str:
        return str(self.chat_id)


@dataclass
class ChatConfig:
    subscribed: bool = False
    follow_mode: bool = False
    history_limit: int = 80


class TelegramStateStore:
    def __init__(self, *, default_history_limit: int = 80):
        self._default_history_limit = int(default_history_limit)
        self._chats: Dict[str, ChatConfig] = {}

    def _get(self, key: ChatKey) -> ChatConfig:
        k = key.as_str()
        if k not in self._chats:
            self._chats[k] = ChatConfig(history_limit=self._default_history_limit)
        return self._chats[k]

    def set_subscribed(self, key: ChatKey, subscribed: bool) -> None:
        self._get(key).subscribed = bool(subscribed)

    def set_follow_mode(self, key: ChatKey, follow_mode: bool) -> None:
        self._get(key).follow_mode = bool(follow_mode)

    def chat_config(self, key: ChatKey) -> ChatConfig:
        return self._get(key)

    def list_subscriptions(self) -> List[Tuple[ChatKey, ChatConfig]]:
        out: List[Tuple[ChatKey, ChatConfig]] = []
        for chat_id, cfg in self._chats.items():
            if cfg.subscribed:
                out.append((ChatKey(chat_id=chat_id), cfg))
        return out

    def export_subscriptions(self) -> Dict[str, Dict]:
        data: Dict[str, Dict] = {}
        for chat_id, cfg in self._chats.items():
            if cfg.subscribed:
                data[str(chat_id)] = {
                    "subscribed": True,
                    "follow_mode": bool(cfg.follow_mode),
                    "history_limit": int(cfg.history_limit),
                }
        return data

    def import_subscriptions(self, data: Dict[str, Dict]) -> None:
        for chat_id, cfg in (data or {}).items():
            try:
                key = ChatKey(chat_id=str(chat_id))
                st = self._get(key)
                st.subscribed = bool(cfg.get("subscribed", True))
                st.follow_mode = bool(cfg.get("follow_mode", False))
                st.history_limit = int(cfg.get("history_limit", self._default_history_limit))
            except Exception:
                continue

    def load_from_path(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        raw = p.read_text(encoding="utf-8")
        obj = json.loads(raw) if raw.strip() else {}
        self.import_subscriptions(obj if isinstance(obj, dict) else {})

    def save_to_path(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = self.export_subscriptions()
        p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

