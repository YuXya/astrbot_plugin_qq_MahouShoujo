from __future__ import annotations

from ...domain.models.data_models import AdventureDiaryAnalysisResult
from ...domain.services.adventure_diary_domain_service import AdventureDiaryDomainService
from .analyzers.adventure_diary_analyzer import AdventureDiaryAnalyzer


class LLMAdventureDiaryAnalyzer:
    def __init__(
        self,
        context,
        config_manager,
        domain_service: AdventureDiaryDomainService,
        editable_manager=None,
    ):
        self.analyzer = AdventureDiaryAnalyzer(
            context,
            config_manager,
            domain_service,
            editable_manager,
        )

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
    ) -> AdventureDiaryAnalysisResult:
        card, usage, raw_response = await self.analyzer.analyze_diary(
            action_text=action_text,
            profile=profile,
            state=state,
            logs=logs,
            cameo_memories=cameo_memories,
            nearby_players=nearby_players,
            user_id=user_id,
            nickname=nickname,
            umo=umo,
            current_world_date=current_world_date,
        )
        if card is None:
            raise ValueError("LLM 响应无法解析为异世界冒险日记卡 JSON")
        return AdventureDiaryAnalysisResult(
            card=card,
            token_usage=usage,
            raw_response=raw_response,
        )

    async def compress_adventure_logs(
        self,
        *,
        logs: list[dict],
        umo: str | None = None,
    ) -> str:
        return await self.analyzer.compress_adventure_logs(logs=logs, umo=umo)

    async def compress_cameo_memories(
        self,
        *,
        memories: list[dict],
        umo: str | None = None,
    ) -> str:
        return await self.analyzer.compress_cameo_memories(memories=memories, umo=umo)
