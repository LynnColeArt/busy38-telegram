from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str):
    path = ROOT / "toolkit" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


telegram_state = _load_module("telegram_state")
ChatKey = telegram_state.ChatKey
TelegramStateStore = telegram_state.TelegramStateStore


def test_config_subscription_tracking():
    store = TelegramStateStore()
    chat = ChatKey("c1")

    assert store.chat_config(chat).subscribed is False
    store.set_subscribed(chat, True)
    store.set_follow_mode(chat, True)

    subs = store.list_subscriptions()
    assert len(subs) == 1
    assert subs[0][0] == chat
    assert subs[0][1].follow_mode is True


def test_export_import_roundtrip():
    store = TelegramStateStore()
    chat = ChatKey("a")
    store.set_subscribed(chat, True)
    store.set_follow_mode(chat, False)
    store.set_follow_mode(chat, True)

    data = store.export_subscriptions()
    assert data["a"]["history_limit"] == 80

    fresh = TelegramStateStore()
    fresh.import_subscriptions(data)
    cfg = fresh.chat_config(chat)
    assert cfg.subscribed is True
    assert cfg.follow_mode is True


def test_load_and_save_path_roundtrip(tmp_path: Path):
    path = tmp_path / "subs.json"
    store = TelegramStateStore()
    chat = ChatKey("123")
    store.set_subscribed(chat, True)
    store.set_follow_mode(chat, True)
    store.save_to_path(str(path))

    loaded = TelegramStateStore()
    loaded.load_from_path(str(path))
    cfg = loaded.chat_config(chat)
    assert cfg.subscribed is True
    assert cfg.follow_mode is True
