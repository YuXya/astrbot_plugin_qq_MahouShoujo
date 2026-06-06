from __future__ import annotations

from typing import Any

from .analyzers.player_relationship_analyzer import PlayerRelationshipAnalyzer


class LLMPlayerRelationshipAnalyzer:
    def __init__(
        self,
        context,
        config_manager,
        editable_manager=None,
    ):
        self.analyzer = PlayerRelationshipAnalyzer(
            context,
            config_manager,
            editable_manager,
        )

    async def analyze_relationships(
        self,
        *,
        card,
        participants_context: dict[str, Any],
        umo: str | None = None,
        world_date: str = "",
    ) -> tuple[dict[str, Any], str]:
        return await self.analyzer.analyze_relationships(
            card=card,
            participants_context=participants_context,
            umo=umo,
            world_date=world_date,
        )
