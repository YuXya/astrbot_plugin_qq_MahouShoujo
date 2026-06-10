from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROGRESS_KEYS = {"进度"}
HIDDEN_STATE_KEYS = {
    "schema_version", "group_id", "user_id", "updated_at",
    "nickname", "avatar_url", "created_at",
}


@dataclass(frozen=True)
class ProgressItem:
    label: str
    value: int
    percent: int


@dataclass(frozen=True)
class ProgressSections:
    skill_items: list[ProgressItem]
    status_items: list[ProgressItem]


@dataclass(frozen=True)
class _CollectedProgress:
    item: ProgressItem
    path: tuple[str, ...]


def build_progress_items(state: dict[str, Any], limit: int = 12) -> list[ProgressItem]:
    if not isinstance(state, dict):
        return []
    items = [collected.item for collected in _collect_progress_items(state)]
    return items[:limit]


def build_progress_sections(
    state: dict[str, Any],
    skill_base_path: str,
    status_base_path: str,
    limit: int = 12,
) -> ProgressSections:
    if not isinstance(state, dict):
        return ProgressSections([], [])

    skill_pattern = _base_path_pattern(skill_base_path)
    status_pattern = _base_path_pattern(status_base_path)
    skill_items: list[ProgressItem] = []
    status_items: list[ProgressItem] = []

    for collected in _collect_progress_items(state):
        parent_path = collected.path[:-1]
        skill_label = _matched_label(parent_path, skill_pattern)
        if skill_label is not None:
            skill_items.append(_with_label(collected.item, skill_label))
            continue

        status_label = _matched_label(parent_path, status_pattern)
        if status_label is not None:
            status_items.append(
                _with_label(
                    collected.item,
                    status_label,
                )
            )

    return ProgressSections(skill_items[:limit], status_items[:limit])


def build_state_display_items(state: dict[str, Any], limit: int = 24) -> list[tuple[str, str]]:
    if not isinstance(state, dict):
        return []
    items: list[tuple[str, str]] = []
    # 优先从主角树读取状态
    protagonist = state.get("主角", {})
    if isinstance(protagonist, dict) and protagonist:
        for key, value in protagonist.items():
            if key in HIDDEN_STATE_KEYS:
                continue
            _append_state_item(items, str(key), value)
    else:
        for key, value in state.items():
            if key in HIDDEN_STATE_KEYS:
                continue
            _append_state_item(items, str(key), value)
    return items[:limit]


def state_label(label: str) -> str:
    labels = {
        "hp": "HP",
        "mp": "MP",
        "inventory": "物品",
        "skills": "技能",
        "quests": "任务",
        "flags": "标记",
    }
    return labels.get(label, label)


def _collect_progress_items(
    value: object,
    path: list[str] | None = None,
) -> list[_CollectedProgress]:
    path = path or []
    items: list[_CollectedProgress] = []
    if not isinstance(value, dict):
        return items
    for key, child in value.items():
        key_text = str(key)
        if key_text in HIDDEN_STATE_KEYS:
            continue
        child_path = [*path, key_text]
        if key_text in PROGRESS_KEYS:
            items.append(
                _CollectedProgress(
                    item=ProgressItem(
                        label=_progress_label(path),
                        value=_clamp_progress(child),
                        percent=_clamp_progress(child),
                    ),
                    path=tuple(child_path),
                )
            )
            continue
        if isinstance(child, dict):
            items.extend(_collect_progress_items(child, child_path))
    return items


def _append_state_item(
    items: list[tuple[str, str]],
    label: str,
    value: object,
) -> None:
    if _is_progress_leaf(label):
        return
    if isinstance(value, dict):
        if not value:
            items.append((state_label(label), "无"))
            return
        for child_key, child_value in value.items():
            child_label = f"{label}/{child_key}"
            if _is_progress_leaf(child_label):
                continue
            _append_state_item(items, child_label, child_value)
        return
    if isinstance(value, list):
        display = "、".join(str(item) for item in value[:12]) if value else "无"
        if len(value) > 12:
            display += f" 等 {len(value)} 项"
        items.append((state_label(label), display))
        return
    items.append((state_label(label), str(value)))


def _progress_label(path: list[str]) -> str:
    for part in reversed(path):
        if part not in {"主角", "技能", "状态", "特质"}:
            return state_label(part)
    return state_label(path[-1]) if path else "进度"


def _base_path_pattern(base_path: str) -> tuple[str, ...]:
    return tuple(part for part in str(base_path or "").strip("/").split("/") if part)


def _matched_label(path: tuple[str, ...], pattern: tuple[str, ...]) -> str | None:
    path = _without_protagonist_prefix(path)
    pattern = _without_protagonist_prefix(pattern)
    if not path or not pattern:
        return None

    if len(path) > len(pattern) and path[: len(pattern)] == pattern:
        return state_label(path[len(pattern)])

    if len(path) != len(pattern):
        return None

    wildcard_index = len(pattern) - 1
    for index, (actual, expected) in enumerate(zip(path, pattern)):
        if index == wildcard_index:
            continue
        if actual != expected:
            return None
    return state_label(path[wildcard_index])


def _without_protagonist_prefix(parts: tuple[str, ...]) -> tuple[str, ...]:
    return parts[1:] if parts and parts[0] == "主角" else parts


def _with_label(item: ProgressItem, label: str) -> ProgressItem:
    return ProgressItem(
        label=label,
        value=item.value,
        percent=item.percent,
    )


def _is_progress_leaf(label: str) -> bool:
    return (
        any(label.endswith(f"/{key}") for key in PROGRESS_KEYS)
    )


def _clamp_progress(value: object) -> int:
    return max(0, min(_int_value(value, 0), 100))


def _int_value(value: object, default: int) -> int:
    try:
        return int(float(value if value is not None else default))
    except Exception:
        return default
