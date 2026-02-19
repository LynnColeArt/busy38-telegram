#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Compatibility wrapper around `toolkit/telegram_transcript.py`.

Busy38's vendor integration expects local-first persistence to DuckDB (chat_entries)
matching the Discord plugin's `dlog:*` behavior.

This module preserves the legacy `TranscriptLogger` symbol by aliasing the current
`TelegramTranscriptLogger`.
"""

from __future__ import annotations

from .telegram_transcript import TelegramTranscriptLogger as TranscriptLogger

__all__ = ["TranscriptLogger"]
