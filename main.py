from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .src.application.services.reincarnation_application_service import (
    ReincarnationApplicationService,
)
from .src.application.services.action_turn_application_service import (
    ActionTurnApplicationService,
)
from .src.domain.services.battle_diary_domain_service import (
    BattleDiaryDomainService,
)
from .src.domain.services.reincarnation_domain_service import ReincarnationDomainService
from .src.infrastructure.analysis.llm_reincarnation_analyzer import LLMReincarnationAnalyzer
from .src.infrastructure.analysis.llm_action_turn_analyzer import (
    LLMActionTurnAnalyzer,
)
from .src.infrastructure.analysis.llm_player_relationship_analyzer import (
    LLMPlayerRelationshipAnalyzer,
)
from .src.infrastructure.analysis.llm_memory_summary_analyzer import LLMMemorySummaryAnalyzer
from .src.infrastructure.config.config_manager import ConfigManager
from .src.infrastructure.editable_resources import EditableResourceManager
from .src.infrastructure.messaging.avatar_service import QQAvatarService
from .src.infrastructure.messaging.message_sender import MessageSender
from .src.infrastructure.reporting.generators import ReportGenerator
from .src.infrastructure.storage import PlayerSaveRepository, PlayerTaskQueue
from .src.infrastructure.web import SaveWebViewer
from .src.utils.logger import logger

REINCARNATION_FIELDS = [
    "姓名", "性格特质", "初始性癖", "使魔种类",
    "与主角关系", "代表色", "核心能力", "外貌描述", "其他设定",
]

REINCARNATION_TEMPLATE = (
    "/魔法少女转生\n"
    "姓名：\n"
    "性格特质：\n"
    "初始性癖：\n"
    "使魔种类：\n"
    "与主角关系：\n"
    "代表色：\n"
    "核心能力：\n"
    "外貌描述：\n"
    "其他设定："
)

PLAYER_PROFILE_PANEL_URL = "https://www.youxiajiang.com/Games/AIBot/"


class QQMahouShoujo(Star):
    config: AstrBotConfig
    config_manager: ConfigManager
    domain_service: ReincarnationDomainService
    diary_domain_service: BattleDiaryDomainService
    editable_manager: EditableResourceManager
    llm_analyzer: LLMReincarnationAnalyzer
    action_turn_llm_analyzer: LLMActionTurnAnalyzer
    relationship_llm_analyzer: LLMPlayerRelationshipAnalyzer
    memory_summary_llm_analyzer: LLMMemorySummaryAnalyzer
    report_generator: ReportGenerator
    reincarnation_service: ReincarnationApplicationService
    action_turn_service: ActionTurnApplicationService
    avatar_service: QQAvatarService
    message_sender: MessageSender
    save_repository: PlayerSaveRepository
    player_queue: PlayerTaskQueue
    web_viewer: SaveWebViewer

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.config_manager = ConfigManager(config)
        self.editable_manager = EditableResourceManager()
        self.domain_service = ReincarnationDomainService()
        self.diary_domain_service = BattleDiaryDomainService()
        self.llm_analyzer = LLMReincarnationAnalyzer(
            context,
            self.config_manager,
            self.domain_service,
            self.editable_manager,
        )
        self.action_turn_llm_analyzer = LLMActionTurnAnalyzer(
            context,
            self.config_manager,
            self.diary_domain_service,
            self.editable_manager,
        )
        self.relationship_llm_analyzer = LLMPlayerRelationshipAnalyzer(
            context,
            self.config_manager,
            self.editable_manager,
        )
        self.memory_summary_llm_analyzer = LLMMemorySummaryAnalyzer(
            context,
            self.config_manager,
            self.editable_manager,
        )
        self.report_generator = ReportGenerator(self.config_manager, self.editable_manager)
        self.reincarnation_service = ReincarnationApplicationService(
            self.config_manager,
            self.domain_service,
            self.llm_analyzer,
            self.report_generator,
        )
        self.avatar_service = QQAvatarService()
        self.message_sender = MessageSender()
        self.save_repository = PlayerSaveRepository(
            editable_manager=self.editable_manager,
        )
        self.action_turn_service = ActionTurnApplicationService(
            self.config_manager,
            self.action_turn_llm_analyzer,
            self.save_repository,
            self.report_generator,
            memory_summary_analyzer=self.memory_summary_llm_analyzer,
        )
        self.player_queue = PlayerTaskQueue()
        self.web_viewer = SaveWebViewer(
            self.save_repository,
            self.editable_manager,
            host=self.config_manager.get_web_host(),
            port=self.config_manager.get_web_port(),
            public_path_prefix=self.config_manager.get_web_public_path_prefix(),
        )
        self._schedule_web_viewer_start()

    def _schedule_web_viewer_start(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._start_web_viewer())

    async def initialize(self) -> None:
        await self._start_web_viewer()

    async def _start_web_viewer(self) -> None:
        try:
            await self.web_viewer.start()
        except Exception as exc:
            logger.warning(f"魔法少女存档网页自动启动失败: {exc}")

    @filter.command("魔法少女帮助", alias={"mahoushoujo_help"})
    async def mahoushoujo_help(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator:
        """显示魔法少女插件的新手指引。用法：/魔法少女帮助"""
        event.should_call_llm(False)

        if not self._is_group_event_allowed(event):
            return

        yield event.plain_result(
            "\n".join(
                [
                    "魔法少女指令",
                    "",
                    "/魔法少女转生：创建魔法少女角色档案。",
                    "/魔法少女行动：进行一次酒馆式行动回合，可追加行动。",
                    "/魔法少女存档删除 确认：删除当前群自己的存档。",
                    "",
                    f"角色档案面板：{self._build_player_profile_panel_url()}",
                ]
            )
        )

    @filter.command("魔法少女存档删除", alias={"mahoushoujo_delete_save"})
    async def delete_mahoushoujo_save(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator:
        """删除触发者在当前群的魔法少女存档。用法：/魔法少女存档删除 确认"""
        event.should_call_llm(False)

        if not self._is_group_event_allowed(event):
            return

        group_id = self._get_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("请在群聊中使用 /魔法少女存档删除。")
            return

        user_id = self._get_sender_id_from_event(event)
        if not user_id:
            yield event.plain_result("没有拿到你的 QQ 号，暂时不能删除玩家存档。")
            return

        confirm_text = self._extract_command_tail(event, "魔法少女存档删除")
        if confirm_text != "确认":
            yield event.plain_result(
                "\n".join(
                    [
                        "这是不可逆操作，会删除你在当前群的魔法少女存档。",
                        "同时会清理其他玩家记忆中由你产生的客串记录。",
                        "确认删除请发送：/魔法少女存档删除 确认",
                    ]
                )
            )
            return

        if await self.player_queue.is_locked(group_id, user_id):
            yield event.plain_result("你的上一条魔法少女请求还在处理，删除请求已进入队列。")

        async with self.player_queue.lock_for(group_id, user_id):
            deleted = self.save_repository.delete_player_save(group_id, user_id)

        if deleted:
            yield event.plain_result("存档已删除，其他玩家记忆中由你产生的客串记录也已清理。")
        else:
            yield event.plain_result("没有找到你的魔法少女存档。")

    @filter.command("魔法少女转生", alias={"reincarnate"})
    async def reincarnate(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator:
        """生成一张魔法少女转生人物卡。用法：/魔法少女转生"""
        async for result in self._handle_reincarnation(
            event,
            command_name="魔法少女转生",
            event_command="/魔法少女转生",
            template=REINCARNATION_TEMPLATE,
            fields=REINCARNATION_FIELDS,
            prompt_name="reincarnation_prompt",
            card_label="魔法少女转生",
        ):
            yield result

    async def _handle_reincarnation(
        self,
        event: AstrMessageEvent,
        *,
        command_name: str,
        event_command: str,
        template: str,
        fields: list[str],
        prompt_name: str,
        card_label: str,
    ) -> AsyncGenerator:
        """处理转生建档命令；不同主题只替换命令名、模板和 Prompt。"""

        if not self._is_group_event_allowed(event):
            return

        group_id = self._get_group_id_from_event(event)
        if not group_id:
            yield event.plain_result(f"请在群聊中使用 {event_command}。")
            return

        user_id = self._get_sender_id_from_event(event)
        if not user_id:
            yield event.plain_result("没有拿到你的 QQ 号，暂时不能创建玩家存档。")
            return

        if await self.player_queue.is_locked(group_id, user_id):
            yield event.plain_result("你的上一条魔法少女请求还在处理，已经进入队列，马上轮到你。")

        # 提取命令后的完整文本（支持多行格式）
        raw_text = self._extract_reincarnation_text(event, command_name)

        # 解析结构化字段
        parsed_fields = self._parse_reincarnation_fields(raw_text, fields)
        filled_count = len(parsed_fields)

        # 至少需要 3 个字段，否则返回模板
        if filled_count < 3:
            event.should_call_llm(False)
            if raw_text and filled_count > 0:
                message = (
                    f"填写信息不足（已填写 {filled_count}/{len(fields)} 项，至少需要 3 项），"
                    f"请按以下格式重新发送：\n\n{template}"
                )
            else:
                message = f"请按以下格式填写转生信息(最少填写三个字段)：\n\n{template}"
            yield event.plain_result(message)
            return

        # 通过验证，将结构化字段拼接为 preference_text 发给 AI
        event.should_call_llm(True)
        preference_text = "\n".join(f"{k}：{v}" for k, v in parsed_fields.items())

        async with self.player_queue.lock_for(group_id, user_id):
            async with self.player_queue.group_lock_for(group_id):
                async for result in self._run_reincarnation(
                    event,
                    group_id,
                    user_id,
                    preference_text,
                    event_command=event_command,
                    prompt_name=prompt_name,
                    card_label=card_label,
                ):
                    yield result

    async def _run_reincarnation(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_id: str,
        preference_text: str = "",
        *,
        event_command: str = "/魔法少女转生",
        prompt_name: str = "reincarnation_prompt",
        card_label: str = "魔法少女转生",
    ) -> AsyncGenerator:
        nickname = self._get_sender_name_from_event(event)
        avatar_url = self.avatar_service.build_avatar_url(user_id)
        if preference_text:
            progress = "已读取转生自定义设定"
        else:
            progress = "未填写自定义设定，将按默认设定生成"
        yield event.plain_result(f"{progress}，准备转生人物卡...")

        umo = getattr(event, "unified_msg_origin", None)
        if not umo:
            platform_id = self._get_platform_id_from_event(event)
            umo = f"{platform_id}:GroupMessage:{group_id}"

        theme = event_command
        if preference_text:
            theme = f"{theme} {preference_text}"

        result = await self.reincarnation_service.execute_reincarnation(
            theme=theme,
            html_render_func=self.html_render,
            user_id=user_id,
            nickname=nickname,
            umo=umo,
            avatar_url=avatar_url,
            prompt_name=prompt_name,
            event_command=event_command,
        )

        if result.error:
            logger.warning(f"{card_label}人物卡流程结束但存在错误: {result.error}")

        if result.card:
            self.save_repository.save_reincarnation(
                group_id=group_id,
                user_id=user_id,
                card=result.card,
                nickname=nickname,
                avatar_url=avatar_url,
            )

        yield await self.message_sender.send_image_or_text(
            event,
            result.image_path,
            result.card,
            fallback_text=result.text,
        )

    @filter.command("魔法少女行动")
    async def action_turn(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator:
        """根据玩家存档运行一次魔法少女行动回合。用法：/魔法少女行动 去学校看看"""
        event.should_call_llm(True)

        if not self._is_group_event_allowed(event):
            return

        group_id = self._get_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("请在群聊中使用 /魔法少女行动。")
            return

        user_id = self._get_sender_id_from_event(event)
        if not user_id:
            yield event.plain_result("没有拿到你的 QQ 号，暂时不能读取玩家存档。")
            return

        if await self.player_queue.is_locked(group_id, user_id):
            yield event.plain_result("你的上一条魔法少女请求还在处理，已经进入队列，马上轮到你。")

        async with self.player_queue.lock_for(group_id, user_id):
            async with self.player_queue.group_lock_for(group_id):
                async for result in self._run_action_turn(event, group_id, user_id):
                    yield result

    async def _run_action_turn(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_id: str,
    ) -> AsyncGenerator:
        save_data = self.save_repository.load_player_save(group_id, user_id)
        if not save_data:
            yield event.plain_result("还没有你的魔法少女转生存档，请先使用 /魔法少女转生 建档。")
            return

        action_text = self._extract_command_tail(event, "魔法少女行动")
        nickname = self._get_sender_name_from_event(event)
        avatar_url = self.avatar_service.build_avatar_url(user_id)
        umo = getattr(event, "unified_msg_origin", None)
        if not umo:
            platform_id = self._get_platform_id_from_event(event)
            umo = f"{platform_id}:GroupMessage:{group_id}"

        display_action = action_text or "自由行动"
        yield event.plain_result(f"正在推进本次魔法少女行动：{display_action}，准备生成回合记录...")

        result = await self.action_turn_service.execute_action_turn(
            group_id=group_id,
            user_id=user_id,
            nickname=nickname,
            action_text=action_text,
            umo=umo,
            html_render_func=self.html_render,
            avatar_url=avatar_url,
        )

        if result.error:
            logger.warning(f"/魔法少女行动 流程结束但存在错误: {result.error}")

        yield await self.message_sender.send_image_or_text(
            event,
            result.image_path,
            None,
            fallback_text=result.text,
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("开启魔法少女网页")
    async def start_mahoushoujo_web(self, event: AstrMessageEvent) -> AsyncGenerator:
        await self.web_viewer.start()
        url = self._build_web_url()
        yield event.plain_result(f"魔法少女存档网页已开启：{url}\n打开后请输入 QQ 号登录。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("关闭魔法少女网页")
    async def stop_mahoushoujo_web(self, event: AstrMessageEvent) -> AsyncGenerator:
        await self.web_viewer.stop()
        yield event.plain_result("魔法少女存档网页已关闭，当前网页登录态已失效。")

    async def terminate(self) -> None:
        await self.web_viewer.stop()

    def _build_web_url(self) -> str:
        base_url = self.config_manager.get_web_public_base_url()
        if not base_url:
            port = self.config_manager.get_web_port()
            base_url = f"http://127.0.0.1:{port}"
        prefix = self.config_manager.get_web_public_path_prefix()
        return f"{base_url.rstrip('/')}{prefix}"

    def _build_player_profile_panel_url(self) -> str:
        return PLAYER_PROFILE_PANEL_URL

    def _get_group_id_from_event(self, event: AstrMessageEvent) -> str | None:
        try:
            group_id = event.get_group_id()
            return str(group_id) if group_id else None
        except Exception:
            return None

    def _is_group_event_allowed(self, event: AstrMessageEvent) -> bool:
        """检查当前事件是否来自允许的群。不在名单中的群直接返回 False，不执行任何代码。"""
        group_id = self._get_group_id_from_event(event)
        if not group_id:
            # 非群聊消息，放行（私聊不影响）
            return True

        # 优先使用 UMO 进行匹配
        check_target = getattr(event, "unified_msg_origin", None)
        if not check_target:
            platform_id = self._get_platform_id_from_event(event)
            check_target = f"{platform_id}:GroupMessage:{group_id}"

        return self.config_manager.is_group_allowed(check_target)

    def _get_platform_id_from_event(self, event: AstrMessageEvent) -> str:
        try:
            platform_id = event.get_platform_id()
            return str(platform_id) if platform_id else "default"
        except Exception:
            return "default"

    def _get_sender_id_from_event(self, event: AstrMessageEvent) -> str | None:
        for attr in ("get_sender_id", "get_user_id"):
            getter = getattr(event, attr, None)
            if callable(getter):
                try:
                    value = getter()
                    if value:
                        return str(value)
                except Exception:
                    pass
        return None

    def _get_sender_name_from_event(self, event: AstrMessageEvent) -> str | None:
        for attr in ("get_sender_name", "get_sender_nickname"):
            getter = getattr(event, attr, None)
            if callable(getter):
                try:
                    value = getter()
                    if value:
                        return str(value)
                except Exception:
                    pass
        return None

    @staticmethod
    def _extract_reincarnation_text(event: AstrMessageEvent, command_name: str = "魔法少女转生") -> str:
        """提取转生命令后的完整文本（支持空格或换行分隔的多行格式）"""
        try:
            text = event.get_message_str()
        except Exception:
            text = getattr(event, "message_str", "")
        text = str(text or "").strip()

        prefixes = [f"/{command_name}", f"／{command_name}", command_name]
        for prefix in prefixes:
            if text == prefix:
                return ""
            if text.startswith(prefix):
                remaining = text[len(prefix):]
                return remaining.strip()
        return ""

    @staticmethod
    def _parse_reincarnation_fields(
        text: str,
        fields: list[str] | None = None,
    ) -> dict[str, str]:
        """解析玩家填写的转生表单字段，返回已填写的字段字典"""
        if not text:
            return {}

        field_names = fields or REINCARNATION_FIELDS
        fields = {}
        for field_name in field_names:
            other_fields = [re.escape(f) for f in field_names if f != field_name]
            lookahead = "|".join(other_fields) if other_fields else "$"
            pattern = rf'{re.escape(field_name)}[：:]\s*(.*?)(?=(?:{lookahead})[：:]|$)'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                value = match.group(1).strip()
                if value:
                    fields[field_name] = value
        return fields

    @staticmethod
    def _extract_command_tail(event: AstrMessageEvent, command_name: str) -> str:
        try:
            text = event.get_message_str()
        except Exception:
            text = getattr(event, "message_str", "")
        text = str(text or "").strip()
        prefixes = [
            command_name,
            f"/{command_name}",
            f"／{command_name}",
        ]
        for prefix in prefixes:
            if text == prefix:
                return ""
            if text.startswith(prefix + " "):
                return text[len(prefix) :].strip()
        return ""
