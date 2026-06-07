from __future__ import annotations

from typing import Any

from ...domain.models.data_models import BattleDiaryExecutionResult
from ...domain.services.battle_diary_domain_service import BattleDiaryDomainService
from ...utils.logger import logger


class BattleDiaryApplicationService:
    def __init__(
        self,
        config_manager: Any,
        domain_service: BattleDiaryDomainService,
        llm_analyzer: Any,
        card_generator: Any,
        save_repository: Any,
        relationship_analyzer: Any | None = None,
    ):
        self.config_manager = config_manager
        self.domain_service = domain_service
        self.llm_analyzer = llm_analyzer
        self.card_generator = card_generator
        self.save_repository = save_repository
        self.relationship_analyzer = relationship_analyzer

    async def execute_diary(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str | None,
        action_text: str,
        umo: str | None,
        html_render_func,
        avatar_url: str | None = None,
        event_command: str = "/魔法少女战斗",
        prompt_name: str = "battle_diary_prompt",
        default_action: str = "自由战斗",
        use_villain_battle_selection: bool = False,
        identity_transition_faction: str | None = None,
    ) -> BattleDiaryExecutionResult:
        try:
            save_data = self.save_repository.load_player_save(group_id, user_id)
            if not save_data:
                return BattleDiaryExecutionResult(
                    success=False,
                    text="还没有你的魔法少女转生存档，请先使用 /魔法少女转生 建档。",
                    error="player_save_not_found",
                )

            world_day_offset = self.save_repository.get_current_world_day_offset(group_id)
            current_world_date = self.save_repository.format_world_date(world_day_offset)
            save_data = self.save_repository.load_player_save(group_id, user_id) or save_data
            player_data = save_data.get("player_data", {})
            logs = save_data.get("logs", [])
            cameo_memories = save_data.get("cameo_memories", [])
            selection_context: dict[str, object] | None = None
            if use_villain_battle_selection:
                selection_context = await self._select_villain_battle_context(
                    group_id=group_id,
                    user_id=user_id,
                    player_data=player_data,
                    logs=logs,
                    cameo_memories=cameo_memories,
                    action_text=action_text,
                    umo=umo,
                )
                target = selection_context.get("target_magical_girl") if selection_context else None
                if not isinstance(target, dict) or not target:
                    command_label = str(event_command or "/反派干部战斗").strip() or "/反派干部战斗"
                    return BattleDiaryExecutionResult(
                        success=False,
                        text=f"当前城市没有可作为目标的魔法少女存档，暂时不能发起 {command_label}。",
                        error="target_magical_girl_not_found",
                    )
                nearby_players = [target]
            else:
                mentioned_players = self.save_repository.find_mentioned_npcs(
                    group_id,
                    user_id,
                    [action_text, self._logs_text_for_teammate_scan(logs)],
                    recent_record_count=self.config_manager.get_teammate_recent_record_count(),
                )
                semantic_players = await self._infer_semantic_teammates(
                    group_id=group_id,
                    user_id=user_id,
                    player_data=player_data,
                    logs=logs,
                    cameo_memories=cameo_memories,
                    action_text=action_text,
                    umo=umo,
                )
                nearby_players = self._merge_nearby_players(mentioned_players, semantic_players)
            analysis = await self.llm_analyzer.analyze_diary(
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
            card = analysis.card

            if avatar_url:
                card.avatar_url = avatar_url

            protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
            current_level = self.domain_service.get_current_level(protagonist)
            new_level = self.domain_service.parse_level_after(
                card.level_change,
                fallback=current_level,
            )
            self.save_repository.save_battle_result(
                group_id,
                user_id,
                card,
                new_level,
                card.level_exp_after,
                world_day_offset=world_day_offset,
                mention_scan_texts=action_text,
                identity_transition_faction=identity_transition_faction,
            )
            await self._maybe_summarize_relationships(
                group_id=group_id,
                card=card,
                umo=umo,
                world_day_offset=world_day_offset,
                current_world_date=current_world_date,
            )
            await self._maybe_compress_battle_logs(
                group_id=group_id,
                user_id=user_id,
                umo=umo,
            )
            participant_players = self.save_repository.find_participant_npcs(
                group_id,
                user_id,
                getattr(card, "participants", []),
                recent_record_count=self.config_manager.get_teammate_recent_record_count(),
            )
            await self._append_cameo_memories(
                group_id=group_id,
                user_id=user_id,
                source_target_name=card.target_name,
                source_profile=self._source_profile_from_player_data(player_data, card.target_name),
                card=card,
                nearby_players=self._merge_nearby_players(participant_players, nearby_players),
                umo=umo,
                world_day_offset=world_day_offset,
                current_world_date=current_world_date,
            )
            image_path, _html = await self.card_generator.generate_diary_image_card(
                card,
                html_render_func,
            )
            if not image_path:
                return BattleDiaryExecutionResult(
                    success=False,
                    card=card,
                    text=card.to_text(),
                    error="图片渲染失败，已回退文本。",
                    raw_response=analysis.raw_response,
                )

            return BattleDiaryExecutionResult(
                success=True,
                card=card,
                image_path=image_path,
                text=card.to_text(),
                raw_response=analysis.raw_response,
            )
        except Exception as exc:
            logger.error(f"执行魔法少女战斗日记流程失败: {exc}", exc_info=True)
            return BattleDiaryExecutionResult(
                success=False,
                text=f"魔法少女战斗日记生成失败：{exc}",
                error=str(exc),
            )

    async def _maybe_summarize_relationships(
        self,
        *,
        group_id: str,
        card,
        umo: str | None,
        world_day_offset: int,
        current_world_date: str,
    ) -> None:
        if self.relationship_analyzer is None:
            return

        participants: list[str] = []
        for raw_name in getattr(card, "participants", []) or []:
            name = str(raw_name or "").strip()
            if name and name not in participants:
                participants.append(name)
        if len(participants) <= 1:
            return

        try:
            participants_context = self.save_repository.build_relationship_participants_context(
                group_id,
                participants,
            )
            relationship_result, _raw_response = await self.relationship_analyzer.analyze_relationships(
                card=card,
                participants_context=participants_context,
                umo=umo,
                world_date=current_world_date,
            )
            if isinstance(relationship_result, dict):
                relationships = relationship_result.get("relationships", [])
                public_reputations = relationship_result.get("public_reputations", [])
            else:
                relationships = relationship_result
                public_reputations = []
            changed = self.save_repository.merge_player_relationships(
                group_id,
                relationships,
                public_reputations=public_reputations,
                participants=participants,
                battle_title=card.title,
                world_day_offset=world_day_offset,
                world_date=current_world_date,
            )
            if changed:
                logger.info(f"已更新人物关系总结: group={group_id}, count={changed}")
        except Exception as exc:
            logger.warning(f"人物关系总结失败，已跳过: group={group_id} {exc}")

    async def _infer_semantic_teammates(
        self,
        *,
        group_id: str,
        user_id: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict],
        action_text: str,
        umo: str | None,
    ) -> list[dict]:
        try:
            recent_record_count = self.config_manager.get_teammate_recent_record_count()
            candidates = self.save_repository.build_city_teammate_candidates(
                group_id,
                user_id,
                recent_record_count=recent_record_count,
            )
            if not candidates:
                return []
            names = await self.llm_analyzer.infer_teammate_names(
                action_text=action_text,
                player_data=player_data,
                logs=logs,
                cameo_memories=cameo_memories,
                candidates=candidates,
                umo=umo,
            )
            if not names:
                return []
            return self.save_repository.find_npcs_by_names(
                group_id,
                user_id,
                names,
                recent_record_count=recent_record_count,
            )
        except Exception as exc:
            logger.warning(f"队友语义识别失败，已回退直接点名扫描: group={group_id} {exc}")
            return []

    async def _select_villain_battle_context(
        self,
        *,
        group_id: str,
        user_id: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict],
        action_text: str,
        umo: str | None,
    ) -> dict[str, object]:
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        current_level = self.domain_service.get_current_level(protagonist)
        recent_record_count = self.config_manager.get_teammate_recent_record_count()
        monster_candidates = self.save_repository.build_villain_monster_candidates(
            group_id,
            user_id,
            player_level=current_level,
        )
        target_candidates = self.save_repository.build_city_magical_girl_candidates(
            group_id,
            user_id,
            recent_record_count=recent_record_count,
        )
        return await self.llm_analyzer.select_villain_battle_context(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            monster_candidates=monster_candidates,
            magical_girl_candidates=target_candidates,
            umo=umo,
        )

    async def _append_cameo_memories(
        self,
        *,
        group_id: str,
        user_id: str,
        source_target_name: str,
        source_profile: dict[str, str],
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
            npc_magical_name = str(npc.get("魔法少女名") or "").strip()
            mention_names = [name for name in (npc_target_name, npc_magical_name) if name]
            if not npc_user_id or not mention_names:
                continue
            is_participant = self._is_participant_npc(card, mention_names)
            if not is_participant and not any(name in mention_text for name in mention_names):
                continue
            try:
                self.save_repository.append_cameo_memory(
                    group_id,
                    npc_user_id,
                    {
                        "source_group_id": str(group_id),
                        "source_user_id": str(user_id),
                        "source_target_name": source_target_name,
                        "source_name": source_profile.get("姓名", source_target_name),
                        "source_age": source_profile.get("年龄", ""),
                        "source_identity": source_profile.get("身份&职业", ""),
                        "source_magical_name": source_profile.get("魔法少女名", ""),
                        "npc_target_name": npc_magical_name or npc_target_name,
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

    @staticmethod
    def _is_participant_npc(card, names: list[str]) -> bool:
        participants = {
            str(name or "").strip()
            for name in getattr(card, "participants", []) or []
            if str(name or "").strip()
        }
        return any(name in participants for name in names)

    async def _maybe_compress_battle_logs(
        self,
        *,
        group_id: str,
        user_id: str,
        umo: str | None,
    ) -> None:
        interval = self.config_manager.get_diary_compress_interval()
        compress_count = self.config_manager.get_diary_compress_count()
        logs = self.save_repository.get_battle_logs_for_compression(
            group_id,
            user_id,
            interval=interval,
            compress_count=compress_count,
        )
        if not logs:
            return
        try:
            summary_text = await self.llm_analyzer.compress_battle_logs(
                logs=logs,
                umo=umo,
            )
            self.save_repository.maybe_compress_battle_logs(
                group_id,
                user_id,
                interval=interval,
                compress_count=compress_count,
                summary_text=summary_text,
            )
        except Exception as exc:
            logger.warning(f"压缩战斗日志失败，保留原始日志: {user_id} {exc}")

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

    @staticmethod
    def _source_profile_from_player_data(player_data: dict, fallback_name: str) -> dict[str, str]:
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        info = protagonist.get("个人信息", {}) if isinstance(protagonist, dict) else {}
        if not isinstance(info, dict):
            info = {}
        return {
            "姓名": str(info.get("姓名") or fallback_name or "").strip(),
            "年龄": str(info.get("年龄") or "").strip(),
            "身份&职业": str(info.get("身份&职业") or "").strip(),
            "魔法少女名": str(info.get("魔法少女名") or "").strip(),
        }

    @staticmethod
    def _logs_text_for_teammate_scan(logs: list[dict]) -> str:
        parts: list[str] = []
        for item in logs or []:
            if not isinstance(item, dict):
                continue
            for key in ("action", "encounter", "result", "title"):
                value = str(item.get(key) or "").strip()
                if value:
                    parts.append(value)
        return "\n".join(parts)
