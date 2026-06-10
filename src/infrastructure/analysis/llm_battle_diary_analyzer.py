from __future__ import annotations

from ...domain.models.data_models import BattleDiaryAnalysisResult
from ...domain.services.battle_diary_domain_service import BattleDiaryDomainService
from .analyzers.battle_diary_analyzer import BattleDiaryAnalyzer


class LLMBattleDiaryAnalyzer:
    def __init__(
        self,
        context,
        config_manager,
        domain_service: BattleDiaryDomainService,
        editable_manager=None,
    ):
        self.analyzer = BattleDiaryAnalyzer(
            context,
            config_manager,
            domain_service,
            editable_manager,
        )

    async def analyze_diary(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None = None,
        nearby_players: list[dict] | None = None,
        selection_context: dict[str, object] | None = None,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
        current_world_date: str = "",
        event_command: str = "/魔法少女战斗",
        prompt_name: str = "battle_diary_prompt",
        default_action: str = "自由战斗",
    ) -> BattleDiaryAnalysisResult:
        card, usage, raw_response = await self.analyzer.analyze_diary(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            nearby_players=nearby_players,
            selection_context=selection_context,
            user_id=user_id,
            nickname=nickname,
            umo=umo,
            current_world_date=current_world_date,
            event_command=event_command,
            prompt_name=prompt_name,
            default_action=default_action,
        )
        if card is None:
            raise ValueError("LLM 响应无法解析为魔法少女战斗日记卡 JSON")
        return BattleDiaryAnalysisResult(
            card=card,
            token_usage=usage,
            raw_response=raw_response,
        )

    async def compress_battle_logs(
        self,
        *,
        logs: list[dict],
        umo: str | None = None,
    ) -> str:
        return await self.analyzer.compress_battle_logs(logs=logs, umo=umo)

    async def compress_cameo_memories(
        self,
        *,
        memories: list[dict],
        umo: str | None = None,
    ) -> str:
        return await self.analyzer.compress_cameo_memories(memories=memories, umo=umo)

    async def infer_teammate_names(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None = None,
        candidates: list[dict] | None = None,
        umo: str | None = None,
    ) -> list[str]:
        return await self.analyzer.infer_teammate_names(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            candidates=candidates or [],
            umo=umo,
        )

    async def select_daily_context(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None = None,
        candidates: list[dict] | None = None,
        monster_candidates: list[dict] | None = None,
        event_command: str,
        umo: str | None = None,
    ) -> dict[str, object]:
        return await self.analyzer.select_daily_context(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            candidates=candidates or [],
            monster_candidates=monster_candidates or [],
            event_command=event_command,
            umo=umo,
        )

    async def select_magical_battle_context(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None = None,
        magical_girl_candidates: list[dict] | None = None,
        monster_candidates: list[dict] | None = None,
        teammate_candidates: list[dict] | None = None,
        umo: str | None = None,
    ) -> dict[str, object]:
        return await self.analyzer.select_magical_battle_context(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            magical_girl_candidates=magical_girl_candidates or [],
            monster_candidates=monster_candidates or [],
            teammate_candidates=teammate_candidates or [],
            umo=umo,
        )
