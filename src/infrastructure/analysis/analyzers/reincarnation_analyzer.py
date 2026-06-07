from __future__ import annotations

from ....domain.models.data_models import ReincarnationCard
from ....domain.services.reincarnation_domain_service import ReincarnationDomainService
from ...event_book import EventBookEngine
from ...world_book import WorldBookEngine
from .base_analyzer import BaseAnalyzer


class ReincarnationAnalyzer(BaseAnalyzer[ReincarnationCard]):
    def __init__(
        self,
        context,
        config_manager,
        domain_service: ReincarnationDomainService,
        editable_manager=None,
    ):
        super().__init__(context, config_manager, editable_manager)
        self.domain_service = domain_service
        self.world_book_engine = WorldBookEngine(editable_manager=self.editable_manager)
        self.status_book_engine = WorldBookEngine(
            book_path=self.editable_manager.status_book_path,
            editable_manager=self.editable_manager,
            display_name="状态书",
        )
        self.event_book_engine = EventBookEngine(editable_manager=self.editable_manager)

    def get_data_type(self) -> str:
        return "魔法少女转生人物卡"

    def build_prompt(
        self,
        theme: str,
        user_id: str | None,
        nickname: str | None,
        prompt_name: str = "reincarnation_prompt",
        event_command: str = "/魔法少女转生",
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
        # --- 世界书、状态书与事件书交叉递归 ---
        world_book_result = self.world_book_engine.build_prompt_text(
            world_book_scan_parts, player_level=1,
        )
        status_book_result = self.status_book_engine.build_prompt_text(
            world_book_scan_parts, player_level=1,
        )
        event_book_result = self.event_book_engine.build_prompt_text(
            world_book_scan_parts,
            current_event=event_command,
            player_level=1,
        )
        cross_hit_parts: list[str] = []
        for entry in world_book_result.entries + status_book_result.entries:
            if entry.recursive and entry.content:
                cross_hit_parts.append(entry.content)
        for entry in event_book_result.local_entries + event_book_result.remote_entries:
            if entry.recursive and entry.content:
                cross_hit_parts.append(entry.content)
        if cross_hit_parts:
            enriched_scan_parts = world_book_scan_parts + cross_hit_parts
            world_book_result = self.world_book_engine.build_prompt_text(
                enriched_scan_parts, player_level=1,
            )
            status_book_result = self.status_book_engine.build_prompt_text(
                enriched_scan_parts, player_level=1,
            )
            event_book_result = self.event_book_engine.build_prompt_text(
                enriched_scan_parts,
                current_event=event_command,
                player_level=1,
            )

        supplement_text = self._join_optional_prompt_parts([
            world_book_result.prompt_text,
            status_book_result.prompt_text,
            event_book_result.prompt_text,
        ])

        return self.editable_manager.render_prompt(
            prompt_name,
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
