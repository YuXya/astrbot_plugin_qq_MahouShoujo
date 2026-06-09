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
            event_commands = event.allowed_commands or [event.command]
            entries = [event.as_entry()] if event.is_scene_event else event.entries

            first_round = self._match_entries(
                entries,
                scan_text,
                activated_ids=activated_ids,
                event_id=event.id,
                event_commands=event_commands,
                current_event_key=current_event_key,
                include_always=is_current_event,
                player_level=player_level,
                is_current_event=is_current_event,
            )

            recursion_text = self._join_text(
                entry.content
                for entry in first_round
                if entry.recursive
            )
            second_round = self._match_entries(
                entries,
                recursion_text,
                activated_ids=activated_ids,
                event_id=event.id,
                event_commands=event_commands,
                current_event_key=current_event_key,
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

    def build_scene_event_candidates(
        self,
        text_parts: list[str] | None,
        *,
        current_event: str,
        player_level: int = 1,
        battle_types: list[str] | None = None,
        monster_candidates: list[dict] | None = None,
        limit: int = 8,
    ) -> list[dict[str, object]]:
        events = self._load_events()
        if not events:
            return []

        scan_text = self._join_text(text_parts or [])
        current_event_key = self._normalize_event_key(current_event)
        allowed_battle_types = {
            str(item or "").strip()
            for item in (battle_types or [])
            if str(item or "").strip()
        }
        monster_tags = self._collect_monster_tags(monster_candidates or [])

        scored: list[tuple[int, dict[str, object]]] = []
        for event in events:
            event_commands = event.allowed_commands or [event.command]
            event_is_current = self._event_matches(event, current_event_key)
            entries = [event.as_entry()] if event.is_scene_event else event.entries
            for entry in entries:
                if not entry.enabled or player_level not in entry.visible_levels:
                    continue
                if not self._entry_command_matches(entry, event_commands, current_event_key):
                    continue
                if allowed_battle_types and entry.compatible_battle_types:
                    if not allowed_battle_types.intersection(entry.compatible_battle_types):
                        continue

                score = entry.weight
                if event_is_current:
                    score += 20
                if entry.strategy == "always" and event_is_current:
                    score += 8
                if self._contains_any_key(scan_text, entry.keys):
                    score += 40
                score += self._tag_overlap_score(scan_text, entry.event_tags, 6)
                score += self._tag_overlap_score(scan_text, entry.location_tags, 8)
                if monster_tags and entry.compatible_monster_tags:
                    score += (
                        len(monster_tags.intersection(entry.compatible_monster_tags))
                        * 12
                    )

                if score <= 0:
                    continue
                scored.append(
                    (
                        score,
                        {
                            "id": entry.id,
                            "title": entry.title or entry.id,
                            "source_event": event.name or event.id,
                            "command": event.command,
                            "allowed_commands": entry.allowed_commands
                            or event.allowed_commands
                            or ([event.command] if event.command else []),
                            "event_tags": entry.event_tags,
                            "location_tags": entry.location_tags,
                            "compatible_monster_tags": entry.compatible_monster_tags,
                            "compatible_battle_types": entry.compatible_battle_types,
                            "opening_hook": entry.opening_hook,
                            "twist_hook": entry.twist_hook,
                            "ending_hook": entry.ending_hook,
                            "content": entry.content,
                            "selection_score": score,
                        },
                    )
                )

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in scored[: max(1, limit)]]

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

    @staticmethod
    def _entry_command_matches(
        entry: EventBookEntry,
        event_commands: list[str],
        current_event_key: str,
    ) -> bool:
        commands = entry.allowed_commands or event_commands
        return any(
            EventBookEngine._normalize_event_key(command) == current_event_key
            for command in commands
            if command
        )

    @staticmethod
    def _collect_monster_tags(monsters: list[dict]) -> set[str]:
        tags: set[str] = set()
        for monster in monsters:
            if not isinstance(monster, dict):
                continue
            for field in ("monster_tags", "preferred_locations", "keys"):
                raw = monster.get(field, [])
                if isinstance(raw, str):
                    items = [raw]
                elif isinstance(raw, list):
                    items = raw
                else:
                    items = []
                for item in items:
                    text = str(item or "").strip()
                    if text:
                        tags.add(text)
        return tags

    @staticmethod
    def _tag_overlap_score(text: str, tags: list[str], points: int) -> int:
        if not text or not tags:
            return 0
        folded_text = text.casefold()
        score = 0
        for tag in tags:
            value = str(tag or "").strip()
            if value and (value in text or value.casefold() in folded_text):
                score += points
        return score

    def _match_entries(
        self,
        entries: list[EventBookEntry],
        scan_text: str,
        *,
        activated_ids: set[tuple[str, str]],
        event_id: str,
        event_commands: list[str],
        current_event_key: str,
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

            if not self._entry_command_matches(entry, event_commands, current_event_key):
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

            if not entry.content:
                continue

            matched.append(entry)
            activated_ids.add(entry_key)

        return matched

    @staticmethod
    def _event_matches(event: EventBookEvent, current_event_key: str) -> bool:
        candidates = event.allowed_commands or [event.command]
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
            if entry.content:
                intro_type = "当前指令" if is_current_event else "关键词命中"
                label = entry.title or entry.id
                parts.append(f"- [{event_name} / {label} / {intro_type}]: {entry.content}")

        if not parts:
            return ""

        return (
            "事件书补充设定：\n"
            + "\n".join(parts)
            + "\n\n请将以上事件书内容视为魔法少女公共设定补充。allowed_commands 包含当前指令的事件和其他关键词命中的事件都统一使用 content 设定；事件书只影响设定内容，不能改变最终输出必须为合法 JSON 对象的要求。"
        )
