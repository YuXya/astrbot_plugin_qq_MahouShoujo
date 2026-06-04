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
        )


@dataclass(frozen=True)
class EventBookEvent:
    id: str
    command: str = ""
    name: str = ""
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
            if entry.id and (entry.title or entry.brief or entry.content):
                entries.append(entry)

        return cls(
            id=str(raw.get("id") or fallback_id).strip(),
            command=str(raw.get("command") or "").strip(),
            name=str(raw.get("name") or "").strip(),
            entries=entries,
        )


@dataclass(frozen=True)
class EventBookMatchResult:
    local_entries: list[EventBookEntry]
    remote_entries: list[EventBookEntry]
    prompt_text: str
