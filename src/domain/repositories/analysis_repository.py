from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.data_models import AdventureAnalysisResult


class IAdventureAnalysisProvider(ABC):
    @abstractmethod
    async def analyze_adventure(
        self,
        theme: str,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
    ) -> AdventureAnalysisResult:
        pass
