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
from .src.application.services.battle_diary_application_service import (
    BattleDiaryApplicationService,
)
from .src.domain.services.battle_diary_domain_service import (
    BattleDiaryDomainService,
)
from .src.domain.services.reincarnation_domain_service import ReincarnationDomainService
from .src.infrastructure.analysis.llm_reincarnation_analyzer import LLMReincarnationAnalyzer
from .src.infrastructure.analysis.llm_battle_diary_analyzer import (
    LLMBattleDiaryAnalyzer,
)
from .src.infrastructure.analysis.llm_player_relationship_analyzer import (
    LLMPlayerRelationshipAnalyzer,
)
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


class QQMahouShoujo(Star):
    config: AstrBotConfig
    config_manager: ConfigManager
    domain_service: ReincarnationDomainService
    diary_domain_service: BattleDiaryDomainService
    editable_manager: EditableResourceManager
    llm_analyzer: LLMReincarnationAnalyzer
    diary_llm_analyzer: LLMBattleDiaryAnalyzer
    relationship_llm_analyzer: LLMPlayerRelationshipAnalyzer
    report_generator: ReportGenerator
    reincarnation_service: ReincarnationApplicationService
    diary_service: BattleDiaryApplicationService
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
        self.diary_llm_analyzer = LLMBattleDiaryAnalyzer(
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
        self.diary_service = BattleDiaryApplicationService(
            self.config_manager,
            self.diary_domain_service,
            self.diary_llm_analyzer,
            self.report_generator,
            self.save_repository,
            self.relationship_llm_analyzer,
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
                    "魔法少女新手指引",
                    "",
                    "玩家可使用指令：",
                    "1. /魔法少女转生",
                    "   创建你的魔法少女角色档案。",
                    "   请按指定格式填写角色设定，至少填写3项属性。",
                    "   示例：",
                    "   /魔法少女转生",
                    "   姓名：星野梦美",
                    "   性格特质：温柔内敛",
                    "   代表色：星空蓝",
                    "",
                    "2. /魔法少女战斗",
                    "   根据你的角色档案、当前状态和最近记录，生成一次战斗日记。",
                    "   可以直接自由战斗，也可以在命令后写本次行动。",
                    "   示例：/魔法少女战斗 去森林战斗爽",
                    "",
                    "3. /魔法少女日常",
                    "   根据你的角色档案、当前状态和最近记录，生成一次日常日记。",
                    "   可以直接自由日常，也可以在命令后写本次行动。",
                    "   示例：/魔法少女日常 和队友一起去买甜点",
                    "",
                    "4. /魔法少女黑化",
                    "   根据你的角色档案、当前状态和最近记录，生成一次黑化日记。",
                    "   可以直接自由黑化，也可以在命令后写本次行动。",
                    "   示例：/魔法少女黑化 独自走进废弃车站",
                    "",
                    "5. /反派干部战斗",
                    "   根据你的角色档案、当前状态和最近记录，生成一次反派干部战斗日记。",
                    "   可以直接自由战斗，也可以在命令后写本次行动。",
                    "   示例：/反派干部战斗 夜袭魔法少女据点",
                    "",
                    "6. /魔法少女存档删除",
                    "   删除你在当前群的魔法少女存档，并清理其他玩家记忆中由你产生的客串记录。",
                    "   为避免误删，需要输入：/魔法少女存档删除 确认",
                    "",
                    "角色档案面板：",
                    "https://www.youxiajiang.com/Games/AIBot/",
                    "创建完角色后，可以在这里查看自己的角色档案、状态和战斗记录。",
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

        if not self._is_group_event_allowed(event):
            return

        group_id = self._get_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("请在群聊中使用 /魔法少女转生。")
            return

        user_id = self._get_sender_id_from_event(event)
        if not user_id:
            yield event.plain_result("没有拿到你的 QQ 号，暂时不能创建玩家存档。")
            return

        if await self.player_queue.is_locked(group_id, user_id):
            yield event.plain_result("你的上一条魔法少女请求还在处理，已经进入队列，马上轮到你。")

        # 提取命令后的完整文本（支持多行格式）
        raw_text = self._extract_reincarnation_text(event)

        # 解析结构化字段
        parsed_fields = self._parse_reincarnation_fields(raw_text)
        filled_count = len(parsed_fields)

        # 至少需要 3 个字段，否则返回模板
        if filled_count < 3:
            event.should_call_llm(False)
            if raw_text and filled_count > 0:
                message = (
                    f"填写信息不足（已填写 {filled_count}/9 项，至少需要 3 项），"
                    f"请按以下格式重新发送：\n\n{REINCARNATION_TEMPLATE}"
                )
            else:
                message = f"请按以下格式填写转生信息(最少填写三个字段)：\n\n{REINCARNATION_TEMPLATE}"
            yield event.plain_result(message)
            return

        # 通过验证，将结构化字段拼接为 preference_text 发给 AI
        event.should_call_llm(True)
        preference_text = "\n".join(f"{k}：{v}" for k, v in parsed_fields.items())

        async with self.player_queue.lock_for(group_id, user_id):
            async for result in self._run_reincarnation(
                event,
                group_id,
                user_id,
                preference_text,
            ):
                yield result

    async def _run_reincarnation(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_id: str,
        preference_text: str = "",
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

        theme = "/魔法少女转生"
        if preference_text:
            theme = f"{theme} {preference_text}"

        result = await self.reincarnation_service.execute_reincarnation(
            theme=theme,
            html_render_func=self.html_render,
            user_id=user_id,
            nickname=nickname,
            umo=umo,
            avatar_url=avatar_url,
        )

        if result.error:
            logger.warning(f"魔法少女转生人物卡流程结束但存在错误: {result.error}")

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

    @filter.command("魔法少女战斗")
    async def battle_diary(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator:
        """根据玩家存档生成一次完整的魔法少女战斗日记卡。用法：/魔法少女战斗"""
        event.should_call_llm(True)

        if not self._is_group_event_allowed(event):
            return

        group_id = self._get_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("请在群聊中使用 /魔法少女战斗。")
            return

        user_id = self._get_sender_id_from_event(event)
        if not user_id:
            yield event.plain_result("没有拿到你的 QQ 号，暂时不能读取玩家存档。")
            return

        if await self.player_queue.is_locked(group_id, user_id):
            yield event.plain_result("你的上一条魔法少女请求还在处理，已经进入队列，马上轮到你。")

        async with self.player_queue.lock_for(group_id, user_id):
            async with self.player_queue.group_lock_for(group_id):
                async for result in self._run_battle_diary(
                    event,
                    group_id,
                    user_id,
                    command_name="魔法少女战斗",
                    event_command="/魔法少女战斗",
                    prompt_name="battle_diary_prompt",
                    default_action="自由战斗",
                    action_label="战斗",
                    card_label="战斗日记卡",
                ):
                    yield result

    @filter.command("魔法少女日常")
    async def daily_diary(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator:
        """根据玩家存档生成一次完整的魔法少女日常日记卡。用法：/魔法少女日常"""
        event.should_call_llm(True)

        if not self._is_group_event_allowed(event):
            return

        group_id = self._get_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("请在群聊中使用 /魔法少女日常。")
            return

        user_id = self._get_sender_id_from_event(event)
        if not user_id:
            yield event.plain_result("没有拿到你的 QQ 号，暂时不能读取玩家存档。")
            return

        if await self.player_queue.is_locked(group_id, user_id):
            yield event.plain_result("你的上一条魔法少女请求还在处理，已经进入队列，马上轮到你。")

        async with self.player_queue.lock_for(group_id, user_id):
            async with self.player_queue.group_lock_for(group_id):
                async for result in self._run_battle_diary(
                    event,
                    group_id,
                    user_id,
                    command_name="魔法少女日常",
                    event_command="/魔法少女日常",
                    prompt_name="daily_diary_prompt",
                    default_action="自由日常",
                    action_label="日常",
                    card_label="日常日记卡",
                ):
                    yield result

    @filter.command("魔法少女黑化")
    async def corruption_diary(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator:
        """根据玩家存档生成一次完整的魔法少女黑化日记卡。用法：/魔法少女黑化"""
        event.should_call_llm(True)

        if not self._is_group_event_allowed(event):
            return

        group_id = self._get_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("请在群聊中使用 /魔法少女黑化。")
            return

        user_id = self._get_sender_id_from_event(event)
        if not user_id:
            yield event.plain_result("没有拿到你的 QQ 号，暂时不能读取玩家存档。")
            return

        if await self.player_queue.is_locked(group_id, user_id):
            yield event.plain_result("你的上一条魔法少女请求还在处理，已经进入队列，马上轮到你。")

        async with self.player_queue.lock_for(group_id, user_id):
            async with self.player_queue.group_lock_for(group_id):
                async for result in self._run_battle_diary(
                    event,
                    group_id,
                    user_id,
                    command_name="魔法少女黑化",
                    event_command="/魔法少女黑化",
                    prompt_name="corruption_diary_prompt",
                    default_action="自由黑化",
                    action_label="黑化",
                    card_label="黑化日记卡",
                ):
                    yield result

    @filter.command("反派干部战斗")
    async def villain_officer_battle_diary(
        self,
        event: AstrMessageEvent,
    ) -> AsyncGenerator:
        """根据玩家存档生成一次完整的反派干部战斗日记卡。用法：/反派干部战斗"""
        event.should_call_llm(True)

        if not self._is_group_event_allowed(event):
            return

        group_id = self._get_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("请在群聊中使用 /反派干部战斗。")
            return

        user_id = self._get_sender_id_from_event(event)
        if not user_id:
            yield event.plain_result("没有拿到你的 QQ 号，暂时不能读取玩家存档。")
            return

        if await self.player_queue.is_locked(group_id, user_id):
            yield event.plain_result("你的上一条魔法少女请求还在处理，已经进入队列，马上轮到你。")

        async with self.player_queue.lock_for(group_id, user_id):
            async with self.player_queue.group_lock_for(group_id):
                async for result in self._run_battle_diary(
                    event,
                    group_id,
                    user_id,
                    command_name="反派干部战斗",
                    event_command="/反派干部战斗",
                    prompt_name="villain_officer_battle_prompt",
                    default_action="自由反派干部战斗",
                    action_label="反派干部战斗",
                    card_label="反派干部战斗日记卡",
                ):
                    yield result

    async def _run_battle_diary(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_id: str,
        *,
        command_name: str = "魔法少女战斗",
        event_command: str = "/魔法少女战斗",
        prompt_name: str = "battle_diary_prompt",
        default_action: str = "自由战斗",
        action_label: str = "战斗",
        card_label: str = "战斗日记卡",
    ) -> AsyncGenerator:
        save_data = self.save_repository.load_player_save(group_id, user_id)
        if not save_data:
            yield event.plain_result("还没有你的魔法少女转生存档，请先使用 /魔法少女转生 建档。")
            return

        action_text = self._extract_command_tail(event, command_name)
        nickname = self._get_sender_name_from_event(event)
        avatar_url = self.avatar_service.build_avatar_url(user_id)
        umo = getattr(event, "unified_msg_origin", None)
        if not umo:
            platform_id = self._get_platform_id_from_event(event)
            umo = f"{platform_id}:GroupMessage:{group_id}"

        display_action = action_text or default_action
        yield event.plain_result(f"正在记录本次{action_label}：{display_action}，准备生成{card_label}...")

        result = await self.diary_service.execute_diary(
            group_id=group_id,
            user_id=user_id,
            nickname=nickname,
            action_text=action_text,
            umo=umo,
            html_render_func=self.html_render,
            avatar_url=avatar_url,
            event_command=event_command,
            prompt_name=prompt_name,
            default_action=default_action,
        )

        if result.error:
            logger.warning(f"{event_command} 日记流程结束但存在错误: {result.error}")

        yield await self.message_sender.send_image_or_text(
            event,
            result.image_path,
            result.card,
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
    def _extract_reincarnation_text(event: AstrMessageEvent) -> str:
        """提取转生命令后的完整文本（支持空格或换行分隔的多行格式）"""
        try:
            text = event.get_message_str()
        except Exception:
            text = getattr(event, "message_str", "")
        text = str(text or "").strip()

        prefixes = ["/魔法少女转生", "／魔法少女转生", "魔法少女转生"]
        for prefix in prefixes:
            if text == prefix:
                return ""
            if text.startswith(prefix):
                remaining = text[len(prefix):]
                return remaining.strip()
        return ""

    @staticmethod
    def _parse_reincarnation_fields(text: str) -> dict[str, str]:
        """解析玩家填写的转生表单字段，返回已填写的字段字典"""
        if not text:
            return {}

        fields = {}
        for field_name in REINCARNATION_FIELDS:
            other_fields = [re.escape(f) for f in REINCARNATION_FIELDS if f != field_name]
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
