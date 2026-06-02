from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RegionBookEntry:
    id: str
    title: str = ""
    enabled: bool = True
    strategy: str = "keyword"
    keys: list[str] = field(default_factory=list)
    min_level: int = 1
    max_level: int = 100
    recursive: bool = True
    brief: str = ""
    content: str = ""

    @classmethod
    def from_dict(cls, raw: dict, fallback_id: str) -> "RegionBookEntry":
        keys = raw.get("keys", [])
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list):
            keys = []

        try:
            min_level = int(raw.get("min_level", 1))
        except (TypeError, ValueError):
            min_level = 1

        try:
            max_level = int(raw.get("max_level", 100))
        except (TypeError, ValueError):
            max_level = 100

        return cls(
            id=str(raw.get("id") or fallback_id).strip(),
            title=str(raw.get("title") or "").strip(),
            enabled=bool(raw.get("enabled", True)),
            strategy=str(raw.get("strategy") or "keyword").strip().lower(),
            keys=[str(key).strip() for key in keys if str(key).strip()],
            min_level=max(1, min_level),
            max_level=max(min_level, max_level),
            recursive=raw.get("recursive", True) is not False,
            brief=str(raw.get("brief") or "").strip(),
            content=str(raw.get("content") or "").strip(),
        )


@dataclass(frozen=True)
class RegionBookRegion:
    id: str
    name: str = ""
    entries: list[RegionBookEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict, fallback_id: str) -> "RegionBookRegion":
        raw_entries = raw.get("entries", [])
        if not isinstance(raw_entries, list):
            raw_entries = []

        entries: list[RegionBookEntry] = []
        for idx, raw_entry in enumerate(raw_entries):
            if not isinstance(raw_entry, dict):
                continue
            entry = RegionBookEntry.from_dict(raw_entry, fallback_id=str(idx))
            if entry.id and (entry.title or entry.brief or entry.content):
                entries.append(entry)

        return cls(
            id=str(raw.get("id") or fallback_id).strip(),
            name=str(raw.get("name") or "").strip(),
            entries=entries,
        )


@dataclass(frozen=True)
class RegionBookMatchResult:
    local_entries: list[RegionBookEntry]
    remote_entries: list[RegionBookEntry]
    prompt_text: str
