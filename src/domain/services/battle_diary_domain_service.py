from __future__ import annotations

from typing import Any

from ..models.data_models import BattleDiaryCard


class BattleDiaryDomainService:
    def normalize_card(
        self,
        raw: dict,
        player_data: dict,
        action_text: str,
    ) -> BattleDiaryCard:
        """规范化战斗日记卡。

        player_data 是完整的当前玩家数据（来自 player_data_update.json 或 player_data.json，含主角树）。
        """
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        update_changes = self.normalize_update_changes(
            raw.get("update", {}).get("changes")
            if isinstance(raw.get("update"), dict)
            else None
        )
        # 从主角树获取默认 target_name
        default_name = self._get_nested(protagonist, ["个人信息", "姓名"], "神秘战斗者")
        target_name = self._clean_text(
            raw.get("target_name"),
            default_name,
        )
        action = self._clean_text(raw.get("action"), action_text or "自由战斗")
        default_participant = self._get_nested(
            protagonist,
            ["个人信息", "魔法少女名"],
            target_name,
        )
        participants = self.normalize_participants(raw.get("participants"), default_participant)
        return BattleDiaryCard(
            title=self._clean_text(raw.get("title"), "魔法少女战斗日记")[:32],
            subtitle=self._clean_text(raw.get("subtitle"), "新的旅途被写进日记")[:64],
            target_name=target_name[:32],
            action=action[:120],
            date_label=self._clean_text(raw.get("date_label"), "第 1 次战斗")[:32],
            diary=self._clean_text(raw.get("diary"), "今天的战斗平稳结束，旅途留下了新的脚印。"),
            encounter=self._clean_text(raw.get("encounter"), "遇到了一些值得记录的小事件。")[:220],
            result=self._clean_text(raw.get("result"), "安全归来，并整理了新的见闻。")[:220],
            participants=participants,
            monster_name=self._clean_text(raw.get("monster_name"), "未知魔物")[:32],
            reason=self.normalize_reason(
                raw.get("update", {}).get("reason")
                if isinstance(raw.get("update"), dict)
                else None
            ),
            update_changes=update_changes,
            footer=self._clean_text(raw.get("footer"), "战斗记录已写入存档。")[:120],
        )

    @staticmethod
    def change_delta_value(value: object) -> int:
        try:
            return max(-100, min(int(value or 0), 100))
        except Exception:
            return 0

    @staticmethod
    def normalize_reason(raw_reason: object) -> list[str]:
        if not isinstance(raw_reason, list):
            return []
        reason: list[str] = []
        for item in raw_reason:
            text = str(item or "").strip()
            if text:
                reason.append(text[:120])
        return reason[:6]

    @staticmethod
    def normalize_participants(raw_participants: object, target_name: str) -> list[str]:
        names: list[str] = []
        if isinstance(raw_participants, list):
            candidates = raw_participants
        else:
            text = str(raw_participants or "").strip()
            candidates = re.split(r"[、,，/|；;\s]+", text) if text else []

        for candidate in [target_name, *candidates]:
            text = str(candidate or "").strip()
            if text and text not in names:
                names.append(text[:32])
        return names or [target_name[:32]]

    @staticmethod
    def normalize_update_changes(raw_changes: object) -> list[dict[str, Any]]:
        if not isinstance(raw_changes, list):
            return []
        changes: list[dict[str, Any]] = []
        for item in raw_changes:
            if not isinstance(item, dict):
                continue
            op = str(item.get("op") or "").strip()
            path = str(item.get("path") or "").strip()
            if op not in {"replace", "insert", "+", "-"} or not path.startswith("/"):
                continue
            change: dict[str, Any] = {"op": op, "path": path}
            if "value" in item:
                change["value"] = item.get("value")
            changes.append(change)
        return changes[:20]

    @staticmethod
    def _get_nested(data: dict, keys: list[str], default: str = "") -> str:
        current = data
        for key in keys:
            if not isinstance(current, dict):
                return default
            current = current.get(key)
            if current is None:
                return default
        return str(current) if current is not None else default

    @staticmethod
    def _clean_text(value: object, default: object) -> str:
        text = str(value if value is not None else default).strip()
        return text if text else str(default).strip()
