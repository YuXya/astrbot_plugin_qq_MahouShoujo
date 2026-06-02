from __future__ import annotations

import json
import logging
from pathlib import Path

from ..editable_resources import EditableResourceManager
from .models import WorldBookEntry, WorldBookMatchResult

try:
    from ...utils.logger import logger
except Exception:
    logger = logging.getLogger(__name__)


class WorldBookEngine:
    def __init__(
        self,
        book_path: Path | None = None,
        editable_manager: EditableResourceManager | None = None,
    ):
        self.editable_manager = editable_manager or EditableResourceManager()
        self.book_path = book_path or self.editable_manager.world_book_path

    def build_prompt_text(
        self,
        player_messages: list[str] | None,
        player_level: int = 1,
    ) -> WorldBookMatchResult:
        entries = self._load_entries()
        if not entries:
            return WorldBookMatchResult(entries=[], prompt_text="")

        scan_text = self._join_text(player_messages or [])
        activated_ids: set[str] = set()

        first_round = self._match_entries(
            entries,
            scan_text,
            activated_ids=activated_ids,
            include_always=True,
            player_level=player_level,
        )
        recursion_text = self._join_text(
            entry.content for entry in first_round if entry.recursive
        )
        second_round = self._match_entries(
            entries,
            recursion_text,
            activated_ids=activated_ids,
            include_always=False,
            player_level=player_level,
        )

        activated = sorted(
            first_round + second_round,
            key=lambda item: (item.min_level, item.id),
        )
        return WorldBookMatchResult(
            entries=activated,
            prompt_text=self._format_prompt_text(activated),
        )

    def _load_entries(self) -> list[WorldBookEntry]:
        if not self.book_path.exists():
            logger.warning(f"世界书文件不存在，跳过加载: {self.book_path}")
            return []

        try:
            raw = json.loads(self.book_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"世界书 JSON 读取失败，跳过加载: {exc}")
            return []

        raw_entries = raw.get("entries", []) if isinstance(raw, dict) else []
        if isinstance(raw_entries, dict):
            iterable = raw_entries.items()
        elif isinstance(raw_entries, list):
            iterable = enumerate(raw_entries)
        else:
            logger.warning("世界书 entries 字段不是列表或对象，跳过加载")
            return []

        entries: list[WorldBookEntry] = []
        for fallback_id, raw_entry in iterable:
            if not isinstance(raw_entry, dict):
                continue
            entry = WorldBookEntry.from_dict(raw_entry, fallback_id=str(fallback_id))
            if entry.id and entry.content:
                entries.append(entry)
        return entries

    def _match_entries(
        self,
        entries: list[WorldBookEntry],
        scan_text: str,
        activated_ids: set[str],
        include_always: bool,
        player_level: int = 1,
    ) -> list[WorldBookEntry]:
        if not scan_text and not include_always:
            return []

        matched: list[WorldBookEntry] = []
        for entry in entries:
            if entry.id in activated_ids or not entry.enabled:
                continue

            if entry.min_level > player_level:
                continue

            if entry.max_level < player_level:
                continue

            if entry.strategy == "always":
                if include_always:
                    matched.append(entry)
                    activated_ids.add(entry.id)
                continue

            if entry.strategy != "keyword":
                logger.debug(f"未知世界书触发策略，已跳过: {entry.id} strategy={entry.strategy}")
                continue

            if self._contains_any_key(scan_text, entry.keys):
                matched.append(entry)
                activated_ids.add(entry.id)

        return matched

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

    def _format_prompt_text(self, entries: list[WorldBookEntry]) -> str:
        if not entries:
            return self.editable_manager.get_prompt("world_book_empty")

        contents = [
            f"- [{entry.title}]: {entry.content}" if entry.title else f"- {entry.content}"
            for entry in entries if entry.content
        ]
        return self.editable_manager.render_prompt(
            "world_book_wrapper",
            {"entries": "\n".join(contents)},
        )
