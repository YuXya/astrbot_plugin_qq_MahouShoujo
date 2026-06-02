from __future__ import annotations

from typing import Any

from ...domain.models.data_models import AdventureDiaryExecutionResult
from ...domain.services.adventure_diary_domain_service import AdventureDiaryDomainService
from ...utils.logger import logger


class AdventureDiaryApplicationService:
    def __init__(
        self,
        config_manager: Any,
        domain_service: AdventureDiaryDomainService,
        llm_analyzer: Any,
        card_generator: Any,
        save_repository: Any,
    ):
        self.config_manager = config_manager
        self.domain_service = domain_service
        self.llm_analyzer = llm_analyzer
        self.card_generator = card_generator
        self.save_repository = save_repository

    async def execute_diary(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str | None,
        action_text: str,
        umo: str | None,
        html_render_func,
    ) -> AdventureDiaryExecutionResult:
        try:
            save_data = self.save_repository.load_player_save(group_id, user_id)
            if not save_data:
                return AdventureDiaryExecutionResult(
                    success=False,
                    text="还没有你的魔法少女转生存档，请先使用 /魔法少女转生 建档。",
                    error="player_save_not_found",
                )

            world_day_offset = self.save_repository.get_current_world_day_offset(group_id)
            current_world_date = self.save_repository.format_world_date(world_day_offset)
            save_data = self.save_repository.load_player_save(group_id, user_id) or save_data
            profile = save_data.get("profile", {})
            state = save_data.get("state", {})
            logs = save_data.get("logs", [])
            cameo_memories = save_data.get("cameo_memories", [])
            mentioned_players = self.save_repository.find_mentioned_npcs(
                group_id,
                user_id,
                action_text,
            )
            nearby_players = self._merge_nearby_players(mentioned_players)
            analysis = await self.llm_analyzer.analyze_diary(
                action_text=action_text,
                profile=profile,
                state=state,
                logs=logs,
                cameo_memories=cameo_memories,
                nearby_players=nearby_players,
                user_id=user_id,
                nickname=nickname,
                umo=umo,
                current_world_date=current_world_date,
            )
            card = analysis.card

            current_level = self.domain_service.get_current_level(state)
            new_level = self.domain_service.parse_level_after(
                card.level_change,
                fallback=current_level,
            )
            self.save_repository.save_adventure_result(
                group_id,
                user_id,
                card,
                new_level,
                card.level_exp_after,
                world_day_offset=world_day_offset,
            )
            await self._maybe_compress_adventure_logs(
                group_id=group_id,
                user_id=user_id,
                umo=umo,
            )
            await self._append_cameo_memories(
                group_id=group_id,
                user_id=user_id,
                source_target_name=card.target_name,
                card=card,
                nearby_players=nearby_players,
                umo=umo,
                world_day_offset=world_day_offset,
                current_world_date=current_world_date,
            )
            image_path, _html = await self.card_generator.generate_diary_image_card(
                card,
                html_render_func,
            )
            if not image_path:
                return AdventureDiaryExecutionResult(
                    success=False,
                    card=card,
                    text=card.to_text(),
                    error="图片渲染失败，已回退文本。",
                    raw_response=analysis.raw_response,
                )

            return AdventureDiaryExecutionResult(
                success=True,
                card=card,
                image_path=image_path,
                text=card.to_text(),
                raw_response=analysis.raw_response,
            )
        except Exception as exc:
            logger.error(f"执行魔法少女冒险日记流程失败: {exc}", exc_info=True)
            return AdventureDiaryExecutionResult(
                success=False,
                text=f"魔法少女冒险日记生成失败：{exc}",
                error=str(exc),
            )

    async def _append_cameo_memories(
        self,
        *,
        group_id: str,
        user_id: str,
        source_target_name: str,
        card,
        nearby_players: list[dict],
        umo: str | None,
        world_day_offset: int,
        current_world_date: str,
    ) -> None:
        mention_text = f"{card.encounter}\n{card.result}"
        for npc in nearby_players:
            if not isinstance(npc, dict):
                continue
            npc_user_id = str(npc.get("_user_id") or "").strip()
            npc_target_name = str(npc.get("target_name") or "").strip()
            if not npc_user_id or not npc_target_name:
                continue
            if npc_target_name not in mention_text:
                continue
            try:
                self.save_repository.append_cameo_memory(
                    group_id,
                    npc_user_id,
                    {
                        "source_group_id": str(group_id),
                        "source_user_id": str(user_id),
                        "source_target_name": source_target_name,
                        "npc_target_name": npc_target_name,
                        "encounter": card.encounter,
                        "result": card.result,
                        "title": card.title,
                        "world_day_offset": world_day_offset,
                        "world_date": current_world_date,
                    },
                )
                await self._maybe_compress_cameo_memories(
                    group_id=group_id,
                    user_id=npc_user_id,
                    umo=umo,
                )
            except Exception as exc:
                logger.warning(f"写入客串记忆失败: {npc_user_id} {exc}")

    async def _maybe_compress_adventure_logs(
        self,
        *,
        group_id: str,
        user_id: str,
        umo: str | None,
    ) -> None:
        interval = self.config_manager.get_diary_compress_interval()
        compress_count = self.config_manager.get_diary_compress_count()
        logs = self.save_repository.get_adventure_logs_for_compression(
            group_id,
            user_id,
            interval=interval,
            compress_count=compress_count,
        )
        if not logs:
            return
        try:
            summary_text = await self.llm_analyzer.compress_adventure_logs(
                logs=logs,
                umo=umo,
            )
            self.save_repository.maybe_compress_adventure_logs(
                group_id,
                user_id,
                interval=interval,
                compress_count=compress_count,
                summary_text=summary_text,
            )
        except Exception as exc:
            logger.warning(f"压缩冒险日志失败，保留原始日志: {user_id} {exc}")

    async def _maybe_compress_cameo_memories(
        self,
        *,
        group_id: str,
        user_id: str,
        umo: str | None,
    ) -> None:
        interval = self.config_manager.get_cameo_compress_interval()
        compress_count = self.config_manager.get_cameo_compress_count()
        memories = self.save_repository.get_cameo_memories_for_compression(
            group_id,
            user_id,
            interval=interval,
            compress_count=compress_count,
        )
        if not memories:
            return
        try:
            summary_text = await self.llm_analyzer.compress_cameo_memories(
                memories=memories,
                umo=umo,
            )
            self.save_repository.maybe_compress_cameo_memories(
                group_id,
                user_id,
                interval=interval,
                compress_count=compress_count,
                summary_text=summary_text,
            )
        except Exception as exc:
            logger.warning(f"压缩互动记录失败，保留原始记录: {user_id} {exc}")

    @staticmethod
    def _merge_nearby_players(*groups: list[dict]) -> list[dict]:
        merged: list[dict] = []
        by_user_id: dict[str, dict] = {}
        for group in groups:
            for npc in group or []:
                if not isinstance(npc, dict):
                    continue
                user_id = str(npc.get("_user_id") or "").strip()
                if not user_id:
                    continue
                existing = by_user_id.get(user_id)
                if existing is None:
                    next_npc = dict(npc)
                    source = str(next_npc.get("_source") or "").strip()
                    next_npc["_sources"] = [source] if source else []
                    by_user_id[user_id] = next_npc
                    merged.append(next_npc)
                    continue
                source = str(npc.get("_source") or "").strip()
                sources = existing.setdefault("_sources", [])
                if source and source not in sources:
                    sources.append(source)
                if source == "mentioned_by_action":
                    existing["_source"] = source
        return merged
