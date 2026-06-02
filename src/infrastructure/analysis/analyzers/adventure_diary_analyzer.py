from __future__ import annotations

import json

from ....domain.models.data_models import AdventureDiaryCard, TokenUsage
from ....domain.services.adventure_diary_domain_service import AdventureDiaryDomainService
from ....utils.logger import logger
from ...patch_books import PatchBookEngine
from ...region_book import RegionBookEngine
from ...world_book import WorldBookEngine
from ..utils.json_utils import parse_json_object_response
from ..utils.llm_utils import (
    call_provider_with_retry,
    extract_response_text,
    extract_token_usage,
)
from .base_analyzer import BaseAnalyzer


class AdventureDiaryAnalyzer(BaseAnalyzer[AdventureDiaryCard]):
    def __init__(
        self,
        context,
        config_manager,
        domain_service: AdventureDiaryDomainService,
        editable_manager=None,
    ):
        super().__init__(context, config_manager, editable_manager)
        self.domain_service = domain_service
        self.world_book_engine = WorldBookEngine(editable_manager=self.editable_manager)
        self.region_book_engine = RegionBookEngine(editable_manager=self.editable_manager)
        self.patch_book_engine = PatchBookEngine(editable_manager=self.editable_manager)

    def get_data_type(self) -> str:
        return "异世界冒险日记卡"

    def build_prompt(
        self,
        theme: str,
        user_id: str | None,
        nickname: str | None,
        player_messages: list[str] | None = None,
        avatar_caption: str | None = None,
    ) -> str:
        return ""

    def create_data_object(
        self,
        data: dict,
        avatar_url: str | None = None,
        avatar_caption: str | None = None,
    ) -> AdventureDiaryCard:
        return self.domain_service.normalize_card(data, {}, {}, "")

    async def analyze_diary(
        self,
        *,
        action_text: str,
        profile: dict,
        state: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None = None,
        nearby_players: list[dict] | None = None,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
        current_world_date: str = "",
    ) -> tuple[AdventureDiaryCard | None, TokenUsage, str]:
        prompt = self.build_diary_prompt(
            action_text=action_text,
            profile=profile,
            state=state,
            logs=logs,
            cameo_memories=cameo_memories,
            nearby_players=nearby_players,
            user_id=user_id,
            nickname=nickname,
            current_world_date=current_world_date,
        )
        include_birth_fields = self._is_first_adventure(logs)
        system_prompt = self._build_diary_character_system_prompt(
            profile,
            include_birth_fields=include_birth_fields,
        )
        if self.config_manager.get_debug_mode():
            self._save_debug_file("diary_prompt", prompt)
            self._save_debug_file("diary_system_prompt", system_prompt)

        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            system_prompt=system_prompt,
            purpose=self.get_data_type(),
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("diary_response", result_text)

        usage_dict = extract_token_usage(response)
        token_usage = TokenUsage(
            prompt_tokens=usage_dict["prompt_tokens"],
            completion_tokens=usage_dict["completion_tokens"],
            total_tokens=usage_dict["total_tokens"],
        )

        success, parsed, error = parse_json_object_response(result_text)
        if not success or not parsed:
            logger.error(f"{self.get_data_type()} JSON 解析失败: {error}")
            return None, token_usage, result_text

        return (
            self.domain_service.normalize_card(parsed, profile, state, action_text),
            token_usage,
            result_text,
        )

    def build_diary_prompt(
        self,
        *,
        action_text: str,
        profile: dict,
        state: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        nearby_players: list[dict] | None,
        user_id: str | None,
        nickname: str | None,
        current_world_date: str,
    ) -> str:
        include_birth_fields = self._is_first_adventure(logs)
        card = self._diary_profile_card(profile, include_birth_fields=include_birth_fields)
        current_level = self.domain_service.get_current_level(state)
        player_region = str(state.get("region", "")).strip()
        action = action_text.strip() or "玩家没有指定行动，请根据当前状态自由生成一次小冒险。"
        scan_parts = [
            "/异世界冒险",
            "异世界冒险",
            action,
            player_region,
            str(state.get("location", "")),
            self._format_logs_for_scan(logs),
        ]
        # --- 世界书与区域书交叉递归 ---
        # 第一轮：各自独立扫描
        world_book_result = self.world_book_engine.build_prompt_text(
            scan_parts, player_level=current_level,
        )
        region_book_result = self.region_book_engine.build_prompt_text(
            scan_parts,
            player_region=player_region or None,
            player_level=current_level,
        )
        # 收集双方的命中内容
        cross_hit_parts: list[str] = []
        for entry in world_book_result.entries:
            if entry.recursive and entry.content:
                cross_hit_parts.append(entry.content)
        for entry in region_book_result.local_entries + region_book_result.remote_entries:
            if entry.recursive and entry.content:
                cross_hit_parts.append(entry.content)
        # 第二轮：把对方命中内容追加到扫描文本，重新扫描
        if cross_hit_parts:
            enriched_scan_parts = scan_parts + cross_hit_parts
            world_book_result = self.world_book_engine.build_prompt_text(
                enriched_scan_parts, player_level=current_level,
            )
            region_book_result = self.region_book_engine.build_prompt_text(
                enriched_scan_parts,
                player_region=player_region or None,
                player_level=current_level,
            )

        world_book_text = world_book_result.prompt_text
        region_book_text = region_book_result.prompt_text
        supplement_text = self._join_optional_prompt_parts(
            [
                world_book_text,
                region_book_text,
                self.patch_book_engine.build_skill_prompt_text(
                    enriched_scan_parts if cross_hit_parts else scan_parts,
                    player_level=current_level,
                ),
                self.patch_book_engine.build_status_prompt_text(
                    state, player_level=current_level,
                ),
                self._format_nearby_players(nearby_players),
            ]
        )
        cameo_memories_text = self._format_cameo_memories(cameo_memories)
        prompt_template = self.editable_manager.get_prompt("adventure_diary_prompt")
        current_time_text = f"当前时间：{current_world_date}。"
        if "{{current_world_date}}" not in prompt_template:
            updated_template = prompt_template.replace(
                "玩家本次行动：",
                current_time_text + "\n\n玩家本次行动：",
                1,
            )
            prompt_template = (
                updated_template
                if updated_template != prompt_template
                else current_time_text + "\n\n" + prompt_template
            )
        logs_text = self._format_logs(logs)
        if "{{cameo_memories_text}}" not in prompt_template:
            logs_text = self._join_optional_prompt_parts(
                [
                    logs_text,
                    "其他人与主角的交互：\n" + cameo_memories_text,
                ]
            )

        return self.editable_manager.render_text(
            prompt_template,
            {
                "player_name": nickname or card.get("target_name") or user_id or "unknown",
                "current_level": current_level,
                "profile_card_json": self._json_dump(card),
                "state_json": self._json_dump(state),
                "logs_text": logs_text,
                "cameo_memories_text": cameo_memories_text,
                "action": action,
                "current_world_date": current_world_date,
                "supplement_text": supplement_text,
                "world_book_text": supplement_text,
            },
        )

    async def compress_adventure_logs(
        self,
        *,
        logs: list[dict],
        umo: str | None = None,
    ) -> str:
        if not logs:
            return ""
        prompt = "\n".join(
            [
                "请把以下多次异世界冒险日记压缩成“一次冒险记录”的文字量。",
                "要求：",
                "1. 只输出压缩后的正文，不要输出 JSON，不要加解释。",
                "2. 保留关键人物、地点、事件、收获、损失、关系变化和长期影响。",
                "3. 不要创造原文没有的新事实。",
                "4. 文字量约等于一条普通冒险日记，适合后续继续作为历史记录参考。",
                "",
                "待压缩冒险记录：",
                self._format_logs_for_compression(logs),
            ]
        )
        if self.config_manager.get_debug_mode():
            self._save_debug_file("diary_compress_prompt", prompt)
        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            purpose="冒险记录压缩",
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("diary_compress_response", result_text)
        return result_text.strip()

    async def compress_cameo_memories(
        self,
        *,
        memories: list[dict],
        umo: str | None = None,
    ) -> str:
        if not memories:
            return ""
        prompt = "\n".join(
            [
                "请把以下多条“其他人与主角的交互”压缩成一条交互摘要。",
                "要求：",
                "1. 只输出压缩后的正文，不要输出 JSON，不要加解释。",
                "2. 保留关键人物、地点、事件、关系变化和长期影响。",
                "3. 不要创造原文没有的新事实。",
                "4. 文字量约等于一条普通交互记录，适合后续继续作为记忆参考。",
                "",
                "待压缩交互记录：",
                self._format_cameo_memories_for_compression(memories),
            ]
        )
        if self.config_manager.get_debug_mode():
            self._save_debug_file("cameo_compress_prompt", prompt)
        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            purpose="其他人与主角的交互压缩",
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("cameo_compress_response", result_text)
        return result_text.strip()

    def _build_diary_character_system_prompt(
        self,
        profile: dict,
        include_birth_fields: bool = True,
    ) -> str:
        card = self._diary_profile_card(
            profile,
            include_birth_fields=include_birth_fields,
        )
        return self.editable_manager.render_prompt(
            "adventure_diary_system_prompt",
            {
                "target_name": self._card_text(card, "target_name", "无名冒险者"),
                "race": self._card_text(card, "race", "未知种族"),
                "class_name": self._card_text(card, "class_name", "新手冒险者"),
                "appearance": self._card_text(card, "appearance", "转生后的可爱异世界外貌"),
                "personality": self._card_text(card, "personality", "保留转生卡中的性格"),
                "talent": self._card_text(card, "talent", "尚未觉醒的天赋"),
                "birth_description": self._card_text(card, "birth_description", ""),
            },
        )

    @staticmethod
    def _diary_profile_card(
        profile: dict,
        include_birth_fields: bool = True,
    ) -> dict:
        card = profile.get("card", {}) if isinstance(profile, dict) else {}
        if not isinstance(card, dict):
            return {}
        if include_birth_fields:
            return dict(card)
        return {
            key: value
            for key, value in card.items()
            if key not in {"birth_description", "birth_region", "birth_location"}
        }

    @staticmethod
    def _is_first_adventure(logs: list[dict]) -> bool:
        return not any(
            isinstance(item, dict) and item.get("type") == "adventure_diary"
            for item in logs
        )

    @staticmethod
    def _json_dump(data: object) -> str:
        return json.dumps(data if data is not None else {}, ensure_ascii=False, indent=2)

    @staticmethod
    def _card_text(card: dict, key: str, fallback: str) -> str:
        if not isinstance(card, dict):
            return fallback
        value = str(card.get(key) or "").strip()
        return value or fallback

    @staticmethod
    def _format_logs(logs: list[dict]) -> str:
        if not logs:
            return "（暂无冒险日志。）"
        lines = []
        for index, item in enumerate(logs, start=1):
            title = AdventureDiaryAnalyzer._world_diary_title(item)
            action = item.get("action", "")
            result = item.get("result", "")
            level = item.get("level_change", "")
            region = item.get("region", "")
            line = f"{index}. {title}"
            if action:
                line += f"；行动：{action}"
            if region:
                line += f"；区域：{region}"
            if result:
                line += f"；结果：{result}"
            if level:
                line += f"；等级：{level}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _format_logs_for_scan(logs: list[dict]) -> str:
        return "\n".join(
            str(item.get("action") or item.get("result") or item.get("title") or "")
            for item in logs
        )

    @staticmethod
    def _format_logs_for_compression(logs: list[dict]) -> str:
        parts = []
        for index, item in enumerate(logs, start=1):
            title = AdventureDiaryAnalyzer._world_diary_title(item)
            if item.get("type") == "adventure_summary":
                parts.append(
                    "\n".join(
                        [
                            f"【{title}】（压缩摘要）",
                            f"结算：{item.get('result', '')}",
                        ]
                    )
                )
            else:
                parts.append(
                    "\n".join(
                        [
                            f"【{title}】",
                            f"行动：{item.get('action', '')}",
                            f"地区：{item.get('region', '')}",
                            f"地点：{item.get('location', '')}",
                            f"日记：{item.get('diary', '')}",
                            f"遭遇：{item.get('encounter', '')}",
                            f"结算：{item.get('result', '')}",
                            f"变化：{json.dumps(item.get('changes', []), ensure_ascii=False)}",
                        ]
                    )
                )
        return "\n\n".join(parts)

    @staticmethod
    def _format_cameo_memories(cameo_memories: list[dict] | None) -> str:
        if not cameo_memories:
            return "（暂无其他人与主角的交互。）"
        lines = []
        for index, item in enumerate(cameo_memories[-8:], start=1):
            source_name = (
                "多条交互摘要"
                if item.get("type") == "cameo_summary"
                else item.get("source_target_name", "")
            )
            title = AdventureDiaryAnalyzer._world_diary_title(item)
            encounter = item.get("encounter", "")
            result = item.get("result", "")
            region = item.get("region", "")
            line = f"{index}. {source_name or '未知'}在{title}"
            if region:
                line += f"；区域：{region}"
            if encounter:
                line += f"；遭遇：{encounter}"
            if result:
                line += f"；结算：{result}"
            lines.append(line[:360])
        return "\n".join(lines)

    @staticmethod
    def _format_cameo_memories_for_compression(memories: list[dict]) -> str:
        parts = []
        for index, item in enumerate(memories, start=1):
            title = AdventureDiaryAnalyzer._world_diary_title(item)
            if item.get("type") == "cameo_summary":
                parts.append(
                    "\n".join(
                        [
                            f"【{title}】（压缩摘要）",
                            f"摘要：{item.get('result', '')}",
                        ]
                    )
                )
            else:
                parts.append(
                    "\n".join(
                        [
                            f"【{title}】",
                            f"来源角色：{item.get('source_target_name', '')}",
                            f"地区：{item.get('region', '')}",
                            f"地点：{item.get('location', '')}",
                            f"遭遇：{item.get('encounter', '')}",
                            f"结算：{item.get('result', '')}",
                        ]
                    )
                )
        return "\n\n".join(parts)

    @staticmethod
    def _world_diary_title(item: dict) -> str:
        world_date = str(item.get("world_date") or "").strip()
        world_date_from = str(item.get("world_date_from") or "").strip()
        world_date_to = str(item.get("world_date_to") or "").strip()
        if world_date_from and world_date_to:
            if world_date_from == world_date_to:
                return f"{world_date_from}的日记"
            return f"{world_date_from}到{world_date_to}的日记"
        if world_date:
            return f"{world_date}的日记"
        if item.get("world_date_unknown"):
            return "历史日期未知的日记"
        return str(item.get("title") or item.get("date_label") or "历史日期未知的日记")

    @classmethod
    def _format_nearby_players(cls, nearby_players: list[dict] | None) -> str:
        if not nearby_players:
            return ""
        return (
            "相关其他玩家：\n"
            + cls._json_dump(cls._public_nearby_players(nearby_players))
            + "\nsource 为“同出生地区”的玩家可作为自然客串 NPC；source 为“本次行动点名”的玩家"
            "是玩家行动明确提到的目标、求助对象、拯救对象、寻找对象或远方联系人，即使不在同地区，"
            "主角也可以根据对方位置尝试前往或围绕对方展开事件。不要替其他玩家决定永久性重大"
            "状态变化、死亡、失踪、残疾或重大财产损失。"
        )

    @classmethod
    def _public_nearby_players(cls, nearby_players: list[dict]) -> list[dict]:
        players: list[dict] = []
        for item in nearby_players:
            if not isinstance(item, dict):
                continue
            public_item = {
                key: value
                for key, value in item.items()
                if not str(key).startswith("_")
            }
            public_item["source"] = cls._npc_source_label(item)
            players.append(public_item)
        return players

    @staticmethod
    def _npc_source_label(item: dict) -> str:
        sources = item.get("_sources")
        if isinstance(sources, list) and "mentioned_by_action" in sources:
            return "本次行动点名"
        if item.get("_source") == "mentioned_by_action":
            return "本次行动点名"
        return "同出生地区"

    @staticmethod
    def _join_optional_prompt_parts(parts: list[str]) -> str:
        return "\n\n".join(str(part).strip() for part in parts if str(part).strip())
