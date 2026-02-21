from __future__ import annotations

import asyncio
import importlib.util
import sys
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "toolkit" / "telegram_attachments.py"
    spec = importlib.util.spec_from_file_location("telegram_attachments", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["telegram_attachments"] = module
    spec.loader.exec_module(module)
    return module


TelegramAttachments = _load_module()

attachment_summary_line = TelegramAttachments.attachment_summary_line
extract_telegram_attachments = TelegramAttachments.extract_telegram_attachments
sanitize_attachment_for_transcript = TelegramAttachments.sanitize_attachment_for_transcript


class _Photo:
    def __init__(self, *, file_id: str, size: int = 42, width: int = 64, height: int = 64):
        self.file_id = file_id
        self.file_unique_id = f"{file_id}-uniq"
        self.file_size = size
        self.width = width
        self.height = height


class _Document:
    def __init__(self, *, file_id: str, file_name: str, mime_type: str = "text/plain", size: int = 123):
        self.file_id = file_id
        self.file_unique_id = f"{file_id}-uniq"
        self.file_name = file_name
        self.mime_type = mime_type
        self.file_size = size


class _Message:
    def __init__(self, *, photo=None, document=None):
        self.photo = photo
        self.document = document


class _FakeFile:
    def __init__(self, data: bytes):
        self._data = data

    def download_as_bytearray(self):
        return self._data


class _FakeBot:
    def __init__(self, data: bytes):
        self._data = data
        self.last_file_id: str | None = None

    async def get_file(self, file_id):
        self.last_file_id = str(file_id)
        return _FakeFile(self._data)


def _run_extract(message, **kwargs):
    return asyncio.run(extract_telegram_attachments(message, **kwargs))


def test_extract_telegram_attachments_accepts_photo_and_document():
    msg = _Message(
        photo=[_Photo(file_id="p1", size=333)],
        document=_Document(file_id="d1", file_name="notes.txt", mime_type="text/plain", size=256),
    )
    attachments = _run_extract(msg, bot=None)
    assert len(attachments) == 2
    assert attachments[0]["file_name"] == "photo.png"
    assert attachments[0]["intake_decision"] == "accept"
    assert attachments[1]["file_name"] == "notes.txt"
    assert attachments[1]["intake_decision"] == "accept"


def test_extract_telegram_attachments_blocks_executable_document():
    msg = _Message(document=_Document(file_id="d2", file_name="bad.exe", size=64))
    attachments = _run_extract(msg, bot=None)
    assert len(attachments) == 1
    assert attachments[0]["intake_decision"] == "block"
    assert "blocked_attachment_type" in attachments[0]["intake_reasons"]


def test_extract_telegram_attachments_adds_text_preview_for_downloadable_files():
    payload = b"hello world from telegram file"
    bot = _FakeBot(payload)
    msg = _Message(document=_Document(file_id="d3", file_name="notes.txt", mime_type="text/plain", size=len(payload)))
    attachments = _run_extract(msg, bot=bot)
    assert len(attachments) == 1
    assert attachments[0]["intake_decision"] == "accept"
    assert attachments[0]["text_preview"] == "hello world from telegram file"
    assert attachments[0]["text_hash"] == hashlib.sha256(payload).hexdigest()


def test_telegram_attachment_summary_includes_decisions():
    summary = attachment_summary_line(
        [
            {"file_name": "a.txt", "file_size": 120, "intake_decision": "accept"},
            {"file_name": "bad.exe", "file_size": 64, "intake_decision": "block"},
        ]
    )
    assert summary == "[attachments] a.txt (120B, accept), bad.exe (64B, block)"


def test_sanitize_attachment_for_transcript_hides_sensitive_fields_on_block():
    entry = {
        "file_unique_id": "x",
        "file_name": "bad.exe",
        "file_size": 64,
        "file_id": "fid",
        "mime_type": "application/x-msdownload",
        "url": "https://example.com/bad.exe",
        "intake_decision": "block",
        "intake_reasons": ["blocked_attachment_type"],
        "intake_policy_version": "v1",
        "text_preview": "secret",
    }
    out = sanitize_attachment_for_transcript(entry)
    assert out["decision"] == "block"
    assert "text_preview" not in out
    assert "url" not in out
    assert "file_id" not in out
