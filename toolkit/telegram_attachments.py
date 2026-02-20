"""Attachment helpers for Telegram transport."""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from core.attachments.intake import (
        ATTACHMENT_DECISION_ACCEPT,
        ATTACHMENT_DECISION_BLOCK,
        ATTACHMENT_DECISION_QUARANTINE,
        _assess_attachment_intake,
        attachment_summary_line,
        sanitize_attachment_for_transcript,
    )
except Exception:  # pragma: no cover
    ATTACHMENT_DECISION_ACCEPT = "accept"
    ATTACHMENT_DECISION_QUARANTINE = "quarantine"
    ATTACHMENT_DECISION_BLOCK = "block"
    _BLOCKED_EXTENSIONS = {
        ".exe",
        ".bat",
        ".cmd",
        ".ps1",
        ".msi",
        ".apk",
        ".jar",
        ".bin",
        ".sh",
        ".scr",
        ".com",
    }

    def _assess_attachment_intake(entry: Dict[str, Any], *, redact_preview: bool = True) -> str:
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
        if any(filename.endswith(ext) for ext in _BLOCKED_EXTENSIONS):
            reasons.append("blocked_attachment_type")
            decision = ATTACHMENT_DECISION_BLOCK
        entry["intake_reasons"] = reasons
        entry["intake_decision"] = decision
        entry["intake_policy_version"] = "v1"
        return decision

    def sanitize_attachment_for_transcript(entry: Dict[str, Any], *, redact_preview: bool = True) -> Dict[str, Any]:
        del redact_preview  # unused
        decision = str(entry.get("intake_decision") or ATTACHMENT_DECISION_ACCEPT)
        payload = {
            "id": entry.get("file_unique_id") or entry.get("file_id"),
            "filename": entry.get("file_name") or entry.get("filename"),
            "size": entry.get("file_size") or entry.get("size") or 0,
            "decision": decision,
            "intake_decision": decision,
            "intake_reasons": entry.get("intake_reasons") or [],
            "intake_policy_version": entry.get("intake_policy_version"),
        }
        if decision != ATTACHMENT_DECISION_BLOCK:
            for key in ("mime_type", "type", "file_id", "file_unique_id", "message_id"):
                if entry.get(key) is not None:
                    payload[key] = entry[key]
        return payload

    def attachment_summary_line(attachments: List[Dict[str, Any]], *, max_items: int = 4) -> str:
        if not attachments:
            return ""
        parts: List[str] = []
        for att in attachments[:max_items]:
            name = str(att.get("file_name") or att.get("filename") or "file")
            size = att.get("file_size") or att.get("size")
            decision = str(att.get("intake_decision") or ATTACHMENT_DECISION_ACCEPT)
            if isinstance(size, int):
                parts.append(f"{name} ({size}B, {decision})")
            else:
                parts.append(f"{name} ({decision})")
        more = len(attachments) - len(parts)
        if more > 0:
            parts.append(f"+{more} more")
        return "[attachments] " + ", ".join(parts)


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
        _assess_attachment_intake(entry)
    return raw
