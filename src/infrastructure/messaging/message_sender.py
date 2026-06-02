from __future__ import annotations

from astrbot.api.message_components import Image

from ...domain.models.data_models import AdventureDiaryCard, ReincarnationCard
from ...utils.logger import logger


class MessageSender:
    async def send_image_or_text(
        self,
        event,
        image_path: str | None,
        fallback_card: ReincarnationCard | AdventureDiaryCard | None,
        fallback_text: str = "",
    ):
        if image_path:
            try:
                return event.chain_result([Image.fromFileSystem(image_path)])
            except Exception as exc:
                logger.warning(f"构造图片消息链失败，回退文本: {exc}")

        text = fallback_text
        if not text and fallback_card:
            text = fallback_card.to_text()
        if not text:
            text = "异世界卡片生成失败，请稍后再试。"
        return event.plain_result(text)
