from __future__ import annotations

from dataclasses import dataclass, field

from ...shared.levels import ALL_VISIBLE_LEVELS, normalize_visible_levels


@dataclass(frozen=True)
class EventBookEntry:
    id: str
    title: str = ""
    enabled: bool = True
    strategy: str = "keyword"
    keys: list[str] = field(default_factory=list)
    visible_levels: tuple[int, ...] = ALL_VISIBLE_LEVELS
    recursive: bool = True
    brief: str = ""
    content: str = ""
    allowed_commands: list[str] = field(default_factory=list)
    event_tags: list[str] = field(default_factory=list)
    location_tags: list[str] = field(default_factory=list)
    compatible_monster_tags: list[str] = field(default_factory=list)
    compatible_battle_types: list[str] = field(default_factory=list)
    opening_hook: str = ""
    twist_hook: str = ""
    ending_hook: str = ""
    weight: int = 10

    @classmethod
    def from_dict(cls, raw: dict, fallback_id: str) -> "EventBookEntry":
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
            brief=str(raw.get("brief") or "").strip(),
            content=str(raw.get("content") or "").strip(),
            allowed_commands=cls._normalize_text_list(raw.get("allowed_commands")),
            event_tags=cls._normalize_text_list(raw.get("event_tags")),
            location_tags=cls._normalize_text_list(raw.get("location_tags")),
            compatible_monster_tags=cls._normalize_text_list(
                raw.get("compatible_monster_tags")
            ),
            compatible_battle_types=cls._normalize_text_list(
                raw.get("compatible_battle_types")
            ),
            opening_hook=str(raw.get("opening_hook") or "").strip(),
            twist_hook=str(raw.get("twist_hook") or "").strip(),
            ending_hook=str(raw.get("ending_hook") or "").strip(),
            weight=cls._normalize_weight(raw.get("weight")),
        )

    @staticmethod
    def _normalize_text_list(value: object) -> list[str]:
        if isinstance(value, str):
            raw_items = value.replace("，", ",").replace("、", ",").split(",")
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        items: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
        return items

    @staticmethod
    def _normalize_weight(value: object) -> int:
        try:
            return max(1, min(100, int(float(value))))
        except Exception:
            return 10


@dataclass(frozen=True)
class EventBookEvent:
    id: str
    command: str = ""
    name: str = ""
    enabled: bool = True
    strategy: str = "keyword"
    keys: list[str] = field(default_factory=list)
    visible_levels: tuple[int, ...] = ALL_VISIBLE_LEVELS
    recursive: bool = True
    brief: str = ""
    content: str = ""
    allowed_commands: list[str] = field(default_factory=list)
    event_tags: list[str] = field(default_factory=list)
    location_tags: list[str] = field(default_factory=list)
    compatible_monster_tags: list[str] = field(default_factory=list)
    compatible_battle_types: list[str] = field(default_factory=list)
    opening_hook: str = ""
    twist_hook: str = ""
    ending_hook: str = ""
    weight: int = 10
    entries: list[EventBookEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict, fallback_id: str) -> "EventBookEvent":
        raw_entries = raw.get("entries", [])
        if not isinstance(raw_entries, list):
            raw_entries = []

        entries: list[EventBookEntry] = []
        for idx, raw_entry in enumerate(raw_entries):
            if not isinstance(raw_entry, dict):
                continue
            entry = EventBookEntry.from_dict(raw_entry, fallback_id=str(idx))
            if entry.id and (
                entry.title
                or entry.brief
                or entry.content
                or entry.event_tags
                or entry.location_tags
                or entry.compatible_monster_tags
                or entry.opening_hook
                or entry.twist_hook
                or entry.ending_hook
            ):
                entries.append(entry)

        return cls(
            id=str(raw.get("id") or fallback_id).strip(),
            command=str(raw.get("command") or "").strip(),
            name=str(raw.get("name") or "").strip(),
            enabled=bool(raw.get("enabled", True)),
            strategy=str(raw.get("strategy") or "keyword").strip().lower(),
            keys=EventBookEntry._normalize_text_list(raw.get("keys")),
            visible_levels=normalize_visible_levels(
                raw.get("visible_levels"),
                min_level=raw.get("min_level", 1),
                max_level=raw.get("max_level", 7),
            ),
            recursive=raw.get("recursive", True) is not False,
            brief=str(raw.get("brief") or "").strip(),
            content=str(raw.get("content") or "").strip(),
            allowed_commands=EventBookEntry._normalize_text_list(
                raw.get("allowed_commands")
            ),
            event_tags=EventBookEntry._normalize_text_list(raw.get("event_tags")),
            location_tags=EventBookEntry._normalize_text_list(raw.get("location_tags")),
            compatible_monster_tags=EventBookEntry._normalize_text_list(
                raw.get("compatible_monster_tags")
            ),
            compatible_battle_types=EventBookEntry._normalize_text_list(
                raw.get("compatible_battle_types")
            ),
            opening_hook=str(raw.get("opening_hook") or "").strip(),
            twist_hook=str(raw.get("twist_hook") or "").strip(),
            ending_hook=str(raw.get("ending_hook") or "").strip(),
            weight=EventBookEntry._normalize_weight(raw.get("weight")),
            entries=entries,
        )

    @property
    def is_scene_event(self) -> bool:
        return bool(
            self.allowed_commands
            or self.keys
            or self.brief
            or self.content
            or self.event_tags
            or self.location_tags
            or self.compatible_monster_tags
            or self.opening_hook
            or self.twist_hook
            or self.ending_hook
        )

    def as_entry(self) -> EventBookEntry:
        return EventBookEntry(
            id=self.id,
            title=self.name,
            enabled=self.enabled,
            strategy=self.strategy,
            keys=self.keys,
            visible_levels=self.visible_levels,
            recursive=self.recursive,
            brief=self.brief,
            content=self.content,
            allowed_commands=self.allowed_commands or ([self.command] if self.command else []),
            event_tags=self.event_tags,
            location_tags=self.location_tags,
            compatible_monster_tags=self.compatible_monster_tags,
            compatible_battle_types=self.compatible_battle_types,
            opening_hook=self.opening_hook,
            twist_hook=self.twist_hook,
            ending_hook=self.ending_hook,
            weight=self.weight,
        )


@dataclass(frozen=True)
class EventBookMatchResult:
    local_entries: list[EventBookEntry]
    remote_entries: list[EventBookEntry]
    prompt_text: str
