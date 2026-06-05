from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models.data_models import BattleDiaryCard, ReincarnationCard


class ICardGenerator(ABC):
    @abstractmethod
    async def generate_image_card(
        self,
        card: ReincarnationCard,
        html_render_func: Any,
    ) -> tuple[str | None, str | None]:
        pass

    @abstractmethod
    async def generate_diary_image_card(
        self,
        card: BattleDiaryCard,
        html_render_func: Any,
    ) -> tuple[str | None, str | None]:
        pass
