from __future__ import annotations

import asyncio
import base64
import os
import time
from typing import Any

from astrbot.api.star import StarTools

from ...domain.models.data_models import AdventureDiaryCard, ReincarnationCard
from ...domain.repositories.card_repository import ICardGenerator
from ...utils.logger import logger
from ..editable_resources import EditableResourceManager
from ..storage.state_progress import (
    build_progress_sections,
    build_state_display_items,
    level_display,
    level_exp_percent,
)
from .templates import HTMLTemplates


class ReportGenerator(ICardGenerator):
    def __init__(self, config_manager, editable_manager: EditableResourceManager | None = None):
        self.config_manager = config_manager
        self.editable_manager = editable_manager or EditableResourceManager()
        self.html_templates = HTMLTemplates()
        self._render_semaphore = asyncio.Semaphore(
            self.config_manager.get_t2i_max_concurrent()
        )

    async def generate_image_card(
        self,
        card: ReincarnationCard,
        html_render_func: Any,
    ) -> tuple[str | None, str | None]:
        html_content = self.html_templates.render_template(
            "card.html",
            card=card,
            likes=card.likes,
        )
        if not html_content:
            return None, None

        async with self._render_semaphore:
            for image_options in self.config_manager.get_t2i_rendering_strategies():
                options = dict(image_options)
                if options.get("type") == "png":
                    options.pop("quality", None)
                try:
                    image_data = await html_render_func(
                        html_content,
                        {},
                        False,
                        options,
                    )
                    image_path = self._persist_image(image_data, options.get("type", "png"))
                    if image_path:
                        return image_path, html_content
                except Exception as exc:
                    logger.warning(f"HTML 转图片失败，尝试下一轮策略: {exc}")

        return None, html_content

    async def generate_diary_image_card(
        self,
        card: AdventureDiaryCard,
        html_render_func: Any,
    ) -> tuple[str | None, str | None]:
        progress_sections = build_progress_sections(
            card.state_snapshot,
            self.editable_manager.read_book_base_path(
                "skill_book/default.json",
                "/主角/技能/",
            ),
            self.editable_manager.read_book_base_path(
                "status_book/default.json",
                "/主角/快感状态/性癖/",
            ),
            limit=8,
        )
        skill_progress_title = self.editable_manager.read_book_display_name(
            "skill_book/default.json",
            "技能&熟练度",
        )
        status_progress_title = self.editable_manager.read_book_display_name(
            "status_book/default.json",
            "特殊状态",
        )
        visible_state = dict(card.state_snapshot)
        for key in ("inventory", "skills", "quests", "物品", "技能", "任务"):
            visible_state.pop(key, None)
        html_content = self.html_templates.render_template(
            "adventure_diary.html",
            card=card,
            changes=card.changes,
            skill_progress_title=skill_progress_title,
            skill_progress_items=progress_sections.skill_items,
            status_progress_title=status_progress_title,
            status_progress_items=progress_sections.status_items,
            state_items=build_state_display_items(visible_state, limit=9),
            level_label=level_display(card.state_snapshot),
            level_exp_percent=level_exp_percent(card.state_snapshot),
        )
        if not html_content:
            return None, None

        async with self._render_semaphore:
            for image_options in self.config_manager.get_t2i_rendering_strategies():
                options = dict(image_options)
                if options.get("type") == "png":
                    options.pop("quality", None)
                try:
                    image_data = await html_render_func(
                        html_content,
                        {},
                        False,
                        options,
                    )
                    image_path = self._persist_image(
                        image_data,
                        options.get("type", "png"),
                        prefix="adventure_diary",
                    )
                    if image_path:
                        return image_path, html_content
                except Exception as exc:
                    logger.warning(f"冒险日记 HTML 转图片失败，尝试下一轮策略: {exc}")

        return None, html_content

    def _persist_image(
        self,
        image_data: object,
        image_type: object,
        prefix: str = "reincarnation",
    ) -> str | None:
        if not image_data:
            return None

        if isinstance(image_data, str) and os.path.isfile(image_data):
            return image_data

        suffix = ".jpg" if str(image_type).lower() in {"jpg", "jpeg"} else ".png"
        output_dir = StarTools.get_data_dir() / "cards"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{prefix}_{int(time.time() * 1000)}{suffix}"

        try:
            if isinstance(image_data, bytes):
                output_path.write_bytes(image_data)
                return str(output_path)

            if isinstance(image_data, str):
                data = image_data
                if data.startswith("base64://"):
                    data = data[len("base64://") :]
                elif data.startswith("data:image/"):
                    data = data.split(",", 1)[1]
                else:
                    logger.warning("html_render 返回了无法识别的字符串图片数据")
                    return None
                output_path.write_bytes(base64.b64decode(data))
                return str(output_path)
        except Exception as exc:
            logger.error(f"保存图片失败: {exc}", exc_info=True)

        return None

