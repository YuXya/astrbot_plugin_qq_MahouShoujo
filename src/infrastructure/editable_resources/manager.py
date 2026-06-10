from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from astrbot.api.star import StarTools

from ...utils.logger import logger
from . import defaults


class EditableResourceManager:
    LEGACY_PROMPT_FILES = {
        "prompts/reincarnation_prompt.txt": "prompts/magical_girl/reincarnation_prompt.txt",
        "prompts/battle_diary_prompt.txt": "prompts/magical_girl/battle_diary_prompt.txt",
        "prompts/battle_target_selection_prompt.txt": "prompts/magical_girl/battle_target_selection_prompt.txt",
        "prompts/daily_diary_prompt.txt": "prompts/magical_girl/daily_diary_prompt.txt",
    }

    PROMPT_FILES = {
        "reincarnation_prompt": "prompts/magical_girl/reincarnation_prompt.txt",
        "battle_diary_prompt": "prompts/magical_girl/battle_diary_prompt.txt",
        "magical_battle_target_selection_prompt": "prompts/magical_girl/battle_target_selection_prompt.txt",
        "daily_diary_prompt": "prompts/magical_girl/daily_diary_prompt.txt",
        "relationship_summary_prompt": "prompts/relationship_summary_prompt.txt",
        "teammate_completion_prompt": "prompts/teammate_completion_prompt.txt",
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
    def fetish_book_path(self) -> Path:
        return self.root_dir / "fetish_book" / "default.json"

    @property
    def event_book_path(self) -> Path:
        return self.root_dir / "event_book" / "default.json"

    @property
    def monster_book_path(self) -> Path:
        return self.root_dir / "monster_book" / "default.json"

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
            "fetish_book/default.json",
            "event_book/default.json",
            "monster_book/default.json",
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
        self._migrate_status_book_to_fetish_book()
        self._migrate_prompt_directories()
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

    def _migrate_prompt_directories(self) -> None:
        for old_relative, new_relative in self.LEGACY_PROMPT_FILES.items():
            path_pairs = [
                (self._resolve(old_relative), self._resolve(new_relative)),
                (
                    self._resolve(self._note_path(old_relative)),
                    self._resolve(self._note_path(new_relative)),
                ),
            ]
            for old_path, new_path in path_pairs:
                if not old_path.exists() or new_path.exists():
                    continue
                try:
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old_path), str(new_path))
                except Exception as exc:
                    logger.warning(f"迁移 Prompt 到阵营目录失败: {old_path} -> {new_path} {exc}")

    def _migrate_status_book_to_fetish_book(self) -> None:
        status_path = self._resolve("status_book/default.json")
        fetish_path = self._resolve("fetish_book/default.json")
        if fetish_path.exists() or not status_path.exists():
            return

        try:
            fetish_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(status_path, fetish_path)
            self._backup(status_path)
            status_path.write_text(defaults.STATUS_BOOK_DEFAULT, encoding="utf-8")

            status_note_path = self._resolve(self._note_path("status_book/default.json"))
            fetish_note_path = self._resolve(self._note_path("fetish_book/default.json"))
            if status_note_path.exists() and not fetish_note_path.exists():
                fetish_note_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(status_note_path, fetish_note_path)
                self._backup(status_note_path)
                status_note_path.write_text(
                    self._default_note_map()["status_book/default.json"],
                    encoding="utf-8",
                )
        except Exception as exc:
            logger.warning(f"迁移状态书到性癖书失败: {exc}")

    def _editable_file_defs(self) -> list[dict[str, str]]:
        return [
            {
                "id": "world_book/default.json",
                "label": "世界书 default.json",
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
                "id": "skill_book/default.json",
                "label": "技能书 default.json",
                "type": "json",
                "category": "world_background",
            },
            {
                "id": "fetish_book/default.json",
                "label": "性癖书 default.json",
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
                "id": "monster_book/default.json",
                "label": "魔物书 default.json",
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
                "id": self.PROMPT_FILES["battle_diary_prompt"],
                "label": "战斗日记 Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["magical_battle_target_selection_prompt"],
                "label": "魔法少女战斗目标判断 Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["daily_diary_prompt"],
                "label": "日常日记 Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["relationship_summary_prompt"],
                "label": "人物关系总结 Prompt",
                "type": "text",
                "category": "text_completion",
            },
            {
                "id": self.PROMPT_FILES["teammate_completion_prompt"],
                "label": "队友语义识别 Prompt",
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
            "fetish_book/default.json": defaults.FETISH_BOOK_DEFAULT,
            "event_book/default.json": defaults.EVENT_BOOK_DEFAULT,
            "monster_book/default.json": defaults.MONSTER_BOOK_DEFAULT,
            self.PROMPT_FILES["reincarnation_prompt"]: defaults.REINCARNATION_PROMPT,
            self.PROMPT_FILES["battle_diary_prompt"]: defaults.BATTLE_DIARY_PROMPT,
            self.PROMPT_FILES["magical_battle_target_selection_prompt"]: defaults.MAGICAL_BATTLE_TARGET_SELECTION_PROMPT,
            self.PROMPT_FILES["daily_diary_prompt"]: defaults.DAILY_DIARY_PROMPT,
            self.PROMPT_FILES["relationship_summary_prompt"]: defaults.RELATIONSHIP_SUMMARY_PROMPT,
            self.PROMPT_FILES["teammate_completion_prompt"]: defaults.TEAMMATE_COMPLETION_PROMPT,
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
                "状态书文件。结构、触发方式和注入逻辑与世界书一致；条目命中后作为状态相关补充设定注入。"
            ),
            "fetish_book/default.json": (
                " 的百分比区间效果。已拥有性癖只会注入简介和当前进度效果；\u201c总是注入\u201d的已拥有性癖每次都会注入，"
                "未拥有时会在待开发列表附带简单介绍。"
            ),
            "event_book/default.json": (
                "Event book v3 uses categories: monster_enemy and character_enemy. "
                "Each category has an events list; allowed_commands declares usable commands. "
                "When player text explicitly asks for monsters, monster_enemy events are filtered by compatible public monster tags first."
            ),
            "monster_book/default.json": (
                "魔物书文件。公共魔物书是普通魔物敌人图鉴，普通魔物战会从这里选择本次敌人或异常源。"
            ),
            self.PROMPT_FILES["reincarnation_prompt"]: (
                "用于 /魔法少女转生 的完整 Prompt。发给 AI 的 user message 就是这个模板渲染后的结果。"
                "可用变量：{{theme}}（触发命令+玩家偏好）、{{player_text}}（目标群友昵称或ID）、"
                "{{supplement_text}}（世界书+状态书+事件书命中的补充设定，未命中时为空）。"
            ),
            self.PROMPT_FILES["battle_diary_prompt"]: (
                "用于 /魔法少女战斗 的完整 Prompt。发给 AI 的 user message 就是这个模板渲染后的结果。"
                "可用变量：{{player_data_update_json}}"
                "（来自玩家当前人物卡，包含全部属性和状态，用于第一人称人格设定和状态参考）；"
                "{{logs_text}}、{{cameo_memories_text}}、{{current_world_date}}、{{action}}、"
                "{{supplement_text}}（世界书+状态书+事件书+技能书+性癖书命中的补充设定，未命中时为空）、"
                "{{teammates_json}}（战斗中为子任务选出的队友 JSON；日常等为命中其他存档角色名时的队友公开字段 JSON；未命中时为空数组）。"
            ),
            self.PROMPT_FILES["magical_battle_target_selection_prompt"]: (
                "用于 /魔法少女战斗 正文生成前的后台目标判断。使用子任务 LLM Provider，"
                "选择 battle_type、scene_event、selected_teammates、selected_enemies，并给出 AI 侧胜率判断。"
            ),
            self.PROMPT_FILES["daily_diary_prompt"]: (
                "用于 /魔法少女日常 的完整 Prompt。变量与战斗日记 Prompt 相同，"
                "发给 AI 的 user message 就是这个模板渲染后的结果；要求仍返回兼容日记卡的纯 JSON。"
            ),
            self.PROMPT_FILES["relationship_summary_prompt"]: (
                "用于多人 /魔法少女战斗 结束后的后台人物关系总结。发给 AI 的 user message 就是这个模板渲染后的结果。"
                "可用变量：{{battle_title}}、{{world_date}}、{{participants_json}}、{{diary}}、{{encounter}}、{{result}}、"
                "{{update_changes_json}}、{{participants_profile_json}}、{{existing_relationships_json}}、{{city_players_json}}。"
                "要求 AI 返回纯 JSON，relationships 数组代表单向关系箭头，public_reputations 数组代表城市风评。"
            ),
            self.PROMPT_FILES["teammate_completion_prompt"]: (
                "用于日常正文生成前的后台日常事件上下文识别；会判断 action_target、names、scene_event 和 selected_monsters。"
                "可用变量还包括 {{scene_events_json}} 与 {{monster_candidates_json}}。"
                "用于 /魔法少女战斗 生成前的后台队友语义识别。发给 AI 的 user message 就是这个模板渲染后的结果。"
                "可用变量：{{action}}、{{player_data_update_json}}、{{logs_text}}、{{cameo_memories_text}}、{{candidates_json}}。"
                "要求 AI 返回纯 JSON，names 数组中的每一项是候选玩家名或魔法少女名。"
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
