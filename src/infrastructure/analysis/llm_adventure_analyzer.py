from __future__ import annotations

from ...domain.models.data_models import AdventureAnalysisResult
from ...domain.repositories.analysis_repository import IAdventureAnalysisProvider
from ...domain.services.adventure_domain_service import AdventureDomainService
from .analyzers.adventure_analyzer import AdventureAnalyzer


class LLMAdventureAnalyzer(IAdventureAnalysisProvider):
    def __init__(
        self,
        context,
        config_manager,
        domain_service: AdventureDomainService,
        editable_manager=None,
    ):
        self.analyzer = AdventureAnalyzer(
            context,
            config_manager,
            domain_service,
            editable_manager,
        )

    async def analyze_adventure(
        self,
        theme: str,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
        player_messages: list[str] | None = None,
        avatar_url: str | None = None,
        avatar_caption: str | None = None,
    ) -> AdventureAnalysisResult:
        card, usage, raw_response = await self.analyzer.analyze(
            theme,
            user_id=user_id,
            nickname=nickname,
            umo=umo,
            player_messages=player_messages,
            avatar_url=avatar_url,
            avatar_caption=avatar_caption,
        )
        if card is None:
            raise ValueError("LLM 响应无法解析为异世界转生人物卡 JSON")
        return AdventureAnalysisResult(
            card=card,
            token_usage=usage,
            raw_response=raw_response,
        )
