from __future__ import annotations

LEVEL_LABELS: tuple[str, ...] = ("F", "E", "D", "C", "B", "A", "S")
LEVEL_NUMBERS: tuple[int, ...] = tuple(range(1, len(LEVEL_LABELS) + 1))
ALL_VISIBLE_LEVELS: tuple[int, ...] = LEVEL_NUMBERS


def clamp_level(value: object, default: int = 1) -> int:
    try:
        level = int(float(value if value is not None else default))
    except Exception:
        level = default
    return max(1, min(level, len(LEVEL_LABELS)))


def level_label(value: object, default: int = 1) -> str:
    return LEVEL_LABELS[clamp_level(value, default) - 1]


def level_change_label(start_level: object, end_level: object) -> str:
    return f"{level_label(start_level)}->{level_label(end_level)}"


def parse_level_label(value: object, default: int = 1) -> int:
    text = str(value or "").strip().upper()
    if text in LEVEL_LABELS:
        return LEVEL_LABELS.index(text) + 1
    return clamp_level(value, default)


def normalize_visible_levels(raw: object, *, min_level: object = 1, max_level: object = 7) -> tuple[int, ...]:
    if isinstance(raw, list):
        levels = {
            parse_level_label(item)
            for item in raw
        }
    elif isinstance(raw, str) and raw.strip():
        levels = {
            parse_level_label(part)
            for part in raw.replace("，", ",").split(",")
            if part.strip()
        }
    else:
        low = clamp_level(min_level)
        high = clamp_level(max_level, default=7)
        if high < low:
            high = low
        levels = set(range(low, high + 1))

    normalized = tuple(level for level in LEVEL_NUMBERS if level in levels)
    return normalized or ALL_VISIBLE_LEVELS


def visible_levels_label(levels: object) -> str:
    normalized = normalize_visible_levels(levels)
    if normalized == ALL_VISIBLE_LEVELS:
        return "F-S"
    return "/".join(level_label(level) for level in normalized)
