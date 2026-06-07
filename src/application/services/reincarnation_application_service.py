from __future__ import annotations

from typing import Any

from ...domain.models.data_models import ReincarnationExecutionResult
from ...domain.repositories.analysis_repository import IReincarnationAnalysisProvider
from ...domain.repositories.card_repository import ICardGenerator
from ...domain.services.reincarnation_domain_service import ReincarnationDomainService
from ...utils.logger import logger


class ReincarnationApplicationService:
    def __init__(
        self,
        config_manager: Any,
        domain_service: ReincarnationDomainService,
        llm_analyzer: IReincarnationAnalysisProvider,
        card_generator: ICardGenerator,
    ):
        self.config_manager = config_manager
        self.domain_service = domain_service
        self.llm_analyzer = llm_analyzer
        self.card_generator = card_generator

    async def execute_reincarnation(
        self,
        theme: str,
        html_render_func,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
        avatar_url: str | None = None,
        prompt_name: str = "reincarnation_prompt",
        event_command: str = "/魔法少女转生",
    ) -> ReincarnationExecutionResult:
        theme = (theme or event_command or "/魔法少女转生").strip()

        try:
            if self.config_manager.get_use_mock_data():
                card = self.domain_service.build_mock_card(theme, nickname)
                raw_response = ""
            else:
                analysis = await self.llm_analyzer.analyze_reincarnation(
                    theme,
                    user_id=user_id,
                    nickname=nickname,
                    umo=umo,
                    prompt_name=prompt_name,
                    event_command=event_command,
                )
                card = analysis.card
                raw_response = analysis.raw_response

            if avatar_url:
                card.avatar_url = avatar_url

            image_path, _html = await self.card_generator.generate_image_card(
                card,
                html_render_func,
            )
            if not image_path:
                return ReincarnationExecutionResult(
                    success=False,
                    card=card,
                    text=card.to_text(),
                    error="图片渲染失败，已回退文本。",
                    raw_response=raw_response,
                )

            return ReincarnationExecutionResult(
                success=True,
                card=card,
                image_path=image_path,
                text=card.to_text(),
                raw_response=raw_response,
            )
        except Exception as exc:
            logger.error(f"执行{event_command}卡片流程失败: {exc}", exc_info=True)
            return ReincarnationExecutionResult(
                success=False,
                text=f"{event_command}卡生成失败：{exc}",
                error=str(exc),
            )
