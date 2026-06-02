from __future__ import annotations

from ....domain.models.data_models import ReincarnationCard
from ....domain.services.adventure_domain_service import AdventureDomainService
from ...region_book import RegionBookEngine
from ...world_book import WorldBookEngine
from .base_analyzer import BaseAnalyzer


class AdventureAnalyzer(BaseAnalyzer[ReincarnationCard]):
    def __init__(
        self,
        context,
        config_manager,
        domain_service: AdventureDomainService,
        editable_manager=None,
    ):
        super().__init__(context, config_manager, editable_manager)
        self.domain_service = domain_service
        self.world_book_engine = WorldBookEngine(editable_manager=self.editable_manager)
        self.region_book_engine = RegionBookEngine(editable_manager=self.editable_manager)

    def get_data_type(self) -> str:
        return "魔法少女转生人物卡"

    def build_prompt(
        self,
        theme: str,
        user_id: str | None,
        nickname: str | None,
    ) -> str:
        player_text = (
            f"目标群友昵称：{nickname}"
            if nickname
            else f"目标群友ID：{user_id or 'unknown'}"
        )
        world_book_scan_parts = [
            theme,
            str(theme or "").lstrip("/／"),
        ]
        # --- 世界书与区域书交叉递归 ---
        world_book_result = self.world_book_engine.build_prompt_text(
            world_book_scan_parts, player_level=1,
        )
        region_book_result = self.region_book_engine.build_prompt_text(
            world_book_scan_parts,
            player_level=1,
        )
        cross_hit_parts: list[str] = []
        for entry in world_book_result.entries:
            if entry.recursive and entry.content:
                cross_hit_parts.append(entry.content)
        for entry in region_book_result.local_entries + region_book_result.remote_entries:
            if entry.recursive and entry.content:
                cross_hit_parts.append(entry.content)
        if cross_hit_parts:
            enriched_scan_parts = world_book_scan_parts + cross_hit_parts
            world_book_result = self.world_book_engine.build_prompt_text(
                enriched_scan_parts, player_level=1,
            )
            region_book_result = self.region_book_engine.build_prompt_text(
                enriched_scan_parts,
                player_level=1,
            )

        supplement_text = self._join_optional_prompt_parts([
            world_book_result.prompt_text,
            region_book_result.prompt_text,
        ])

        return self.editable_manager.render_prompt(
            "reincarnation_prompt",
            {
                "theme": theme,
                "player_text": player_text,
                "supplement_text": supplement_text,
            },
        )

    def create_data_object(
        self,
        data: dict,
    ) -> ReincarnationCard:
        return self.domain_service.normalize_card(data)

    @staticmethod
    def _join_optional_prompt_parts(parts: list[str]) -> str:
        return "\n\n".join(str(part).strip() for part in parts if str(part).strip())
