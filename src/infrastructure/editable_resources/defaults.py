from __future__ import annotations

import json
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _PROJECT_DIR / "prompts"
_BOOKS_DIR = _PROJECT_DIR / "books"


def _load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_book(filename: str) -> str:
    path = _BOOKS_DIR / filename
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return json.dumps({"version": 1, "entries": []}, ensure_ascii=False, indent=2)


WORLD_BOOK_DEFAULT = _load_book("world_book.json")

SKILL_BOOK_DEFAULT = _load_book("skill_book.json")

STATUS_BOOK_DEFAULT = _load_book("status_book.json")

EVENT_BOOK_DEFAULT = _load_book("event_book.json")

REINCARNATION_PROMPT = _load_prompt("reincarnation_prompt.txt")

BATTLE_DIARY_PROMPT = _load_prompt("battle_diary_prompt.txt")

DEFAULT_SYSTEM_PROMPT = _load_prompt("default_system_prompt.txt")
