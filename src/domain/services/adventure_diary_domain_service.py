from __future__ import annotations

import re
from typing import Any

from ..models.data_models import AdventureDiaryCard


class AdventureDiaryDomainService:
    DEFAULT_STATS = {"魔力": "B", "力量": "D", "敏捷": "C", "体质": "E"}
    STAT_KEYS = ("魔力", "力量", "敏捷", "体质")

    def normalize_card(
        self,
        raw: dict,
        profile: dict,
        state: dict,
        action_text: str,
    ) -> AdventureDiaryCard:
        card_data = profile.get("card", {}) if isinstance(profile, dict) else {}
        update_patches = self.normalize_update_patches(
            raw.get("update", {}).get("patches")
            if isinstance(raw.get("update"), dict)
            else None
        )
        level_change, level_exp_after = self.calculate_level_progression(
            state,
            update_patches,
        )
        target_name = self._clean_text(
            raw.get("target_name"),
            card_data.get("target_name") or profile.get("nickname") or "神秘冒险者",
        )
        action = self._clean_text(raw.get("action"), action_text or "自由冒险")
        region = self._clean_text(
            raw.get("region"),
            state.get("region") or "未知区域",
        )
        location = self._clean_text(
            raw.get("location"),
            state.get("location") or "未知旅途",
        )

        return AdventureDiaryCard(
            title=self._clean_text(raw.get("title"), "异世界冒险日记")[:32],
            subtitle=self._clean_text(raw.get("subtitle"), "新的旅途被写进日记")[:64],
            target_name=target_name[:32],
            action=action[:120],
            date_label=self._clean_text(raw.get("date_label"), "第 1 次冒险")[:32],
            region=region[:48],
            location=location[:48],
            diary=self._clean_text(raw.get("diary"), "今天的冒险平稳结束，旅途留下了新的脚印。"),
            encounter=self._clean_text(raw.get("encounter"), "遇到了一些值得记录的小事件。")[:220],
            result=self._clean_text(raw.get("result"), "安全归来，并整理了新的见闻。")[:220],
            level_change=level_change,
            level_exp_after=level_exp_after,
            stats=self.normalize_stats(raw.get("stats"), card_data.get("stats")),
            changes=self.normalize_changes(raw.get("changes", raw.get("rewards"))),
            update_patches=update_patches,
            footer=self._clean_text(raw.get("footer"), "冒险记录已写入存档。")[:120],
            avatar_url=str(card_data.get("avatar_url") or "").strip(),
        )

    def calculate_level_progression(
        self,
        state: dict,
        patches: list[dict[str, Any]],
    ) -> tuple[str, int]:
        start_level = self.get_current_level(state)
        level = start_level
        level_exp = self.get_level_exp(state)
        level_exp += sum(
            self.patch_delta_value(patch.get("value"))
            for patch in patches
            if patch.get("op") == "+" and self.is_level_exp_path(patch.get("path"))
        )
        level_exp = max(0, level_exp)
        while level_exp >= 100 and level < 100:
            level += 1
            level_exp -= 100
        if level >= 100:
            level = 100
            level_exp = 0
        return f"Lv.{start_level}->Lv.{level}", level_exp

    def normalize_level_change(self, raw: object, current_level: int) -> str:
        start_level = self.clamp_level(current_level)
        text = str(raw or "").strip()
        match = re.search(r"Lv\.?\s*(\d+)\s*[-=]>\s*Lv\.?\s*(\d+)", text, re.I)
        if match:
            end_level = self.clamp_level(int(match.group(2)))
        else:
            end_level = start_level
        if end_level < start_level:
            end_level = start_level
        return f"Lv.{start_level}->Lv.{end_level}"

    def parse_level_after(self, level_change: str, fallback: int) -> int:
        match = re.search(r"->\s*Lv\.?\s*(\d+)", str(level_change), re.I)
        if not match:
            return self.clamp_level(fallback)
        return self.clamp_level(int(match.group(1)))

    def get_current_level(self, state: dict) -> int:
        try:
            return self.clamp_level(int(state.get("level", 1) or 1))
        except Exception:
            return 1

    @staticmethod
    def get_level_exp(state: dict) -> int:
        try:
            return max(0, min(int(state.get("level_exp", 0) or 0), 99))
        except Exception:
            return 0

    @staticmethod
    def is_level_exp_path(path: object) -> bool:
        return str(path or "").strip() in {
            "/level/经验",
            "/等级/经验",
            "/主角/等级/经验",
        }

    @staticmethod
    def patch_delta_value(value: object) -> int:
        try:
            return max(-100, min(int(value or 0), 100))
        except Exception:
            return 0

    @classmethod
    def clamp_level(cls, value: int) -> int:
        return max(1, min(int(value), 100))

    @classmethod
    def normalize_stats(cls, raw_stats: object, fallback_stats: object = None) -> dict[str, str]:
        source = raw_stats if isinstance(raw_stats, dict) else fallback_stats
        if not isinstance(source, dict):
            source = cls.DEFAULT_STATS
        result: dict[str, str] = {}
        for key in cls.STAT_KEYS:
            value = str(source.get(key, "")).strip()
            result[key] = value[:16] if value else cls.DEFAULT_STATS[key]
        return result

    @staticmethod
    def normalize_changes(raw_changes: object) -> list[str]:
        if not isinstance(raw_changes, list):
            return ["见闻"]
        changes = [str(item).strip()[:32] for item in raw_changes if str(item).strip()]
        return changes[:6] or ["见闻"]

    @staticmethod
    def normalize_update_patches(raw_patches: object) -> list[dict[str, Any]]:
        if not isinstance(raw_patches, list):
            return []
        patches: list[dict[str, Any]] = []
        for item in raw_patches:
            if not isinstance(item, dict):
                continue
            op = str(item.get("op") or "").strip()
            path = str(item.get("path") or "").strip()
            if op not in {"replace", "insert", "+", "-"} or not path.startswith("/"):
                continue
            patch: dict[str, Any] = {"op": op, "path": path}
            if "value" in item:
                patch["value"] = item.get("value")
            patches.append(patch)
        return patches[:20]

    @staticmethod
    def _clean_text(value: object, default: object) -> str:
        text = str(value if value is not None else default).strip()
        return text if text else str(default).strip()
