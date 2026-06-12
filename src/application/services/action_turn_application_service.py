from __future__ import annotations

from copy import deepcopy
from typing import Any

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
    ):
        self.config_manager = config_manager
        self.llm_analyzer = llm_analyzer
        self.save_repository = save_repository
        self.card_generator = card_generator
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
                    selection_context = self._active_event_context(current_event)
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
            )
            result.state_snapshot = updated_state
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

    def _active_event_context(self, current_event: dict[str, object]) -> dict[str, object]:
        stored_scene = current_event.get("scene_event")
        event_id = str(stored_scene.get("id") or "").strip() if isinstance(stored_scene, dict) else ""
        latest_scene = self.event_book_engine.get_scene_event(event_id)
        scene_event = dict(latest_scene or stored_scene or {})
        if isinstance(stored_scene, dict):
            scene_event["reason"] = str(stored_scene.get("reason") or "").strip()
        ai_rate = self._clamp_percent(current_event.get("ai_win_rate"))
        desire_rate = self._clamp_percent(current_event.get("desire_win_rate"))
        final_rate = round(ai_rate * 0.5 + desire_rate * 0.5)
        outcome = "player_win" if final_rate >= 50 else "player_lose"
        return {
            "battle_type": "event",
            "scene_event": scene_event,
            "selected_participants": self._dict_list(current_event.get("selected_participants")),
            "selected_targets": self._dict_list(current_event.get("selected_targets")),
            "battle_odds": {
                "player_win_rate": final_rate,
                "outcome": outcome,
                "ai_win_rate": ai_rate,
                "desire_win_rate": desire_rate,
                "battle_kind": "free_progress_event",
            },
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
