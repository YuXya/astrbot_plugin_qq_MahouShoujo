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
        player_messages: list[str] | None,
        player_region: str | None = None,
        player_level: int = 1,
    ) -> RegionBookMatchResult:
        regions = self._load_regions()
        if not regions:
            return RegionBookMatchResult(
                local_entries=[], remote_entries=[], prompt_text=""
            )

        scan_text = self._join_text(player_messages or [])
        local_matched: list[tuple[RegionBookEntry, str]] = []  # (entry, region_name)
        remote_matched: list[tuple[RegionBookEntry, str]] = []  # (entry, region_name)
        activated_ids: set[str] = set()

        # When player_region is None (e.g. reincarnation), all matches are local (detailed)
        all_local = player_region is None

        for region in regions:
            is_local = all_local or self._is_local_region(region, player_region)
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
            target = local_matched if is_local else remote_matched
            for entry in all_matched:
                target.append((entry, region_name))

        prompt_text = self._format_prompt_text(local_matched, remote_matched)
        return RegionBookMatchResult(
            local_entries=[e for e, _ in local_matched],
            remote_entries=[e for e, _ in remote_matched],
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
    def _is_local_region(region: RegionBookRegion, player_region: str | None) -> bool:
        if not player_region or not region.name:
            return False
        pr = player_region.strip()
        rn = region.name.strip()
        if not pr or not rn:
            return False
        return rn in pr or pr in rn

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
        local_entries: list[tuple[RegionBookEntry, str]],
        remote_entries: list[tuple[RegionBookEntry, str]],
    ) -> str:
        local_parts = []
        for entry, _region_name in local_entries:
            text = entry.content or entry.brief
            if text:
                label = f"[{entry.title}]: " if entry.title else ""
                local_parts.append(f"- {label}{text}")

        remote_parts = []
        for entry, region_name in remote_entries:
            text = entry.brief
            if text:
                label = f"[{entry.title}]: " if entry.title else ""
                remote_parts.append(f"- [{region_name}] {label}{text}")

        if not local_parts and not remote_parts:
            return self.editable_manager.get_prompt("region_book_empty")

        variables: dict[str, object] = {}
        if local_parts:
            variables["local_entries"] = "\n".join(local_parts)
        else:
            variables["local_entries"] = "（无）"

        if remote_parts:
            variables["remote_entries"] = "\n".join(remote_parts)
        else:
            variables["remote_entries"] = "（无）"

        return self.editable_manager.render_prompt("region_book_wrapper", variables)
