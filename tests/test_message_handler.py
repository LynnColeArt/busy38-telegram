from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timezone
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


MessageHandler = _load_module("message_handler").MessageHandler


class _User:
    def __init__(self, user_id: int = 1):
        self.id = user_id
        self.username = "tester"
        self.first_name = "Test"
        self.last_name = "User"
        self.is_bot = False


class _Message:
    def __init__(self, text: str, message_id: int = 1, has_reply: bool = False):
        self.message_id = message_id
        self.chat_id = 77
        self.text = text
        self.caption = None
        self.from_user = _User()
        self.date = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.reply_to_message = object() if has_reply else None
        self.photo = None
        self.video = None
        self.audio = None
        self.document = None
        self.voice = None
        self.poll = None
        self.location = None
        self.contact = None
        self.entities = []


class _FakeTranscriptLogger:
    def __init__(self):
        self.logged: list[dict] = []

    def log_message(self, **kwargs):
        self.logged.append(kwargs)


def test_extract_command_parses_bot_suffix_and_args():
    h = MessageHandler()
    assert h._extract_command("/ping@bot hi") == "ping"
    assert h._extract_command("/stats all") == "stats"


def test_normalize_message_and_type():
    h = MessageHandler()
    msg = _Message("hello")
    normalized = h.normalize_message(msg)
    assert normalized["id"] == "1"
    assert normalized["chat_id"] == "77"
    assert normalized["message_type"] == "text"
    assert normalized["is_reply"] is False


def test_should_speak_rules():
    h = MessageHandler(bot_username="Busy38")
    assert h.should_speak({"text": "/help", "is_reply": False}) is True
    assert h.should_speak({"text": "hello @busy38", "is_reply": False}) is True
    assert h.should_speak({"text": "hello @other", "is_reply": False}) is False
    assert h.should_speak({"text": "hello", "is_reply": True}) is True
    assert h.should_speak({"text": "hello", "is_reply": False}) is False


def test_should_speak_respects_entities_and_bot_username():
    h = MessageHandler(bot_username="@busy38")
    assert h.should_speak(
        {
            "text": "hey @busy38 can you help?",
            "is_reply": False,
            "entities": [
                {"type": "mention", "offset": 4, "length": 8},
            ],
        }
    ) is True
    assert h.should_speak(
        {
            "text": "hey @other can you help?",
            "is_reply": False,
            "entities": [
                {"type": "mention", "offset": 4, "length": 6},
            ],
        }
    ) is False


def test_command_and_processor_hooks():
    h = MessageHandler()
    called = {"cmd": 0, "processor": 0}

    @h.on_command("hello")
    async def _cmd(msg):
        called["cmd"] += 1
        assert msg["id"] == "1"

    @h.on_message
    async def _processor(msg):
        called["processor"] += 1

    result = asyncio.run(h.process_message(h.normalize_message(_Message("/hello world"))))
    assert called["cmd"] == 1
    assert called["processor"] == 1
    assert result["commands_found"] == ["hello"]
    assert result["handlers_triggered"] == ["command:hello", "_processor"]


def test_log_to_busy38_persists_normalized_message():
    fake_logger = _FakeTranscriptLogger()
    h = MessageHandler(transcript_logger=fake_logger)
    normalized = MessageHandler().normalize_message(_Message("hello world", message_id=17))

    asyncio.run(h.log_to_busy38(normalized))

    assert len(fake_logger.logged) == 1
    payload = fake_logger.logged[0]
    assert payload["chat_id"] == "77"
    assert payload["message_id"] == "17"
    assert payload["content"] == "hello world"
    assert payload["metadata"]["transport"] == "telegram"
    assert payload["metadata"]["message_type"] == "text"


def test_log_to_busy38_handles_bad_timestamp_gracefully():
    fake_logger = _FakeTranscriptLogger()
    h = MessageHandler(transcript_logger=fake_logger)
    msg = MessageHandler().normalize_message(_Message("hello", message_id=8))
    msg["timestamp"] = "not-a-timestamp"

    asyncio.run(h.log_to_busy38(msg))

    assert len(fake_logger.logged) == 1
    assert fake_logger.logged[0]["message_id"] == "8"
