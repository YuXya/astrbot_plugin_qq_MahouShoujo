from __future__ import annotations

import asyncio
import base64
import html as html_lib
import json
import os
import re
import time
from dataclasses import replace
from typing import Any

from astrbot.api.star import StarTools

from ...domain.models.data_models import AdventureDiaryCard, ReincarnationCard
from ...domain.repositories.card_repository import ICardGenerator
from ...utils.logger import logger
from ..editable_resources import EditableResourceManager
from ..storage.state_progress import (
    build_progress_sections,
    level_display,
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
        skill_progress_items = self._with_book_effects(
            progress_sections.skill_items,
            "skill_book/default.json",
            prefer_level_effect=False,
        )
        status_progress_items = self._with_book_effects(
            progress_sections.status_items,
            "status_book/default.json",
            prefer_level_effect=True,
        )
        skill_progress_title = self.editable_manager.read_book_display_name(
            "skill_book/default.json",
            "技能&熟练度",
        )
        status_progress_title = self.editable_manager.read_book_display_name(
            "status_book/default.json",
            "特殊状态",
        )
        participants = [str(name).strip() for name in card.participants if str(name).strip()]
        if not participants:
            participants = [card.target_name]
        battle_magical_girl_label = "魔法少女 " + "、".join(participants)
        battle_mode_label = "多人行动" if len(participants) > 1 else "单独行动"
        html_content = self.html_templates.render_template(
            "adventure_diary.html",
            card=card,
            reason=card.reason,
            battle_magical_girl_label=battle_magical_girl_label,
            battle_mode_label=battle_mode_label,
            monster_name=card.monster_name or "未知魔物",
            skill_progress_title=skill_progress_title,
            skill_progress_items=skill_progress_items,
            status_progress_title=status_progress_title,
            status_progress_items=status_progress_items,
            level_label=level_display(card.state_snapshot),
            diary_html=self._highlight_diary_quotes(card.diary),
            change_items=self._display_change_items(card.update_changes),
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
                        prefix="adventure_diary",
                    )
                    if image_path:
                        return image_path, html_content
                except Exception as exc:
                    logger.warning(f"战斗日记 HTML 转图片失败，尝试下一轮策略: {exc}")

        return None, html_content

    @staticmethod
    def _highlight_diary_quotes(text: object) -> str:
        raw_text = str(text or "")
        parts: list[str] = []
        cursor = 0
        for match in re.finditer(r"“[^”]*”", raw_text):
            parts.append(html_lib.escape(raw_text[cursor:match.start()]))
            parts.append(
                '<span class="diary-quote">'
                + html_lib.escape(match.group(0))
                + "</span>"
            )
            cursor = match.end()
        parts.append(html_lib.escape(raw_text[cursor:]))
        return "".join(parts)

    def _with_book_effects(
        self,
        items: list[Any],
        relative_path: str,
        *,
        prefer_level_effect: bool,
    ) -> list[Any]:
        entries = self._book_entries_by_title(relative_path)
        enriched: list[Any] = []
        for item in items:
            entry = entries.get(str(getattr(item, "label", "")).strip())
            effect = ""
            if entry:
                level = max(1, min(int(getattr(item, "level", 1) or 1), 5))
                level_descriptions = entry.get("level_descriptions")
                if prefer_level_effect and isinstance(level_descriptions, dict):
                    effect = str(level_descriptions.get(str(level)) or "").strip()
                if not effect and isinstance(level_descriptions, dict):
                    effect = str(level_descriptions.get(str(level)) or "").strip()
                if not effect:
                    effect = str(entry.get("content") or "").strip()
            enriched.append(replace(item, effect=effect))
        return enriched

    def _book_entries_by_title(self, relative_path: str) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.editable_manager.read_text(relative_path))
        except Exception as exc:
            logger.warning(f"读取效果书失败: {relative_path} {exc}")
            return {}
        raw_entries = data.get("entries", []) if isinstance(data, dict) else []
        if not isinstance(raw_entries, list):
            return {}
        entries: dict[str, dict[str, Any]] = {}
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or raw.get("id") or "").strip()
            if title:
                entries[title] = raw
        return entries

    @staticmethod
    def _display_change_items(changes: list[dict[str, Any]]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        op_labels = {
            "+": "增加",
            "-": "减少",
            "replace": "更新",
            "insert": "新增",
        }
        for change in changes if isinstance(changes, list) else []:
            if not isinstance(change, dict):
                continue
            op = str(change.get("op") or "").strip()
            path = str(change.get("path") or "").strip()
            value = change.get("value")
            label = path.strip("/").replace("/", " / ") or "存档"
            if value is None or value == "":
                value_text = ""
            else:
                value_text = str(value)
                if op in {"+", "-"}:
                    value_text = f"{op}{value_text}"
            items.append(
                {
                    "op": op_labels.get(op, op or "变化"),
                    "label": label,
                    "value": value_text,
                }
            )
        return items[:12]

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

