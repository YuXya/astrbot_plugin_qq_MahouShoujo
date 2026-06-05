from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from ...utils.logger import logger

SPEECH_QUOTE_RE = re.compile(r'(“[^”]*”|"[^"]*"|「[^」]*」)', re.S)


class HTMLTemplates:
    def __init__(self):
        self.base_dir = Path(__file__).resolve().parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(self.base_dir / "default")),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.filters["speech_quotes"] = self._highlight_speech_quotes

    def render_template(self, template_name: str, **kwargs) -> str:
        try:
            template = self.env.get_template(template_name)
            return template.render(**kwargs)
        except Exception as exc:
            logger.error(f"渲染模板失败: {template_name}, {exc}", exc_info=True)
            return ""

    @staticmethod
    def _highlight_speech_quotes(value: object) -> Markup:
        text = str(value or "")
        chunks: list[str] = []
        position = 0
        for match in SPEECH_QUOTE_RE.finditer(text):
            chunks.append(str(escape(text[position : match.start()])))
            chunks.append(
                '<span class="speech-quote">'
                f"{escape(match.group(0))}"
                "</span>"
            )
            position = match.end()
        chunks.append(str(escape(text[position:])))
        return Markup("".join(chunks))
