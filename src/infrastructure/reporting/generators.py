from __future__ import annotations

import asyncio
import base64
import json
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
        avatar_html = self._action_avatar_html(result.avatar_url)
        magical_girl = self._action_turn_profile_value(
            result.state_snapshot,
            ["主角", "个人信息", "魔法少女名"],
        ) or self._action_turn_profile_value(
            result.state_snapshot,
            ["主角", "个人信息", "姓名"],
        ) or "未命名"
        target = str(result.action or "").strip() or "自由行动"
        meta_items = [
            ("魔法少女", magical_girl),
            ("目标", target),
            ("阶段", result.phase or "未知"),
            ("时间", result.date_label or "未知"),
        ]
        meta_html = "".join(
            '<div class="meta-item">'
            f'<span class="meta-label">{html_lib.escape(label)}</span>'
            f'<span class="meta-value">{html_lib.escape(str(value))}</span>'
            "</div>"
            for label, value in meta_items
        )
        options = "".join(
            '<div class="option-card">'
            f'<span class="option-index">{index:02d}</span>'
            f'<span>{html_lib.escape(str(option))}</span>'
            "</div>"
            for index, option in enumerate(result.action_options[:4], start=1)
        )
        patch_items = "".join(
            f'<span class="patch-chip">{self._format_action_patch(item)}</span>'
            for item in result.json_patch[:8]
            if isinstance(item, dict)
        )
        patch_section = (
            f"""
    <section class="section">
      <div class="section-title">变量更新</div>
      <div class="patch-grid">{patch_items}</div>
    </section>"""
            if patch_items
            else ""
        )
        options_section = (
            f"""
    <section class="section">
      <div class="section-title">行动选项</div>
      <div class="options-grid">{options}</div>
    </section>"""
            if options
            else ""
        )
        story_html = self._highlight_action_quotes(result.story_text)
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
* {{
  box-sizing: border-box;
}}
body {{
  margin: 0;
  padding: 34px;
  background:
    linear-gradient(135deg, rgba(255, 246, 250, .96), rgba(236, 251, 255, .95) 48%, rgba(255, 247, 224, .94)),
    repeating-linear-gradient(45deg, rgba(233, 90, 147, .08) 0 2px, transparent 2px 18px);
  color: #302331;
  font-family: "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
}}
.card {{
  position: relative;
  overflow: hidden;
  max-width: 880px;
  margin: 0 auto;
  background: rgba(255, 255, 255, .92);
  border: 1px solid rgba(232, 98, 153, .24);
  border-radius: 8px;
  padding: 30px;
  box-shadow: 0 22px 52px rgba(102, 53, 89, .18);
}}
.card::before {{
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(255, 255, 255, .52) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, .42) 1px, transparent 1px);
  background-size: 32px 32px;
  opacity: .44;
}}
.header {{
  position: relative;
  min-height: 108px;
  padding-right: 132px;
  border-bottom: 1px solid rgba(232, 98, 153, .18);
  margin-bottom: 20px;
}}
.eyebrow {{
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 3px 12px;
  border: 1px solid rgba(38, 168, 175, .28);
  border-radius: 999px;
  color: #157d84;
  background: rgba(229, 252, 254, .72);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0;
}}
h1 {{
  margin: 12px 0 14px;
  color: #49253f;
  font-size: 31px;
  line-height: 1.18;
}}
.avatar {{
  position: absolute;
  right: 0;
  top: 0;
  width: 104px;
  height: 104px;
  border-radius: 8px;
  padding: 5px;
  background: linear-gradient(135deg, #ff8fbd, #ffd76d 52%, #56d6dc);
  box-shadow: 0 12px 26px rgba(142, 70, 112, .22);
}}
.avatar img,
.avatar-fallback {{
  width: 100%;
  height: 100%;
  border-radius: 6px;
  border: 3px solid rgba(255, 255, 255, .9);
  object-fit: cover;
  background: #fff8fb;
}}
.avatar-fallback {{
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c8457d;
  font-size: 30px;
  font-weight: 800;
}}
.meta-row {{
  position: relative;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin: 0 0 22px;
}}
.meta-item {{
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid rgba(38, 168, 175, .18);
  border-radius: 8px;
  background: rgba(246, 253, 255, .82);
}}
.meta-label {{
  display: block;
  color: #8a6076;
  font-size: 12px;
  line-height: 1.2;
}}
.meta-value {{
  display: block;
  overflow-wrap: anywhere;
  margin-top: 4px;
  color: #332333;
  font-size: 15px;
  line-height: 1.35;
  font-weight: 700;
}}
.section {{
  position: relative;
  margin-top: 20px;
}}
.section-title {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  color: #c34d82;
  font-size: 16px;
  font-weight: 800;
}}
.section-title::before {{
  content: "";
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ffd36a;
  box-shadow: 12px 0 0 #69d6dc;
}}
.story {{
  position: relative;
  white-space: pre-wrap;
  line-height: 1.9;
  font-size: 17px;
  padding: 18px 20px;
  border: 1px solid rgba(255, 196, 87, .28);
  border-radius: 8px;
  background: rgba(255, 253, 247, .82);
}}
.quote {{
  color: #e46a24;
  font-weight: 700;
}}
.options-grid {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}}
.option-card {{
  min-height: 74px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px;
  border: 1px solid rgba(232, 98, 153, .22);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 247, 251, .95), rgba(246, 253, 255, .92));
  color: #382533;
  font-size: 15px;
  line-height: 1.5;
  box-shadow: 0 8px 18px rgba(96, 60, 84, .08);
}}
.option-index {{
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #c84d83;
  color: #fff;
  font-size: 12px;
  font-weight: 800;
}}
.patch-grid {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}}
.patch-chip {{
  display: inline-flex;
  max-width: 100%;
  min-height: 34px;
  align-items: center;
  padding: 7px 12px;
  border: 1px solid rgba(38, 168, 175, .22);
  border-radius: 8px;
  background: rgba(232, 251, 252, .86);
  color: #28505a;
  font-size: 14px;
  line-height: 1.35;
  font-weight: 700;
  overflow-wrap: anywhere;
}}
.footer {{
  position: relative;
  margin-top: 18px;
  color: #8a6076;
  font-size: 13px;
  text-align: right;
}}
</style>
</head>
<body>
  <article class="card">
    <header class="header">
      <div class="eyebrow">Mahou Shoujo Turn</div>
      <h1>{html_lib.escape(result.title or "魔法少女行动")}</h1>
      {avatar_html}
    </header>
    <div class="meta-row">{meta_html}</div>
    <section class="story">{story_html}</section>
    {options_section}
    {patch_section}
    <div class="footer">{html_lib.escape(result.footer or "行动记录已写入存档。")}</div>
  </article>
</body>
</html>"""

    @classmethod
    def _highlight_action_quotes(cls, text: object) -> str:
        return cls._highlight_quotes(str(text or ""), "quote")

    @staticmethod
    def _highlight_diary_quotes(text: object) -> str:
        return ReportGenerator._highlight_quotes(str(text or ""), "diary-quote")

    @staticmethod
    def _highlight_quotes(raw_text: str, class_name: str) -> str:
        parts: list[str] = []
        cursor = 0
        for match in re.finditer(
            r"\u201c[^\u201d]*\u201d|\"[^\"\n]*\"|\u300c[^\u300d]*\u300d|\u300e[^\u300f]*\u300f|『[^』]*』|「[^」]*」",
            raw_text,
        ):
            parts.append(html_lib.escape(raw_text[cursor:match.start()]))
            parts.append(
                f'<span class="{html_lib.escape(class_name)}">'
                + html_lib.escape(match.group(0))
                + "</span>"
            )
            cursor = match.end()
        parts.append(html_lib.escape(raw_text[cursor:]))
        return "".join(parts)

    @staticmethod
    def _action_avatar_html(avatar_url: str) -> str:
        avatar_url = str(avatar_url or "").strip()
        if avatar_url:
            escaped_url = html_lib.escape(avatar_url, quote=True)
            return f'<div class="avatar"><img src="{escaped_url}" alt="玩家头像"></div>'
        return '<div class="avatar"><div class="avatar-fallback">魔</div></div>'

    @staticmethod
    def _action_turn_profile_value(state: dict[str, Any], path: list[str]) -> str:
        current: Any = state
        for key in path:
            if not isinstance(current, dict):
                return ""
            current = current.get(key)
        return str(current or "").strip()

    @classmethod
    def _format_action_patch(cls, item: dict[str, Any]) -> str:
        label = cls._json_path_leaf(item.get("path"))
        value = cls._display_patch_value(item)
        return html_lib.escape(f"{label}：{value}")

    @staticmethod
    def _json_path_leaf(path: object) -> str:
        parts = [part for part in str(path or "").split("/") if part]
        return parts[-1] if parts else "变量"

    @staticmethod
    def _display_patch_value(item: dict[str, Any]) -> str:
        if "value" in item:
            value = item.get("value")
        else:
            value = item.get("op", "")
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

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
