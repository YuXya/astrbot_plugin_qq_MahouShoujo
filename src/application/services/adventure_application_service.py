from __future__ import annotations

from typing import Any

from ...domain.models.data_models import AdventureExecutionResult
from ...domain.repositories.analysis_repository import IAdventureAnalysisProvider
from ...domain.repositories.card_repository import ICardGenerator
from ...domain.services.adventure_domain_service import AdventureDomainService
from ...utils.logger import logger


class AdventureApplicationService:
    def __init__(
        self,
        config_manager: Any,
        domain_service: AdventureDomainService,
        llm_analyzer: IAdventureAnalysisProvider,
        card_generator: ICardGenerator,
    ):
        self.config_manager = config_manager
        self.domain_service = domain_service
        self.llm_analyzer = llm_analyzer
        self.card_generator = card_generator

    async def execute_adventure(
        self,
        theme: str,
        html_render_func,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
        player_messages: list[str] | None = None,
        avatar_url: str | None = None,
        avatar_caption: str | None = None,
    ) -> AdventureExecutionResult:
        theme = (theme or "/异世界转生").strip()

        try:
            if self.config_manager.get_use_mock_data():
                card = self.domain_service.build_mock_card(
                    theme,
                    nickname,
                    avatar_url=avatar_url,
                    avatar_caption=avatar_caption,
                )
                raw_response = ""
            else:
                analysis = await self.llm_analyzer.analyze_adventure(
                    theme,
                    user_id=user_id,
                    nickname=nickname,
                    umo=umo,
                    player_messages=player_messages,
                    avatar_url=avatar_url,
                    avatar_caption=avatar_caption,
                )
                card = analysis.card
                raw_response = analysis.raw_response

            image_path, _html = await self.card_generator.generate_image_card(
                card,
                html_render_func,
            )
            if not image_path:
                return AdventureExecutionResult(
                    success=False,
                    card=card,
                    text=card.to_text(),
                    error="图片渲染失败，已回退文本。",
                    raw_response=raw_response,
                )

            return AdventureExecutionResult(
                success=True,
                card=card,
                image_path=image_path,
                text=card.to_text(),
                raw_response=raw_response,
            )
        except Exception as exc:
            logger.error(f"执行异世界转生卡片流程失败: {exc}", exc_info=True)
            return AdventureExecutionResult(
                success=False,
                text=f"异世界转生卡生成失败：{exc}",
                error=str(exc),
            )
