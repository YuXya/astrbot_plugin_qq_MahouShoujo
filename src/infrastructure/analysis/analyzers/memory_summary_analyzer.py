from __future__ import annotations

import json
from typing import Any

from ....utils.logger import logger
from ..utils.json_utils import parse_json_object_response
from ..utils.llm_utils import (
    call_provider_with_retry,
    extract_response_text,
    mark_latest_llm_error,
)
from .base_analyzer import BaseAnalyzer


class MemorySummaryAnalyzer(BaseAnalyzer[dict[str, Any]]):
    def get_data_type(self) -> str:
        return "故事记忆整理"

    def build_prompt(self, theme: str, user_id: str | None, nickname: str | None) -> str:
        return theme

    def create_data_object(self, data: dict) -> dict[str, Any]:
        return data

    async def summarize_interactions(
        self,
        *,
        action: str,
        story_text: str,
        world_date: str,
        protagonist: dict,
        participants: list[dict],
        umo: str | None = None,
    ) -> list[dict[str, str]]:
        if not participants:
            return []
        prompt = self.editable_manager.render_prompt(
            "interaction_memory_summary_prompt",
            {
                "target_chars": self.config_manager.get_interaction_memory_target_chars(),
                "world_date": world_date,
                "action": action,
                "protagonist_json": self._json_dump(protagonist),
                "participants_json": self._json_dump(participants),
                "story_text": story_text,
            },
        )
        result_text = await self._call(prompt, umo=umo, purpose="其他人与主角的交互记忆")
        success, parsed, error = parse_json_object_response(result_text)
        if not success or not isinstance(parsed, dict):
            mark_latest_llm_error(f"交互记忆 JSON parse failed: {error}")
            raise ValueError(f"交互记忆 JSON 解析失败: {error}")
        allowed = self._participant_names(participants)
        interactions: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in parsed.get("interactions", []):
            if not isinstance(item, dict):
                continue
            target = " ".join(str(item.get("target") or "").split())
            summary = " ".join(str(item.get("summary") or "").split())
            canonical = allowed.get(target)
            if not canonical or not summary or canonical in seen:
                continue
            seen.add(canonical)
            interactions.append({"target": canonical, "summary": summary})
        return interactions

    async def compact_memories(
        self,
        *,
        records: list[dict],
        umo: str | None = None,
    ) -> str:
        if not records:
            return ""
        prompt = self.editable_manager.render_prompt(
            "memory_compaction_prompt",
            {
                "target_chars": self.config_manager.get_memory_compaction_target_chars(),
                "memory_records_json": self._json_dump(records),
            },
        )
        return (await self._call(prompt, umo=umo, purpose="长期故事记忆压缩")).strip()

    async def _call(self, prompt: str, *, umo: str | None, purpose: str) -> str:
        if self.config_manager.get_debug_mode():
            self._save_debug_file(purpose, prompt)
        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            system_prompt=self.editable_manager.get_prompt("default_system_prompt"),
            purpose=purpose,
            provider_id_override=self.config_manager.get_subtask_llm_provider_id(),
        )
        result_text = extract_response_text(response)
        if not result_text.strip():
            logger.warning(f"{purpose}返回空文本")
            raise ValueError(f"{purpose}返回空文本")
        return result_text

    @staticmethod
    def _participant_names(participants: list[dict]) -> dict[str, str]:
        names: dict[str, str] = {}
        for item in participants:
            if not isinstance(item, dict):
                continue
            canonical = str(item.get("target_name") or item.get("姓名") or "").strip()
            if not canonical:
                continue
            for value in (
                canonical,
                item.get("角色名"),
                item.get("姓名"),
                item.get("魔法少女名"),
            ):
                name = str(value or "").strip()
                if name:
                    names[name] = canonical
        return names

    @staticmethod
    def _json_dump(data: object) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2)
