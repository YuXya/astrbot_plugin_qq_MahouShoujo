from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from ...domain.services.battle_outcome_service import resolve_battle_outcome
from ...infrastructure.event_book import EventBookEngine

from ...domain.models.data_models import ActionTurnExecutionResult
from ...utils.logger import logger


class ActionTurnApplicationService:
    def __init__(
        self,
        config_manager: Any,
        llm_analyzer: Any,
        save_repository: Any,
        card_generator: Any | None = None,
        memory_summary_analyzer: Any | None = None,
    ):
        self.config_manager = config_manager
        self.llm_analyzer = llm_analyzer
        self.save_repository = save_repository
        self.card_generator = card_generator
        self.memory_summary_analyzer = memory_summary_analyzer
        editable_manager = getattr(getattr(llm_analyzer, "analyzer", None), "editable_manager", None)
        self.event_book_engine = EventBookEngine(editable_manager=editable_manager)

    async def execute_action_turn(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str | None,
        action_text: str,
        umo: str | None,
        html_render_func=None,
        avatar_url: str | None = None,
    ) -> ActionTurnExecutionResult:
        try:
            save_data = self.save_repository.load_player_save(group_id, user_id)
            if not save_data:
                return ActionTurnExecutionResult(
                    success=False,
                    text="还没有你的魔法少女转生存档，请先使用 /魔法少女转生 建档。",
                    error="player_save_not_found",
                )

            world_day_offset = self.save_repository.get_current_world_day_offset(group_id)
            current_world_date = self.save_repository.format_world_date(world_day_offset)
            player_data = save_data.get("player_data", {})
            phase = self._current_phase(player_data)
            current_event = self._current_event(player_data)
            event_started = False
            if current_event:
                if phase != "事件":
                    player_data = deepcopy(player_data)
                    player_data.setdefault("进程", {})["阶段"] = "事件"
                    phase = "事件"
                selection_context = self._active_event_context(current_event)
            else:
                selection_context = await self._select_context(
                    group_id=group_id,
                    user_id=user_id,
                    player_data=player_data,
                    logs=save_data.get("logs", []),
                    cameo_memories=save_data.get("cameo_memories", []),
                    action_text=action_text,
                    phase=phase,
                    umo=umo,
                )
                selection_context = self._ensure_event_context(
                    selection_context,
                    action_text=action_text,
                )
                current_event = self._build_event_runtime(
                    selection_context,
                    current_world_date=current_world_date,
                )
                event_started = current_event is not None
                if current_event:
                    player_data = deepcopy(player_data)
                    player_data.setdefault("进程", {})["阶段"] = "事件"
                    player_data["进程"]["当前事件"] = deepcopy(current_event)
                    phase = "事件"
                    selection_context = self._active_event_context(
                        current_event,
                        battle_odds=selection_context.get("battle_odds"),
                    )
            nearby_players = self._selected_participants(selection_context)
            analysis = await self.llm_analyzer.analyze_action_turn(
                action_text=action_text,
                player_data=player_data,
                logs=save_data.get("logs", []),
                cameo_memories=save_data.get("cameo_memories", []),
                nearby_players=nearby_players,
                selection_context=selection_context,
                umo=umo,
                current_world_date=current_world_date,
            )
            result = analysis.result
            result.phase = phase
            result.action = action_text
            result.selected_targets = self._dict_list(
                selection_context.get("selected_targets")
            )
            result.date_label = current_world_date
            if avatar_url:
                result.avatar_url = avatar_url

            updated_state = self.save_repository.save_action_turn_result(
                group_id=group_id,
                user_id=user_id,
                result=result,
                world_day_offset=world_day_offset,
                event_runtime=current_event if event_started else None,
                battle_odds=selection_context.get("battle_odds"),
            )
            result.state_snapshot = updated_state
            affected_users = await self._append_interaction_memories(
                group_id=group_id,
                user_id=user_id,
                player_data=player_data,
                result=result,
                participants=nearby_players,
                umo=umo,
                world_day_offset=world_day_offset,
                world_date=current_world_date,
            )
            await self._compact_affected_memories(
                group_id=group_id,
                user_ids=[user_id, *affected_users],
                umo=umo,
            )
            image_path = None
            if self.card_generator is not None and html_render_func is not None:
                image_path, _html = await self.card_generator.generate_action_turn_image_card(
                    result,
                    html_render_func,
                )
            return ActionTurnExecutionResult(
                success=True,
                result=result,
                image_path=image_path,
                text=result.to_text(),
                raw_response=analysis.raw_response,
            )
        except Exception as exc:
            logger.error(f"执行魔法少女行动回合失败: {exc}", exc_info=True)
            return ActionTurnExecutionResult(
                success=False,
                text=f"魔法少女行动生成失败：{exc}",
                error=str(exc),
            )

    async def _append_interaction_memories(
        self,
        *,
        group_id: str,
        user_id: str,
        player_data: dict,
        result,
        participants: list[dict],
        umo: str | None,
        world_day_offset: int,
        world_date: str,
    ) -> list[str]:
        memory_analyzer = getattr(self, "memory_summary_analyzer", None)
        if memory_analyzer is None or not participants:
            return []
        try:
            protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
            interactions = await memory_analyzer.summarize_interactions(
                action=result.action,
                story_text=result.story_text,
                world_date=world_date,
                protagonist=protagonist if isinstance(protagonist, dict) else {},
                participants=participants,
                umo=umo,
            )
            by_name: dict[str, dict] = {}
            for item in participants:
                if not isinstance(item, dict):
                    continue
                canonical = str(item.get("target_name") or item.get("姓名") or "").strip()
                if canonical:
                    by_name[canonical] = item

            source_info = protagonist.get("个人信息", {}) if isinstance(protagonist, dict) else {}
            source_info = source_info if isinstance(source_info, dict) else {}
            source_name = str(source_info.get("姓名") or user_id).strip()
            affected: list[str] = []
            for interaction in interactions:
                participant = by_name.get(str(interaction.get("target") or "").strip())
                target_user_id = str((participant or {}).get("_user_id") or "").strip()
                summary = str(interaction.get("summary") or "").strip()
                if not target_user_id or not summary:
                    continue
                self.save_repository.append_interaction_memory(
                    group_id,
                    target_user_id,
                    {
                        "source_group_id": str(group_id),
                        "source_user_id": str(user_id),
                        "source_target_name": source_name,
                        "source_name": source_name,
                        "source_age": source_info.get("年龄", ""),
                        "source_identity": source_info.get("身份&职业", ""),
                        "source_magical_name": source_info.get("魔法少女名", ""),
                        "title": result.title,
                        "summary": summary,
                        "world_day_offset": world_day_offset,
                        "world_date": world_date,
                    },
                )
                affected.append(target_user_id)
            return affected
        except Exception as exc:
            logger.warning(f"生成交互记忆失败，已跳过本轮交互记忆: {exc}")
            return []

    async def _compact_affected_memories(
        self,
        *,
        group_id: str,
        user_ids: list[str],
        umo: str | None,
    ) -> None:
        memory_analyzer = getattr(self, "memory_summary_analyzer", None)
        if memory_analyzer is None:
            return
        threshold = self.config_manager.get_memory_compaction_threshold_chars()
        seen: set[str] = set()
        for user_id in user_ids:
            safe_user_id = str(user_id or "").strip()
            if not safe_user_id or safe_user_id in seen:
                continue
            seen.add(safe_user_id)
            prepared = self.save_repository.prepare_memory_compaction(
                group_id,
                safe_user_id,
                threshold_chars=threshold,
            )
            if not prepared:
                continue
            try:
                summary = await memory_analyzer.compact_memories(
                    records=prepared["records"],
                    umo=umo,
                )
                self.save_repository.apply_memory_compaction(
                    group_id,
                    safe_user_id,
                    prepared=prepared,
                    summary_text=summary,
                )
            except Exception as exc:
                logger.warning(f"长期记忆压缩失败，保留原始记忆: {safe_user_id} {exc}")

    async def _select_context(
        self,
        *,
        group_id: str,
        user_id: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict],
        action_text: str,
        phase: str,
        umo: str | None,
    ) -> dict[str, object]:
        try:
            if phase == "战斗":
                magical_girls = self.save_repository.build_city_magical_girl_candidates(
                    group_id,
                    user_id,
                    recent_record_count=self.config_manager.get_teammate_recent_record_count(),
                )
                monsters = self.save_repository.build_public_monster_candidates()
                return await self.llm_analyzer.select_magical_battle_context(
                    action_text=action_text,
                    player_data=player_data,
                    logs=logs,
                    cameo_memories=cameo_memories,
                    magical_girl_candidates=magical_girls,
                    monster_candidates=monsters,
                    teammate_candidates=magical_girls,
                    umo=umo,
                )
            candidates = self.save_repository.build_city_teammate_candidates(
                group_id,
                user_id,
                recent_record_count=self.config_manager.get_teammate_recent_record_count(),
            )
            monsters = self.save_repository.build_public_monster_candidates()
            return await self.llm_analyzer.select_daily_context(
                action_text=action_text,
                player_data=player_data,
                logs=logs,
                cameo_memories=cameo_memories,
                candidates=candidates,
                monster_candidates=monsters,
                event_command="/魔法少女行动",
                umo=umo,
            )
        except Exception as exc:
            logger.warning(f"魔法少女行动上下文选择失败，降级为空上下文: {exc}")
            return {}

    @staticmethod
    def _ensure_event_context(
        selection_context: dict[str, object] | None,
        *,
        action_text: str,
    ) -> dict[str, object]:
        context = dict(selection_context) if isinstance(selection_context, dict) else {}
        scene = context.get("scene_event")
        event_id = str(scene.get("id") or "").strip() if isinstance(scene, dict) else ""
        if event_id:
            return context

        action = str(action_text or "").strip() or "自由行动"
        fallback_id = f"free_action_{uuid4().hex}"
        context["battle_type"] = str(context.get("battle_type") or "daily")
        context["scene_event"] = {
            "id": fallback_id,
            "title": "自由行动",
            "reason": f"玩家发起行动：{action}",
            "content": "根据玩家行动自然推进本次事件，目标完成后结束事件。",
            "event_gimmick": "事件可以在一轮内完成，也可以根据剧情需要持续多轮。",
            "success_ending": "本次行动的目标得到解决，事件自然收束。",
            "obstacle_ending": "本次行动遇到阻碍，事件继续推进。",
        }
        context.setdefault("selected_participants", [])
        context.setdefault("selected_targets", [])
        return context

    @staticmethod
    def _current_phase(player_data: dict) -> str:
        phase = ""
        if isinstance(player_data, dict) and isinstance(player_data.get("进程"), dict):
            phase = str(player_data["进程"].get("阶段") or "").strip()
        return phase if phase in {"日常", "战斗", "事件"} else "日常"

    @staticmethod
    def _current_event(player_data: dict) -> dict[str, object] | None:
        process = player_data.get("进程", {}) if isinstance(player_data, dict) else {}
        current = process.get("当前事件") if isinstance(process, dict) else None
        scene = current.get("scene_event") if isinstance(current, dict) else None
        return current if isinstance(scene, dict) and str(scene.get("id") or "").strip() else None

    def _active_event_context(
        self,
        current_event: dict[str, object],
        *,
        battle_odds: object = None,
    ) -> dict[str, object]:
        stored_scene = current_event.get("scene_event")
        event_id = str(stored_scene.get("id") or "").strip() if isinstance(stored_scene, dict) else ""
        latest_scene = self.event_book_engine.get_scene_event(event_id)
        scene_event = dict(latest_scene or stored_scene or {})
        if isinstance(stored_scene, dict):
            scene_event["reason"] = str(stored_scene.get("reason") or "").strip()
        previous_odds = battle_odds if isinstance(battle_odds, dict) else {}
        odds = resolve_battle_outcome(
            current_event.get("ai_win_rate"),
            current_event.get("desire_win_rate"),
            dice_roll=previous_odds.get("dice_roll"),
        )
        previous_outcome = str(previous_odds.get("outcome") or "").strip()
        if previous_outcome in {"player_win", "player_lose"}:
            odds["outcome"] = previous_outcome
        outcome = str(odds["outcome"])
        return {
            "battle_type": "event",
            "scene_event": scene_event,
            "selected_participants": self._dict_list(current_event.get("selected_participants")),
            "selected_targets": self._dict_list(current_event.get("selected_targets")),
            "battle_odds": {**odds, "battle_kind": "free_progress_event"},
            "event_runtime": {
                "started_at": str(current_event.get("started_at") or "").strip(),
                "turn_count": max(0, int(current_event.get("turn_count", 0) or 0)),
            },
            "event_outcome": {
                "result": "success" if outcome == "player_win" else "obstacle",
                "battle_result": outcome,
                "guidance": scene_event.get(
                    "success_ending" if outcome == "player_win" else "obstacle_ending",
                    "",
                ),
            },
        }

    def _build_event_runtime(
        self,
        selection_context: dict[str, object],
        *,
        current_world_date: str,
    ) -> dict[str, object] | None:
        scene = selection_context.get("scene_event") if isinstance(selection_context, dict) else None
        event_id = str(scene.get("id") or "").strip() if isinstance(scene, dict) else ""
        if not event_id:
            return None
        odds = selection_context.get("battle_odds")
        odds = odds if isinstance(odds, dict) else {}
        return {
            "scene_event": {
                "id": event_id,
                "title": str(scene.get("title") or event_id).strip(),
                "reason": str(scene.get("reason") or "").strip(),
            },
            "selected_participants": self._dict_list(selection_context.get("selected_participants")),
            "selected_targets": self._dict_list(selection_context.get("selected_targets")),
            "ai_win_rate": self._clamp_percent(odds.get("ai_win_rate")),
            "desire_win_rate": self._clamp_percent(odds.get("desire_win_rate")),
            "started_at": current_world_date,
            "turn_count": 0,
        }

    @staticmethod
    def _dict_list(value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    @staticmethod
    def _clamp_percent(value: object) -> int:
        try:
            return max(0, min(100, int(float(value))))
        except Exception:
            return 50

    @staticmethod
    def _selected_participants(selection_context: dict[str, object]) -> list[dict]:
        value = selection_context.get("selected_participants") if isinstance(selection_context, dict) else []
        return value if isinstance(value, list) else []
