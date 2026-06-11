from __future__ import annotations

from ...domain.models.data_models import ActionTurnAnalysisResult
from ...domain.services.battle_diary_domain_service import BattleDiaryDomainService
from .analyzers.action_turn_analyzer import ActionTurnAnalyzer


class LLMActionTurnAnalyzer:
    def __init__(
        self,
        context,
        config_manager,
        domain_service: BattleDiaryDomainService,
        editable_manager=None,
    ):
        self.analyzer = ActionTurnAnalyzer(
            context,
            config_manager,
            domain_service,
            editable_manager,
        )

    async def analyze_action_turn(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None = None,
        nearby_players: list[dict] | None = None,
        selection_context: dict[str, object] | None = None,
        umo: str | None = None,
        current_world_date: str = "",
    ) -> ActionTurnAnalysisResult:
        result, usage, raw_response = await self.analyzer.analyze_action_turn(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            nearby_players=nearby_players,
            selection_context=selection_context,
            umo=umo,
            current_world_date=current_world_date,
        )
        if result is None:
            raise ValueError("LLM 响应无法解析为魔法少女行动回合")
        return ActionTurnAnalysisResult(
            result=result,
            token_usage=usage,
            raw_response=raw_response,
        )

    async def select_daily_context(self, **kwargs):
        return await self.analyzer.select_daily_context(**kwargs)

    async def select_magical_battle_context(self, **kwargs):
        return await self.analyzer.select_magical_battle_context(**kwargs)
