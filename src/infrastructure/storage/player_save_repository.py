from __future__ import annotations

import json
import re
import shutil
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from astrbot.api.star import StarTools

from ...domain.models.data_models import BattleDiaryCard, ReincarnationCard
from ...shared.levels import level_label, normalize_visible_levels
from ...utils.logger import logger
from .state_progress import PROGRESS_KEYS

# ── 时间格式工具 ──────────────────────────────────

def _format_date(value: object) -> str:
    """将毫秒时间戳或字符串转为 '2020年4月1日' 格式；已是该格式则原样返回。"""
    if not value:
        return ""
    text = str(value).strip()
    if "年" in text:
        return text
    try:
        ts = float(text)
        if ts > 1e12:
            ts = ts / 1000
        from datetime import datetime
        dt = datetime.fromtimestamp(ts)
        return f"{dt.year}年{dt.month}月{dt.day}日"
    except Exception:
        return text


def _now_date_str() -> str:
    from datetime import datetime
    dt = datetime.now()
    return f"{dt.year}年{dt.month}月{dt.day}日"


class PlayerSaveRepository:
    PLAYER_FACTIONS = {"魔法少女", "反派干部"}
    SOURCE_FILE_NAMES = {
        "player_data.json",
        "player_data_update.json",
        "player_monster_book.json",
        "relationships.json",
        "cameo_memory.jsonl",
        "daily_memory.jsonl",
    }
    WORLD_ERA_START = date(2020, 4, 1)

    def __init__(
        self,
        plugin_name: str = "astrbot_plugin_qq_MahouShoujo",
        editable_manager: Any | None = None,
    ):
        self.root_dir = StarTools.get_data_dir(plugin_name) / "saves"
        self.editable_manager = editable_manager

    # ── 世界时钟 ──────────────────────────────────

    @classmethod
    def format_world_date(cls, day_offset: int) -> str:
        value = cls.WORLD_ERA_START + timedelta(days=max(0, int(day_offset)))
        return f"公元{value.year}年{value.month}月{value.day}日"

    def get_current_world_day_offset(self, group_id: str) -> int:
        clock_path = self._world_clock_path(group_id)
        clock = self._read_json(clock_path)
        if not clock:
            clock = self._migrate_world_clock(group_id)
            self._atomic_write_json(clock_path, clock)
        return max(0, int(clock.get("next_day_offset", 0) or 0))

    def get_current_world_date(self, group_id: str) -> str:
        return self.format_world_date(self.get_current_world_day_offset(group_id))

    def advance_world_clock(self, group_id: str, *, expected_day_offset: int) -> None:
        clock_path = self._world_clock_path(group_id)
        clock = self._read_json(clock_path)
        if not clock:
            clock = self._migrate_world_clock(group_id)
        current = max(0, int(clock.get("next_day_offset", 0) or 0))
        if current != int(expected_day_offset):
            raise ValueError(f"群世界时间已变化: expected={expected_day_offset}, actual={current}")
        clock["next_day_offset"] = current + 1
        clock["updated_at"] = self._now_ms()
        self._atomic_write_json(clock_path, clock)

    def _migrate_world_clock(self, group_id: str) -> dict[str, Any]:
        group_dir = self.root_dir / "groups" / self._safe_id(group_id)
        users_dir = group_dir / "users"
        records: list[tuple[int, Path, int, dict[str, Any]]] = []
        if users_dir.exists():
            for user_dir in sorted(path for path in users_dir.iterdir() if path.is_dir()):
                log_path = user_dir / "daily_memory.jsonl"
                if not log_path.exists():
                    log_path = user_dir / "battle_log.jsonl"
                for index, item in enumerate(self._read_recent_logs(log_path, limit=0)):
                    if item.get("type") not in {"battle_diary", "battle_summary"}:
                        continue
                    records.append(
                        (
                            int(item.get("created_at", 0) or 0),
                            log_path,
                            int(item.get("_log_index", index)),
                            item,
                        )
                    )

        next_day_offset = 0
        changed_paths: dict[Path, list[str]] = {}
        for _created_at, log_path, line_index, item in sorted(
            records,
            key=lambda value: (value[0], str(value[1]), value[2]),
        ):
            if item.get("type") == "battle_summary":
                if "world_date_from" not in item and "world_date_to" not in item:
                    item["world_date_unknown"] = True
                    changed_paths.setdefault(log_path, log_path.read_text(encoding="utf-8").splitlines())
                    changed_paths[log_path][line_index] = json.dumps(item, ensure_ascii=False)
                next_day_offset += max(1, int(item.get("compressed_count", 1) or 1))
                continue
            if "world_day_offset" not in item:
                item["world_day_offset"] = next_day_offset
                item["world_date"] = self.format_world_date(next_day_offset)
                item["date_label"] = item["world_date"]
                changed_paths.setdefault(log_path, log_path.read_text(encoding="utf-8").splitlines())
                changed_paths[log_path][line_index] = json.dumps(item, ensure_ascii=False)
            next_day_offset = max(
                next_day_offset + 1,
                int(item.get("world_day_offset", next_day_offset) or 0) + 1,
            )

        for path, lines in changed_paths.items():
            self._write_jsonl_lines(path, lines)
        self._migrate_cameo_world_dates(group_id)
        return {
            "schema_version": 1,
            "next_day_offset": next_day_offset,
            "updated_at": self._now_ms(),
        }

    def _migrate_cameo_world_dates(self, group_id: str) -> None:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return
        source_dates: dict[tuple[str, str], tuple[int, str]] = {}
        for user_dir in sorted(path for path in users_dir.iterdir() if path.is_dir()):
            log_path = user_dir / "daily_memory.jsonl"
            if not log_path.exists():
                log_path = user_dir / "battle_log.jsonl"
            for item in self._read_recent_logs(log_path, limit=0):
                if item.get("type") != "battle_diary" or not item.get("world_date"):
                    continue
                source_dates[(user_dir.name, str(item.get("title") or ""))] = (
                    int(item.get("world_day_offset", 0) or 0),
                    str(item.get("world_date")),
                )
        for user_dir in sorted(path for path in users_dir.iterdir() if path.is_dir()):
            path = user_dir / "cameo_memory.jsonl"
            if not path.exists():
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            changed = False
            for index, line in enumerate(lines):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict) or item.get("world_date"):
                    continue
                matched = source_dates.get(
                    (self._safe_id(item.get("source_user_id", "")), str(item.get("title") or ""))
                )
                if matched:
                    item["world_day_offset"], item["world_date"] = matched
                else:
                    item["world_date_unknown"] = True
                lines[index] = json.dumps(item, ensure_ascii=False)
                changed = True
            if changed:
                self._write_jsonl_lines(path, lines)

    def _world_clock_path(self, group_id: str) -> Path:
        return self.root_dir / "groups" / self._safe_id(group_id) / "world_clock.json"

    @classmethod
    def _world_date_range_title(cls, first: dict[str, Any], last: dict[str, Any]) -> str:
        first_date = str(first.get("world_date_from") or first.get("world_date") or "").strip()
        last_date = str(last.get("world_date_to") or last.get("world_date") or "").strip()
        if not first_date or not last_date:
            return "历史日期未知的日记"
        if first_date == last_date:
            return f"{first_date}的日记"
        return f"{first_date}到{last_date}的日记"

    @classmethod
    def _copy_world_date_range(
        cls,
        target: dict[str, Any],
        first: dict[str, Any],
        last: dict[str, Any],
    ) -> None:
        first_date = str(first.get("world_date_from") or first.get("world_date") or "").strip()
        last_date = str(last.get("world_date_to") or last.get("world_date") or "").strip()
        if not first_date or not last_date:
            target["world_date_unknown"] = True
            return
        target["world_date_from"] = first_date
        target["world_date_to"] = last_date

    @staticmethod
    def _write_jsonl_lines(path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        text = "\n".join(lines)
        tmp_path.write_text(text + ("\n" if text else ""), encoding="utf-8")
        tmp_path.replace(path)

    # ── 玩家数据读取 ──────────────────────────────────

    def _load_current_player_data(self, user_dir: Path) -> dict[str, Any]:
        """读取当前玩家数据（优先 player_data_update.json，回退 player_data.json）。"""
        update_data = self._read_json(user_dir / "player_data_update.json")
        if update_data:
            return update_data
        return self._read_json(user_dir / "player_data.json")

    def _save_current_player_data(self, user_dir: Path, data: dict[str, Any]) -> None:
        """保存玩家数据变更到 player_data_update.json。"""
        update_path = user_dir / "player_data_update.json"
        self._atomic_write_json(update_path, data)

    # ── 转生存档 ──────────────────────────────────

    def save_reincarnation(
        self,
        group_id: str,
        user_id: str,
        card: ReincarnationCard,
        nickname: str | None = None,
        avatar_url: str | None = None,
        faction: str = "魔法少女",
    ) -> Path:
        user_dir = self.get_user_dir(group_id, user_id)
        user_dir.mkdir(parents=True, exist_ok=True)

        faction = self._normalize_player_faction(faction)
        protagonist_tree = card.build_protagonist_tree()
        protagonist_tree.setdefault("主角", {}).setdefault("阵营", {})["身份"] = (
            faction
        )

        # 确保等级节点存在
        if "等级" not in protagonist_tree.get("主角", {}):
            protagonist_tree.setdefault("主角", {})["等级"] = {"等级": 1, "经验": 0}
        self._ensure_battle_count(protagonist_tree)

        player_data: dict[str, Any] = {
            "schema_version": 2,
            "group_id": str(group_id),
            "user_id": str(user_id),
            "nickname": nickname or card.target_name,
            "avatar_url": avatar_url or "",
            "created_at": _now_date_str(),
            "updated_at": _now_date_str(),
        }
        player_data.update(protagonist_tree)

        player_data_path = user_dir / "player_data.json"
        self._atomic_write_json(player_data_path, player_data)

        # 转生会重置基础人物卡；实时状态只同步覆盖主角树，保留其他顶层词条。
        update_data = self._read_json(user_dir / "player_data_update.json") or dict(player_data)
        update_data["主角"] = player_data["主角"]
        self._atomic_write_json(user_dir / "player_data_update.json", update_data)

        self.append_log(
            group_id,
            user_id,
            {
                "type": "reincarnation",
                "message": f"完成{faction}转生",
                "created_at": self._now_ms(),
                "title": f"{faction}转生人物卡",
                "target_name": card.target_name,
            },
        )
        return user_dir

    # ── 加载存档 ──────────────────────────────────

    def load_player_save(
        self,
        group_id: str,
        user_id: str,
        log_limit: int = 0,
    ) -> dict[str, Any] | None:
        user_dir = self.get_user_dir(group_id, user_id)
        player_data_path = user_dir / "player_data.json"
        if not player_data_path.exists():
            return None

        player_data = self._load_current_player_data(user_dir)
        if not player_data:
            return None

        self._remove_location_state(player_data)
        self._remove_economy_state(player_data)
        self._normalize_status_progress_in_data(player_data)
        self._ensure_battle_count(player_data)

        return {
            "group_id": self._safe_id(group_id),
            "user_id": self._safe_id(user_id),
            "player_data": player_data,
            "logs": self._read_recent_logs(
                user_dir / "daily_memory.jsonl",
                limit=log_limit,
            ),
            "cameo_memories": self._read_recent_cameo_memories(
                user_dir / "cameo_memory.jsonl",
                limit=12,
            ),
        }

    # ── 战斗日记结果保存 ──────────────────────────────────

    def save_battle_result(
        self,
        group_id: str,
        user_id: str,
        card: BattleDiaryCard,
        new_level: int,
        new_level_exp: int = 0,
        world_day_offset: int | None = None,
        mention_scan_texts: str | list[str] | None = None,
        identity_transition_faction: str | None = None,
    ) -> None:
        user_dir = self.get_user_dir(group_id, user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        now = self._now_ms()
        if world_day_offset is None:
            world_day_offset = self.get_current_world_day_offset(group_id)
        world_day_offset = max(0, int(world_day_offset))
        world_date = self.format_world_date(world_day_offset)
        card.date_label = world_date

        player_data = self._load_current_player_data(user_dir)
        if not player_data:
            player_data = self._create_default_player_data(group_id, user_id)

        if identity_transition_faction is not None:
            protagonist = player_data.setdefault("主角", {})
            if not isinstance(protagonist, dict):
                protagonist = {}
                player_data["主角"] = protagonist
            faction_node = protagonist.setdefault("阵营", {})
            if not isinstance(faction_node, dict):
                faction_node = {}
                protagonist["阵营"] = faction_node
            faction_node["身份"] = self._normalize_player_faction(identity_transition_faction)
            card.state_snapshot = dict(player_data)
            self._save_current_player_data(user_dir, player_data)
            self._append_battle_result_log(
                group_id,
                user_id,
                card,
                now=now,
                world_day_offset=world_day_offset,
                world_date=world_date,
            )
            self.advance_world_clock(group_id, expected_day_offset=world_day_offset)
            return

        # 更新等级
        protagonist = player_data.setdefault("主角", {})
        level_node = protagonist.setdefault("等级", {"等级": 1, "经验": 0})
        level_node["等级"] = max(1, min(int(new_level), 7))
        level_node["经验"] = max(0, min(int(new_level_exp), 99))
        protagonist["等级"] = level_node
        self._ensure_battle_count(player_data)

        player_data["updated_at"] = _now_date_str()
        self._remove_economy_state(player_data)
        self._remove_location_state(player_data)

        # 应用状态变化
        teammate_names = self._find_teammate_names(group_id, card.target_name)
        teammate_state_changes = self._apply_state_changes(
            player_data,
            card.update_changes,
            teammate_names=teammate_names,
        )
        self._increment_battle_count(player_data)
        card.state_snapshot = dict(player_data)
        self._save_current_player_data(user_dir, player_data)

        # 应用队友状态变化
        if teammate_state_changes:
            self._apply_teammate_state_changes(group_id, teammate_state_changes)

        # 应用队友等级经验（纯代码，AI 无需输出）
        level_exp_delta = self._extract_level_exp_delta(card.update_changes)
        if level_exp_delta > 0:
            mentioned_names = self._find_mentioned_teammate_names(
                group_id,
                card,
                mention_scan_texts=mention_scan_texts,
            )
            if mentioned_names:
                self._apply_teammate_level_exp(
                    group_id,
                    max(1, min(int(new_level), 7)),
                    level_exp_delta,
                    mentioned_names,
                )

        self._increment_participant_teammate_battle_counts(
            group_id,
            card,
            protagonist_name=card.target_name,
        )

        self._append_battle_result_log(
            group_id,
            user_id,
            card,
            now=now,
            world_day_offset=world_day_offset,
            world_date=world_date,
        )
        self.advance_world_clock(group_id, expected_day_offset=world_day_offset)

    def _append_battle_result_log(
        self,
        group_id: str,
        user_id: str,
        card: BattleDiaryCard,
        *,
        now: int,
        world_day_offset: int,
        world_date: str,
    ) -> None:
        self.append_log(
            group_id,
            user_id,
            {
                "type": "battle_diary",
                "created_at": now,
                "title": card.title,
                "date_label": card.date_label,
                "world_day_offset": world_day_offset,
                "world_date": world_date,
                "action": card.action,
                "participants": card.participants,
                "monster_name": card.monster_name,
                "diary": card.diary,
                "encounter": card.encounter,
                "level_change": card.level_change,
                "level_exp": card.level_exp_after,
                "result": card.result,
                "reason": card.reason,
                "update_changes": card.update_changes,
            },
        )

    # ── 日志压缩 ──────────────────────────────────

    def maybe_compress_battle_logs(
        self,
        group_id: str,
        user_id: str,
        *,
        interval: int,
        compress_count: int,
        summary_text: str,
    ) -> bool:
        if interval <= 0 or compress_count <= 0 or compress_count >= interval:
            return False
        text = str(summary_text or "").strip()
        if not text:
            return False

        user_dir = self.get_user_dir(group_id, user_id)
        log_path = user_dir / "daily_memory.jsonl"
        if not log_path.exists():
            # 兼容旧文件名
            log_path = user_dir / "battle_log.jsonl"
        if not log_path.exists():
            return False

        try:
            raw_lines = log_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.warning(f"读取战斗日志失败，跳过压缩: {log_path} {exc}")
            return False

        parsed: list[dict[str, Any] | None] = []
        compressible: list[tuple[int, dict[str, Any]]] = []
        battle_ordinal = 0
        for index, line in enumerate(raw_lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                parsed.append(None)
                continue
            if not isinstance(item, dict):
                parsed.append(None)
                continue
            parsed.append(item)
            log_type = item.get("type")
            if log_type == "battle_summary":
                battle_ordinal = max(
                    battle_ordinal,
                    int(item.get("battle_to", 0) or 0),
                )
                compressible.append((index, item))
            elif log_type == "battle_diary":
                battle_ordinal += 1
                compressible.append((index, item))

        if len(compressible) < interval:
            return False

        selected = compressible[:compress_count]
        selected_indices = {index for index, _item in selected}
        first_ordinal = self._battle_ordinal_from_for_log(raw_lines, selected[0][0])
        last_ordinal = self._battle_ordinal_to_for_log(raw_lines, selected[-1][0])
        summary_record = {
            "type": "battle_summary",
            "created_at": self._now_ms(),
            "title": self._world_date_range_title(selected[0][1], selected[-1][1]),
            "date_label": self._world_date_range_title(selected[0][1], selected[-1][1]),
            "battle_from": first_ordinal,
            "battle_to": last_ordinal,
            "compressed_count": len(selected),
            "result": text,
        }
        self._copy_world_date_range(summary_record, selected[0][1], selected[-1][1])

        next_lines: list[str] = []
        inserted = False
        for index, line in enumerate(raw_lines):
            if index in selected_indices:
                if not inserted:
                    next_lines.append(json.dumps(summary_record, ensure_ascii=False))
                    inserted = True
                continue
            next_lines.append(line)

        tmp_path = log_path.with_suffix(log_path.suffix + ".tmp")
        output = "\n".join(next_lines)
        if output:
            output += "\n"
        tmp_path.write_text(output, encoding="utf-8")
        tmp_path.replace(log_path)
        return True

    def get_battle_logs_for_compression(
        self,
        group_id: str,
        user_id: str,
        *,
        interval: int,
        compress_count: int,
    ) -> list[dict[str, Any]]:
        if interval <= 0 or compress_count <= 0 or compress_count >= interval:
            return []
        user_dir = self.get_user_dir(group_id, user_id)
        log_path = user_dir / "daily_memory.jsonl"
        if not log_path.exists():
            log_path = user_dir / "battle_log.jsonl"
        logs = self._read_recent_logs(log_path, limit=0)
        compressible = [
            item
            for item in logs
            if isinstance(item, dict)
            and item.get("type") in ("battle_diary", "battle_summary")
        ]
        if len(compressible) < interval:
            return []
        return compressible[:compress_count]

    def maybe_compress_cameo_memories(
        self,
        group_id: str,
        user_id: str,
        *,
        interval: int,
        compress_count: int,
        summary_text: str,
    ) -> bool:
        if interval <= 0 or compress_count <= 0 or compress_count >= interval:
            return False
        text = str(summary_text or "").strip()
        if not text:
            return False

        log_path = self.get_user_dir(group_id, user_id) / "cameo_memory.jsonl"
        if not log_path.exists():
            return False

        try:
            raw_lines = log_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.warning(f"读取互动记录失败，跳过压缩: {log_path} {exc}")
            return False

        compressible: list[tuple[int, dict[str, Any]]] = []
        interaction_ordinal = 0
        for index, line in enumerate(raw_lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            log_type = item.get("type")
            if log_type == "cameo_summary":
                interaction_ordinal = max(
                    interaction_ordinal,
                    int(item.get("interaction_to", 0) or 0),
                )
                compressible.append((index, item))
            elif log_type == "cameo_memory":
                interaction_ordinal += 1
                compressible.append((index, item))

        if len(compressible) < interval:
            return False

        selected = compressible[:compress_count]
        selected_indices = {index for index, _item in selected}
        first_ordinal = self._cameo_ordinal_from_for_log(raw_lines, selected[0][0])
        last_ordinal = self._cameo_ordinal_to_for_log(raw_lines, selected[-1][0])
        summary_record = {
            "type": "cameo_summary",
            "created_at": self._now_ms(),
            "title": self._world_date_range_title(selected[0][1], selected[-1][1]),
            "interaction_from": first_ordinal,
            "interaction_to": last_ordinal,
            "compressed_count": len(selected),
            "result": text,
        }
        self._copy_world_date_range(summary_record, selected[0][1], selected[-1][1])

        next_lines: list[str] = []
        inserted = False
        for index, line in enumerate(raw_lines):
            if index in selected_indices:
                if not inserted:
                    next_lines.append(json.dumps(summary_record, ensure_ascii=False))
                    inserted = True
                continue
            next_lines.append(line)

        tmp_path = log_path.with_suffix(log_path.suffix + ".tmp")
        output = "\n".join(next_lines)
        if output:
            output += "\n"
        tmp_path.write_text(output, encoding="utf-8")
        tmp_path.replace(log_path)
        return True

    def get_cameo_memories_for_compression(
        self,
        group_id: str,
        user_id: str,
        *,
        interval: int,
        compress_count: int,
    ) -> list[dict[str, Any]]:
        if interval <= 0 or compress_count <= 0 or compress_count >= interval:
            return []
        log_path = self.get_user_dir(group_id, user_id) / "cameo_memory.jsonl"
        memories = self._read_recent_logs(log_path, limit=0)
        compressible = [
            item
            for item in memories
            if isinstance(item, dict)
            and item.get("type") in ("cameo_memory", "cameo_summary")
        ]
        if len(compressible) < interval:
            return []
        return compressible[:compress_count]

    # ── 日志追加 ──────────────────────────────────

    def append_log(self, group_id: str, user_id: str, record: dict[str, Any]) -> None:
        user_dir = self.get_user_dir(group_id, user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        log_path = user_dir / "daily_memory.jsonl"
        payload = dict(record)
        payload.setdefault("created_at", self._now_ms())
        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def append_cameo_memory(
        self,
        group_id: str,
        npc_user_id: str,
        record: dict[str, Any],
    ) -> None:
        user_dir = self.get_user_dir(group_id, npc_user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        log_path = user_dir / "cameo_memory.jsonl"
        payload = dict(record)
        payload["type"] = "cameo_memory"
        payload.setdefault("created_at", self._now_ms())
        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    # ── 目录与列表 ──────────────────────────────────

    def get_user_dir(self, group_id: str, user_id: str) -> Path:
        safe_group = self._safe_id(group_id)
        safe_user = self._safe_id(user_id)
        return self.root_dir / "groups" / safe_group / "users" / safe_user

    def list_cities(self) -> list[dict[str, Any]]:
        groups_dir = self.root_dir / "groups"
        if not groups_dir.exists():
            return []

        cities: list[dict[str, Any]] = []
        for group_dir in sorted(p for p in groups_dir.iterdir() if p.is_dir()):
            users_dir = group_dir / "users"
            player_count = 0
            updated_at = ""
            if users_dir.exists():
                for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
                    player_data = self._load_current_player_data(user_dir)
                    if not player_data:
                        continue
                    player_count += 1
                    item_updated_at = str(player_data.get("updated_at", "") or "")
                    if item_updated_at > updated_at:
                        updated_at = item_updated_at
            if player_count <= 0:
                continue
            city_id = group_dir.name
            cities.append(
                {
                    "city_id": city_id,
                    "city_name": self.get_city_name(city_id),
                    "player_count": player_count,
                    "updated_at": updated_at,
                }
            )
        return cities

    def get_city_name(self, group_id: str) -> str:
        city_id = self._safe_id(group_id)
        meta = self._read_json(self._city_meta_path(city_id))
        name = str(meta.get("city_name") or "").strip()
        return name or city_id

    def update_city_name(self, group_id: str, city_name: str) -> dict[str, Any]:
        city_id = self._safe_id(group_id)
        name = str(city_name or "").strip() or city_id
        meta = {
            "schema_version": 1,
            "city_id": city_id,
            "city_name": name,
            "updated_at": self._now_ms(),
        }
        self._atomic_write_json(self._city_meta_path(city_id), meta)
        return meta

    def list_saves_by_city(self, group_id: str) -> list[dict[str, Any]]:
        city_id = self._safe_id(group_id)
        return [item for item in self.list_saves() if item.get("group_id") == city_id]

    def list_saves(self) -> list[dict[str, Any]]:
        groups_dir = self.root_dir / "groups"
        if not groups_dir.exists():
            return []

        saves: list[dict[str, Any]] = []
        for group_dir in sorted(p for p in groups_dir.iterdir() if p.is_dir()):
            users_dir = group_dir / "users"
            if not users_dir.exists():
                continue
            for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
                player_data = self._load_current_player_data(user_dir)
                if not player_data:
                    continue
                protagonist = player_data.get("主角", {})
                target_name = self._get_nested(
                    protagonist, ["个人信息", "姓名"], player_data.get("nickname", "")
                )
                level_node = protagonist.get("等级", {})
                level = level_node.get("等级", 1) if isinstance(level_node, dict) else 1
                saves.append(
                    {
                        "group_id": group_dir.name,
                        "user_id": user_dir.name,
                        "nickname": player_data.get("nickname", ""),
                        "target_name": target_name,
                        "level": level,
                        "updated_at": player_data.get("updated_at", ""),
                    }
                )
        return saves

    def list_saves_by_user(self, user_id: str) -> list[dict[str, Any]]:
        safe_user = self._safe_id(user_id)
        return [item for item in self.list_saves() if item.get("user_id") == safe_user]

    def read_relationship_graph(
        self,
        group_id: str,
        viewer_user_id: str,
    ) -> dict[str, Any]:
        city_id = self._safe_id(group_id)
        viewer_id = self._safe_id(viewer_user_id)
        users_dir = self.root_dir / "groups" / city_id / "users"
        if not users_dir.exists():
            return {"viewer_user_id": viewer_id, "nodes": [], "edges": []}

        nodes: list[dict[str, Any]] = []
        profiles_by_user: dict[str, dict[str, Any]] = {}
        user_by_name: dict[str, str] = {}
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            profile = self._build_relationship_player_profile(user_dir)
            if not profile:
                continue
            user_id = str(profile.get("user_id") or user_dir.name)
            profiles_by_user[user_id] = profile
            for name in self._relationship_names(profile):
                user_by_name[name] = user_id
            nodes.append(
                {
                    "user_id": user_id,
                    "target_name": str(profile.get("target_name") or user_id),
                    "magical_name": str(profile.get("magical_name") or ""),
                    "faction": str(profile.get("阵营") or "魔法少女"),
                    "level": profile.get("等级", 1),
                }
            )

        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str]] = set()
        for source_user_id, profile in profiles_by_user.items():
            user_dir = users_dir / source_user_id
            data = self._read_json(user_dir / "relationships.json")
            relationships = data.get("relationships", {}) if isinstance(data, dict) else {}
            if not isinstance(relationships, dict):
                continue
            for target_key, value in relationships.items():
                if not isinstance(value, dict):
                    continue
                target_user_id = str(value.get("target_user_id") or "").strip()
                if not target_user_id or target_user_id not in profiles_by_user:
                    target_user_id = user_by_name.get(str(target_key or "").strip(), "")
                if (
                    not target_user_id
                    or target_user_id not in profiles_by_user
                    or target_user_id == source_user_id
                ):
                    continue
                edge_key = (source_user_id, target_user_id)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                edges.append(
                    {
                        "from_user_id": source_user_id,
                        "to_user_id": target_user_id,
                        "from_name": (
                            profile.get("magical_name")
                            or profile.get("target_name")
                            or source_user_id
                        ),
                        "to_name": (
                            profiles_by_user[target_user_id].get("magical_name")
                            or profiles_by_user[target_user_id].get("target_name")
                            or target_user_id
                        ),
                        "relationship": str(value.get("relationship") or "")[:12],
                        "impression": str(value.get("impression") or ""),
                        "evidence": str(value.get("evidence") or ""),
                        "summary": str(value.get("summary") or ""),
                        "tags": value.get("tags") if isinstance(value.get("tags"), list) else [],
                        "updated_at": value.get("updated_at", ""),
                        "last_world_date": str(value.get("last_world_date") or ""),
                    }
                )

        return {
            "viewer_user_id": viewer_id,
            "nodes": nodes,
            "edges": edges,
        }

    def _city_meta_path(self, group_id: str) -> Path:
        return self.root_dir / "groups" / self._safe_id(group_id) / "city.json"

    # ── NPC 查找 ──────────────────────────────────

    def find_mentioned_npcs(
        self,
        group_id: str,
        user_id: str,
        scan_texts: str | list[str],
        *,
        recent_record_count: int = 1,
    ) -> list[dict[str, Any]]:
        if isinstance(scan_texts, list):
            text = "\n".join(str(item or "") for item in scan_texts).strip()
        else:
            text = str(scan_texts or "").strip()
        if not text:
            return []

        current_user = self._safe_id(user_id)
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return []

        npcs: list[dict[str, Any]] = []
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            if user_dir.name == current_user:
                continue

            player_data = self._load_current_player_data(user_dir)
            protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
            matched = any(name and name in text for name in self._player_public_names(protagonist))
            if not matched:
                continue
            npc = self._build_npc_package(
                user_dir,
                source="mentioned_by_action",
                recent_record_count=recent_record_count,
            )
            if npc:
                npcs.append(npc)
        return npcs

    def find_participant_npcs(
        self,
        group_id: str,
        user_id: str,
        participants: list[str],
        *,
        recent_record_count: int = 1,
    ) -> list[dict[str, Any]]:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return []

        current_user = self._safe_id(user_id)
        npcs: list[dict[str, Any]] = []
        seen_users: set[str] = set()
        for raw_name in participants or []:
            name = str(raw_name or "").strip()
            if not name:
                continue
            user_dir = self._find_user_dir_by_target_name(users_dir, name)
            if not user_dir or user_dir.name == current_user or user_dir.name in seen_users:
                continue
            npc = self._build_npc_package(
                user_dir,
                source="participant",
                recent_record_count=recent_record_count,
            )
            if npc:
                seen_users.add(user_dir.name)
                npcs.append(npc)
        return npcs

    def find_npcs_by_names(
        self,
        group_id: str,
        user_id: str,
        names: list[str],
        *,
        recent_record_count: int = 1,
    ) -> list[dict[str, Any]]:
        return self.find_participant_npcs(
            group_id,
            user_id,
            names,
            recent_record_count=recent_record_count,
        )

    def build_city_teammate_candidates(
        self,
        group_id: str,
        user_id: str,
        *,
        recent_record_count: int = 1,
    ) -> list[dict[str, Any]]:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return []

        current_user = self._safe_id(user_id)
        candidates: list[dict[str, Any]] = []
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            if user_dir.name == current_user:
                continue
            npc = self._build_npc_package(
                user_dir,
                source="city_candidate",
                recent_record_count=recent_record_count,
            )
            if not npc:
                continue
            public_reputation = self._read_public_reputation(user_dir)
            if public_reputation:
                npc["城市风评"] = public_reputation
                npc["public_reputation"] = public_reputation
            candidates.append(npc)
        return candidates

    def build_city_magical_girl_candidates(
        self,
        group_id: str,
        user_id: str,
        *,
        recent_record_count: int = 1,
    ) -> list[dict[str, Any]]:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return []

        current_user = self._safe_id(user_id)
        candidates: list[dict[str, Any]] = []
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            if user_dir.name == current_user:
                continue
            npc = self._build_npc_package(
                user_dir,
                source="city_magical_girl_candidate",
                recent_record_count=recent_record_count,
            )
            if not npc:
                continue
            if str(npc.get("阵营") or "").strip() != "魔法少女":
                continue
            public_reputation = self._read_public_reputation(user_dir)
            if public_reputation:
                npc["城市风评"] = public_reputation
                npc["public_reputation"] = public_reputation
            candidates.append(npc)
        return candidates

    def build_villain_monster_candidates(
        self,
        group_id: str,
        user_id: str,
        *,
        player_level: int,
        include_overleveled: bool = False,
    ) -> list[dict[str, Any]]:
        return self._read_monster_candidates_from_path(
            self.get_user_dir(group_id, user_id) / "player_monster_book.json",
            player_level=player_level,
            source="player",
            include_overleveled=include_overleveled,
        )

    def _read_monster_candidates_from_path(
        self,
        path: Path,
        *,
        player_level: int,
        source: str,
        include_overleveled: bool = False,
    ) -> list[dict[str, Any]]:
        raw = self._read_json(path)
        entries = raw.get("entries", []) if isinstance(raw, dict) else []
        if isinstance(entries, dict):
            iterable = entries.items()
        elif isinstance(entries, list):
            iterable = enumerate(entries)
        else:
            return []

        candidates: list[dict[str, Any]] = []
        for fallback_id, entry in iterable:
            if not isinstance(entry, dict):
                continue
            normalized = self._monster_entry_for_selection(
                entry,
                fallback_id=str(fallback_id),
                player_level=player_level,
                source=source,
                include_overleveled=include_overleveled,
            )
            if normalized:
                candidates.append(normalized)
        return candidates

    @staticmethod
    def _monster_entry_for_selection(
        entry: dict[str, Any],
        *,
        fallback_id: str,
        player_level: int,
        source: str,
        include_overleveled: bool = False,
    ) -> dict[str, Any] | None:
        visible_levels = normalize_visible_levels(
            entry.get("visible_levels"),
            min_level=entry.get("min_level", 1),
            max_level=entry.get("max_level", 7),
        )
        if player_level not in visible_levels:
            return None

        monster_levels = normalize_visible_levels(
            entry.get("monster_levels"),
            min_level=entry.get("min_monster_level", 1),
            max_level=entry.get("max_monster_level", 7),
        )
        default_levels = [level for level in monster_levels if level <= player_level]
        selectable_levels = list(monster_levels) if include_overleveled else default_levels
        if not selectable_levels:
            return None

        raw_settings = entry.get("level_settings")
        level_settings = raw_settings if isinstance(raw_settings, dict) else {}
        usable_level_settings: dict[str, dict[str, str]] = {}
        for level in selectable_levels:
            raw_setting = level_settings.get(str(level), {})
            if not isinstance(raw_setting, dict):
                raw_setting = {}
            usable_level_settings[level_label(level)] = {
                "brief": str(raw_setting.get("brief") or "").strip(),
                "content": str(raw_setting.get("content") or "").strip(),
            }

        name = str(entry.get("name") or entry.get("title") or "").strip()
        content = str(entry.get("content") or entry.get("detail") or "").strip()
        brief = str(entry.get("brief") or entry.get("summary") or "").strip()
        if not name and not content and not brief:
            return None

        return {
            "id": str(entry.get("id") or fallback_id).strip(),
            "name": name,
            "source": source,
            "visible_levels": [level_label(level) for level in visible_levels],
            "monster_levels": [level_label(level) for level in selectable_levels],
            "default_levels": [level_label(level) for level in default_levels],
            "brief": brief,
            "content": content,
            "level_settings": usable_level_settings,
        }

    def read_save_detail(self, group_id: str, user_id: str) -> dict[str, Any] | None:
        user_dir = self.get_user_dir(group_id, user_id)
        if not user_dir.exists():
            return None

        player_data = self._load_current_player_data(user_dir)
        if not player_data:
            return None

        self._remove_economy_state(player_data)
        self._remove_location_state(player_data)
        self._normalize_status_progress_in_data(player_data)

        log_path = user_dir / "daily_memory.jsonl"
        if not log_path.exists():
            log_path = user_dir / "battle_log.jsonl"

        return {
            "group_id": self._safe_id(group_id),
            "user_id": self._safe_id(user_id),
            "player_data": player_data,
            "logs": self._read_recent_logs(log_path, limit=80),
            "cameo_memories": self._read_recent_cameo_memories(
                user_dir / "cameo_memory.jsonl",
                limit=80,
            ),
        }

    # ── 人物卡更新 ──────────────────────────────────

    def update_profile_card(
        self,
        group_id: str,
        user_id: str,
        updates: dict[str, Any],
    ) -> None:
        user_dir = self.get_user_dir(group_id, user_id)
        player_data = self._load_current_player_data(user_dir)
        if not player_data:
            raise ValueError("玩家存档不存在或无法读取")

        protagonist = player_data.setdefault("主角", {})
        # updates 是一个 { path_tail: value } 的扁平字典
        # 将更新写入主角树对应位置
        for key, value in updates.items():
            if not isinstance(value, str) or not value.strip():
                continue
            # 尝试匹配到主角树中的某个路径
            self._update_protagonist_field(protagonist, key, str(value).strip())

        # 更新昵称为角色名
        name = self._get_nested(protagonist, ["个人信息", "姓名"], "")
        if name:
            player_data["nickname"] = name

        player_data["updated_at"] = _now_date_str()
        self._save_current_player_data(user_dir, player_data)

    # ── 源码文件管理 ──────────────────────────────────

    def list_player_source_files(self, group_id: str, user_id: str) -> list[dict[str, Any]]:
        user_dir = self.get_user_dir(group_id, user_id)
        return [
            {
                "name": file_name,
                "exists": (user_dir / file_name).exists(),
                "kind": "jsonl" if file_name.endswith(".jsonl") else "json",
            }
            for file_name in sorted(self.SOURCE_FILE_NAMES)
        ]

    def read_player_source_file(
        self,
        group_id: str,
        user_id: str,
        file_name: str,
    ) -> str:
        path = self._player_source_path(group_id, user_id, file_name)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_player_source_file(
        self,
        group_id: str,
        user_id: str,
        file_name: str,
        content: str,
    ) -> None:
        path = self._player_source_path(group_id, user_id, file_name)
        self._validate_source_content(file_name, content)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self._backup_source_file(path)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(str(content), encoding="utf-8")
        tmp_path.replace(path)

    # ── 存档删除与清理 ──────────────────────────────────

    def delete_battle_log(self, group_id: str, user_id: str, log_index: int) -> bool:
        user_dir = self.get_user_dir(group_id, user_id)
        log_path = user_dir / "daily_memory.jsonl"
        if not log_path.exists():
            log_path = user_dir / "battle_log.jsonl"
        root = self.root_dir.resolve()
        target = log_path.resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"非法日志路径: {target}")
        if not log_path.exists():
            return False

        lines = log_path.read_text(encoding="utf-8").splitlines()
        if log_index < 0 or log_index >= len(lines):
            return False

        del lines[log_index]
        tmp_path = log_path.with_suffix(log_path.suffix + ".tmp")
        text = "\n".join(lines)
        if text:
            text += "\n"
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(log_path)
        return True

    def clear_battle_logs(self, group_id: str, user_id: str) -> bool:
        user_dir = self.get_user_dir(group_id, user_id)
        log_path = user_dir / "daily_memory.jsonl"
        if not log_path.exists():
            log_path = user_dir / "battle_log.jsonl"
        return self._clear_file(log_path)

    def clear_cameo_memories(self, group_id: str, user_id: str) -> bool:
        log_path = self.get_user_dir(group_id, user_id) / "cameo_memory.jsonl"
        return self._clear_file(log_path)

    def reset_player_state(self, group_id: str, user_id: str) -> None:
        user_dir = self.get_user_dir(group_id, user_id)
        player_data_path = user_dir / "player_data.json"
        player_data = self._read_json(player_data_path)
        if not player_data:
            raise ValueError("玩家存档不存在或无法读取")

        self._atomic_write_json(user_dir / "player_data_update.json", player_data)

    def delete_player_save(self, group_id: str, user_id: str) -> bool:
        user_dir = self.get_user_dir(group_id, user_id)
        root = self.root_dir.resolve()
        target = user_dir.resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"非法存档路径: {target}")
        if not user_dir.exists():
            return False

        self.delete_cameo_memories_by_source(group_id, user_id)
        shutil.rmtree(user_dir)
        self._cleanup_empty_parent_dirs(user_dir)
        return True

    def delete_cameo_memories_by_source(self, group_id: str, source_user_id: str) -> int:
        source_user = self._safe_id(source_user_id)
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return 0

        removed_count = 0
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            log_path = user_dir / "cameo_memory.jsonl"
            if not log_path.exists():
                continue
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()
            except Exception as exc:
                logger.warning(f"读取客串记忆失败，跳过清理: {log_path} {exc}")
                continue

            kept_lines: list[str] = []
            changed = False
            for line in lines:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    kept_lines.append(line)
                    continue
                if (
                    isinstance(item, dict)
                    and item.get("type") == "cameo_memory"
                    and self._safe_id(item.get("source_user_id", "")) == source_user
                ):
                    removed_count += 1
                    changed = True
                    continue
                kept_lines.append(line)

            if not changed:
                continue
            tmp_path = log_path.with_suffix(log_path.suffix + ".tmp")
            text = "\n".join(kept_lines)
            if text:
                text += "\n"
            tmp_path.write_text(text, encoding="utf-8")
            tmp_path.replace(log_path)
        return removed_count

    def delete_cameo_memory(self, group_id: str, user_id: str, log_index: int) -> bool:
        user_dir = self.get_user_dir(group_id, user_id)
        log_path = user_dir / "cameo_memory.jsonl"
        root = self.root_dir.resolve()
        target = log_path.resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"非法快照路径: {target}")
        if not log_path.exists():
            return False

        lines = log_path.read_text(encoding="utf-8").splitlines()
        if log_index < 0 or log_index >= len(lines):
            return False

        del lines[log_index]
        tmp_path = log_path.with_suffix(log_path.suffix + ".tmp")
        text = "\n".join(lines)
        if text:
            text += "\n"
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(log_path)
        return True

    # ── 内部辅助 ──────────────────────────────────

    @classmethod
    def _normalize_player_faction(cls, faction: object) -> str:
        text = str(faction or "").strip()
        return text if text in cls.PLAYER_FACTIONS else "魔法少女"

    def _create_default_player_data(self, group_id: str, user_id: str) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "group_id": str(group_id),
            "user_id": str(user_id),
            "nickname": "",
            "avatar_url": "",
            "created_at": _now_date_str(),
            "updated_at": _now_date_str(),
            "主角": {
                "等级": {"等级": 1, "经验": 0},
                "战斗": {"战斗次数": 0},
            },
        }

    @classmethod
    def _ensure_battle_count(cls, player_data: dict[str, Any]) -> int:
        protagonist = player_data.setdefault("主角", {})
        if not isinstance(protagonist, dict):
            protagonist = {}
            player_data["主角"] = protagonist
        battle_node = protagonist.setdefault("战斗", {})
        if not isinstance(battle_node, dict):
            battle_node = {}
            protagonist["战斗"] = battle_node
        count = max(0, cls._safe_int(battle_node.get("战斗次数"), 0))
        battle_node["战斗次数"] = count
        return count

    @classmethod
    def _increment_battle_count(cls, player_data: dict[str, Any]) -> int:
        count = cls._ensure_battle_count(player_data) + 1
        player_data["主角"]["战斗"]["战斗次数"] = count
        return count

    @staticmethod
    def _safe_int(value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

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
    def _update_protagonist_field(protagonist: dict, field_label: str, value: str) -> None:
        """根据字段标签尝试更新主角树中对应节点。"""
        # 映射：field 标签 → 路径
        field_path_map = {
            "姓名": ["个人信息", "姓名"],
            "性格特质": ["个人信息", "性格特质"],
            "代表色": ["个人信息", "代表色"],
            "核心能力": ["个人信息", "核心能力"],
            "使魔伙伴种类": ["个人信息", "使魔伙伴种类"],
            "使魔伙伴与主角关系": ["个人信息", "使魔伙伴与主角关系"],
            "年龄": ["个人信息", "年龄"],
            "身份&职业": ["个人信息", "身份&职业"],
            "身份/职业": ["个人信息", "身份&职业"],
            "魔法少女名": ["个人信息", "魔法少女名"],
            "武装": ["个人信息", "武装"],
            "变身服": ["个人信息", "变身服"],
            "脸型": ["相貌特征", "脸型"],
            "五官": ["相貌特征", "五官"],
            "眼睛颜色": ["相貌特征", "眼睛颜色"],
            "发型与发色": ["相貌特征", "发型与发色"],
            "特殊记号": ["相貌特征", "特殊记号"],
            "身高": ["身材细节", "身高"],
            "三围": ["身材细节", "三围"],
            "体态": ["身材细节", "体态"],
            "肌肉线条": ["身材细节", "肌肉线条"],
            "体脂率": ["身材细节", "体脂率"],
            "皮肤状态": ["身材细节", "皮肤状态"],
            "乳房形状": ["性器官特征", "乳房形状"],
            "乳晕与乳头颜色": ["性器官特征", "乳晕与乳头颜色"],
            "小穴形态": ["性器官特征", "小穴形态"],
            "体毛状况": ["性器官特征", "体毛状况"],
            "天生敏感度": ["性器官特征", "天生敏感度"],
        }
        path = field_path_map.get(field_label)
        if not path:
            return
        current = protagonist
        for key in path[:-1]:
            if key not in current or not isinstance(current.get(key), dict):
                current[key] = {}
            current = current[key]
        current[path[-1]] = value

    def _cleanup_empty_parent_dirs(self, user_dir: Path) -> None:
        for path in [user_dir.parent, user_dir.parent.parent]:
            try:
                if path.exists() and path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except Exception:
                break

    def _clear_file(self, path: Path) -> bool:
        if not path.exists():
            return False
        self._backup_source_file(path)
        path.write_text("", encoding="utf-8")
        return True

    def _build_npc_package(
        self,
        user_dir: Path,
        *,
        source: str,
        recent_record_count: int = 1,
    ) -> dict[str, Any] | None:
        player_data = self._load_current_player_data(user_dir)
        if not player_data:
            return None
        protagonist = player_data.get("主角", {})
        if not isinstance(protagonist, dict):
            return None

        target_name = self._get_nested(protagonist, ["个人信息", "姓名"], user_dir.name)
        magical_name = self._get_nested(protagonist, ["个人信息", "魔法少女名"], target_name)
        faction = self._get_nested(protagonist, ["阵营", "身份"], "魔法少女")
        level_node = protagonist.get("等级", {})
        level = level_node.get("等级", 1) if isinstance(level_node, dict) else 1
        battle_count = self._ensure_battle_count(player_data)

        log_path = user_dir / "daily_memory.jsonl"
        if not log_path.exists():
            log_path = user_dir / "battle_log.jsonl"

        return {
            "_user_id": user_dir.name,
            "_source": source,
            "target_name": target_name,
            "主角": protagonist,
            "阵营": faction,
            "姓名": target_name,
            "年龄": self._get_nested(protagonist, ["个人信息", "年龄"], ""),
            "身份&职业": self._get_nested(protagonist, ["个人信息", "身份&职业"], ""),
            "魔法少女名": magical_name,
            "武装": self._get_nested(protagonist, ["个人信息", "武装"], ""),
            "变身服": self._get_nested(protagonist, ["个人信息", "变身服"], ""),
            "性格特质": self._get_nested(protagonist, ["个人信息", "性格特质"], ""),
            "代表色": self._get_nested(protagonist, ["个人信息", "代表色"], ""),
            "核心能力": self._get_nested(protagonist, ["个人信息", "核心能力"], ""),
            "相貌特征": self._public_nested_value(protagonist.get("相貌特征")),
            "身材细节": self._public_nested_value(protagonist.get("身材细节")),
            "性器官特征": self._public_nested_value(protagonist.get("性器官特征")),
            "等级": level,
            "战斗次数": battle_count,
            "最近记录": self._read_recent_battle_summaries(
                log_path,
                limit=recent_record_count,
            ),
        }

    def build_relationship_participants_context(
        self,
        group_id: str,
        participants: list[str],
    ) -> dict[str, Any]:
        resolved = self._resolve_relationship_participants(group_id, participants)
        return {
            "participants": [
                item["profile"] if item.get("profile") else {
                    "participant_name": item["name"],
                    "resolved": False,
                }
                for item in resolved
            ],
            "existing_relationships": self._existing_relationship_summaries(resolved),
            "city_players": self._city_relationship_player_summaries(group_id),
        }

    def merge_player_relationships(
        self,
        group_id: str,
        relationships: list[dict[str, Any]],
        public_reputations: list[dict[str, Any]] | None = None,
        *,
        participants: list[str],
        battle_title: str,
        world_day_offset: int,
        world_date: str,
    ) -> int:
        resolved = self._resolve_relationship_participants(group_id, participants)
        by_name: dict[str, dict[str, Any]] = {}
        for item in resolved:
            user_dir = item.get("user_dir")
            profile = item.get("profile")
            if not user_dir or not isinstance(profile, dict):
                continue
            for name in self._relationship_names(profile):
                by_name[name] = item

        changed = 0
        now = self._now_ms()
        for relationship in relationships:
            if not isinstance(relationship, dict):
                continue
            source = str(relationship.get("from") or "").strip()
            target = str(relationship.get("to") or "").strip()
            source_item = by_name.get(source)
            target_item = by_name.get(target)
            if not source_item or not target_item or source_item is target_item:
                continue

            source_dir = source_item["user_dir"]
            target_profile = target_item["profile"]
            owner_profile = source_item["profile"]
            path = source_dir / "relationships.json"
            data = self._read_json(path)
            if not data:
                data = {
                    "schema_version": 1,
                    "owner": self._relationship_owner(owner_profile),
                    "relationships": {},
                }
            data["schema_version"] = 1
            data["owner"] = self._relationship_owner(owner_profile)
            relationships_node = data.setdefault("relationships", {})
            if not isinstance(relationships_node, dict):
                relationships_node = {}
                data["relationships"] = relationships_node

            target_key = (
                target_profile.get("magical_name")
                or target_profile.get("target_name")
                or target
            )
            current = relationships_node.get(target_key)
            if not isinstance(current, dict):
                current = {}
            history = current.get("history", [])
            if not isinstance(history, list):
                history = []

            relationship_label = str(
                relationship.get("relationship") or relationship.get("关系") or ""
            ).strip()[:12]
            impression = str(relationship.get("impression") or "").strip()
            evidence = str(relationship.get("evidence") or "").strip()
            summary = str(relationship.get("summary") or "").strip()
            tags = relationship.get("tags", [])
            if not isinstance(tags, list):
                tags = [str(tags)]
            clean_tags = []
            for tag in tags:
                text = str(tag or "").strip()
                if text and text not in clean_tags:
                    clean_tags.append(text[:20])
                if len(clean_tags) >= 4:
                    break

            history.append(
                {
                    "world_day_offset": max(0, int(world_day_offset)),
                    "world_date": world_date,
                    "battle_title": battle_title,
                    "relationship": relationship_label,
                    "impression": impression,
                    "evidence": evidence,
                }
            )
            current.update(
                {
                    "target_user_id": target_profile.get("user_id", ""),
                    "target_name": target_profile.get("target_name", ""),
                    "magical_name": target_profile.get("magical_name", ""),
                    "relationship": relationship_label,
                    "impression": impression,
                    "summary": summary,
                    "tags": clean_tags,
                    "updated_at": now,
                    "last_world_date": world_date,
                    "history": history[-20:],
                }
            )
            relationships_node[target_key] = current
            self._atomic_write_json(path, data)
            changed += 1
        changed += self._merge_public_reputations(
            public_reputations or [],
            by_name=by_name,
            battle_title=battle_title,
            world_day_offset=world_day_offset,
            world_date=world_date,
            now=now,
        )
        return changed

    def _merge_public_reputations(
        self,
        public_reputations: list[dict[str, Any]],
        *,
        by_name: dict[str, dict[str, Any]],
        battle_title: str,
        world_day_offset: int,
        world_date: str,
        now: int,
    ) -> int:
        changed = 0
        for item in public_reputations:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target") or "").strip()
            summary = str(
                item.get("public_reputation") or item.get("summary") or item.get("城市风评") or ""
            ).strip()
            if not target or not summary:
                continue
            target_item = by_name.get(target)
            if not target_item:
                continue
            user_dir = target_item["user_dir"]
            owner_profile = target_item["profile"]
            path = user_dir / "relationships.json"
            data = self._read_json(path)
            if not data:
                data = {
                    "schema_version": 1,
                    "owner": self._relationship_owner(owner_profile),
                    "relationships": {},
                }
            data["schema_version"] = 1
            data["owner"] = self._relationship_owner(owner_profile)
            tags = item.get("tags", [])
            if not isinstance(tags, list):
                tags = [str(tags)]
            clean_tags: list[str] = []
            for tag in tags:
                text = str(tag or "").strip()
                if text and text not in clean_tags:
                    clean_tags.append(text[:20])
                if len(clean_tags) >= 4:
                    break
            data["public_reputation"] = {
                "summary": summary[:360],
                "evidence": str(item.get("evidence") or "").strip()[:240],
                "tags": clean_tags,
                "updated_at": now,
                "world_day_offset": max(0, int(world_day_offset)),
                "last_world_date": world_date,
                "last_battle_title": battle_title,
            }
            self._atomic_write_json(path, data)
            changed += 1
        return changed

    def _resolve_relationship_participants(
        self,
        group_id: str,
        participants: list[str],
    ) -> list[dict[str, Any]]:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        resolved: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        seen_users: set[str] = set()
        for raw_name in participants or []:
            name = str(raw_name or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            user_dir = self._find_user_dir_by_target_name(users_dir, name)
            if user_dir and user_dir.name in seen_users:
                continue
            if user_dir:
                profile = self._build_relationship_player_profile(user_dir)
                if profile:
                    seen_users.add(user_dir.name)
                    resolved.append({"name": name, "user_dir": user_dir, "profile": profile})
                    continue
            resolved.append({"name": name, "user_dir": None, "profile": None})
        return resolved

    def _build_relationship_player_profile(self, user_dir: Path) -> dict[str, Any] | None:
        player_data = self._load_current_player_data(user_dir)
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        if not isinstance(protagonist, dict):
            return None
        target_name = self._get_nested(protagonist, ["个人信息", "姓名"], user_dir.name)
        magical_name = self._get_nested(protagonist, ["个人信息", "魔法少女名"], target_name)
        faction = self._get_nested(protagonist, ["阵营", "身份"], "魔法少女")
        level_node = protagonist.get("等级", {})
        level = level_node.get("等级", 1) if isinstance(level_node, dict) else 1
        return {
            "resolved": True,
            "user_id": user_dir.name,
            "target_name": target_name,
            "magical_name": magical_name,
            "阵营": faction,
            "武装": self._get_nested(protagonist, ["个人信息", "武装"], ""),
            "变身服": self._get_nested(protagonist, ["个人信息", "变身服"], ""),
            "性格特质": self._get_nested(protagonist, ["个人信息", "性格特质"], ""),
            "代表色": self._get_nested(protagonist, ["个人信息", "代表色"], ""),
            "核心能力": self._get_nested(protagonist, ["个人信息", "核心能力"], ""),
            "相貌特征": self._public_nested_value(protagonist.get("相貌特征")),
            "身材细节": self._public_nested_value(protagonist.get("身材细节")),
            "等级": level,
        }

    def _existing_relationship_summaries(
        self,
        resolved: list[dict[str, Any]],
    ) -> dict[str, Any]:
        summaries: dict[str, Any] = {}
        for item in resolved:
            user_dir = item.get("user_dir")
            profile = item.get("profile")
            if not user_dir or not isinstance(profile, dict):
                continue
            data = self._read_json(user_dir / "relationships.json")
            relationships = data.get("relationships", {}) if isinstance(data, dict) else {}
            if not isinstance(relationships, dict):
                relationships = {}
            owner_name = profile.get("magical_name") or profile.get("target_name") or item["name"]
            summaries[owner_name] = {
                target: {
                    "impression": value.get("impression", ""),
                    "relationship": value.get("relationship", ""),
                    "summary": value.get("summary", ""),
                    "tags": value.get("tags", []),
                    "last_world_date": value.get("last_world_date", ""),
                }
                for target, value in relationships.items()
                if isinstance(value, dict)
            }
        return summaries

    def _city_relationship_player_summaries(self, group_id: str) -> list[dict[str, Any]]:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return []

        players: list[dict[str, Any]] = []
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            profile = self._build_relationship_player_profile(user_dir)
            if not profile:
                continue
            public_reputation = self._read_public_reputation(user_dir)
            players.append(
                {
                    "user_id": profile.get("user_id", user_dir.name),
                    "target_name": profile.get("target_name", ""),
                    "magical_name": profile.get("magical_name", ""),
                    "faction": profile.get("阵营", "魔法少女"),
                    "level": profile.get("等级", 1),
                    "public_reputation": public_reputation,
                }
            )
        return players

    def _read_public_reputation(self, user_dir: Path) -> dict[str, Any]:
        data = self._read_json(user_dir / "relationships.json")
        reputation = data.get("public_reputation", {}) if isinstance(data, dict) else {}
        if not isinstance(reputation, dict):
            return {}
        return {
            "summary": str(reputation.get("summary") or ""),
            "evidence": str(reputation.get("evidence") or ""),
            "tags": reputation.get("tags") if isinstance(reputation.get("tags"), list) else [],
            "last_world_date": str(reputation.get("last_world_date") or ""),
            "last_battle_title": str(reputation.get("last_battle_title") or ""),
        }

    @staticmethod
    def _relationship_names(profile: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for key in ("target_name", "magical_name"):
            name = str(profile.get(key) or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _relationship_owner(profile: dict[str, Any]) -> dict[str, str]:
        return {
            "user_id": str(profile.get("user_id") or ""),
            "target_name": str(profile.get("target_name") or ""),
            "magical_name": str(profile.get("magical_name") or ""),
            "faction": str(profile.get("阵营") or "魔法少女"),
        }

    def _replace_protagonist_key(self, value: Any, target_name: str) -> Any:
        if isinstance(value, dict):
            replaced: dict[str, Any] = {}
            for key, child in value.items():
                next_key = target_name if key == "主角" else key
                replaced[next_key] = self._replace_protagonist_key(child, target_name)
            return replaced
        if isinstance(value, list):
            return [self._replace_protagonist_key(item, target_name) for item in value]
        return value

    def _read_recent_battle_summaries(
        self,
        path: Path,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        count = max(1, min(int(limit or 1), 5))
        records: list[dict[str, Any]] = []
        for item in reversed(self._read_recent_logs(path, limit=0)):
            if item.get("type") != "battle_diary":
                continue
            records.append(
                {
                    "world_day_offset": item.get("world_day_offset"),
                    "world_date": item.get("world_date", ""),
                    "title": item.get("title", ""),
                    "participants": item.get("participants", []),
                    "monster_name": item.get("monster_name", ""),
                    "action": item.get("action", ""),
                    "encounter": item.get("encounter", ""),
                    "result": item.get("result", ""),
                }
            )
            if len(records) >= count:
                break
        return records

    @classmethod
    def _public_nested_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            cleaned = {
                str(key): cls._public_nested_value(child)
                for key, child in value.items()
                if child not in (None, "")
            }
            return cleaned
        if isinstance(value, list):
            return [cls._public_nested_value(item) for item in value if item not in (None, "")]
        return value if value is not None else ""

    def _player_public_names(self, protagonist: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for keys in (["个人信息", "姓名"], ["个人信息", "魔法少女名"]):
            name = self._get_nested(protagonist, keys, "").strip()
            if name and name not in names:
                names.append(name)
        return names

    def _player_source_path(self, group_id: str, user_id: str, file_name: str) -> Path:
        if file_name not in self.SOURCE_FILE_NAMES:
            raise ValueError(f"不允许编辑这个存档文件: {file_name}")
        user_dir = self.get_user_dir(group_id, user_id)
        root = user_dir.resolve()
        target = (user_dir / file_name).resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"非法存档源码路径: {target}")
        return target

    @staticmethod
    def _validate_source_content(file_name: str, content: str) -> None:
        text = str(content)
        if file_name.endswith(".json"):
            data = json.loads(text or "{}")
            if not isinstance(data, dict):
                raise ValueError(f"{file_name} 必须是 JSON 对象")
            return
        if file_name.endswith(".jsonl"):
            for line_no, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise ValueError(f"{file_name} 第 {line_no} 行必须是 JSON 对象")
            return
        raise ValueError(f"不支持的存档源码类型: {file_name}")

    @staticmethod
    def _backup_source_file(path: Path) -> None:
        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_path = path.with_name(f"{path.name}.{timestamp}.bak")
            shutil.copy2(path, backup_path)
        except Exception as exc:
            logger.warning(f"备份存档源码失败: {path} {exc}")

    # ── 状态变化应用 ──────────────────────────────────

    def _apply_state_changes(
        self,
        player_data: dict[str, Any],
        changes: list[dict[str, Any]],
        *,
        teammate_names: set[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """应用当前玩家变化，返回需要写入其他玩家存档的队友变化。"""
        teammate_state_changes: dict[str, list[dict[str, Any]]] = {}
        if not isinstance(changes, list):
            return teammate_state_changes
        for change in changes:
            if not isinstance(change, dict):
                continue
            op = str(change.get("op") or "").strip()
            path = str(change.get("path") or "").strip()
            if not path.startswith("/") or self._is_location_path(path):
                continue
            if self._is_economy_path(path):
                continue
            raw_parts = path.split("/")[1:]
            first_part = (
                raw_parts[0].replace("~1", "/").replace("~0", "~")
                if raw_parts
                else ""
            )
            if teammate_names and first_part in teammate_names and len(raw_parts) > 1:
                teammate_change = dict(change)
                teammate_change["path"] = "/" + "/".join(raw_parts[1:])
                teammate_state_changes.setdefault(first_part, []).append(teammate_change)
                continue
            if path in {"/level/经验", "/等级/经验", "/主角/等级/经验"}:
                continue
            parts = self._split_change_path(path)
            if not parts:
                continue
            # 确保路径从主角节点开始
            if parts and parts[0] == "主角":
                # 已经在 player_data 中，直接操作
                pass
            elif parts and parts[0] not in {"schema_version", "group_id", "user_id",
                                           "nickname", "avatar_url", "created_at", "updated_at", "主角"}:
                # 路径不含主角前缀，自动补上
                parts = ["主角"] + parts

            if op == "+":
                self._apply_add_change(player_data, parts, change.get("value"))
            elif op == "-":
                self._apply_sub_change(player_data, parts, change.get("value"))
            elif op in {"replace", "insert"}:
                self._set_nested_value(player_data, parts, change.get("value"))
        return teammate_state_changes

    def _apply_teammate_state_changes(
        self,
        group_id: str,
        teammate_state_changes: dict[str, list[dict[str, Any]]],
    ) -> None:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return

        for teammate_name, changes in teammate_state_changes.items():
            if not teammate_name or not changes:
                continue
            target_user_dir = self._find_user_dir_by_target_name(users_dir, teammate_name)
            if not target_user_dir:
                logger.debug(f"未找到队友 {teammate_name} 的存档，跳过状态变化")
                continue
            player_data = self._load_current_player_data(target_user_dir)
            if not isinstance(player_data, dict):
                continue
            self._remove_economy_state(player_data)
            self._remove_location_state(player_data)
            self._apply_state_changes(player_data, changes)
            player_data["updated_at"] = _now_date_str()
            self._save_current_player_data(target_user_dir, player_data)
            logger.info(f"已应用队友 {teammate_name} 的状态变化: {changes}")

    @staticmethod
    def _is_economy_path(path: str) -> bool:
        return any(
            part.replace("~1", "/").replace("~0", "~") in {"gold", "金币"}
            for part in str(path or "").split("/")[1:]
        )

    @staticmethod
    def _is_location_path(path: str) -> bool:
        return any(
            part.replace("~1", "/").replace("~0", "~")
            in {"region", "location", "birth_region", "birth_location"}
            for part in str(path or "").split("/")[1:]
        )

    @classmethod
    def _remove_economy_state(cls, value: object) -> bool:
        changed = False
        if isinstance(value, dict):
            for key in list(value):
                if str(key) in {"gold", "金币"}:
                    value.pop(key, None)
                    changed = True
                    continue
                if cls._remove_economy_state(value.get(key)):
                    changed = True
        elif isinstance(value, list):
            for item in value:
                if cls._remove_economy_state(item):
                    changed = True
        return changed

    @classmethod
    def _remove_location_state(cls, value: object) -> bool:
        changed = False
        if isinstance(value, dict):
            for key in list(value):
                if str(key) in {"region", "location", "birth_region", "birth_location"}:
                    value.pop(key, None)
                    changed = True
                    continue
                if cls._remove_location_state(value.get(key)):
                    changed = True
        elif isinstance(value, list):
            for item in value:
                if cls._remove_location_state(item):
                    changed = True
        return changed

    def _find_teammate_names(self, group_id: str, protagonist: str) -> set[str]:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return set()

        protagonist = str(protagonist or "").strip()
        names: set[str] = set()
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            player_data = self._load_current_player_data(user_dir)
            if not isinstance(player_data, dict):
                continue
            protagonist_tree = player_data.get("主角", {})
            primary_name = self._get_nested(
                protagonist_tree,
                ["个人信息", "姓名"],
                "",
            )
            if primary_name and primary_name != protagonist:
                names.update(self._player_public_names(protagonist_tree))
        return names

    LEVEL_EXP_PATHS = {"/level/经验", "/等级/经验", "/主角/等级/经验"}

    def _extract_level_exp_delta(self, changes: list[dict[str, Any]]) -> int:
        if not isinstance(changes, list):
            return 0
        delta = 0
        for change in changes:
            if not isinstance(change, dict):
                continue
            op = str(change.get("op") or "").strip()
            path = str(change.get("path") or "").strip()
            if op == "+" and path in self.LEVEL_EXP_PATHS:
                delta += self._number_value(change.get("value"))
        return delta

    def _apply_teammate_level_exp(
        self,
        group_id: str,
        main_level: int,
        level_exp_delta: int,
        teammate_names: set[str],
    ) -> None:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return

        for name in teammate_names:
            if not name:
                continue
            target_user_dir = self._find_user_dir_by_target_name(users_dir, name)
            if not target_user_dir:
                logger.debug(f"未找到队友 {name} 的存档，跳过等级经验")
                continue
            player_data = self._load_current_player_data(target_user_dir)
            if not isinstance(player_data, dict):
                continue
            self._remove_economy_state(player_data)
            self._remove_location_state(player_data)

            protagonist = player_data.get("主角", {})
            level_node = protagonist.get("等级", {}) if isinstance(protagonist, dict) else {}
            teammate_level = max(1, min(int(level_node.get("等级", 1) or 1), 7))
            level_diff = main_level - teammate_level

            if level_diff > 0:
                adjusted = level_exp_delta * (level_diff + 1)
            elif level_diff < 0:
                adjusted = int(level_exp_delta / (abs(level_diff) + 1))
            else:
                adjusted = level_exp_delta

            if adjusted <= 0:
                continue

            current_exp = max(0, min(int(level_node.get("经验", 0) or 0), 99))
            new_exp = current_exp + adjusted
            level = teammate_level

            while new_exp >= 100 and level < 7:
                level += 1
                new_exp -= 100

            if level >= 7:
                level = 7
                new_exp = 0

            if isinstance(protagonist, dict):
                protagonist["等级"] = {"等级": level, "经验": max(0, min(new_exp, 99))}
            player_data["updated_at"] = _now_date_str()
            self._save_current_player_data(target_user_dir, player_data)
            logger.info(
                f"已应用队友 {name} 的等级经验: "
                f"base={level_exp_delta}, adjusted={adjusted}, "
                f"Lv.{teammate_level}->Lv.{level}"
            )

    def _increment_participant_teammate_battle_counts(
        self,
        group_id: str,
        card: BattleDiaryCard,
        *,
        protagonist_name: str,
    ) -> None:
        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return

        protagonist_name = str(protagonist_name or "").strip()
        seen_users: set[str] = set()
        for raw_name in getattr(card, "participants", []) or []:
            name = str(raw_name or "").strip()
            if not name or name == protagonist_name:
                continue
            target_user_dir = self._find_user_dir_by_target_name(users_dir, name)
            if not target_user_dir or target_user_dir.name in seen_users:
                continue
            player_data = self._load_current_player_data(target_user_dir)
            if not isinstance(player_data, dict):
                continue
            protagonist_tree = player_data.get("主角", {})
            primary_name = self._get_nested(
                protagonist_tree,
                ["个人信息", "姓名"],
                "",
            )
            if primary_name == protagonist_name:
                continue
            seen_users.add(target_user_dir.name)
            self._remove_economy_state(player_data)
            self._remove_location_state(player_data)
            self._increment_battle_count(player_data)
            player_data["updated_at"] = _now_date_str()
            self._save_current_player_data(target_user_dir, player_data)
            logger.info(f"已增加队友 {name} 的战斗次数")

    def _find_mentioned_teammate_names(
        self,
        group_id: str,
        card: BattleDiaryCard,
        mention_scan_texts: str | list[str] | None = None,
    ) -> set[str]:
        text_parts = [
            str(card.action or ""),
            str(card.encounter or ""),
            str(card.result or ""),
        ]
        if isinstance(card.reason, list):
            text_parts.extend(str(c) for c in card.reason)
        if isinstance(mention_scan_texts, list):
            text_parts.extend(str(item or "") for item in mention_scan_texts)
        elif mention_scan_texts:
            text_parts.append(str(mention_scan_texts))
        mention_text = "\n".join(text_parts)
        if not mention_text.strip():
            return set()

        users_dir = self.root_dir / "groups" / self._safe_id(group_id) / "users"
        if not users_dir.exists():
            return set()

        protagonist_name = str(card.target_name or "").strip()
        matched: set[str] = set()
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            player_data = self._load_current_player_data(user_dir)
            if not isinstance(player_data, dict):
                continue
            protagonist_tree = player_data.get("主角", {})
            names = self._player_public_names(protagonist_tree)
            primary_name = self._get_nested(protagonist_tree, ["个人信息", "姓名"], "")
            if not names or primary_name == protagonist_name:
                continue
            for name in names:
                if name in mention_text:
                    matched.add(name)
        return matched

    def _find_user_dir_by_target_name(
        self,
        users_dir: Path,
        target_name: str,
    ) -> Path | None:
        if not users_dir.exists():
            return None
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            player_data = self._load_current_player_data(user_dir)
            if isinstance(player_data, dict):
                names = self._player_public_names(player_data.get("主角", {}))
                if target_name in names:
                    return user_dir
        return None

    # ── 状态变化原语 ──────────────────────────────────

    def _apply_add_change(
        self,
        state: dict[str, Any],
        parts: list[str],
        value: object,
    ) -> None:
        delta = self._number_value(value)
        parent = self._ensure_nested_parent(state, parts)
        key = parts[-1]
        current = self._number_value(parent.get(key, 0))
        next_value = current + delta
        if key in PROGRESS_KEYS:
            self._ensure_progress_level(parent)
            next_value = self._normalize_progress_value(
                parent,
                next_value,
                max_level=5 if self._is_status_progress_path(parts) else None,
            )
        parent[key] = next_value

    def _apply_sub_change(
        self,
        state: dict[str, Any],
        parts: list[str],
        value: object,
    ) -> None:
        delta = self._number_value(value)
        parent = self._ensure_nested_parent(state, parts)
        key = parts[-1]
        current = self._number_value(parent.get(key, 0))
        next_value = current - delta
        if key in PROGRESS_KEYS:
            self._ensure_progress_level(parent)
            next_value = self._normalize_progress_value(
                parent,
                next_value,
                max_level=5 if self._is_status_progress_path(parts) else None,
            )
        parent[key] = max(0, next_value)

    def _set_nested_value(
        self,
        state: dict[str, Any],
        parts: list[str],
        value: object,
    ) -> None:
        parent = self._ensure_nested_parent(state, parts)
        key = parts[-1]
        if isinstance(parent.get(key), list) and not isinstance(value, list):
            parent[key].append(value)
            return
        parent[key] = value
        if key in PROGRESS_KEYS:
            self._ensure_progress_level(parent)
            parent[key] = self._normalize_progress_value(
                parent,
                self._number_value(value),
                max_level=5 if self._is_status_progress_path(parts) else None,
            )

    @staticmethod
    def _ensure_progress_level(parent: dict[str, Any]) -> None:
        for level_key in ("等级", "level", "Lv", "lv"):
            if level_key in parent:
                try:
                    parent[level_key] = max(1, int(float(parent.get(level_key) or 1)))
                except Exception:
                    parent[level_key] = 1
                if level_key != "等级":
                    parent["等级"] = parent[level_key]
                    parent.pop(level_key, None)
                return
        parent["等级"] = 1

    @staticmethod
    def _normalize_progress_value(
        parent: dict[str, Any],
        value: int | float,
        *,
        max_level: int | None = None,
    ) -> int | float:
        current_level = max(
            1,
            int(PlayerSaveRepository._number_value(parent.get("等级", 1))),
        )
        if max_level is not None and current_level >= max_level:
            parent["等级"] = max_level
            return 0
        next_value = max(0, value)
        while next_value >= 100:
            current_level += 1
            parent["等级"] = current_level
            next_value -= 100
            if max_level is not None and current_level >= max_level:
                parent["等级"] = max_level
                return 0
        return max(0, min(next_value, 99))

    def _normalize_status_progress_in_data(self, player_data: dict[str, Any]) -> bool:
        """规范化 player_data 中主角树下的状态进度。"""
        protagonist = player_data.get("主角", {})
        if not isinstance(protagonist, dict):
            return False
        changed = False

        def visit(value: object, path: list[str]) -> None:
            nonlocal changed
            if not isinstance(value, dict):
                return
            for key, child in list(value.items()):
                key_text = str(key)
                child_path = [*path, key_text]
                if key_text in PROGRESS_KEYS and self._is_status_progress_path(child_path):
                    before = (value.get("等级"), child)
                    self._ensure_progress_level(value)
                    value[key] = self._normalize_progress_value(
                        value,
                        self._number_value(child),
                        max_level=5,
                    )
                    if before != (value.get("等级"), value.get(key)):
                        changed = True
                    continue
                visit(child, child_path)

        visit(protagonist, [])
        return changed

    def _is_status_progress_path(self, parts: list[str]) -> bool:
        base_parts = self._status_book_base_parts()
        normalized_parts = tuple(parts)
        if normalized_parts and normalized_parts[0] == "主角":
            normalized_parts = normalized_parts[1:]
        return (
            len(normalized_parts) > len(base_parts)
            and normalized_parts[: len(base_parts)] == base_parts
        )

    def _status_book_base_parts(self) -> tuple[str, ...]:
        fallback = "/主角/快感状态/性癖/"
        editable_manager = getattr(self, "editable_manager", None)
        if editable_manager is not None:
            base_path = editable_manager.read_book_base_path(
                "fetish_book/default.json",
                fallback,
            )
        else:
            base_path = fallback
        parts = tuple(self._split_change_path(base_path))
        return parts[1:] if parts and parts[0] == "主角" else parts

    def _ensure_nested_parent(
        self,
        root: dict[str, Any],
        parts: list[str],
    ) -> dict[str, Any]:
        current = root
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        return current

    # ── JSON/JSONL 读写 ──────────────────────────────────

    def _atomic_write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning(f"读取存档 JSON 失败: {path} {exc}")
            return {}

    def _read_recent_logs(self, path: Path, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            all_lines = path.read_text(encoding="utf-8").splitlines()
            if limit and limit > 0:
                start_index = max(0, len(all_lines) - limit)
                lines = all_lines[start_index:]
            else:
                start_index = 0
                lines = all_lines
        except Exception as exc:
            logger.warning(f"读取战斗日志失败: {path} {exc}")
            return []

        logs: list[dict[str, Any]] = []
        for offset, line in enumerate(lines):
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    self._remove_location_state(item)
                    item["_log_index"] = start_index + offset
                    logs.append(item)
            except json.JSONDecodeError:
                continue
        return logs

    def _battle_ordinal_for_log(self, raw_lines: list[str], target_index: int) -> int:
        ordinal = 0
        for index, line in enumerate(raw_lines):
            if index > target_index:
                break
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "battle_summary":
                ordinal = max(ordinal, int(item.get("battle_to", 0) or 0))
            elif item.get("type") == "battle_diary":
                ordinal += 1
        return max(1, ordinal)

    def _battle_ordinal_from_for_log(self, raw_lines: list[str], target_index: int) -> int:
        try:
            item = json.loads(raw_lines[target_index])
            if isinstance(item, dict) and item.get("type") == "battle_summary":
                from_val = int(item.get("battle_from", 0) or 0)
                if from_val > 0:
                    return from_val
        except (json.JSONDecodeError, IndexError):
            pass
        return self._battle_ordinal_for_log(raw_lines, target_index)

    def _battle_ordinal_to_for_log(self, raw_lines: list[str], target_index: int) -> int:
        try:
            item = json.loads(raw_lines[target_index])
            if isinstance(item, dict) and item.get("type") == "battle_summary":
                to_val = int(item.get("battle_to", 0) or 0)
                if to_val > 0:
                    return to_val
        except (json.JSONDecodeError, IndexError):
            pass
        return self._battle_ordinal_for_log(raw_lines, target_index)

    def _cameo_ordinal_for_log(self, raw_lines: list[str], target_index: int) -> int:
        ordinal = 0
        for index, line in enumerate(raw_lines):
            if index > target_index:
                break
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "cameo_summary":
                ordinal = max(ordinal, int(item.get("interaction_to", 0) or 0))
            elif item.get("type") == "cameo_memory":
                ordinal += 1
        return max(1, ordinal)

    def _cameo_ordinal_from_for_log(self, raw_lines: list[str], target_index: int) -> int:
        try:
            item = json.loads(raw_lines[target_index])
            if isinstance(item, dict) and item.get("type") == "cameo_summary":
                from_val = int(item.get("interaction_from", 0) or 0)
                if from_val > 0:
                    return from_val
        except (json.JSONDecodeError, IndexError):
            pass
        return self._cameo_ordinal_for_log(raw_lines, target_index)

    def _cameo_ordinal_to_for_log(self, raw_lines: list[str], target_index: int) -> int:
        try:
            item = json.loads(raw_lines[target_index])
            if isinstance(item, dict) and item.get("type") == "cameo_summary":
                to_val = int(item.get("interaction_to", 0) or 0)
                if to_val > 0:
                    return to_val
        except (json.JSONDecodeError, IndexError):
            pass
        return self._cameo_ordinal_for_log(raw_lines, target_index)

    def _read_last_battle_summary(self, path: Path) -> dict[str, Any]:
        for item in reversed(self._read_recent_logs(path, limit=80)):
            if item.get("type") != "battle_diary":
                continue
            return {
                "encounter": item.get("encounter", ""),
                "result": item.get("result", ""),
                "created_at": item.get("created_at", 0),
                "world_date": item.get("world_date", ""),
            }
        return {}

    def _read_recent_cameo_memories(self, path: Path, limit: int = 5) -> list[dict[str, Any]]:
        memories = [
            {
                "type": item.get("type", ""),
                "created_at": item.get("created_at", 0),
                "source_target_name": item.get("source_target_name", ""),
                "source_name": item.get("source_name", ""),
                "source_age": item.get("source_age", ""),
                "source_identity": item.get("source_identity", ""),
                "source_magical_name": item.get("source_magical_name", ""),
                "encounter": item.get("encounter", ""),
                "result": item.get("result", ""),
                "title": item.get("title", ""),
                "world_day_offset": item.get("world_day_offset"),
                "world_date": item.get("world_date", ""),
                "world_date_from": item.get("world_date_from", ""),
                "world_date_to": item.get("world_date_to", ""),
                "world_date_unknown": item.get("world_date_unknown", False),
                "_log_index": item.get("_log_index"),
            }
            for item in self._read_recent_logs(path, limit=max(limit * 4, limit))
            if item.get("type") in ("cameo_memory", "cameo_summary")
        ]
        return memories[-limit:]

    # ── 路径解析 ──────────────────────────────────

    @staticmethod
    def _split_change_path(path: str) -> list[str]:
        return [
            part.replace("~1", "/").replace("~0", "~")
            for part in path.split("/")[1:]
            if part
        ]

    @staticmethod
    def _number_value(value: object) -> int | float:
        try:
            number = float(value or 0)
        except Exception:
            return 0
        return int(number) if number.is_integer() else number

    @staticmethod
    def _safe_id(value: object) -> str:
        text = str(value or "unknown").strip()
        text = re.sub(r"[^0-9A-Za-z_.-]+", "_", text)
        return text[:80] or "unknown"

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
