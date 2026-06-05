from __future__ import annotations

from ...domain.models.data_models import BattleDiaryAnalysisResult
from ...domain.services.battle_diary_domain_service import BattleDiaryDomainService
from .analyzers.battle_diary_analyzer import BattleDiaryAnalyzer


class LLMBattleDiaryAnalyzer:
    def __init__(
        self,
        context,
        config_manager,
        domain_service: BattleDiaryDomainService,
        editable_manager=None,
    ):
        self.analyzer = BattleDiaryAnalyzer(
            context,
            config_manager,
            domain_service,
            editable_manager,
        )

    async def analyze_diary(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None = None,
        nearby_players: list[dict] | None = None,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
        current_world_date: str = "",
    ) -> BattleDiaryAnalysisResult:
        card, usage, raw_response = await self.analyzer.analyze_diary(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            nearby_players=nearby_players,
            user_id=user_id,
            nickname=nickname,
            umo=umo,
            current_world_date=current_world_date,
        )
        if card is None:
            raise ValueError("LLM 响应无法解析为魔法少女战斗日记卡 JSON")
        return BattleDiaryAnalysisResult(
            card=card,
            token_usage=usage,
            raw_response=raw_response,
        )

    async def compress_battle_logs(
        self,
        *,
        logs: list[dict],
        umo: str | None = None,
    ) -> str:
        return await self.analyzer.compress_battle_logs(logs=logs, umo=umo)

    async def compress_cameo_memories(
        self,
        *,
        memories: list[dict],
        umo: str | None = None,
    ) -> str:
        return await self.analyzer.compress_cameo_memories(memories=memories, umo=umo)
