from __future__ import annotations

from ...domain.models.data_models import ReincarnationAnalysisResult
from ...domain.repositories.analysis_repository import IReincarnationAnalysisProvider
from ...domain.services.reincarnation_domain_service import ReincarnationDomainService
from .analyzers.reincarnation_analyzer import ReincarnationAnalyzer


class LLMReincarnationAnalyzer(IReincarnationAnalysisProvider):
    def __init__(
        self,
        context,
        config_manager,
        domain_service: ReincarnationDomainService,
        editable_manager=None,
    ):
        self.analyzer = ReincarnationAnalyzer(
            context,
            config_manager,
            domain_service,
            editable_manager,
        )

    async def analyze_reincarnation(
        self,
        theme: str,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
        prompt_name: str = "reincarnation_prompt",
        event_command: str = "/魔法少女转生",
    ) -> ReincarnationAnalysisResult:
        card, usage, raw_response = await self.analyzer.analyze(
            theme,
            user_id=user_id,
            nickname=nickname,
            umo=umo,
            prompt_name=prompt_name,
            event_command=event_command,
        )
        if card is None:
            raise ValueError("LLM 响应无法解析为转生人物卡 JSON")
        return ReincarnationAnalysisResult(
            card=card,
            token_usage=usage,
            raw_response=raw_response,
        )
