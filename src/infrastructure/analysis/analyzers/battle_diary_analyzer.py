from __future__ import annotations

import json
import random

from ....domain.models.data_models import BattleDiaryCard, TokenUsage
from ....domain.services.battle_diary_domain_service import BattleDiaryDomainService
from ....shared.levels import level_label, parse_level_label
from ....utils.logger import logger
from ...change_books import ChangeBookEngine
from ...event_book import EventBookEngine
from ...world_book import WorldBookEngine
from ..utils.json_utils import parse_json_object_response
from ..utils.llm_utils import (
    call_provider_with_retry,
    extract_response_text,
    extract_token_usage,
)
from .base_analyzer import BaseAnalyzer


class BattleDiaryAnalyzer(BaseAnalyzer[BattleDiaryCard]):
    def __init__(
        self,
        context,
        config_manager,
        domain_service: BattleDiaryDomainService,
        editable_manager=None,
    ):
        super().__init__(context, config_manager, editable_manager)
        self.domain_service = domain_service
        self.world_book_engine = WorldBookEngine(editable_manager=self.editable_manager)
        self.status_book_engine = WorldBookEngine(
            book_path=self.editable_manager.status_book_path,
            editable_manager=self.editable_manager,
            display_name="状态书",
        )
        self.event_book_engine = EventBookEngine(editable_manager=self.editable_manager)
        self.change_book_engine = ChangeBookEngine(editable_manager=self.editable_manager)

    def get_data_type(self) -> str:
        return "魔法少女战斗日记卡"

    def build_prompt(
        self,
        theme: str,
        user_id: str | None,
        nickname: str | None,
    ) -> str:
        return ""

    def create_data_object(
        self,
        data: dict,
    ) -> BattleDiaryCard:
        return self.domain_service.normalize_card(data, {}, "")

    async def analyze_diary(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None = None,
        nearby_players: list[dict] | None = None,
        selection_context: dict[str, object] | None = None,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
        current_world_date: str = "",
        event_command: str = "/魔法少女战斗",
        prompt_name: str = "battle_diary_prompt",
        default_action: str = "自由战斗",
    ) -> tuple[BattleDiaryCard | None, TokenUsage, str]:
        prompt = self.build_diary_prompt(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            nearby_players=nearby_players,
            selection_context=selection_context,
            user_id=user_id,
            nickname=nickname,
            current_world_date=current_world_date,
            event_command=event_command,
            prompt_name=prompt_name,
            default_action=default_action,
        )
        system_prompt = self.editable_manager.get_prompt("default_system_prompt")
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
            self.domain_service.normalize_card(parsed, player_data, action_text),
            token_usage,
            result_text,
        )

    def build_diary_prompt(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        nearby_players: list[dict] | None,
        user_id: str | None,
        nickname: str | None,
        current_world_date: str,
        selection_context: dict[str, object] | None = None,
        event_command: str = "/魔法少女战斗",
        prompt_name: str = "battle_diary_prompt",
        default_action: str = "自由战斗",
    ) -> str:
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        current_level = self.domain_service.get_current_level(protagonist)
        action = action_text.strip() or f"玩家没有指定行动，请根据当前状态自由生成一次{default_action}。"
        scan_parts = [
            event_command,
            event_command.lstrip("/"),
            action,
            self._format_logs_for_scan(logs),
        ]
        # --- 世界书、状态书与事件书交叉递归 ---
        world_book_result = self.world_book_engine.build_prompt_text(
            scan_parts, player_level=current_level,
        )
        status_book_result = self.status_book_engine.build_prompt_text(
            scan_parts, player_level=current_level,
        )
        event_book_result = self.event_book_engine.build_prompt_text(
            scan_parts,
            current_event=event_command,
            player_level=current_level,
        )
        cross_hit_parts: list[str] = []
        for entry in world_book_result.entries + status_book_result.entries:
            if entry.recursive and entry.content:
                cross_hit_parts.append(entry.content)
        for entry in event_book_result.local_entries + event_book_result.remote_entries:
            if entry.recursive and entry.content:
                cross_hit_parts.append(entry.content)
        if cross_hit_parts:
            enriched_scan_parts = scan_parts + cross_hit_parts
            world_book_result = self.world_book_engine.build_prompt_text(
                enriched_scan_parts, player_level=current_level,
            )
            status_book_result = self.status_book_engine.build_prompt_text(
                enriched_scan_parts, player_level=current_level,
            )
            event_book_result = self.event_book_engine.build_prompt_text(
                enriched_scan_parts,
                current_event=event_command,
                player_level=current_level,
            )

        world_book_text = world_book_result.prompt_text
        status_book_text = status_book_result.prompt_text
        event_book_text = event_book_result.prompt_text
        supplement_text = self._join_optional_prompt_parts(
            [
                world_book_text,
                status_book_text,
                event_book_text,
                self.change_book_engine.build_skill_prompt_text(
                    enriched_scan_parts if cross_hit_parts else scan_parts,
                    player_level=current_level,
                ),
                self.change_book_engine.build_fetish_prompt_text(
                    protagonist, player_level=current_level,
                ),
            ]
        )
        cameo_memories_text = self._format_cameo_memories(cameo_memories)
        logs_text = self._format_logs(logs)
        teammate_info = self._format_teammate_info(nearby_players)

        return self.editable_manager.render_prompt(
            prompt_name,
            {
                "player_data_update_json": self._json_dump(player_data),
                "player_name": self._get_nested(protagonist, ["个人信息", "姓名"], "") or "主角",
                "current_level": level_label(current_level),
                "logs_text": logs_text,
                "cameo_memories_text": cameo_memories_text,
                "action": action,
                "current_world_date": current_world_date,
                "supplement_text": supplement_text,
                "teammate_count": teammate_info["count"],
                "recent_record_count": teammate_info["recent_record_count"],
                "teammates_json": teammate_info["json"],
                "sortie_familiar_json": self._json_dump(
                    (selection_context or {}).get("familiar")
                ),
                "battle_target_type": str(
                    (selection_context or {}).get("battle_type") or "monster"
                ),
                "target_villain_witch_json": self._json_dump(
                    self._prompt_protagonist_profile(
                        (selection_context or {}).get("target_villain_witch")
                    )
                ),
                "target_magical_girl_json": self._json_dump(
                    self._prompt_protagonist_profile(
                        (selection_context or {}).get("target_magical_girl")
                    )
                ),
                "battle_odds_json": self._json_dump(
                    (selection_context or {}).get("battle_odds")
                ),
            },
        )

    async def select_magical_battle_context(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        villain_witch_candidates: list[dict],
        umo: str | None = None,
    ) -> dict[str, object]:
        if not villain_witch_candidates:
            return {
                "battle_type": "monster",
                "target_villain_witch": None,
                "battle_odds": self._build_battle_odds_context(
                    player_data=player_data,
                    opponent_data=None,
                    battle_kind="magical_girl_vs_monster",
                ),
            }

        prompt = self.editable_manager.render_prompt(
            "magical_battle_target_selection_prompt",
            {
                "player_data_update_json": self._json_dump(player_data),
                "action": action_text.strip(),
                "logs_text": self._format_logs(logs),
                "cameo_memories_text": self._format_cameo_memories(cameo_memories),
                "candidates_json": self._json_dump(
                    {
                        "villain_witches": self._prompt_protagonist_profiles(
                            villain_witch_candidates
                        ),
                    }
                ),
            },
        )
        system_prompt = self.editable_manager.get_prompt("default_system_prompt")
        if self.config_manager.get_debug_mode():
            self._save_debug_file("magical_battle_target_selection_prompt", prompt)

        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            system_prompt=system_prompt,
            purpose="魔法少女战斗目标判断",
            provider_id_override=self.config_manager.get_subtask_llm_provider_id(),
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("magical_battle_target_selection_response", result_text)

        success, parsed, error = parse_json_object_response(result_text)
        if not success or not isinstance(parsed, dict):
            logger.warning(f"魔法少女战斗目标判断 JSON 解析失败，按魔物战斗处理: {error}")
            return {
                "battle_type": "monster",
                "target_villain_witch": None,
                "battle_odds": self._build_battle_odds_context(
                    player_data=player_data,
                    opponent_data=None,
                    battle_kind="magical_girl_vs_monster",
                ),
            }

        battle_type = str(parsed.get("battle_type") or "").strip()
        if battle_type != "villain_witch":
            return {
                "battle_type": "monster",
                "target_villain_witch": None,
                "battle_odds": self._build_battle_odds_context(
                    player_data=player_data,
                    opponent_data=None,
                    battle_kind="magical_girl_vs_monster",
                    ai_win_rate=parsed.get("ai_win_rate"),
                ),
            }

        target = self._resolve_selected_villain_witch(
            parsed.get("target_villain_witch"),
            villain_witch_candidates,
        )
        if not target:
            return {
                "battle_type": "monster",
                "target_villain_witch": None,
                "battle_odds": self._build_battle_odds_context(
                    player_data=player_data,
                    opponent_data=None,
                    battle_kind="magical_girl_vs_monster",
                    ai_win_rate=parsed.get("ai_win_rate"),
                ),
            }
        return {
            "battle_type": "villain_witch",
            "target_villain_witch": target,
            "battle_odds": self._build_battle_odds_context(
                player_data=player_data,
                opponent_data=target,
                battle_kind="magical_girl_vs_villain_witch",
                ai_win_rate=parsed.get("ai_win_rate"),
            ),
        }

    async def select_villain_battle_context(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        monster_candidates: list[dict],
        magical_girl_candidates: list[dict],
        umo: str | None = None,
    ) -> dict[str, object]:
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        current_level = self.domain_service.get_current_level(protagonist)
        if not magical_girl_candidates:
            familiar = self._resolve_default_monster(
                monster_candidates,
                current_level=current_level,
                action_text="",
            )
            return {
                "familiar": familiar,
                "target_magical_girl": None,
                "battle_odds": self._build_battle_odds_context(
                    player_data=player_data,
                    opponent_data=None,
                    battle_kind="villain_witch_vs_magical_girl",
                    force_lose=bool(familiar and familiar.get("overleveled")),
                    force_reason="随行魔物越级失控" if familiar and familiar.get("overleveled") else "",
                ),
            }

        prompt = self.editable_manager.render_prompt(
            "villain_battle_selection_prompt",
            {
                "current_level": level_label(current_level),
                "player_data_update_json": self._json_dump(player_data),
                "action": action_text.strip(),
                "logs_text": self._format_logs(logs),
                "cameo_memories_text": self._format_cameo_memories(cameo_memories),
                "candidates_json": self._json_dump(
                    {
                        "monsters": monster_candidates,
                        "magical_girls": self._prompt_protagonist_profiles(
                            magical_girl_candidates
                        ),
                    }
                ),
            },
        )
        system_prompt = self.editable_manager.get_prompt("default_system_prompt")
        if self.config_manager.get_debug_mode():
            self._save_debug_file("villain_battle_selection_prompt", prompt)

        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            system_prompt=system_prompt,
            purpose="反派魔女战斗出战选择",
            provider_id_override=self.config_manager.get_subtask_llm_provider_id(),
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("villain_battle_selection_response", result_text)

        success, parsed, error = parse_json_object_response(result_text)
        if not success or not isinstance(parsed, dict):
            logger.warning(f"反派魔女战斗出战选择 JSON 解析失败，使用候选兜底: {error}")
            familiar = self._resolve_default_monster(
                monster_candidates,
                current_level=current_level,
                action_text=action_text,
            )
            target = magical_girl_candidates[0]
            return {
                "familiar": familiar,
                "target_magical_girl": target,
                "battle_odds": self._build_battle_odds_context(
                    player_data=player_data,
                    opponent_data=target,
                    battle_kind="villain_witch_vs_magical_girl",
                    force_lose=bool(familiar and familiar.get("overleveled")),
                    force_reason="随行魔物越级失控" if familiar and familiar.get("overleveled") else "",
                ),
            }

        familiar = self._resolve_selected_monster(
            parsed.get("familiar"),
            monster_candidates,
            current_level=current_level,
            action_text=action_text,
        )
        target = (
            self._resolve_selected_magical_girl(
                parsed.get("target_magical_girl"),
                magical_girl_candidates,
            )
            or magical_girl_candidates[0]
        )
        return {
            "familiar": familiar,
            "target_magical_girl": target,
            "battle_odds": self._build_battle_odds_context(
                player_data=player_data,
                opponent_data=target,
                battle_kind="villain_witch_vs_magical_girl",
                ai_win_rate=parsed.get("ai_win_rate"),
                force_lose=bool(familiar and familiar.get("overleveled")),
                force_reason="随行魔物越级失控" if familiar and familiar.get("overleveled") else "",
            ),
        }

    def _build_battle_odds_context(
        self,
        *,
        player_data: dict,
        opponent_data: dict | None,
        battle_kind: str,
        ai_win_rate: object = None,
        force_lose: bool = False,
        force_reason: str = "",
    ) -> dict[str, object]:
        """Build deterministic battle odds used by the diary prompt."""
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        player_level = self.domain_service.get_current_level(protagonist)
        opponent_level = self._profile_level(opponent_data) if opponent_data else player_level
        d20 = random.randint(1, 20)
        ai_rate = self._clamp_percent(ai_win_rate, default=50)

        if force_lose:
            final_rate = 0
            code_rate = 0
            dice_result = "forced_failure"
            outcome = "player_lose"
        elif d20 == 1:
            final_rate = 0
            code_rate = 0
            dice_result = "critical_failure"
            outcome = "player_lose"
        elif d20 == 20:
            final_rate = 100
            code_rate = 100
            dice_result = "critical_success"
            outcome = "player_win"
        elif battle_kind == "magical_girl_vs_monster":
            # Monster fights use a flatter rule: AI side is neutral 50, code side is 25 + d20.
            ai_rate = 50
            code_rate = max(0, min(100, 25 + d20))
            final_rate = round((code_rate + ai_rate) / 2)
            dice_result = "failure" if d20 < 10 else "success"
            outcome = "player_win" if final_rate >= 50 else "player_lose"
        else:
            power_rate = max(5, min(95, 50 + (player_level - opponent_level) * 12))
            if d20 < 10:
                dice_rate = 10 + (d20 - 2) * 5
                dice_result = "failure"
            else:
                dice_rate = 55 + round((d20 - 10) * (40 / 9))
                dice_result = "success"
            code_rate = round((dice_rate * 0.5 + power_rate * 0.2) / 0.7)
            final_rate = round(dice_rate * 0.5 + power_rate * 0.2 + ai_rate * 0.3)
            outcome = "player_win" if final_rate >= 50 else "player_lose"

        closeness = 50 - abs(final_rate - 50)
        if final_rate in {0, 100}:
            tempo = "一边倒，战斗干脆利落"
        elif closeness >= 35:
            tempo = "难解难分，双方反复交换优势"
        elif closeness >= 20:
            tempo = "有来有回，但胜负逐渐清晰"
        else:
            tempo = "优势明显，收束较快"

        return {
            "player_win_rate": final_rate,
            "outcome": outcome,
            "d20": d20,
            "dice_result": dice_result,
            "code_win_rate": code_rate,
            "ai_win_rate": ai_rate,
            "player_level": level_label(player_level),
            "opponent_level": level_label(opponent_level),
            "battle_kind": battle_kind,
            "tempo": tempo,
            "force_reason": force_reason,
        }

    def _profile_level(self, item: object) -> int:
        profile = self._prompt_protagonist_profile(item)
        return self.domain_service.get_current_level(profile or {})

    @staticmethod
    def _clamp_percent(value: object, *, default: int = 50) -> int:
        try:
            return max(0, min(100, int(float(value))))
        except Exception:
            return default

    async def compress_battle_logs(
        self,
        *,
        logs: list[dict],
        umo: str | None = None,
    ) -> str:
        if not logs:
            return ""
        prompt = "\n".join(
            [
                "请把以下多次魔法少女战斗日记压缩成\u201c一次战斗记录\u201d的文字量。",
                "要求：",
                "1. 只输出压缩后的正文，不要输出 JSON，不要加解释。",
                "2. 保留关键人物、地点、事件、收获、损失、关系变化和长期影响。",
                "3. 不要创造原文没有的新事实。",
                "4. 文字量约等于一条普通战斗日记，适合后续继续作为历史记录参考。",
                "",
                "待压缩战斗记录：",
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
            purpose="战斗记录压缩",
            provider_id_override=self.config_manager.get_subtask_llm_provider_id(),
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
                "请把以下多条\u201c其他人与主角的交互\u201d压缩成一条交互摘要。",
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
            provider_id_override=self.config_manager.get_subtask_llm_provider_id(),
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("cameo_compress_response", result_text)
        return result_text.strip()

    async def infer_teammate_names(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        candidates: list[dict],
        umo: str | None = None,
    ) -> list[str]:
        if not candidates:
            return []
        prompt = self.build_teammate_completion_prompt(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            candidates=candidates,
        )
        system_prompt = self.editable_manager.get_prompt("default_system_prompt")
        if self.config_manager.get_debug_mode():
            self._save_debug_file("teammate_completion_prompt", prompt)

        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            system_prompt=system_prompt,
            purpose="队友语义识别",
            provider_id_override=self.config_manager.get_subtask_llm_provider_id(),
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("teammate_completion_response", result_text)

        success, parsed, error = parse_json_object_response(result_text)
        if not success or not isinstance(parsed, dict):
            logger.warning(f"队友语义识别 JSON 解析失败，已跳过: {error}")
            return []
        return self._normalize_teammate_names(parsed)

    def build_teammate_completion_prompt(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        candidates: list[dict],
    ) -> str:
        return self.editable_manager.render_prompt(
            "teammate_completion_prompt",
            {
                "action": action_text.strip() or "自由战斗",
                "player_data_update_json": self._json_dump(player_data),
                "logs_text": self._format_logs(logs),
                "cameo_memories_text": self._format_cameo_memories(cameo_memories),
                "candidates_json": self._json_dump(
                    self._prompt_protagonist_profiles(candidates)
                ),
            },
        )

    def _resolve_selected_monster(
        self,
        selected: object,
        candidates: list[dict],
        *,
        current_level: int,
        action_text: str,
    ) -> dict | None:
        if not selected or not isinstance(selected, dict):
            return None
        selected_id = str(selected.get("id") or "").strip()
        selected_name = str(selected.get("name") or "").strip()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("id") or "").strip()
            candidate_name = str(candidate.get("name") or "").strip()
            if (selected_id and selected_id == candidate_id) or (
                selected_name and selected_name == candidate_name
            ):
                resolved = dict(candidate)
                resolved["selection_reason"] = str(selected.get("reason") or "").strip()
                selected_level = str(selected.get("level") or "").strip()
                selectable_levels = [
                    str(level or "").strip()
                    for level in candidate.get("monster_levels", [])
                    if str(level or "").strip()
                ]
                default_levels = [
                    str(level or "").strip()
                    for level in candidate.get("default_levels", [])
                    if str(level or "").strip()
                ]
                action = str(action_text or "")
                explicitly_requested = bool(
                    (candidate_name and candidate_name in action)
                    or (candidate_id and candidate_id in action)
                )
                if not selectable_levels:
                    return None
                if selected_level not in selectable_levels:
                    selected_level = ""
                if (
                    selected_level
                    and parse_level_label(selected_level) > current_level
                    and not explicitly_requested
                ):
                    selected_level = ""
                if not selected_level:
                    if not default_levels and not explicitly_requested:
                        return None
                    selected_level = default_levels[-1] if default_levels else selectable_levels[0]
                resolved["selected_level"] = selected_level
                resolved["explicitly_requested"] = explicitly_requested
                resolved["overleveled"] = parse_level_label(selected_level) > current_level
                return resolved
        return None

    def _resolve_default_monster(
        self,
        candidates: list[dict],
        *,
        current_level: int,
        action_text: str,
    ) -> dict | None:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            resolved = self._resolve_selected_monster(
                {"id": candidate.get("id")},
                candidates,
                current_level=current_level,
                action_text=action_text,
            )
            if resolved:
                return resolved
        return None

    @staticmethod
    def _resolve_selected_villain_witch(
        selected: object,
        candidates: list[dict],
    ) -> dict | None:
        if not selected or not isinstance(selected, dict):
            return None
        selected_target = str(selected.get("target_name") or "").strip()
        selected_villain = str(
            selected.get("villain_witch_name")
            or selected.get("villain_name")
            or selected.get("反派魔女名")
            or ""
        ).strip()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if selected_target and selected_target == str(candidate.get("target_name") or "").strip():
                resolved = dict(candidate)
                resolved["selection_reason"] = str(selected.get("reason") or "").strip()
                return resolved
            if selected_villain and selected_villain == str(candidate.get("反派魔女名") or "").strip():
                resolved = dict(candidate)
                resolved["selection_reason"] = str(selected.get("reason") or "").strip()
                return resolved
        return None

    @staticmethod
    def _resolve_selected_magical_girl(
        selected: object,
        candidates: list[dict],
    ) -> dict | None:
        if not selected or not isinstance(selected, dict):
            return None
        selected_target = str(selected.get("target_name") or "").strip()
        selected_magical = str(selected.get("magical_name") or "").strip()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if selected_target and selected_target == str(candidate.get("target_name") or "").strip():
                resolved = dict(candidate)
                resolved["selection_reason"] = str(selected.get("reason") or "").strip()
                return resolved
            if selected_magical and selected_magical == str(candidate.get("魔法少女名") or "").strip():
                resolved = dict(candidate)
                resolved["selection_reason"] = str(selected.get("reason") or "").strip()
                return resolved
        return None

    @staticmethod
    def _normalize_teammate_names(data: dict[str, object]) -> list[str]:
        raw_names = data.get("names", [])
        if not isinstance(raw_names, list):
            raw_names = str(raw_names or "").replace("，", ",").split(",")
        names: list[str] = []
        for raw_name in raw_names:
            name = str(raw_name or "").strip()
            if name and name not in names:
                names.append(name[:40])
        return names[:8]

    @staticmethod
    def _get_nested(data: dict, keys: list[str], default: str = "") -> str:
        current = data
        for key in keys:
            if not isinstance(current, dict):
                return default
            current = current.get(key)
            if current is None:
                return default
        return str(current) if current is not None else default

    @staticmethod
    def _is_first_battle(logs: list[dict]) -> bool:
        return not any(
            isinstance(item, dict) and item.get("type") == "battle_diary"
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
            return "（暂无战斗日志。）"
        lines = []
        for index, item in enumerate(logs, start=1):
            title = BattleDiaryAnalyzer._world_diary_title(item)
            action = item.get("action", "")
            result = item.get("result", "")
            level = item.get("level_change", "")
            line = f"{index}. {title}"
            if action:
                line += f"；行动：{action}"
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
            title = BattleDiaryAnalyzer._world_diary_title(item)
            if item.get("type") == "battle_summary":
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
            source_label = (
                "多条交互摘要"
                if item.get("type") == "cameo_summary"
                else BattleDiaryAnalyzer._cameo_source_label(item)
            )
            title = BattleDiaryAnalyzer._world_diary_title(item)
            encounter = item.get("encounter", "")
            result = item.get("result", "")
            line = f"{index}. {source_label or '未知'}在{title}"
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
            title = BattleDiaryAnalyzer._world_diary_title(item)
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
                            f"来源角色：{BattleDiaryAnalyzer._cameo_source_label(item)}",
                            f"遭遇：{item.get('encounter', '')}",
                            f"结算：{item.get('result', '')}",
                        ]
                    )
                )
        return "\n\n".join(parts)

    def _format_teammate_info(self, nearby_players: list[dict] | None) -> dict[str, object]:
        recent_record_count = self.config_manager.get_teammate_recent_record_count()
        if not nearby_players:
            return {
                "count": 0,
                "recent_record_count": recent_record_count,
                "json": "[]",
            }
        teammates: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in nearby_players:
            if not isinstance(item, dict):
                continue
            profile = self._prompt_protagonist_profile(item)
            if not isinstance(profile, dict):
                continue
            personal_info = profile.get("个人信息", {})
            if not isinstance(personal_info, dict):
                personal_info = {}
            name = str(
                personal_info.get("魔法少女名")
                or personal_info.get("反派魔女名")
                or personal_info.get("姓名")
                or ""
            ).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            teammates.append(profile)
        if not teammates:
            return {
                "count": 0,
                "recent_record_count": recent_record_count,
                "json": "[]",
            }
        return {
            "count": len(teammates),
            "recent_record_count": recent_record_count,
            "json": self._json_dump(teammates),
        }

    @staticmethod
    def _cameo_source_label(item: dict) -> str:
        source_name = str(
            item.get("source_name") or item.get("source_target_name") or ""
        ).strip()
        magical_name = str(item.get("source_magical_name") or "").strip()
        age = str(item.get("source_age") or "").strip()
        identity = str(item.get("source_identity") or "").strip()

        details = [value for value in (magical_name, age, identity) if value]
        if source_name and details:
            return f"{source_name}（{'，'.join(details)}）"
        if source_name:
            return source_name
        if details:
            return "（" + "，".join(details) + "）"
        return ""

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
            + cls._json_dump(cls._prompt_protagonist_profiles(nearby_players))
            + "\n以上每个对象都是其他玩家存档里“主角”下的完整资料。主角可以根据玩家行动、"
            "最近记录和交互记忆尝试前往或围绕对方展开事件。不要替其他玩家决定永久性重大"
            "状态变化、死亡、失踪、残疾或重大物品损失。"
        )

    @staticmethod
    def _prompt_protagonist_profile(item: object) -> dict[str, object] | None:
        if not isinstance(item, dict):
            return None
        protagonist = item.get("主角", item)
        if not isinstance(protagonist, dict):
            return None
        return dict(protagonist)

    @classmethod
    def _prompt_protagonist_profiles(cls, items: list[dict] | None) -> list[dict[str, object]]:
        profiles: list[dict[str, object]] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            profile = cls._prompt_protagonist_profile(item)
            if profile:
                profiles.append(profile)
        return profiles

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
