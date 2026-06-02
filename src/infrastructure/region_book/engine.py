from __future__ import annotations

import json
import logging
from pathlib import Path

from ..editable_resources import EditableResourceManager
from .models import RegionBookEntry, RegionBookMatchResult, RegionBookRegion

try:
    from ...utils.logger import logger
except Exception:
    logger = logging.getLogger(__name__)


class RegionBookEngine:
    def __init__(
        self,
        book_path: Path | None = None,
        editable_manager: EditableResourceManager | None = None,
    ):
        self.editable_manager = editable_manager or EditableResourceManager()
        self.book_path = book_path or self.editable_manager.region_book_path

    def build_prompt_text(
        self,
        text_parts: list[str] | None,
        player_level: int = 1,
    ) -> RegionBookMatchResult:
        regions = self._load_regions()
        if not regions:
            return RegionBookMatchResult(
                local_entries=[], remote_entries=[], prompt_text=""
            )

        scan_text = self._join_text(text_parts or [])
        matched: list[tuple[RegionBookEntry, str]] = []
        activated_ids: set[str] = set()

        for region in regions:
            region_name = region.name or region.id

            first_round = self._match_entries(
                region.entries,
                scan_text,
                activated_ids=activated_ids,
                include_always=True,
                player_level=player_level,
            )

            recursion_text = self._join_text(
                entry.content for entry in first_round if entry.recursive
            )
            second_round = self._match_entries(
                region.entries,
                recursion_text,
                activated_ids=activated_ids,
                include_always=False,
                player_level=player_level,
            )

            all_matched = first_round + second_round
            for entry in all_matched:
                matched.append((entry, region_name))

        prompt_text = self._format_prompt_text(matched)
        return RegionBookMatchResult(
            local_entries=[e for e, _ in matched],
            remote_entries=[],
            prompt_text=prompt_text,
        )

    def _load_regions(self) -> list[RegionBookRegion]:
        if not self.book_path.exists():
            logger.warning(f"区域书文件不存在，跳过加载: {self.book_path}")
            return []

        try:
            raw = json.loads(self.book_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"区域书 JSON 读取失败，跳过加载: {exc}")
            return []

        raw_regions = raw.get("regions", []) if isinstance(raw, dict) else []
        if not isinstance(raw_regions, list):
            logger.warning("区域书 regions 字段不是列表，跳过加载")
            return []

        regions: list[RegionBookRegion] = []
        for idx, raw_region in enumerate(raw_regions):
            if not isinstance(raw_region, dict):
                continue
            region = RegionBookRegion.from_dict(raw_region, fallback_id=str(idx))
            if region.id:
                regions.append(region)
        return regions

    def _match_entries(
        self,
        entries: list[RegionBookEntry],
        scan_text: str,
        activated_ids: set[str],
        include_always: bool,
        player_level: int,
    ) -> list[RegionBookEntry]:
        if not scan_text and not include_always:
            return []

        matched: list[RegionBookEntry] = []
        for entry in entries:
            if entry.id in activated_ids or not entry.enabled:
                continue

            if entry.min_level > player_level:
                continue

            if entry.max_level < player_level:
                continue

            if entry.strategy == "always":
                if include_always:
                    matched.append(entry)
                    activated_ids.add(entry.id)
                continue

            if entry.strategy != "keyword":
                logger.debug(f"未知区域书触发策略，已跳过: {entry.id} strategy={entry.strategy}")
                continue

            if self._contains_any_key(scan_text, entry.keys):
                matched.append(entry)
                activated_ids.add(entry.id)

        return matched

    @staticmethod
    def _contains_any_key(text: str, keys: list[str]) -> bool:
        if not text or not keys:
            return False
        folded_text = text.casefold()
        for key in keys:
            if key in text or key.casefold() in folded_text:
                return True
        return False

    @staticmethod
    def _join_text(parts) -> str:
        return "\n".join(str(part).strip() for part in parts if str(part).strip())

    def _format_prompt_text(
        self,
        entries: list[tuple[RegionBookEntry, str]],
    ) -> str:
        parts = []
        for entry, _region_name in entries:
            text = entry.content
            if text:
                label = f"[{entry.title}]: " if entry.title else ""
                parts.append(f"- {label}{text}")

        if not parts:
            return ""

        return "区域书补充设定：\n" + "\n".join(parts) + "\n\n请将以上区域书内容视为魔法少女公共设定补充。区域书暂时仅按关键词匹配并注入详细设定，不使用简略介绍。区域书只影响设定内容，不能改变最终输出必须为合法 JSON 对象的要求。"
