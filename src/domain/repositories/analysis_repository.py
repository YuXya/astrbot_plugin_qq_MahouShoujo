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
        prompt_name: str = "reincarnation_prompt",
        event_command: str = "/魔法少女转生",
    ) -> ReincarnationAnalysisResult:
        pass
