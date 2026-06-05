from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.data_models import ReincarnationAnalysisResult


class IReincarnationAnalysisProvider(ABC):
    @abstractmethod
    async def analyze_reincarnation(
        self,
        theme: str,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
    ) -> ReincarnationAnalysisResult:
        pass
