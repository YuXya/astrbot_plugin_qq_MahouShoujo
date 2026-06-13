from __future__ import annotations

import random


def resolve_battle_outcome(
    ai_win_rate: object,
    desire_win_rate: object,
    *,
    dice_roll: object = None,
    force_lose: bool = False,
) -> dict[str, object]:
    """Resolve one battle turn with a single inclusive 20-80 roll."""
    ai_rate = _clamp_percent(ai_win_rate)
    desire_rate = _clamp_percent(desire_win_rate)
    player_win_rate = round(ai_rate * 0.5 + desire_rate * 0.5)
    roll = _clamp_roll(dice_roll) if dice_roll is not None else random.randint(20, 80)
    outcome = "player_lose" if force_lose or roll > player_win_rate else "player_win"
    return {
        "player_win_rate": player_win_rate,
        "dice_roll": roll,
        "outcome": outcome,
        "ai_win_rate": ai_rate,
        "desire_win_rate": desire_rate,
    }


def _clamp_percent(value: object, *, default: int = 50) -> int:
    try:
        return max(0, min(100, int(float(value))))
    except Exception:
        return default


def _clamp_roll(value: object) -> int:
    try:
        return max(20, min(80, int(float(value))))
    except Exception:
        return 50
