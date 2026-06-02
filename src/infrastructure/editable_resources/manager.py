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
        "adventure_diary_system_prompt": "prompts/adventure_diary_system_prompt.txt",
        "persona_reinforcement": "prompts/persona_reinforcement.txt",
        "default_system_prompt": "prompts/default_system_prompt.txt",
        "world_book_wrapper": "prompts/world_book_wrapper.txt",
        "skill_book_wrapper": "prompts/skill_book_wrapper.txt",
        "status_book_wrapper": "prompts/status_book_wrapper.txt",
        "world_book_empty": "prompts/world_book_empty.txt",
        "region_book_wrapper": "prompts/region_book_wrapper.txt",
        "region_book_empty": "prompts/region_book_empty.txt",
    }

    def __init__(self, plugin_name: str = "astrbot_plugin_qq_adventurer"):
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
    def region_book_path(self) -> Path:
        return self.root_dir / "region_book" / "default.json"

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
            "region_book/default.json",
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
                "id": "region_book/default.json",
                "label": "区域书 default.json",
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
                "label": "冒险日记 Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["adventure_diary_system_prompt"],
                "label": "冒险日记第一人称人格 Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["persona_reinforcement"],
                "label": "人格格式优先级 Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["default_system_prompt"],
                "label": "默认 System Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["world_book_wrapper"],
                "label": "世界书包装话术",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["skill_book_wrapper"],
                "label": "技能书包装话术",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["status_book_wrapper"],
                "label": "状态书包装话术",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["world_book_empty"],
                "label": "世界书未命中话术",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["region_book_wrapper"],
                "label": "区域书包装话术",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["region_book_empty"],
                "label": "区域书未命中话术",
                "type": "text",
                "category": "text_completion",
            },
        ]

    def _default_content_map(self) -> dict[str, str]:
        return {
            "world_book/default.json": defaults.load_builtin_world_book(),
            "skill_book/default.json": defaults.SKILL_BOOK_DEFAULT,
            "status_book/default.json": defaults.STATUS_BOOK_DEFAULT,
            "region_book/default.json": defaults.REGION_BOOK_DEFAULT,
            self.PROMPT_FILES["reincarnation_prompt"]: defaults.REINCARNATION_PROMPT,
            self.PROMPT_FILES["adventure_diary_prompt"]: defaults.ADVENTURE_DIARY_PROMPT,
            self.PROMPT_FILES[
                "adventure_diary_system_prompt"
            ]: defaults.ADVENTURE_DIARY_SYSTEM_PROMPT,
            self.PROMPT_FILES["persona_reinforcement"]: defaults.PERSONA_REINFORCEMENT_PROMPT,
            self.PROMPT_FILES["default_system_prompt"]: defaults.DEFAULT_SYSTEM_PROMPT,
            self.PROMPT_FILES["world_book_wrapper"]: defaults.WORLD_BOOK_WRAPPER,
            self.PROMPT_FILES["skill_book_wrapper"]: defaults.SKILL_BOOK_WRAPPER,
            self.PROMPT_FILES["status_book_wrapper"]: defaults.STATUS_BOOK_WRAPPER,
            self.PROMPT_FILES["world_book_empty"]: defaults.WORLD_BOOK_EMPTY,
            self.PROMPT_FILES["region_book_wrapper"]: defaults.REGION_BOOK_WRAPPER,
            self.PROMPT_FILES["region_book_empty"]: defaults.REGION_BOOK_EMPTY,
        }

    def _default_note_map(self) -> dict[str, str]:
        return {
            "world_book/default.json": (
                "世界书公共设定文件。转生卡和冒险日记生成前会扫描聊天记录、玩家行动、"
                "当前位置和日志等文本，命中 always 或 keyword 条目后，把条目内容通过世界书"
                "包装话术注入主任务 Prompt。这个说明文件只用于网页提示，不会发送给 AI。"
            ),
            "skill_book/default.json": (
                "技能书文件。结构与世界书一致，冒险日记生成前会扫描玩家行动、当前位置和日志，"
                "命中条目后把技能说明注入 Prompt。base_path 是给 AI 输出 update.patches 的路径提示。"
            ),
            "status_book/default.json": (
                "状态书文件。条目标题代表全部可觉醒状态，content 是简单介绍，level_descriptions 是 Lv.1 到 Lv.Max"
                " 的分级效果。已拥有状态只会注入简介和当前等级效果；“总是注入”的已拥有状态每次都会注入，"
                "未拥有时会在待觉醒列表附带简单介绍。"
                "状态最高 Lv.5；base_path 是给 AI 输出 update.patches 的路径提示。"
            ),
            "region_book/default.json": (
                "区域书文件。按区域（如艾尔森林、碧叶镇、德尔地下城）划分条目，每个条目有简略介绍和详细介绍。"
                "玩家当前区域命中的条目返回详细介绍，其他区域命中的条目返回简略介绍。"
                "min_level 为等级门槛，max_level 为等级上限，玩家等级不足或超过上限时不命中。排序由网页上的上下位置决定。"
            ),
            self.PROMPT_FILES["reincarnation_prompt"]: (
                "用于 /异世界转生 的主任务 Prompt。它会组合触发命令、目标群友昵称或 ID、"
                "最近聊天记录、头像转述结果和世界书补充设定，然后要求 AI 输出转生人物卡 JSON。"
            ),
            self.PROMPT_FILES["adventure_diary_prompt"]: (
                "用于 /异世界冒险 的主任务 Prompt。它会组合玩家人物卡、当前状态、最近冒险日志、"
                "群级当前时间、本次行动和补充设定，然后要求 AI 输出冒险日记卡 JSON。"
                "当前时间变量为 {{current_world_date}}，补充设定变量为 {{supplement_text}}，"
                "旧变量 {{world_book_text}} 仍兼容。"
            ),
            self.PROMPT_FILES["adventure_diary_system_prompt"]: (
                "只用于 /异世界冒险 的 system_prompt。它由玩家转生人物卡中的名称、种族、职阶、"
                "外貌、性格、天赋和初醒之地渲染，要求 AI 以该角色第一人称写冒险日记，不继承 AstrBot 全局人格。"
            ),
            self.PROMPT_FILES["persona_reinforcement"]: (
                "只用于 /异世界转生 流程。系统人格确定后，会先作为 llm_generate 的 system_prompt 发送，"
                "再通过这个模板嵌入普通 Prompt 的 [SYSTEM_IDENTITY] 区域，用来强化人格与 JSON 格式优先级。"
            ),
            self.PROMPT_FILES["default_system_prompt"]: (
                "用于 /异世界转生 的默认 system_prompt。当没有插件指定人格、会话人格或全局默认人格可用时，"
                "会使用这里的内容；同一内容还会被人格格式优先级 Prompt 嵌入普通 Prompt。"
            ),
            self.PROMPT_FILES["world_book_wrapper"]: (
                "当世界书命中至少一个条目时使用。命中的条目会先格式化为列表，再填入这里的 {{entries}}。"
                "它只包装世界背景条目，不包装技能书和状态书。"
            ),
            self.PROMPT_FILES["skill_book_wrapper"]: (
                "当技能书命中至少一个条目时使用。可用变量：{{base_path}} 和 {{entries}}。"
                "最终会嵌入冒险日记主任务 Prompt 的补充设定区域。"
            ),
            self.PROMPT_FILES["status_book_wrapper"]: (
                "每次冒险日记生成时用于包装状态书内容。可用变量：{{base_path}}、{{owned_entries}}、{{pending_entries}}。"
                "{{owned_entries}} 是已拥有状态的简介与当前等级效果，{{pending_entries}} 是未拥有状态标题列表。"
            ),
            self.PROMPT_FILES["world_book_empty"]: (
                "当世界书没有命中任何条目时使用。它会作为占位文本嵌入转生卡或冒险日记的主任务 Prompt，"
                "说明本次没有额外世界书补充设定。"
            ),
            self.PROMPT_FILES["region_book_wrapper"]: (
                "当区域书命中至少一个条目时使用。可用变量：{{local_entries}}（当前区域详细设定）和 "
                "{{remote_entries}}（其他区域简略设定）。最终会嵌入冒险日记主任务 Prompt 的补充设定区域。"
            ),
            self.PROMPT_FILES["region_book_empty"]: (
                "当区域书没有命中任何条目时使用。它会作为占位文本嵌入冒险日记的主任务 Prompt，"
                "说明本次没有额外区域书补充设定。"
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
