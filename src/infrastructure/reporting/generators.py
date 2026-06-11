from __future__ import annotations

import asyncio
import base64
import html as html_lib
import os
import re
import time
from typing import Any

from astrbot.api.star import StarTools

from ...domain.models.data_models import ActionTurnResult, BattleDiaryCard, ReincarnationCard
from ...domain.repositories.card_repository import ICardGenerator
from ...utils.logger import logger
from ..editable_resources import EditableResourceManager
from ..storage.state_progress import (
    build_progress_sections,
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
            avatar_url=card.avatar_url,
            card_faction="魔法少女",
            card_identity_label="魔法少女",
            card_identity_name=self._reincarnation_card_codename(card),
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
        card: BattleDiaryCard,
        html_render_func: Any,
    ) -> tuple[str | None, str | None]:
        progress_sections = build_progress_sections(
            card.state_snapshot,
            self.editable_manager.read_book_base_path(
                "skill_book/default.json",
                "/主角/技能/",
            ),
            self.editable_manager.read_book_base_path(
                "fetish_book/default.json",
                "/主角/快感状态/性癖/",
            ),
            limit=8,
        )
        skill_progress_items = progress_sections.skill_items
        skill_progress_title = self.editable_manager.read_book_display_name(
            "skill_book/default.json",
            "技能进度",
        )
        status_progress_title = self.editable_manager.read_book_display_name(
            "fetish_book/default.json",
            "特殊状态",
        )
        participants = [str(name).strip() for name in card.participants if str(name).strip()]
        if not participants:
            participants = [card.target_name]
        battle_magical_girl_label = "、".join(participants)
        battle_mode_label = "多人行动" if len(participants) > 1 else "单独行动"
        html_content = self.html_templates.render_template(
            "battle_diary.html",
            card=card,
            reason=card.reason,
            battle_magical_girl_label=battle_magical_girl_label,
            battle_mode_label=battle_mode_label,
            monster_name=card.monster_name or "未知魔物",
            skill_progress_title=skill_progress_title,
            skill_progress_items=skill_progress_items,
            status_progress_title=status_progress_title,
            status_progress_items=progress_sections.status_items,
            diary_html=self._highlight_diary_quotes(card.diary),
            avatar_url=card.avatar_url,
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
                        prefix="battle_diary",
                    )
                    if image_path:
                        return image_path, html_content
                except Exception as exc:
                    logger.warning(f"战斗日记 HTML 转图片失败，尝试下一轮策略: {exc}")

        return None, html_content

    async def generate_action_turn_image_card(
        self,
        result: ActionTurnResult,
        html_render_func: Any,
    ) -> tuple[str | None, str | None]:
        html_content = self._render_action_turn_html(result)
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
                        prefix="action_turn",
                    )
                    if image_path:
                        return image_path, html_content
                except Exception as exc:
                    logger.warning(f"行动回合 HTML 转图片失败，尝试下一轮策略: {exc}")
        return None, html_content

    def _render_action_turn_html(self, result: ActionTurnResult) -> str:
        options = "".join(
            f"<li>{html_lib.escape(str(option))}</li>"
            for option in result.action_options
        )
        patch_items = "".join(
            "<li>"
            + html_lib.escape(
                f"{item.get('op', '')} {item.get('path', '')}"
            )
            + "</li>"
            for item in result.json_patch[:8]
            if isinstance(item, dict)
        )
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
body {{
  margin: 0;
  padding: 32px;
  background: #f6f1ff;
  color: #241827;
  font-family: "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
}}
.card {{
  max-width: 820px;
  margin: 0 auto;
  background: #fff;
  border: 1px solid #eadff4;
  border-radius: 8px;
  padding: 28px;
  box-shadow: 0 18px 40px rgba(36, 24, 39, .12);
}}
h1 {{ margin: 0 0 8px; font-size: 28px; }}
.meta {{ color: #75647d; margin-bottom: 22px; }}
.story {{ white-space: pre-wrap; line-height: 1.85; font-size: 17px; }}
h2 {{ font-size: 18px; margin-top: 24px; }}
li {{ margin: 6px 0; }}
</style>
</head>
<body>
  <article class="card">
    <h1>{html_lib.escape(result.title or "魔法少女行动")}</h1>
    <div class="meta">阶段：{html_lib.escape(result.phase)}　时间：{html_lib.escape(result.date_label)}</div>
    <section class="story">{html_lib.escape(result.story_text)}</section>
    <h2>行动选项</h2>
    <ul>{options}</ul>
    <h2>变量更新</h2>
    <ul>{patch_items}</ul>
  </article>
</body>
</html>"""

    @staticmethod
    def _highlight_diary_quotes(text: object) -> str:
        raw_text = str(text or "")
        parts: list[str] = []
        cursor = 0
        for match in re.finditer(
            r"\u201c[^\u201d]*\u201d|\"[^\"\n]*\"|\u300c[^\u300d]*\u300d",
            raw_text,
        ):
            parts.append(html_lib.escape(raw_text[cursor:match.start()]))
            parts.append(
                '<span class="diary-quote">'
                + html_lib.escape(match.group(0))
                + "</span>"
            )
            cursor = match.end()
        parts.append(html_lib.escape(raw_text[cursor:]))
        return "".join(parts)

    @staticmethod
    def _reincarnation_card_codename(card: ReincarnationCard) -> str:
        for path in ("/主角/个人信息/魔法少女名", "/主角/个人信息/姓名"):
            for item in card.info:
                if item.get("path") == path:
                    name = str(item.get("description") or "").strip()
                    if name:
                        return name
        return card.target_name

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
