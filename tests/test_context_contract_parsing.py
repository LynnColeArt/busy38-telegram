from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PROJECT = (ROOT.parent / "busy-38-ongoing").resolve()

sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(CORE_PROJECT))
from toolkit.telegram_bot import Busy38TelegramBot


def _bot():
    # Avoid __init__ side effects (token, bot application bootstrap) by creating only shell instance.
    return Busy38TelegramBot.__new__(Busy38TelegramBot)


def test_extract_context_metadata_prefers_modern_contract_fields():
    bot = _bot()
    payload = {
        "context_contract": {"context_schema_version": 2, "context_source": "session", "context_budget_tokens": 11000},
        "context_payload": {"context_budget_tokens": 11000, "context_source": "session", "budget_usage_tokens": 420},
    }

    context = bot._extract_context_metadata(payload)

    assert context["context_schema_version"] == 2
    assert context["context_source"] == "session"
    assert context["context_budget_tokens"] == 11000
    assert context["budget_usage_tokens"] == 420
    assert context["context_contract_compat_mode"] == "modern"
    assert context["context_contract"] == payload["context_contract"]
    assert context["context_payload"] == payload["context_payload"]


def test_extract_context_metadata_handles_legacy_nested_contract():
    bot = _bot()
    payload = {
        "context_payload": {
            "context_contract": {
                "context_schema_version": 1,
                "context_source": "resume",
                "context_budget_tokens": 4096,
                "budget_usage_tokens": 11,
            }
        }
    }

    context = bot._extract_context_metadata(payload)

    assert context["context_schema_version"] == 1
    assert context["context_source"] == "resume"
    assert context["context_budget_tokens"] == 4096
    assert context["budget_usage_tokens"] == 11
    assert context["context_contract_compat_mode"] == "legacy"
    assert context["context_payload"] == payload["context_payload"]


def test_extract_context_metadata_preserves_compat_mode_override():
    bot = _bot()
    payload = {
        "context_contract_compat_mode": "Modern",
        "context_schema_version": "3",
        "context_budget_tokens": "6400",
    }

    context = bot._extract_context_metadata(payload)

    assert context["context_schema_version"] == 3
    assert context["context_contract_compat_mode"] == "modern"
