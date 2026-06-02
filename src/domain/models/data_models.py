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
    title: str
    subtitle: str
    target_name: str
    race: str
    class_name: str
    appearance: str
    personality: str
    talent: str
    birth_description: str = ""
    birth_region: str = ""
    birth_location: str = ""
    stats: dict[str, str] = field(default_factory=dict)
    likes: list[str] = field(default_factory=list)
    quote: str = ""
    footer: str = ""
    avatar_url: str = ""
    avatar_caption: str = ""

    def to_text(self) -> str:
        stats_text = " / ".join(
            f"{key}: {value}" for key, value in self.stats.items() if value
        )
        likes_text = "、".join(self.likes)
        parts = [
            f"{self.title} - {self.subtitle}".strip(" -"),
            f"转生对象：{self.target_name}",
            f"种族：{self.race} / 职阶：{self.class_name}",
            f"外貌：{self.appearance}",
            f"性格：{self.personality}",
            f"天赋：{self.talent}",
            f"初醒之地：{self.birth_description}",
        ]
        if stats_text:
            parts.append(f"能力值：{stats_text}")
        if likes_text:
            parts.append(f"喜欢：{likes_text}")
        if self.quote:
            parts.append(f"台词：{self.quote}")
        if self.footer:
            parts.append(self.footer)
        return "\n".join(part for part in parts if part)


@dataclass
class AdventureAnalysisResult:
    card: ReincarnationCard
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    raw_response: str = ""


@dataclass
class AdventureDiaryCard:
    title: str
    subtitle: str
    target_name: str
    action: str
    date_label: str
    region: str
    location: str
    diary: str
    encounter: str
    result: str
    level_change: str
    level_exp_after: int = 0
    stats: dict[str, str] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)
    update_patches: list[dict[str, Any]] = field(default_factory=list)
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    footer: str = ""
    avatar_url: str = ""

    def to_text(self) -> str:
        stats_text = " / ".join(
            f"{key}: {value}" for key, value in self.stats.items() if value
        )
        parts = [
            f"{self.title} - {self.subtitle}".strip(" -"),
            f"冒险者：{self.target_name}",
            f"时间：{self.date_label}",
            f"行动：{self.action}",
            f"区域：{self.region} / 地点：{self.location} / {self.level_change}",
            f"日记：{self.diary}",
            f"遭遇：{self.encounter}",
            f"结算：{self.result}",
        ]
        if stats_text:
            parts.append(f"基础四维：{stats_text}")
        if self.changes:
            parts.append(f"变化：{'、'.join(self.changes)}")
        if self.footer:
            parts.append(self.footer)
        return "\n".join(part for part in parts if part)


@dataclass
class AdventureDiaryAnalysisResult:
    card: AdventureDiaryCard
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    raw_response: str = ""


@dataclass
class AdventureExecutionResult:
    success: bool
    card: ReincarnationCard | AdventureDiaryCard | None = None
    image_path: str | None = None
    text: str = ""
    error: str | None = None
    raw_response: str = ""


@dataclass
class AdventureDiaryExecutionResult:
    success: bool
    card: AdventureDiaryCard | None = None
    image_path: str | None = None
    text: str = ""
    error: str | None = None
    raw_response: str = ""
