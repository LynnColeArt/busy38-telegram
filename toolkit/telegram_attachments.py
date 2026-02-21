"""Attachment helpers for Telegram transport."""

from __future__ import annotations

from typing import Any, Dict, List

from core.attachments.intake import attachment_summary_line

try:
    from core.cognition.attachment_intake import (
        ATTACHMENT_DECISION_ACCEPT,
        ATTACHMENT_DECISION_BLOCK,
        ATTACHMENT_DECISION_QUARANTINE,
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
            sanitize_attachment_for_transcript,
        )
    except Exception:  # pragma: no cover
        ATTACHMENT_DECISION_ACCEPT = "accept"
        ATTACHMENT_DECISION_QUARANTINE = "quarantine"
        ATTACHMENT_DECISION_BLOCK = "block"

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


def extract_telegram_attachments(message: Any) -> List[Dict[str, Any]]:
    """
    Extract Telegram attachment metadata with intake decisions applied.
    """
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
                    "file_size": getattr(photo, "file_size", None),
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
                "file_size": getattr(doc, "file_size", None),
                "type": "document",
            }
        )

    for entry in raw:
        make_intake_decision(entry)
    return raw
