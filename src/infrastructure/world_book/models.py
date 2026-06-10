from __future__ import annotations

from dataclasses import dataclass, field

@dataclass(frozen=True)
class WorldBookEntry:
    id: str
    title: str = ""
    enabled: bool = True
    strategy: str = "keyword"
    keys: list[str] = field(default_factory=list)
    recursive: bool = True
    content: str = ""
    percentage_descriptions: dict[str, str] = field(default_factory=dict)

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
            recursive=raw.get("recursive", True) is not False,
            content=str(raw.get("content") or "").strip(),
            percentage_descriptions={
                key: str((raw.get("percentage_descriptions") or {}).get(key) or "").strip()
                for key in ("0-20", "21-40", "41-60", "61-80", "81-100")
            }
            if isinstance(raw.get("percentage_descriptions"), dict)
            else {},
        )


@dataclass(frozen=True)
class WorldBookMatchResult:
    entries: list[WorldBookEntry]
    prompt_text: str
