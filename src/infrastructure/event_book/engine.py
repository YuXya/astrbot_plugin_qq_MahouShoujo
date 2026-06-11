from __future__ import annotations

import json
import logging
from pathlib import Path

from ..editable_resources import EditableResourceManager
from .models import EventBookEntry, EventBookEvent

try:
    from ...utils.logger import logger
except Exception:
    logger = logging.getLogger(__name__)


class EventBookEngine:
    DEFAULT_EVENT_CATEGORIES = [
        {"id": "monster_enemy", "name": "目标是魔物"},
        {"id": "character_enemy", "name": "目标是魔法少女"},
    ]

    def __init__(
        self,
        book_path: Path | None = None,
        editable_manager: EditableResourceManager | None = None,
    ):
        self.editable_manager = editable_manager or EditableResourceManager()
        self.book_path = book_path or self.editable_manager.event_book_path

    def build_scene_event_candidates(
        self,
        text_parts: list[str] | None,
        *,
        current_event: str,
        category_ids: list[str] | None = None,
        monster_candidates: list[dict] | None = None,
        limit: int = 8,
    ) -> list[dict[str, object]]:
        events = self._load_events()
        if not events:
            return []

        current_event_key = self._normalize_event_key(current_event)
        allowed_category_ids = {
            str(item or "").strip()
            for item in (category_ids or [])
            if str(item or "").strip()
        }
        candidates: list[dict[str, object]] = []
        for event in events:
            if allowed_category_ids and event.category_id not in allowed_category_ids:
                continue
            event_commands = event.allowed_commands or [event.command]
            entries = [event.as_entry()] if event.is_scene_event else event.entries
            for entry in entries:
                if not entry.enabled:
                    continue
                if not self._entry_command_matches(entry, event_commands, current_event_key):
                    continue

                candidates.append(
                    {
                        "id": entry.id,
                        "category_id": entry.category_id or event.category_id,
                        "category_name": entry.category_name or event.category_name,
                        "title": entry.title or entry.id,
                        "source_event": event.name or event.id,
                        "command": event.command,
                        "allowed_commands": entry.allowed_commands
                        or event.allowed_commands
                        or ([event.command] if event.command else []),
                        "location_tags": entry.location_tags,
                        "compatible_monsters": entry.compatible_monsters,
                        "opening_hook": entry.opening_hook,
                        "twist_hook": entry.twist_hook,
                        "ending_hook": entry.ending_hook,
                        "content": entry.content,
                    }
                )

        return candidates[: max(1, limit)]

    def _load_events(self) -> list[EventBookEvent]:
        if not self.book_path.exists():
            logger.warning(f"事件书文件不存在: {self.book_path}")
            return []

        try:
            raw = json.loads(self.book_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"事件书 JSON 读取失败: {exc}")
            return []

        events: list[EventBookEvent] = []
        if isinstance(raw, dict) and isinstance(raw.get("categories"), list):
            for category_idx, raw_category in enumerate(raw.get("categories", [])):
                if not isinstance(raw_category, dict):
                    continue
                category_id = str(
                    raw_category.get("id") or f"category_{category_idx + 1}"
                ).strip()
                category_name = str(raw_category.get("name") or category_id).strip()
                raw_events = raw_category.get("events", [])
                if not isinstance(raw_events, list):
                    continue
                for idx, raw_event in enumerate(raw_events):
                    if not isinstance(raw_event, dict):
                        continue
                    event = EventBookEvent.from_dict(
                        raw_event,
                        fallback_id=f"{category_id}_{idx + 1}",
                        category_id=category_id,
                        category_name=category_name,
                    )
                    if event.id:
                        events.append(event)
            return events

        raw_events = raw.get("events", []) if isinstance(raw, dict) else []
        if not isinstance(raw_events, list):
            logger.warning("事件书 events 字段不是列表")
            return []

        default_category = self.DEFAULT_EVENT_CATEGORIES[0]
        for idx, raw_event in enumerate(raw_events):
            if not isinstance(raw_event, dict):
                continue
            event = EventBookEvent.from_dict(
                raw_event,
                fallback_id=str(idx),
                category_id=default_category["id"],
                category_name=default_category["name"],
            )
            if event.id:
                events.append(event)
        return events

    @staticmethod
    def _entry_command_matches(
        entry: EventBookEntry,
        event_commands: list[str],
        current_event_key: str,
    ) -> bool:
        commands = entry.allowed_commands or event_commands
        if not any(command for command in commands):
            return True
        return any(
            EventBookEngine._normalize_event_key(command) == current_event_key
            for command in commands
            if command
        )

    @staticmethod
    def _normalize_event_key(value: str) -> str:
        text = str(value or "").strip()
        return text if text.startswith("/") else f"/{text}" if text else ""
