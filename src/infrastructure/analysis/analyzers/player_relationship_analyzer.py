from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ....utils.logger import logger
from ..utils.json_utils import parse_json_object_response
from ..utils.llm_utils import (
    call_provider_with_retry,
    extract_response_text,
    mark_latest_llm_error,
)
from .base_analyzer import BaseAnalyzer


class PlayerRelationshipAnalyzer(BaseAnalyzer[dict[str, Any]]):
    def get_data_type(self) -> str:
        return "人物关系总结"

    def build_prompt(
        self,
        theme: str,
        user_id: str | None,
        nickname: str | None,
    ) -> str:
        return theme

    def create_data_object(
        self,
        data: dict,
    ) -> dict[str, Any]:
        return self._normalize_result(data)

    async def analyze_relationships(
        self,
        *,
        card,
        participants_context: dict[str, Any],
        umo: str | None = None,
        world_date: str = "",
    ) -> tuple[dict[str, Any], str]:
        prompt = self.build_relationship_prompt(
            card=card,
            participants_context=participants_context,
            world_date=world_date,
        )
        system_prompt = self.editable_manager.get_prompt("default_system_prompt")
        if self.config_manager.get_debug_mode():
            self._save_debug_file("relationship_prompt", prompt)

        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            system_prompt=system_prompt,
            purpose=self.get_data_type(),
            provider_id_override=self.config_manager.get_subtask_llm_provider_id(),
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("relationship_response", result_text)

        success, parsed, error = parse_json_object_response(result_text)
        if not success or not isinstance(parsed, dict):
            mark_latest_llm_error(f"{self.get_data_type()} JSON parse failed: {error}")
            logger.error(f"{self.get_data_type()} JSON 解析失败: {error}")
            return {"relationships": [], "public_reputations": []}, result_text
        return self._normalize_result(parsed), result_text

    def build_relationship_prompt(
        self,
        *,
        card,
        participants_context: dict[str, Any],
        world_date: str,
    ) -> str:
        event_book_json = self._json_dump(
            self._load_compact_book(
                "event_book/default.json",
                self._compact_event_book,
            )
        )
        monster_book_json = self._json_dump(
            self._load_compact_book(
                "monster_book/default.json",
                self._compact_monster_book,
            )
        )
        template = self.editable_manager.get_prompt("relationship_summary_prompt")
        prompt = self.editable_manager.render_text(
            template,
            {
                "battle_title": str(getattr(card, "title", "") or ""),
                "world_date": str(world_date or getattr(card, "date_label", "") or ""),
                "participants_json": self._json_dump(getattr(card, "participants", [])),
                "diary": str(getattr(card, "diary", "") or ""),
                "encounter": str(getattr(card, "encounter", "") or ""),
                "result": str(getattr(card, "result", "") or ""),
                "update_changes_json": self._json_dump(
                    getattr(card, "update_changes", [])
                ),
                "participants_profile_json": self._json_dump(
                    participants_context.get("participants", [])
                ),
                "existing_relationships_json": self._json_dump(
                    participants_context.get("existing_relationships", {})
                ),
                "city_players_json": self._json_dump(
                    participants_context.get("city_players", [])
                ),
                "event_book_json": event_book_json,
                "monster_book_json": monster_book_json,
            },
        )
        if "{{event_book_json}}" not in template:
            prompt += f"\n\n事件书参考（仅名称、关键词、地点标签、兼容魔物、正文）：\n{event_book_json}"
        if "{{monster_book_json}}" not in template:
            prompt += f"\n\n魔物书参考（仅名称、关键词、正文）：\n{monster_book_json}"
        return prompt

    def _load_compact_book(
        self,
        relative_path: str,
        compact: Callable[[object], list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.editable_manager.read_text(relative_path))
            return compact(raw)
        except Exception as exc:
            logger.warning(f"人物关系总结读取书籍失败，已使用空列表: {relative_path} {exc}")
            return []

    @classmethod
    def _compact_event_book(cls, raw: object) -> list[dict[str, Any]]:
        if not isinstance(raw, dict) or not isinstance(raw.get("categories"), list):
            raise ValueError("事件书 categories 字段不是列表")

        events: list[dict[str, Any]] = []
        for category in raw["categories"]:
            if not isinstance(category, dict):
                continue
            category_events = category.get("events", [])
            if not isinstance(category_events, list):
                continue
            for event in category_events:
                if not isinstance(event, dict):
                    continue
                events.append(
                    cls._select_fields(
                        event,
                        (
                            "name",
                            "keys",
                            "location_tags",
                            "compatible_monsters",
                            "content",
                        ),
                    )
                )
        return events

    @classmethod
    def _compact_monster_book(cls, raw: object) -> list[dict[str, Any]]:
        if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
            raise ValueError("魔物书 entries 字段不是列表")
        return [
            cls._select_fields(entry, ("name", "keys", "content"))
            for entry in raw["entries"]
            if isinstance(entry, dict)
        ]

    @staticmethod
    def _select_fields(
        entry: dict[str, Any],
        fields: tuple[str, ...],
    ) -> dict[str, Any]:
        return {field: entry.get(field) for field in fields}

    @classmethod
    def _normalize_result(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "relationships": cls._normalize_relationships(data),
            "public_reputations": cls._normalize_public_reputations(data),
        }

    @classmethod
    def _normalize_relationships(cls, data: dict[str, Any]) -> list[dict[str, Any]]:
        raw_items = data.get("relationships", [])
        if not isinstance(raw_items, list):
            return []

        relationships: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            source = cls._clean_text(item.get("from"))
            target = cls._clean_text(item.get("to"))
            if not source or not target or source == target:
                continue
            relationships.append(
                {
                    "from": source,
                    "to": target,
                    "relationship": cls._clean_text(
                        item.get("relationship") or item.get("关系")
                    )[:12],
                    "impression": cls._clean_text(item.get("impression"))[:80],
                    "evidence": cls._clean_text(item.get("evidence"))[:240],
                    "summary": cls._clean_text(item.get("summary"))[:360],
                    "tags": cls._clean_tags(item.get("tags")),
                }
            )
        return relationships

    @classmethod
    def _normalize_public_reputations(cls, data: dict[str, Any]) -> list[dict[str, Any]]:
        raw_items = data.get("public_reputations", [])
        if not isinstance(raw_items, list):
            return []

        reputations: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            target = cls._clean_text(item.get("target"))
            summary = cls._clean_text(
                item.get("public_reputation") or item.get("summary") or item.get("城市风评")
            )
            if not target or not summary:
                continue
            reputations.append(
                {
                    "target": target[:40],
                    "public_reputation": summary[:360],
                    "evidence": cls._clean_text(item.get("evidence"))[:240],
                    "tags": cls._clean_tags(item.get("tags")),
                }
            )
        return reputations

    @staticmethod
    def _clean_text(value: object) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _clean_tags(value: object) -> list[str]:
        if isinstance(value, list):
            candidates = value
        else:
            candidates = str(value or "").replace("，", ",").split(",")
        tags: list[str] = []
        for item in candidates:
            tag = PlayerRelationshipAnalyzer._clean_text(item)[:20]
            if tag and tag not in tags:
                tags.append(tag)
            if len(tags) >= 4:
                break
        return tags

    @staticmethod
    def _json_dump(data: object) -> str:
        return json.dumps(data if data is not None else {}, ensure_ascii=False, indent=2)
