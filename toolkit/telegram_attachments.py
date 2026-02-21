"""Attachment helpers for Telegram transport."""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any, Dict, List, Optional

from core.attachments.intake import attachment_summary_line

try:
    from core.cognition.attachment_intake import (
        ATTACHMENT_DECISION_ACCEPT,
        ATTACHMENT_DECISION_BLOCK,
        ATTACHMENT_DECISION_QUARANTINE,
        extract_attachment_text_preview,
        make_intake_decision,
        sanitize_attachment_for_transcript,
    )
except Exception:  # pragma: no cover
    try:
        from core.attachments.intake import (
            ATTACHMENT_DECISION_ACCEPT,
            ATTACHMENT_DECISION_BLOCK,
            ATTACHMENT_DECISION_QUARANTINE,
            _assess_attachment_intake as make_intake_decision,
            _extract_text_from_markitdown as _extract_attachment_text_preview,
            sanitize_attachment_for_transcript,
        )
    except Exception:  # pragma: no cover
        ATTACHMENT_DECISION_ACCEPT = "accept"
        ATTACHMENT_DECISION_QUARANTINE = "quarantine"
        ATTACHMENT_DECISION_BLOCK = "block"

        def _extract_attachment_text_preview(*_args, **_kwargs) -> Optional[str]:
            return None

        def make_intake_decision(entry: Dict[str, Any], *, redact_preview: bool = True) -> str:
            del redact_preview  # unused
            decision = ATTACHMENT_DECISION_ACCEPT
            reasons = list(entry.get("intake_reasons") or [])

            filename = str(entry.get("file_name") or entry.get("filename") or "").lower()
            size = entry.get("size")
            if size in (None, 0):
                size = entry.get("file_size")
            entry["size"] = size

            try:
                if int(size or 0) <= 0:
                    reasons.append("invalid_or_missing_size")
                    decision = ATTACHMENT_DECISION_BLOCK
            except (TypeError, ValueError):
                reasons.append("invalid_or_missing_size")
                decision = ATTACHMENT_DECISION_BLOCK

            if not entry.get("file_id") and not entry.get("file_unique_id") and not entry.get("url") and not entry.get("path"):
                reasons.append("missing_attachment_source")
                decision = ATTACHMENT_DECISION_BLOCK

            blocked_ext = (".exe", ".bat", ".cmd", ".ps1", ".msi", ".apk", ".jar", ".bin", ".sh", ".scr", ".com")
            if any(filename.endswith(ext) for ext in blocked_ext):
                reasons.append("blocked_attachment_type")
                decision = ATTACHMENT_DECISION_BLOCK

            entry["intake_reasons"] = reasons
            entry["intake_decision"] = decision
            entry["intake_policy_version"] = "v1"
            return decision

        def sanitize_attachment_for_transcript(entry: Dict[str, Any], *, redact_preview: bool = True) -> Dict[str, Any]:
            del redact_preview
            return dict(entry)


if "extract_attachment_text_preview" not in globals():
    # pragma: no cover - fallback compatibility for historical import environments.
    extract_attachment_text_preview = _extract_attachment_text_preview  # type: ignore[name-defined]


def _truthy_env(name: str, default: str = "0") -> bool:
    raw = os.getenv(name, default).strip().lower()
    return raw not in ("", "0", "false", "no", "off")


def _max_attachment_bytes() -> int:
    try:
        return max(1, int(os.getenv("TELEGRAM_ATTACHMENT_TEXT_PREVIEW_MAX_BYTES", "65536")))
    except (TypeError, ValueError):
        return 65536


def _max_attachment_chars() -> int:
    try:
        return max(1, int(os.getenv("TELEGRAM_ATTACHMENT_TEXT_PREVIEW_MAX_CHARS", "1200")))
    except (TypeError, ValueError):
        return 1200


def _should_preview_with_ocr() -> bool:
    return _truthy_env("TELEGRAM_ATTACHMENT_OCR_PREVIEW", "1")


async def _safe_download_attachment_bytes(bot: Any, file_id: str, *, max_bytes: int) -> Optional[bytes]:
    if not bot or not file_id:
        return None

    try:
        file_obj = await bot.get_file(file_id)
    except Exception:
        return None

    try:
        payload = file_obj.download_as_bytearray()
        if asyncio.iscoroutine(payload):
            payload = await payload
    except Exception:
        file_path = getattr(file_obj, "file_path", None)
        if not file_path:
            return None
        try:
            payload = bot.download_file(file_path)
            if asyncio.iscoroutine(payload):
                payload = await payload
        except Exception:
            return None

    if not isinstance(payload, (bytes, bytearray)):
        return None
    data = bytes(payload)
    if len(data) > max_bytes:
        return None
    return data


async def extract_telegram_attachments(
    message: Any,
    *,
    bot: Any = None,
    include_text_preview: Optional[bool] = None,
    preview_max_bytes: Optional[int] = None,
    preview_max_chars: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Extract Telegram attachment metadata with intake decisions applied.
    """
    include_preview = _truthy_env("TELEGRAM_ATTACHMENT_TEXT_PREVIEW_ENABLE", "1")
    if include_text_preview is not None:
        include_preview = bool(include_text_preview)

    max_preview_bytes = int(preview_max_bytes or _max_attachment_bytes())
    max_preview_chars = int(preview_max_chars or _max_attachment_chars())

    raw: List[Dict[str, Any]] = []
    if getattr(message, "photo", None):
        try:
            photo = list(message.photo)[-1]
            raw.append(
                {
                    "id": getattr(photo, "file_id", None),
                    "file_unique_id": getattr(photo, "file_unique_id", None),
                    "file_name": "photo.png",
                    "mime_type": "image/png",
                    "size": getattr(photo, "file_size", None),
                    "width": getattr(photo, "width", None),
                    "height": getattr(photo, "height", None),
                    "type": "photo",
                }
            )
        except Exception:
            pass

    doc = getattr(message, "document", None)
    if doc is not None:
        raw.append(
            {
                "id": getattr(doc, "file_id", None),
                "file_unique_id": getattr(doc, "file_unique_id", None),
                "file_name": getattr(doc, "file_name", None),
                "mime_type": getattr(doc, "mime_type", None),
                "size": getattr(doc, "file_size", None),
                "type": "document",
            }
        )

    for entry in raw:
        file_size = int(entry.get("size") or entry.get("file_size") or 0)
        entry["size"] = file_size
        if (
            include_preview
            and bot is not None
            and file_size > 0
            and file_size <= max_preview_bytes
        ):
            source_id = str(entry.get("id") or "")
            file_data = await _safe_download_attachment_bytes(bot, source_id, max_bytes=max_preview_bytes)
            if file_data:
                entry["text_hash"] = hashlib.sha256(file_data).hexdigest()
                text_preview = extract_attachment_text_preview(  # type: ignore[name-defined]
                    data=file_data,
                    filename=str(entry.get("file_name") or "attachment"),
                    content_type=entry.get("mime_type"),
                    max_chars=max_preview_chars,
                    enable_ocr=_should_preview_with_ocr(),
                )
                if text_preview:
                    entry["text_preview"] = text_preview

        if (entry.get("type") == "photo" or (entry.get("mime_type", "").lower().startswith("image/"))) and entry.get("width"):
            entry["visual_descriptor"] = f"image:{entry.get('width')}x{entry.get('height') or 'unknown'}"

        make_intake_decision(entry)
    return raw
