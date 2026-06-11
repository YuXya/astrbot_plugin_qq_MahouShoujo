from __future__ import annotations

from typing import Any

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
            nearby_players = self._selected_teammates(selection_context)
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
            if avatar_url:
                result.avatar_url = avatar_url

            updated_state = self.save_repository.save_action_turn_result(
                group_id=group_id,
                user_id=user_id,
                result=result,
                world_day_offset=world_day_offset,
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
    def _selected_teammates(selection_context: dict[str, object]) -> list[dict]:
        value = selection_context.get("selected_teammates") if isinstance(selection_context, dict) else []
        return value if isinstance(value, list) else []
