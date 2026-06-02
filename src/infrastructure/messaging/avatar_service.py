from __future__ import annotations

from typing import Any

import aiohttp

from ...utils.logger import logger


class QQAvatarService:
    USER_AVATAR_HD_TEMPLATE = (
        "https://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640&img_type=jpg"
    )

    def __init__(self, context: Any, config_manager: Any):
        self.context = context
        self.config_manager = config_manager

    def build_avatar_url(self, user_id: str | None) -> str:
        user_id = str(user_id or "").strip()
        if not user_id or not user_id.isdigit():
            return ""
        return self.USER_AVATAR_HD_TEMPLATE.format(user_id=user_id)

    async def validate_avatar_url(self, avatar_url: str) -> tuple[bool, str]:
        avatar_url = str(avatar_url or "").strip()
        if not avatar_url:
            return False, "头像地址为空"

        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(avatar_url, allow_redirects=True) as response:
                    if response.status < 200 or response.status >= 300:
                        return False, f"HTTP {response.status}"
                    content_type = str(response.headers.get("Content-Type") or "").lower()
                    if not content_type.startswith("image/"):
                        return False, f"返回类型不是图片: {content_type or 'unknown'}"
                    first_chunk = await response.content.read(1)
                    if not first_chunk:
                        return False, "头像图片内容为空"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

        return True, ""

    async def describe_avatar_with_error(self, avatar_url: str) -> tuple[str, str]:
        if not avatar_url or not self.config_manager.get_enable_avatar_caption():
            return "", ""

        provider_id = self.config_manager.get_vision_provider_id()
        if not provider_id:
            return "", ""

        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=self.config_manager.get_avatar_caption_prompt(),
                image_urls=[avatar_url],
            )
            caption = str(getattr(response, "completion_text", "") or "").strip()
            if caption:
                logger.info(f"[Avatar] avatar caption loaded, caption_len={len(caption)}")
                return caption[:240], ""
            return "", "视觉模型没有返回头像描述"
        except Exception as exc:
            logger.warning(f"[Avatar] avatar caption failed: {exc}")
            return "", f"{type(exc).__name__}: {exc}"

    async def describe_avatar(self, avatar_url: str) -> str:
        if not avatar_url or not self.config_manager.get_enable_avatar_caption():
            return ""

        provider_id = self.config_manager.get_vision_provider_id()
        if not provider_id:
            logger.info("[Avatar] 未配置视觉 Provider，跳过头像转述")
            return ""

        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=self.config_manager.get_avatar_caption_prompt(),
                image_urls=[avatar_url],
            )
            caption = str(getattr(response, "completion_text", "") or "").strip()
            if caption:
                logger.info(f"[Avatar] 头像转述成功，caption_len={len(caption)}")
                return caption[:240]
        except Exception as exc:
            logger.warning(f"[Avatar] 头像转述失败，继续生成卡片: {exc}")

        return ""
