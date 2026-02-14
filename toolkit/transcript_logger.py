#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
Compatibility wrapper around `toolkit/telegram_transcript.py`.

Kat's initial draft implemented an in-memory cache with TODO Busy38 logging.
Busy38's vendor scheme expects local-first persistence to DuckDB (chat_entries),
matching the Discord plugin's `dlog:*` behavior.

New code should import and use `TelegramTranscriptLogger` directly. This wrapper
keeps the original `TranscriptLogger` name for minimal churn.
"""

from __future__ import annotations

from .telegram_transcript import TelegramTranscriptLogger as TranscriptLogger

__all__ = ["TranscriptLogger"]

