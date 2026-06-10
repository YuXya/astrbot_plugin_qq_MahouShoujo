from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..editable_resources import EditableResourceManager
from ..world_book.models import WorldBookEntry

try:
    from ...utils.logger import logger
except Exception:
    logger = logging.getLogger(__name__)


class ChangeBookEngine:
    def __init__(self, editable_manager: EditableResourceManager | None = None):
        self.editable_manager = editable_manager or EditableResourceManager()

    def build_skill_prompt_text(
        self,
        scan_parts: list[str] | None,
    ) -> str:
        book = self._load_book(self.editable_manager.skill_book_path, "技能书")
        entries = self._entries_from_book(book)
        scan_text = self._join_text(scan_parts or [])
        activated_ids: set[str] = set()

        # 第一轮：扫描原始文本
        first_round = self._match_entries(
            entries, scan_text, activated_ids=activated_ids,
            include_always=True,
        )
        # 第二轮：用第一轮命中条目的内容做递归扫描
        recursion_text = self._join_text(
            entry.content for entry in first_round if entry.recursive
        )
        second_round = self._match_entries(
            entries, recursion_text, activated_ids=activated_ids,
            include_always=False,
        ) if recursion_text else []

        matched = sorted(
            first_round + second_round,
            key=lambda item: item.id,
        )
        if not matched:
            return ""

        base_path = self._book_base_path(book, "/主角/技能/")
        entries_text = "\n".join(
            f"- {entry.title or entry.id}：{entry.content}"
            for entry in matched
            if entry.content
        )
        if not entries_text:
            return ""
        return (
            "技能书补充设定：\n"
            f"默认 change 基础路径：{base_path}\n"
            "命中技能说明：\n"
            + entries_text
        )

    def build_fetish_prompt_text(
        self,
        state: dict[str, Any],
    ) -> str:
        book = self._load_book(self.editable_manager.fetish_book_path, "性癖书")
        entries = self._entries_from_book(book)
        enabled_entries = [
            entry
            for entry in entries
            if entry.enabled
        ]
        if not enabled_entries:
            return ""

        owned_names = self._owned_status_names(state, enabled_entries)
        owned_candidates = [
            entry
            for entry in enabled_entries
            if (entry.title or entry.id) in owned_names
        ]
        matched = self._match_entries(
            owned_candidates,
            self._join_text(sorted(owned_names)),
            include_always=True,
        )
        pending_candidates = [
            entry
            for entry in enabled_entries
            if (entry.title or entry.id) and (entry.title or entry.id) not in owned_names
        ]

        base_path = self._book_base_path(book, "/主角/快感状态/性癖/")
        if matched:
            owned_entries = "\n".join(
                self._format_owned_status_entry(
                    entry,
                    self._owned_status_progress(state, entry.title or entry.id),
                )
                for entry in matched
                if entry.content or entry.percentage_descriptions
            )
        else:
            owned_entries = ""

        if pending_candidates:
            pending_entries = "\n".join(
                self._format_pending_status_entry(entry)
                for entry in pending_candidates
            )
        else:
            pending_entries = ""

        if not owned_entries and not pending_entries:
            return ""

        parts = ["性癖书补充设定：", f"默认 change 基础路径：{base_path}"]
        if owned_entries:
            parts.append("已拥有性癖说明：")
            parts.append(owned_entries)
        if pending_entries:
            parts.append("待开发列表：")
            parts.append(pending_entries)
        return "\n".join(parts)

    @staticmethod
    def _format_owned_status_entry(entry: WorldBookEntry, progress: int) -> str:
        title = entry.title or entry.id
        lines = [f"- 性癖：{title}"]
        if entry.content:
            lines.append(f"  简介：{entry.content}")
        lines.append(f"  当前进度：{progress}%")
        effect = entry.percentage_descriptions.get(
            ChangeBookEngine._percentage_range(progress),
            "",
        )
        if effect:
            lines.append(f"  当前进度效果：{effect}")
        return "\n".join(lines)

    @staticmethod
    def _format_pending_status_entry(entry: WorldBookEntry) -> str:
        title = entry.title or entry.id
        if entry.strategy == "always" and entry.content:
            return f"- {title}：{entry.content}"
        return f"- {title}"

    @staticmethod
    def _owned_status_progress(state: dict[str, Any], name: str) -> int:
        def visit(value: object) -> int | None:
            if not isinstance(value, dict):
                return None
            for key, child in value.items():
                if str(key) == name and isinstance(child, dict):
                    try:
                        return max(0, min(int(float(child.get("进度") or 0)), 100))
                    except Exception:
                        return 0
                found = visit(child)
                if found is not None:
                    return found
            return None

        return visit(state if isinstance(state, dict) else {}) or 0

    @staticmethod
    def _percentage_range(progress: int) -> str:
        value = max(0, min(int(progress), 100))
        if value <= 20:
            return "0-20"
        if value <= 40:
            return "21-40"
        if value <= 60:
            return "41-60"
        if value <= 80:
            return "61-80"
        return "81-100"

    def _load_book(self, path: Path, label: str) -> dict[str, Any]:
        if not path.exists():
            logger.warning(f"{label}文件不存在，跳过加载: {path}")
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning(f"{label} JSON 读取失败，跳过加载: {exc}")
            return {}

    @staticmethod
    def _entries_from_book(book: dict[str, Any]) -> list[WorldBookEntry]:
        raw_entries = book.get("entries", []) if isinstance(book, dict) else []
        if isinstance(raw_entries, dict):
            iterable = raw_entries.items()
        elif isinstance(raw_entries, list):
            iterable = enumerate(raw_entries)
        else:
            return []

        entries: list[WorldBookEntry] = []
        for fallback_id, raw_entry in iterable:
            if not isinstance(raw_entry, dict):
                continue
            entry = WorldBookEntry.from_dict(raw_entry, fallback_id=str(fallback_id))
            if entry.id and (entry.title or entry.content):
                entries.append(entry)
        return entries

    def _match_entries(
        self,
        entries: list[WorldBookEntry],
        scan_text: str,
        include_always: bool = True,
        activated_ids: set[str] | None = None,
    ) -> list[WorldBookEntry]:
        matched: list[WorldBookEntry] = []
        if activated_ids is None:
            activated_ids = set()
        if not scan_text and not include_always:
            return []

        for entry in entries:
            if entry.id in activated_ids or not entry.enabled:
                continue
            if entry.strategy == "always":
                if include_always:
                    matched.append(entry)
                    activated_ids.add(entry.id)
                continue
            if entry.strategy != "keyword":
                continue
            if self._contains_any_key(scan_text, entry.keys):
                matched.append(entry)
                activated_ids.add(entry.id)

        return sorted(matched, key=lambda item: item.id)

    @staticmethod
    def _contains_any_key(text: str, keys: list[str]) -> bool:
        if not text or not keys:
            return False
        folded_text = text.casefold()
        return any(key in text or key.casefold() in folded_text for key in keys)

    @staticmethod
    def _owned_status_names(
        state: dict[str, Any],
        entries: list[WorldBookEntry],
    ) -> set[str]:
        names = {entry.title or entry.id for entry in entries if entry.title or entry.id}
        owned: set[str] = set()

        def visit(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key)
                    if key_text in names:
                        owned.add(key_text)
                    visit(child)
            elif isinstance(value, list):
                for item in value:
                    if str(item) in names:
                        owned.add(str(item))
                    visit(item)

        visit(state if isinstance(state, dict) else {})
        return owned

    @staticmethod
    def _book_base_path(book: dict[str, Any], fallback: str) -> str:
        base_path = str(book.get("base_path") or "").strip()
        return base_path or fallback

    @staticmethod
    def _join_text(parts) -> str:
        return "\n".join(str(part).strip() for part in parts if str(part).strip())
