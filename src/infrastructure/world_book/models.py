from __future__ import annotations

from dataclasses import dataclass, field

from ...shared.levels import ALL_VISIBLE_LEVELS, normalize_visible_levels


@dataclass(frozen=True)
class WorldBookEntry:
    id: str
    title: str = ""
    enabled: bool = True
    strategy: str = "keyword"
    keys: list[str] = field(default_factory=list)
    visible_levels: tuple[int, ...] = ALL_VISIBLE_LEVELS
    recursive: bool = True
    content: str = ""
    level_descriptions: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict, fallback_id: str) -> "WorldBookEntry":
        keys = raw.get("keys", [])
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list):
            keys = []

        return cls(
            id=str(raw.get("id") or fallback_id).strip(),
            title=str(raw.get("title") or "").strip(),
            enabled=bool(raw.get("enabled", True)),
            strategy=str(raw.get("strategy") or "keyword").strip().lower(),
            keys=[str(key).strip() for key in keys if str(key).strip()],
            visible_levels=normalize_visible_levels(
                raw.get("visible_levels"),
                min_level=raw.get("min_level", 1),
                max_level=raw.get("max_level", 7),
            ),
            recursive=raw.get("recursive", True) is not False,
            content=str(raw.get("content") or "").strip(),
            level_descriptions={
                str(level): str((raw.get("level_descriptions") or {}).get(str(level)) or "").strip()
                for level in range(1, 6)
            }
            if isinstance(raw.get("level_descriptions"), dict)
            else {},
        )


@dataclass(frozen=True)
class WorldBookMatchResult:
    entries: list[WorldBookEntry]
    prompt_text: str
