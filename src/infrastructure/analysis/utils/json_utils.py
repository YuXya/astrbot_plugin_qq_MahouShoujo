from __future__ import annotations

import json
import re
from typing import Any

from ....utils.logger import logger


def strip_markdown_fence(text: str) -> str:
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()


def extract_json_object(text: str) -> str | None:
    clean = strip_markdown_fence(text)
    if clean.startswith("{") and clean.endswith("}"):
        return clean

    match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
    if match:
        return match.group(0)
    return None


def parse_json_object_response(text: str) -> tuple[bool, dict[str, Any] | None, str | None]:
    json_text = extract_json_object(text)
    if not json_text:
        return False, None, "响应中未找到 JSON 对象"

    try:
        data = json.loads(json_text)
        if not isinstance(data, dict):
            return False, None, "JSON 顶层不是对象"
        return True, data, None
    except json.JSONDecodeError as exc:
        logger.warning(f"JSON 解析失败: {exc}")
        return False, None, str(exc)

