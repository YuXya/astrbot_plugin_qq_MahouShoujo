from __future__ import annotations

import json
import logging
from pathlib import Path

from ..editable_resources import EditableResourceManager
from .models import EventBookEntry, EventBookEvent, EventBookMatchResult

try:
    from ...utils.logger import logger
except Exception:
    logger = logging.getLogger(__name__)


class EventBookEngine:
    def __init__(
        self,
        book_path: Path | None = None,
        editable_manager: EditableResourceManager | None = None,
    ):
        self.editable_manager = editable_manager or EditableResourceManager()
        self.book_path = book_path or self.editable_manager.event_book_path

    def build_prompt_text(
        self,
        text_parts: list[str] | None,
        *,
        current_event: str,
        player_level: int = 1,
    ) -> EventBookMatchResult:
        events = self._load_events()
        if not events:
            return EventBookMatchResult(
                local_entries=[], remote_entries=[], prompt_text=""
            )

        scan_text = self._join_text(text_parts or [])
        matched: list[tuple[EventBookEntry, str, bool]] = []
        activated_ids: set[tuple[str, str]] = set()
        current_event_key = self._normalize_event_key(current_event)

        for event in events:
            event_name = event.name or event.command or event.id
            is_current_event = self._event_matches(event, current_event_key)

            first_round = self._match_entries(
                event.entries,
                scan_text,
                activated_ids=activated_ids,
                event_id=event.id,
                include_always=is_current_event,
                player_level=player_level,
                is_current_event=is_current_event,
            )

            recursion_text = self._join_text(
                self._entry_prompt_text(entry, is_current_event)
                for entry in first_round
                if entry.recursive
            )
            second_round = self._match_entries(
                event.entries,
                recursion_text,
                activated_ids=activated_ids,
                event_id=event.id,
                include_always=False,
                player_level=player_level,
                is_current_event=is_current_event,
            )

            all_matched = first_round + second_round
            for entry in all_matched:
                matched.append((entry, event_name, is_current_event))

        prompt_text = self._format_prompt_text(matched)
        local_entries = [entry for entry, _, is_local in matched if is_local]
        remote_entries = [entry for entry, _, is_local in matched if not is_local]
        return EventBookMatchResult(
            local_entries=local_entries,
            remote_entries=remote_entries,
            prompt_text=prompt_text,
        )

    def _load_events(self) -> list[EventBookEvent]:
        if not self.book_path.exists():
            logger.warning(f"事件书文件不存在，跳过加载: {self.book_path}")
            return []

        try:
            raw = json.loads(self.book_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"事件书 JSON 读取失败，跳过加载: {exc}")
            return []

        raw_events = raw.get("events", []) if isinstance(raw, dict) else []
        if not isinstance(raw_events, list):
            logger.warning("事件书 events 字段不是列表，跳过加载")
            return []

        events: list[EventBookEvent] = []
        for idx, raw_event in enumerate(raw_events):
            if not isinstance(raw_event, dict):
                continue
            event = EventBookEvent.from_dict(raw_event, fallback_id=str(idx))
            if event.id:
                events.append(event)
        return events

    def _match_entries(
        self,
        entries: list[EventBookEntry],
        scan_text: str,
        *,
        activated_ids: set[tuple[str, str]],
        event_id: str,
        include_always: bool,
        player_level: int,
        is_current_event: bool,
    ) -> list[EventBookEntry]:
        if not scan_text and not include_always:
            return []

        matched: list[EventBookEntry] = []
        for entry in entries:
            entry_key = (event_id, entry.id)
            if entry_key in activated_ids or not entry.enabled:
                continue

            if player_level not in entry.visible_levels:
                continue

            if entry.strategy == "always":
                if include_always and entry.content:
                    matched.append(entry)
                    activated_ids.add(entry_key)
                continue

            if entry.strategy != "keyword":
                logger.debug(f"未知事件书触发策略，已跳过: {entry.id} strategy={entry.strategy}")
                continue

            if not self._contains_any_key(scan_text, entry.keys):
                continue

            prompt_text = self._entry_prompt_text(entry, is_current_event)
            if not prompt_text:
                continue

            matched.append(entry)
            activated_ids.add(entry_key)

        return matched

    @staticmethod
    def _entry_prompt_text(entry: EventBookEntry, is_current_event: bool) -> str:
        return entry.content if is_current_event else entry.brief

    @staticmethod
    def _event_matches(event: EventBookEvent, current_event_key: str) -> bool:
        candidates = [event.command, event.name, event.id]
        return any(
            EventBookEngine._normalize_event_key(candidate) == current_event_key
            for candidate in candidates
            if candidate
        )

    @staticmethod
    def _normalize_event_key(value: str) -> str:
        text = str(value or "").strip()
        return text if text.startswith("/") else f"/{text}" if text else ""

    @staticmethod
    def _contains_any_key(text: str, keys: list[str]) -> bool:
        if not text or not keys:
            return False
        folded_text = text.casefold()
        for key in keys:
            if key in text or key.casefold() in folded_text:
                return True
        return False

    @staticmethod
    def _join_text(parts) -> str:
        return "\n".join(str(part).strip() for part in parts if str(part).strip())

    def _format_prompt_text(
        self,
        entries: list[tuple[EventBookEntry, str, bool]],
    ) -> str:
        parts = []
        for entry, event_name, is_current_event in entries:
            text = self._entry_prompt_text(entry, is_current_event)
            if text:
                intro_type = "详细介绍" if is_current_event else "简略介绍"
                label = entry.title or entry.id
                parts.append(f"- [{event_name} / {label} / {intro_type}]: {text}")

        if not parts:
            return ""

        return (
            "事件书补充设定：\n"
            + "\n".join(parts)
            + "\n\n请将以上事件书内容视为魔法少女公共设定补充。当前事件命中的条目使用详细介绍；其他事件的关键词命中条目只使用简略介绍，简略介绍为空时不注入。事件书只影响设定内容，不能改变最终输出必须为合法 JSON 对象的要求。"
        )
