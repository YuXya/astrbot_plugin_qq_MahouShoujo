from __future__ import annotations

from dataclasses import dataclass, field

@dataclass(frozen=True)
class EventBookEntry:
    id: str
    category_id: str = ""
    category_name: str = ""
    title: str = ""
    enabled: bool = True
    strategy: str = "keyword"
    keys: list[str] = field(default_factory=list)
    recursive: bool = True
    content: str = ""
    allowed_commands: list[str] = field(default_factory=list)
    location_tags: list[str] = field(default_factory=list)
    compatible_monsters: list[str] = field(default_factory=list)
    event_gimmick: str = ""
    success_ending: str = ""
    obstacle_ending: str = ""

    @classmethod
    def from_dict(
        cls,
        raw: dict,
        fallback_id: str,
        *,
        category_id: str = "",
        category_name: str = "",
    ) -> "EventBookEntry":
        keys = raw.get("keys", [])
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list):
            keys = []

        return cls(
            id=str(raw.get("id") or fallback_id).strip(),
            category_id=str(raw.get("category_id") or category_id or "").strip(),
            category_name=str(raw.get("category_name") or category_name or "").strip(),
            title=str(raw.get("title") or "").strip(),
            enabled=bool(raw.get("enabled", True)),
            strategy=str(raw.get("strategy") or "keyword").strip().lower(),
            keys=[str(key).strip() for key in keys if str(key).strip()],
            recursive=raw.get("recursive", True) is not False,
            content=str(raw.get("content") or "").strip(),
            allowed_commands=cls._normalize_text_list(raw.get("allowed_commands")),
            location_tags=cls._normalize_text_list(raw.get("location_tags")),
            compatible_monsters=cls._normalize_text_list(raw.get("compatible_monsters")),
            event_gimmick=str(raw.get("event_gimmick") or "").strip(),
            success_ending=str(raw.get("success_ending") or "").strip(),
            obstacle_ending=str(raw.get("obstacle_ending") or "").strip(),
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

@dataclass(frozen=True)
class EventBookEvent:
    id: str
    category_id: str = ""
    category_name: str = ""
    command: str = ""
    name: str = ""
    enabled: bool = True
    strategy: str = "keyword"
    keys: list[str] = field(default_factory=list)
    recursive: bool = True
    content: str = ""
    allowed_commands: list[str] = field(default_factory=list)
    location_tags: list[str] = field(default_factory=list)
    compatible_monsters: list[str] = field(default_factory=list)
    event_gimmick: str = ""
    success_ending: str = ""
    obstacle_ending: str = ""
    entries: list[EventBookEntry] = field(default_factory=list)

    @classmethod
    def from_dict(
        cls,
        raw: dict,
        fallback_id: str,
        *,
        category_id: str = "",
        category_name: str = "",
    ) -> "EventBookEvent":
        raw_entries = raw.get("entries", [])
        if not isinstance(raw_entries, list):
            raw_entries = []

        entries: list[EventBookEntry] = []
        for idx, raw_entry in enumerate(raw_entries):
            if not isinstance(raw_entry, dict):
                continue
            entry = EventBookEntry.from_dict(
                raw_entry,
                fallback_id=str(idx),
                category_id=category_id,
                category_name=category_name,
            )
            if entry.id and (
                entry.title
                or entry.content
                or entry.location_tags
                or entry.compatible_monsters
                or entry.event_gimmick
                or entry.success_ending
                or entry.obstacle_ending
            ):
                entries.append(entry)

        return cls(
            id=str(raw.get("id") or fallback_id).strip(),
            category_id=str(raw.get("category_id") or category_id or "").strip(),
            category_name=str(raw.get("category_name") or category_name or "").strip(),
            command=str(raw.get("command") or "").strip(),
            name=str(raw.get("name") or "").strip(),
            enabled=bool(raw.get("enabled", True)),
            strategy=str(raw.get("strategy") or "keyword").strip().lower(),
            keys=EventBookEntry._normalize_text_list(raw.get("keys")),
            recursive=raw.get("recursive", True) is not False,
            content=str(raw.get("content") or "").strip(),
            allowed_commands=EventBookEntry._normalize_text_list(
                raw.get("allowed_commands")
            ),
            location_tags=EventBookEntry._normalize_text_list(raw.get("location_tags")),
            compatible_monsters=EventBookEntry._normalize_text_list(
                raw.get("compatible_monsters")
            ),
            event_gimmick=str(raw.get("event_gimmick") or "").strip(),
            success_ending=str(raw.get("success_ending") or "").strip(),
            obstacle_ending=str(raw.get("obstacle_ending") or "").strip(),
            entries=entries,
        )

    @property
    def is_scene_event(self) -> bool:
        return bool(
            self.allowed_commands
            or self.keys
            or self.content
            or self.location_tags
            or self.compatible_monsters
            or self.event_gimmick
            or self.success_ending
            or self.obstacle_ending
        )

    def as_entry(self) -> EventBookEntry:
        return EventBookEntry(
            id=self.id,
            category_id=self.category_id,
            category_name=self.category_name,
            title=self.name,
            enabled=self.enabled,
            strategy=self.strategy,
            keys=self.keys,
            recursive=self.recursive,
            content=self.content,
            allowed_commands=self.allowed_commands or ([self.command] if self.command else []),
            location_tags=self.location_tags,
            compatible_monsters=self.compatible_monsters,
            event_gimmick=self.event_gimmick,
            success_ending=self.success_ending,
            obstacle_ending=self.obstacle_ending,
        )
