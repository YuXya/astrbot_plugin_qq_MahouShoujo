from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from astrbot.api.star import StarTools

from ...utils.logger import logger
from . import defaults


class EditableResourceManager:
    PROMPT_FILES = {
        "reincarnation_prompt": "prompts/reincarnation_prompt.txt",
        "adventure_diary_prompt": "prompts/adventure_diary_prompt.txt",
        "teammate_info_prompt": "prompts/teammate_info_prompt.txt",
        "default_system_prompt": "prompts/default_system_prompt.txt",
    }

    def __init__(self, plugin_name: str = "astrbot_plugin_qq_MahouShoujo"):
        self.root_dir = StarTools.get_data_dir(plugin_name) / "editable"
        self.backup_dir = self.root_dir / "backups"
        self._ensure_defaults()

    @property
    def world_book_path(self) -> Path:
        return self.root_dir / "world_book" / "default.json"

    @property
    def skill_book_path(self) -> Path:
        return self.root_dir / "skill_book" / "default.json"

    @property
    def status_book_path(self) -> Path:
        return self.root_dir / "status_book" / "default.json"

    @property
    def event_book_path(self) -> Path:
        return self.root_dir / "event_book" / "default.json"

    def get_prompt(self, name: str) -> str:
        relative = self.PROMPT_FILES[name]
        return self.read_text(relative)

    def render_prompt(self, name: str, variables: dict[str, object]) -> str:
        text = self.get_prompt(name)
        return self.render_text(text, variables)

    @staticmethod
    def render_text(text: str, variables: dict[str, object]) -> str:
        rendered = str(text)
        for key, value in variables.items():
            rendered = rendered.replace("{{" + key + "}}", str(value))
        return rendered

    def read_text(self, relative_path: str) -> str:
        path = self._resolve(relative_path)
        try:
            return path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"读取可编辑资源失败: {path} {exc}")
            return ""

    def write_text(self, relative_path: str, content: str) -> None:
        path = self._resolve(relative_path)
        if path.exists():
            self._backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")

    def write_world_book(self, content: str) -> None:
        json.loads(content)
        self.write_text("world_book/default.json", content)

    def write_json_book(self, relative_path: str, content: str) -> None:
        json.loads(content)
        self.write_text(relative_path, content)

    def read_book_base_path(self, relative_path: str, fallback: str) -> str:
        try:
            data = json.loads(self.read_text(relative_path))
            if isinstance(data, dict):
                base_path = str(data.get("base_path") or "").strip()
                if base_path:
                    return base_path
        except Exception as exc:
            logger.warning(f"读取书籍 base_path 失败: {relative_path} {exc}")
        return fallback

    def read_book_display_name(self, relative_path: str, fallback: str) -> str:
        try:
            data = json.loads(self.read_text(relative_path))
            if isinstance(data, dict):
                display_name = str(data.get("display_name") or "").strip()
                if display_name:
                    return display_name
        except Exception as exc:
            logger.warning(f"读取书籍 display_name 失败: {relative_path} {exc}")
        return fallback

    def read_note(self, relative_path: str) -> str:
        if relative_path not in self._default_note_map():
            raise ValueError(f"资源没有说明: {relative_path}")
        return self.read_text(self._note_path(relative_path))

    def write_note(self, relative_path: str, content: str) -> None:
        if relative_path not in self._default_note_map():
            raise ValueError(f"资源没有说明: {relative_path}")
        self.write_text(self._note_path(relative_path), content)

    def reset_note_to_default(self, relative_path: str) -> None:
        notes_map = self._default_note_map()
        if relative_path not in notes_map:
            raise ValueError(f"资源没有默认说明: {relative_path}")
        self.write_note(relative_path, notes_map[relative_path])

    def get_default_note(self, relative_path: str) -> str:
        notes_map = self._default_note_map()
        if relative_path not in notes_map:
            raise ValueError(f"资源没有默认说明: {relative_path}")
        return notes_map[relative_path]

    def reset_to_default(self, relative_path: str) -> None:
        defaults_map = self._default_content_map()
        if relative_path not in defaults_map:
            raise ValueError(f"资源没有默认内容: {relative_path}")

        content = defaults_map[relative_path]
        if relative_path in {
            "world_book/default.json",
            "skill_book/default.json",
            "status_book/default.json",
            "event_book/default.json",
        }:
            json.loads(content)
        self.write_text(relative_path, content)

    def get_default_text(self, relative_path: str) -> str:
        defaults_map = self._default_content_map()
        if relative_path not in defaults_map:
            raise ValueError(f"资源没有默认内容: {relative_path}")
        return defaults_map[relative_path]

    def list_editable_files(self) -> list[dict[str, str]]:
        files = []
        for item in self._editable_file_defs():
            note = self.read_note(item["id"])
            preview = " ".join(note.split())
            files.append(
                {
                    **item,
                    "note": note,
                    "note_preview": preview[:180],
                }
            )
        return files

    def _ensure_defaults(self) -> None:
        for relative, content in self._default_content_map().items():
            path = self._resolve(relative)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        for relative, content in self._default_note_map().items():
            path = self._resolve(self._note_path(relative))
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

    def _editable_file_defs(self) -> list[dict[str, str]]:
        return [
            {
                "id": "world_book/default.json",
                "label": "世界书 default.json",
                "type": "json",
                "category": "world_background",
            },
            {
                "id": "skill_book/default.json",
                "label": "技能书 default.json",
                "type": "json",
                "category": "world_background",
            },
            {
                "id": "status_book/default.json",
                "label": "状态书 default.json",
                "type": "json",
                "category": "world_background",
            },
            {
                "id": "event_book/default.json",
                "label": "事件书 default.json",
                "type": "json",
                "category": "world_background",
            },
            {
                "id": self.PROMPT_FILES["reincarnation_prompt"],
                "label": "转生卡 Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["adventure_diary_prompt"],
                "label": "战斗日记 Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["teammate_info_prompt"],
                "label": "多人发送的队友信息",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["default_system_prompt"],
                "label": "默认 System Prompt",
                "type": "text",
                "category": "text_completion",
            },
        ]

    def _default_content_map(self) -> dict[str, str]:
        return {
            "world_book/default.json": defaults.WORLD_BOOK_DEFAULT,
            "skill_book/default.json": defaults.SKILL_BOOK_DEFAULT,
            "status_book/default.json": defaults.STATUS_BOOK_DEFAULT,
            "event_book/default.json": defaults.EVENT_BOOK_DEFAULT,
            self.PROMPT_FILES["reincarnation_prompt"]: defaults.REINCARNATION_PROMPT,
            self.PROMPT_FILES["adventure_diary_prompt"]: defaults.ADVENTURE_DIARY_PROMPT,
            self.PROMPT_FILES["teammate_info_prompt"]: defaults.TEAMMATE_INFO_PROMPT,
            self.PROMPT_FILES["default_system_prompt"]: defaults.DEFAULT_SYSTEM_PROMPT,
        }

    def _default_note_map(self) -> dict[str, str]:
        return {
            "world_book/default.json": (
                "世界书公共设定文件。转生卡和战斗日记生成前会扫描玩家偏好、玩家行动、"
                "日志等文本，命中 always 或 keyword 条目后，把条目内容注入主任务 Prompt。"
                "这个说明文件只用于网页提示，不会发送给 AI。"
            ),
            "skill_book/default.json": (
                "技能书文件。结构与世界书一致，战斗日记生成前会扫描玩家行动和日志，"
                "命中条目后把技能说明注入 Prompt。base_path 是给 AI 输出 update.changes 的路径提示。"
            ),
            "status_book/default.json": (
                "状态书文件。条目标题代表全部可觉醒状态，content 是简单介绍，level_descriptions 是 Lv.1 到 Lv.Max"
                " 的分级效果。已拥有状态只会注入简介和当前等级效果；\u201c总是注入\u201d的已拥有状态每次都会注入，"
                "未拥有时会在待觉醒列表附带简单介绍。"
                "状态最高 Lv.5；base_path 是给 AI 输出 update.changes 的路径提示。"
            ),
            "event_book/default.json": (
                "事件书文件。按 /魔法少女转生、/魔法少女战斗、/魔法少女日常 分组。"
                "当前事件内关键词命中或 always 条目会注入详细介绍；其他事件只在关键词命中且简略介绍不为空时注入简略介绍。"
                "visible_levels 为可见主角等级，数字 1-7 对应 F、E、D、C、B、A、S；未填写时默认全部可见。排序由网页上的上下位置决定。"
            ),
            self.PROMPT_FILES["reincarnation_prompt"]: (
                "用于 /魔法少女转生 的完整 Prompt。发给 AI 的 user message 就是这个模板渲染后的结果。"
                "可用变量：{{theme}}（触发命令+玩家偏好）、{{player_text}}（目标群友昵称或ID）、"
                "{{supplement_text}}（世界书+事件书命中的补充设定，未命中时为空）。"
            ),
            self.PROMPT_FILES["adventure_diary_prompt"]: (
                "用于 /魔法少女战斗 的完整 Prompt。发给 AI 的 user message 就是这个模板渲染后的结果。"
                "可用变量：{{player_data_update_json}}"
                "（来自玩家当前人物卡，包含全部属性和状态，用于第一人称人格设定和状态参考）；"
                "{{player_name}}、{{current_level}}（字母等级 F/E/D/C/B/A/S）、"
                "{{logs_text}}、{{cameo_memories_text}}、{{current_world_date}}、{{action}}、"
                "{{supplement_text}}（世界书+事件书+技能书+状态书命中的补充设定，未命中时为空）、"
                "{{teammate_info_text}}（命中其他存档角色名时，由“多人发送的队友信息”模板渲染，未命中时为空）。"
            ),
            self.PROMPT_FILES["teammate_info_prompt"]: (
                "用于 /魔法少女战斗 的队友信息片段。只有本次行动或主角近期日志提到同群其他存档角色名时才会注入。"
                "可用变量：{{teammates_json}}（队友公开字段和最近记录 JSON）、"
                "{{teammate_count}}（队友数量）、{{recent_record_count}}（每名队友最近记录条数）。"
            ),
            self.PROMPT_FILES["default_system_prompt"]: (
                "用于 /魔法少女转生 和 /魔法少女战斗 的 system message。"
                "你可以在这里定义 AI 的基础人格和行为准则。"
            ),
        }

    @staticmethod
    def _note_path(relative_path: str) -> str:
        return f"{relative_path}.note.txt"

    def _resolve(self, relative_path: str) -> Path:
        path = (self.root_dir / relative_path).resolve()
        root = self.root_dir.resolve()
        if root != path and root not in path.parents:
            raise ValueError(f"非法资源路径: {relative_path}")
        return path

    def _backup(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.root_dir)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_path = self.backup_dir / relative.parent / f"{path.name}.{timestamp}.bak"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_path)
        except Exception as exc:
            logger.warning(f"备份可编辑资源失败: {path} {exc}")
