from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ReincarnationCard:
    """转生人物卡 — 基于 /主角/ 路径树格式。

    LLM 输出 info 数组，每项含 field/path/description。
    build_protagonist_tree() 将 info 数组转换为嵌套字典存入 player_data.json。
    运行时所有变更写入 player_data_update.json，player_data.json 仅在转生时写入。
    属性访问器从树中提取关键值，供文本展示和图片渲染使用。
    """
    info: list[dict[str, Any]] = field(default_factory=list)
    avatar_url: str = ""

    # ── 路径树构建 ──────────────────────────────────

    def build_protagonist_tree(self) -> dict[str, Any]:
        """从 info 数组构建 /主角/ 嵌套字典。"""
        tree: dict[str, Any] = {}
        for item in self.info:
            path = str(item.get("path", "")).strip()
            description = item.get("description", "")
            if not path.startswith("/主角/"):
                continue
            parts = path.strip("/").split("/")
            current = tree
            for part in parts[:-1]:
                if part not in current or not isinstance(current.get(part), dict):
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = description
        return tree

    # ── 便捷属性 ──────────────────────────────────

    def _get_path_value(self, path: str, default: str = "") -> str:
        for item in self.info:
            if item.get("path") == path:
                return str(item.get("description", default))
        return default

    @property
    def target_name(self) -> str:
        return self._get_path_value("/主角/个人信息/姓名", "神秘群友")

    @property
    def class_name(self) -> str:
        return self._get_path_value("/主角/个人信息/身份&职业", "见习战斗者")

    @property
    def appearance(self) -> str:
        return self._get_path_value("/主角/相貌特征/脸型", "")

    @property
    def personality(self) -> str:
        return self._get_path_value("/主角/个人信息/性格特质", "")

    @property
    def talent(self) -> str:
        return self._get_path_value("/主角/个人信息/核心能力", "")

    @property
    def likes(self) -> list[str]:
        for item in self.info:
            if item.get("field") == "代表色":
                return [str(item.get("description", "")).strip()]
        return []

    # ── 文本展示 ──────────────────────────────────

    def to_text(self) -> str:
        parts = []
        for item in self.info:
            field_name = str(item.get("field", "")).strip()
            description = str(item.get("description", "")).strip()
            if field_name and description:
                parts.append(f"{field_name}：{description}")
        return "\n".join(parts)


@dataclass
class ReincarnationAnalysisResult:
    card: ReincarnationCard
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    raw_response: str = ""


@dataclass
class BattleDiaryCard:
    title: str
    subtitle: str
    target_name: str
    action: str
    date_label: str
    diary: str
    encounter: str
    result: str
    participants: list[str] = field(default_factory=list)
    monster_name: str = ""
    reason: list[str] = field(default_factory=list)
    update_changes: list[dict[str, Any]] = field(default_factory=list)
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    footer: str = ""
    avatar_url: str = ""

    def to_text(self) -> str:
        parts = [
            f"{self.title} - {self.subtitle}".strip(" -"),
            f"战斗者：{self.target_name}",
            f"时间：{self.date_label}",
            f"行动：{self.action}",
            f"日记：{self.diary}",
            f"遭遇：{self.encounter}",
            f"结算：{self.result}",
        ]
        if self.reason:
            parts.append(f"原因：{'、'.join(self.reason)}")
        if self.footer:
            parts.append(self.footer)
        return "\n".join(part for part in parts if part)


@dataclass
class BattleDiaryAnalysisResult:
    card: BattleDiaryCard
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    raw_response: str = ""


@dataclass
class ReincarnationExecutionResult:
    success: bool
    card: ReincarnationCard | None = None
    image_path: str | None = None
    text: str = ""
    error: str | None = None
    raw_response: str = ""


@dataclass
class BattleDiaryExecutionResult:
    success: bool
    card: BattleDiaryCard | None = None
    image_path: str | None = None
    text: str = ""
    error: str | None = None
    raw_response: str = ""


@dataclass
class PatchOperation:
    op: str
    path: str
    value: Any = None


@dataclass
class ActionTurnResult:
    story_text: str
    action_options: list[str] = field(default_factory=list)
    analysis: str = ""
    json_patch: list[dict[str, Any]] = field(default_factory=list)
    raw_response: str = ""
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    phase: str = "日常"
    action: str = ""
    date_label: str = ""
    title: str = "魔法少女行动"
    footer: str = ""
    avatar_url: str = ""

    def to_text(self) -> str:
        parts = [self.story_text.strip()]
        if self.action_options:
            parts.extend(["", "【行动选项】", *self.action_options])
        if self.footer:
            parts.extend(["", self.footer])
        return "\n".join(part for part in parts if part is not None).strip()


@dataclass
class ActionTurnAnalysisResult:
    result: ActionTurnResult
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    raw_response: str = ""


@dataclass
class ActionTurnExecutionResult:
    success: bool
    result: ActionTurnResult | None = None
    image_path: str | None = None
    text: str = ""
    error: str | None = None
    raw_response: str = ""
