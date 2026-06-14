from __future__ import annotations

import json

from ....domain.models.data_models import BattleDiaryCard, TokenUsage
from ....domain.services.battle_outcome_service import resolve_battle_outcome
from ....domain.services.battle_diary_domain_service import BattleDiaryDomainService
from ....utils.logger import logger
from ...change_books import ChangeBookEngine
from ...event_book import EventBookEngine
from ...world_book import WorldBookEngine
from ..utils.json_utils import parse_json_object_response
from ..utils.llm_utils import (
    call_provider_with_retry,
    extract_response_text,
    extract_token_usage,
    mark_latest_llm_error,
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

    async def select_action_context(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        participant_candidates: list[dict],
        magical_girl_candidates: list[dict],
        monster_candidates: list[dict],
        current_event: dict | None = None,
        event_command: str = "/魔法少女行动",
        umo: str | None = None,
    ) -> dict[str, object]:
        scene_event_candidates = self.event_book_engine.build_scene_event_candidates(
            current_event=event_command,
            limit=80,
        )
        prompt = self.build_action_context_prompt(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            participant_candidates=participant_candidates,
            magical_girl_candidates=magical_girl_candidates,
            monster_candidates=monster_candidates,
            scene_event_candidates=scene_event_candidates,
            current_event=current_event,
        )
        system_prompt = self.editable_manager.get_prompt("default_system_prompt")
        if self.config_manager.get_debug_mode():
            self._save_debug_file("action_context_selection_prompt", prompt)

        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            system_prompt=system_prompt,
            purpose="魔法少女行动上下文判断",
            provider_id_override=self.config_manager.get_subtask_llm_provider_id(),
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("action_context_selection_response", result_text)

        success, parsed, error = parse_json_object_response(result_text)
        if not success or not isinstance(parsed, dict):
            mark_latest_llm_error(f"action context selection JSON parse failed: {error}")
            logger.warning(f"魔法少女行动上下文判断 JSON 解析失败，降级为普通日常: {error}")
            parsed = {}

        participants = self._resolve_selected_profiles(
            parsed.get("selected_participants"),
            participant_candidates,
        )
        selected_payload = self._as_list(parsed.get("selected_targets"))
        magical_targets = self._resolve_selected_magical_girls(
            selected_payload,
            magical_girl_candidates,
        )
        monster_targets = self._resolve_selected_targets(
            selected_payload,
            monster_candidates,
        )
        selected_targets = [*magical_targets, *monster_targets]
        scene_event = self._resolve_selected_scene_event(
            parsed.get("scene_event"), scene_event_candidates
        )
        action_target = self._normalize_action_target(parsed.get("action_target"))
        if not action_target:
            action_target = {
                "type": "日常",
                "target": "自由行动",
                "reason": "未识别到需要持续处理的明确目标",
            }
        is_continuous_event = (
            parsed.get("is_continuous_event") is True and scene_event is not None
        )
        context: dict[str, object] = {
            "battle_type": "event" if is_continuous_event else "daily",
            "action_target": action_target,
            "is_continuous_event": is_continuous_event,
            "selected_participants": participants,
            "selected_targets": selected_targets,
            "scene_event": scene_event,
            "ai_win_rate": self._clamp_percent(parsed.get("ai_win_rate")),
            "desire_win_rate": self._clamp_percent(parsed.get("desire_win_rate")),
        }
        if not is_continuous_event:
            context["ai_win_rate"] = 50
            context["desire_win_rate"] = 50
            return context

        context["battle_odds"] = self._build_battle_odds_context(
            player_data=player_data,
            teammate_data=participants,
            enemy_data=selected_targets,
            battle_kind="free_progress_event",
            ai_win_rate=context["ai_win_rate"],
            desire_win_rate=context["desire_win_rate"],
        )
        outcome = str(context["battle_odds"].get("outcome") or "")
        context["event_outcome"] = {
            "result": "success" if outcome == "player_win" else "obstacle",
            "battle_result": outcome,
            "guidance": scene_event.get(
                "success_ending" if outcome == "player_win" else "obstacle_ending",
                "",
            ),
        }
        return context

    def build_action_context_prompt(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        participant_candidates: list[dict],
        magical_girl_candidates: list[dict],
        monster_candidates: list[dict],
        scene_event_candidates: list[dict],
        current_event: dict | None = None,
    ) -> str:
        return self.editable_manager.render_prompt(
            "action_context_selection_prompt",
            {
                "action": action_text.strip() or "自由行动",
                "player_data_update_json": self._json_dump(
                    self._prompt_player_data(player_data)
                ),
                "logs_text": self._format_logs(logs),
                "cameo_memories_text": self._format_cameo_memories(cameo_memories),
                "current_event_json": self._json_dump(current_event),
                "candidates_json": self._json_dump(
                    {
                        "participants": self._prompt_protagonist_profiles(
                            participant_candidates
                        ),
                        "targets": {
                            "magical_girls": self._prompt_protagonist_profiles(
                                magical_girl_candidates
                            ),
                            "monsters": self._prompt_monster_candidates(
                                monster_candidates
                            ),
                        },
                        "scene_events": self._prompt_scene_event_candidates(
                            scene_event_candidates
                        ),
                    }
                ),
            },
        )

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
            mark_latest_llm_error(f"{self.get_data_type()} JSON parse failed: {error}")
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
        action = action_text.strip() or f"玩家没有指定行动，请根据当前状态自由生成一次{default_action}。"
        scan_parts = [
            event_command,
            event_command.lstrip("/"),
            action,
            self._format_logs_for_scan(logs),
        ]
        # --- 世界书与状态书交叉递归 ---
        world_book_result = self.world_book_engine.build_prompt_text(
            scan_parts,
        )
        status_book_result = self.status_book_engine.build_prompt_text(
            scan_parts,
        )
        cross_hit_parts: list[str] = []
        for entry in world_book_result.entries + status_book_result.entries:
            if entry.recursive and entry.content:
                cross_hit_parts.append(entry.content)
        if cross_hit_parts:
            enriched_scan_parts = scan_parts + cross_hit_parts
            world_book_result = self.world_book_engine.build_prompt_text(
                enriched_scan_parts,
            )
            status_book_result = self.status_book_engine.build_prompt_text(
                enriched_scan_parts,
            )

        world_book_text = world_book_result.prompt_text
        status_book_text = status_book_result.prompt_text
        supplement_text = self._join_optional_prompt_parts(
            [
                world_book_text,
                status_book_text,
                self.change_book_engine.build_skill_prompt_text(
                    enriched_scan_parts if cross_hit_parts else scan_parts,
                ),
                self.change_book_engine.build_fetish_prompt_text(
                    protagonist,
                ),
            ]
        )
        cameo_memories_text = self._format_cameo_memories(cameo_memories)
        logs_text = self._format_logs(logs)
        context_participants = (selection_context or {}).get("selected_participants")
        teammate_info = self._format_teammate_info(
            context_participants if isinstance(context_participants, list) else nearby_players
        )
        selected_targets = self._as_list((selection_context or {}).get("selected_targets"))
        selected_player_targets = [
            target for target in selected_targets
            if isinstance(target, dict) and target.get("主角")
        ]
        first_magical = (selection_context or {}).get("target_magical_girl")
        for target in selected_player_targets:
            faction = str(target.get("阵营") or "").strip()
            if faction == "魔法少女" and first_magical is None:
                first_magical = target

        return self.editable_manager.render_prompt(
            prompt_name,
            {
                "player_data_update_json": self._json_dump(
                    self._prompt_player_data(player_data)
                ),
                "player_name": self._get_nested(protagonist, ["个人信息", "姓名"], "") or "主角",
                "logs_text": logs_text,
                "cameo_memories_text": cameo_memories_text,
                "action": action,
                "current_world_date": current_world_date,
                "supplement_text": supplement_text,
                "participant_count": teammate_info["count"],
                "recent_record_count": teammate_info["recent_record_count"],
                "participants_json": teammate_info["json"],
                "selected_targets_json": self._json_dump(selected_targets),
                "scene_event_json": self._json_dump(
                    (selection_context or {}).get("scene_event")
                ),
                "action_target_json": self._json_dump(
                    (selection_context or {}).get("action_target")
                ),
                "battle_target_type": str(
                    (selection_context or {}).get("battle_type") or "monster"
                ),
                "target_magical_girl_json": self._json_dump(
                    self._prompt_protagonist_profile(first_magical)
                ),
                "battle_odds_json": self._json_dump(
                    (selection_context or {}).get("battle_odds")
                ),
            },
        )

    def _build_battle_odds_context(
        self,
        *,
        player_data: dict,
        battle_kind: str,
        opponent_data: dict | None = None,
        ai_win_rate: object = None,
        desire_win_rate: object = None,
        force_lose: bool = False,
        force_reason: str = "",
        teammate_data: list[dict] | None = None,
        enemy_data: list[dict] | None = None,
    ) -> dict[str, object]:
        """Build randomized battle odds used by the diary prompt."""
        odds = resolve_battle_outcome(
            ai_win_rate,
            desire_win_rate,
            force_lose=force_lose,
        )
        final_rate = int(odds["player_win_rate"])

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
            **odds,
            "battle_kind": battle_kind,
            "tempo": tempo,
            "force_reason": force_reason,
        }

    @staticmethod
    def _clamp_percent(value: object, *, default: int = 50) -> int:
        try:
            return max(0, min(100, int(float(value))))
        except Exception:
            return default

    @staticmethod
    def _prompt_scene_event_candidates(candidates: list[dict]) -> list[dict]:
        fields = (
            "id",
            "title",
            "keys",
            "location_tags",
            "compatible_monsters",
            "content",
        )
        return [
            {field: candidate.get(field) for field in fields}
            for candidate in candidates
            if isinstance(candidate, dict)
        ]

    @staticmethod
    def _prompt_monster_candidates(candidates: list[dict]) -> list[dict]:
        fields = ("id", "name", "keys", "content")
        return [
            {field: candidate.get(field) for field in fields}
            for candidate in candidates
            if isinstance(candidate, dict)
        ]

    @staticmethod
    def _normalize_action_target(value: object) -> dict[str, str]:
        if isinstance(value, dict):
            return {
                "type": str(value.get("type") or "").strip(),
                "target": str(value.get("target") or value.get("name") or "").strip(),
                "reason": str(value.get("reason") or "").strip(),
            }
        text = str(value or "").strip()
        return {"type": "", "target": text, "reason": ""} if text else {}

    def _resolve_selected_scene_event(
        self,
        selected: object,
        candidates: list[dict],
    ) -> dict | None:
        if not selected or not isinstance(selected, dict):
            return None
        selected_id = str(selected.get("id") or "").strip()
        selected_title = str(selected.get("title") or "").strip()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("id") or "").strip()
            candidate_title = str(candidate.get("title") or "").strip()
            if (selected_id and selected_id == candidate_id) or (
                selected_title and selected_title == candidate_title
            ):
                resolved = dict(candidate)
                resolved["selection_reason"] = str(selected.get("reason") or "").strip()
                return resolved
        return None

    def _resolve_selected_target(
        self,
        selected: object,
        candidates: list[dict],
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
                return resolved
        return None

    def _resolve_selected_targets(
        self,
        selected: object,
        candidates: list[dict],
    ) -> list[dict]:
        selected_items = self._as_list(selected)
        resolved: list[dict] = []
        seen: set[str] = set()
        for item in selected_items:
            monster = self._resolve_selected_target(
                item,
                candidates,
            )
            if not monster:
                continue
            key = str(monster.get("id") or monster.get("name") or len(resolved))
            if key in seen:
                continue
            seen.add(key)
            resolved.append(monster)
        return resolved[:5]

    @staticmethod
    def _resolve_selected_magical_girl(
        selected: object,
        candidates: list[dict],
    ) -> dict | None:
        if not selected or not isinstance(selected, dict):
            return None
        selected_target = str(selected.get("target_name") or "").strip()
        selected_magical = str(
            selected.get("magical_name")
            or selected.get("magical_girl_name")
            or selected.get("魔法少女名")
            or ""
        ).strip()
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

    def _resolve_selected_magical_girls(
        self,
        selected: object,
        candidates: list[dict],
    ) -> list[dict]:
        return self._resolve_selected_profile_list(
            selected,
            candidates,
            self._resolve_selected_magical_girl,
        )

    def _resolve_selected_profiles(
        self,
        selected: object,
        candidates: list[dict],
    ) -> list[dict]:
        return self._resolve_selected_profile_list(
            selected,
            candidates,
            self._resolve_selected_profile,
        )

    @staticmethod
    def _resolve_selected_profile(selected: object, candidates: list[dict]) -> dict | None:
        if not selected or not isinstance(selected, dict):
            return None
        selected_target = str(selected.get("target_name") or selected.get("name") or "").strip()
        selected_magical = str(selected.get("magical_name") or selected.get("魔法少女名") or "").strip()
        selected_user = str(selected.get("_user_id") or selected.get("user_id") or "").strip()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if selected_user and selected_user == str(candidate.get("_user_id") or "").strip():
                resolved = dict(candidate)
                resolved["selection_reason"] = str(selected.get("reason") or "").strip()
                return resolved
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
    def _resolve_selected_profile_list(selected: object, candidates: list[dict], resolver) -> list[dict]:
        selected_items = BattleDiaryAnalyzer._as_list(selected)
        resolved: list[dict] = []
        seen: set[str] = set()
        for item in selected_items:
            profile = resolver(item, candidates)
            if not profile:
                continue
            key = str(profile.get("_user_id") or profile.get("target_name") or len(resolved))
            if key in seen:
                continue
            seen.add(key)
            resolved.append(profile)
        return resolved[:5]

    @staticmethod
    def _as_list(value: object) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    @staticmethod
    def _normalize_participant_names(data: dict[str, object]) -> list[str]:
        raw_names = data.get("participant_names", [])
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
            return "（暂无行动记忆。）"
        selected = [
            item for item in logs if isinstance(item, dict) and item.get("type") == "memory_summary"
        ]
        latest_action = next(
            (
                item
                for item in reversed(logs)
                if isinstance(item, dict) and item.get("type") == "action_turn"
            ),
            None,
        )
        if latest_action:
            selected.append(latest_action)
        lines = []
        for index, item in enumerate(selected, start=1):
            title = BattleDiaryAnalyzer._world_diary_title(item)
            text = str(item.get("summary") or item.get("story_text") or "").strip()
            line = f"{index}. {title}；{text}"
            lines.append(line)
        return "\n".join(lines) if lines else "（暂无行动记忆。）"

    @staticmethod
    def _format_logs_for_scan(logs: list[dict]) -> str:
        return "\n".join(
            str(
                item.get("story_text")
                or item.get("summary")
                or item.get("action")
                or item.get("title")
                or ""
            )
            for item in logs
        )

    @staticmethod
    def _format_cameo_memories(cameo_memories: list[dict] | None) -> str:
        if not cameo_memories:
            return "（暂无其他人与主角的交互。）"
        lines = []
        for index, item in enumerate(cameo_memories[-8:], start=1):
            source_label = BattleDiaryAnalyzer._cameo_source_label(item)
            title = BattleDiaryAnalyzer._world_diary_title(item)
            memory_text = str(item.get("memory_text") or "").strip()
            lines.append(f"{index}. {source_label or '未知'}在{title}；{memory_text}")
        return "\n".join(lines)

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
            recent_events = item.get("最近事件")
            if isinstance(recent_events, list) and recent_events:
                profile["最近事件"] = recent_events
            personal_info = profile.get("个人信息", {})
            if not isinstance(personal_info, dict):
                personal_info = {}
            name = str(
                personal_info.get("魔法少女名")
                or personal_info.get("魔法少女名")
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
        world_time = str(item.get("world_time") or "").strip()
        if world_time:
            return f"{world_time}的日记"
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

    @staticmethod
    def _prompt_player_data(player_data: object) -> dict[str, object]:
        if not isinstance(player_data, dict):
            return {}
        visible = dict(player_data)
        visible.pop("player_clock", None)
        return visible

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
