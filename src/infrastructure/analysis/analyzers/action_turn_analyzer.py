from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from ....domain.models.data_models import ActionTurnResult, TokenUsage
from ....domain.services.battle_diary_domain_service import BattleDiaryDomainService
from ....utils.logger import logger
from ..utils.llm_utils import (
    call_provider_with_retry,
    extract_response_text,
    extract_token_usage,
    mark_latest_llm_error,
)
from .battle_diary_analyzer import BattleDiaryAnalyzer


HIDDEN_CURRENT_VARIABLE_KEYS = {
    "schema_version",
    "group_id",
    "user_id",
    "nickname",
    "avatar_url",
    "player_clock",
    "created_at",
    "updated_at",
}


class ActionTurnAnalyzer(BattleDiaryAnalyzer):
    def __init__(
        self,
        context,
        config_manager,
        domain_service: BattleDiaryDomainService,
        editable_manager=None,
    ):
        super().__init__(
            context,
            config_manager,
            domain_service,
            editable_manager,
        )

    def get_data_type(self) -> str:
        return "魔法少女行动回合"

    async def analyze_action_turn(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None = None,
        nearby_players: list[dict] | None = None,
        selection_context: dict[str, object] | None = None,
        current_world_date: str = "",
        umo: str | None = None,
    ) -> tuple[ActionTurnResult | None, TokenUsage, str]:
        prompt = self.build_action_prompt(
            action_text=action_text,
            player_data=player_data,
            logs=logs,
            cameo_memories=cameo_memories,
            nearby_players=nearby_players,
            selection_context=selection_context,
            current_world_date=current_world_date,
        )
        system_prompt = self.editable_manager.get_prompt("default_system_prompt")
        messages = self._build_action_messages(prompt, system_prompt)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("action_turn_prompt", prompt)
            self._save_debug_file("action_turn_system_prompt", system_prompt)
            self._save_debug_file(
                "action_turn_messages",
                json.dumps(messages, ensure_ascii=False, indent=2),
            )

        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            system_prompt=system_prompt,
            messages=messages,
            purpose=self.get_data_type(),
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("action_turn_response", result_text)

        usage_dict = extract_token_usage(response)
        token_usage = TokenUsage(
            prompt_tokens=usage_dict["prompt_tokens"],
            completion_tokens=usage_dict["completion_tokens"],
            total_tokens=usage_dict["total_tokens"],
        )

        try:
            result = self.parse_action_turn_response(result_text)
        except Exception as exc:
            logger.warning(f"{self.get_data_type()} 解析失败，准备重试修复格式: {exc}")
            repair_text, repair_usage = await self._retry_action_turn_format(
                prompt=prompt,
                system_prompt=system_prompt,
                bad_response=result_text,
                parse_error=exc,
                umo=umo,
            )
            token_usage = self._merge_token_usage(token_usage, repair_usage)
            if self.config_manager.get_debug_mode():
                self._save_debug_file("action_turn_repair_response", repair_text)
            try:
                result = self.parse_action_turn_response(repair_text)
            except Exception as repair_exc:
                error = (
                    f"{self.get_data_type()} parse failed after repair retry: "
                    f"{repair_exc}; first_error={exc}"
                )
                mark_latest_llm_error(error)
                logger.error(f"{self.get_data_type()} 解析重试后仍失败: {repair_exc}")
                return None, token_usage, repair_text or result_text
            result_text = repair_text

        result.raw_response = result_text
        result.action = action_text
        result.date_label = current_world_date
        result.phase = self._current_phase(player_data)
        return result, token_usage, result_text

    async def _retry_action_turn_format(
        self,
        *,
        prompt: str,
        system_prompt: str,
        bad_response: str,
        parse_error: Exception,
        umo: str | None,
    ) -> tuple[str, TokenUsage]:
        repair_prompt = self._build_action_repair_prompt(
            prompt=prompt,
            bad_response=bad_response,
            parse_error=parse_error,
        )
        repair_messages = self._build_action_messages(repair_prompt, system_prompt)
        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=repair_prompt,
            umo=umo,
            system_prompt=system_prompt,
            messages=repair_messages,
            purpose=f"{self.get_data_type()}格式修复",
        )
        usage_dict = extract_token_usage(response)
        return (
            extract_response_text(response),
            TokenUsage(
                prompt_tokens=usage_dict["prompt_tokens"],
                completion_tokens=usage_dict["completion_tokens"],
                total_tokens=usage_dict["total_tokens"],
            ),
        )

    @staticmethod
    def _merge_token_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=left.prompt_tokens + right.prompt_tokens,
            completion_tokens=left.completion_tokens + right.completion_tokens,
            total_tokens=left.total_tokens + right.total_tokens,
        )

    @staticmethod
    def _build_action_repair_prompt(
        *,
        prompt: str,
        bad_response: str,
        parse_error: Exception,
    ) -> str:
        response_text = str(bad_response or "").strip()
        if response_text:
            response_part = (
                "上一次回复如下，请保留其中已经发生的剧情事实，并修复为合规格式：\n"
                f"{response_text}"
            )
        else:
            response_part = "上一次回复为空，请重新生成完整的行动回合输出。"
        return "\n\n".join(
            [
                prompt,
                "【格式修复重试】",
                f"上一次解析错误：{parse_error}",
                response_part,
                (
                    "请只输出最终可解析内容，不要解释原因。输出必须按顺序包含：\n"
                    "1. 故事正文\n"
                    "2. <行动选项>...</行动选项>\n"
                    "3. <UpdateVariable><Analysis>...</Analysis>"
                    "<JSONPatch>[...]</JSONPatch></UpdateVariable>\n"
                    "<JSONPatch> 必须包含且只包含一个 /世界/时间 delta；"
                    "其他变量没有变化时，也不能省略该时间补丁。"
                ),
            ]
        )

    def build_action_prompt(
        self,
        *,
        action_text: str,
        player_data: dict,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        nearby_players: list[dict] | None,
        selection_context: dict[str, object] | None,
        current_world_date: str,
    ) -> str:
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        phase = self._current_phase(player_data)
        action = action_text.strip() or "自由行动"
        pending_events = self._pending_events(player_data)
        scan_parts = [
            "/魔法少女行动",
            action,
            phase,
            self._format_logs_for_scan(logs),
        ]
        world_book_result = self.world_book_engine.build_prompt_text(scan_parts)
        status_book_result = self.status_book_engine.build_prompt_text(scan_parts)
        supplement_text = self._join_optional_prompt_parts(
            [
                world_book_result.prompt_text,
                status_book_result.prompt_text,
                self.change_book_engine.build_skill_prompt_text(scan_parts),
                self.change_book_engine.build_fetish_prompt_text(protagonist),
            ]
        )
        teammate_info = self._format_teammate_info(nearby_players)
        return self.editable_manager.render_prompt(
            "action_turn_prompt",
            {
                "history_context": self._build_history_context(
                    logs=logs,
                    cameo_memories=cameo_memories,
                    action=action,
                    current_world_date=current_world_date,
                ),
                "phase_protocol": self._phase_protocol(phase),
                "event_completion_protocol": self._event_completion_protocol(
                    phase,
                    selection_context,
                ),
                "current_variables_json": self._json_dump(
                    self._visible_current_variables(player_data)
                ),
                "variable_api_document": self._variable_api_document(),
                "backend_event_protocol": self._backend_event_protocol(pending_events),
                "examples_library": self._examples_library(),
                "phase": phase,
                "action": action,
                "current_world_date": current_world_date,
                "player_name": self._get_nested(protagonist, ["个人信息", "姓名"], "") or "主角",
                "supplement_text": supplement_text or "无",
                "participants_json": teammate_info["json"],
                "selection_context_json": self._json_dump(selection_context or {}),
            },
        )

    @classmethod
    def parse_action_turn_response(cls, text: str) -> ActionTurnResult:
        raw = str(text or "").strip()
        options_block = cls._extract_tag(raw, "行动选项")
        update_block = cls._extract_tag(raw, "UpdateVariable")
        if not update_block:
            raise ValueError("缺少 <UpdateVariable> 块")
        analysis = cls._extract_tag(update_block, "Analysis")
        patch_text = cls._extract_tag(update_block, "JSONPatch")
        if not patch_text:
            raise ValueError("缺少 <JSONPatch> 块")
        patch = cls._parse_json_patch(patch_text)
        cls._validate_world_time_patch(patch)
        story = raw
        for tag in ("行动选项", "UpdateVariable"):
            match = re.search(rf"<{tag}>.*?</{tag}>", story, flags=re.S)
            if match:
                story = story[: match.start()].strip()
        options = [
            line.strip()
            for line in (options_block or "").splitlines()
            if line.strip()
        ][:6]
        return ActionTurnResult(
            story_text=story,
            action_options=options,
            analysis=(analysis or "").strip(),
            json_patch=patch,
            footer="行动记录已写入存档。",
        )

    @staticmethod
    def _extract_tag(text: str, tag: str) -> str:
        match = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, flags=re.S)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _parse_json_patch(text: str) -> list[dict[str, Any]]:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            raise ValueError("JSONPatch 必须是数组")
        return [item for item in parsed if isinstance(item, dict)]

    @staticmethod
    def _validate_world_time_patch(patch: list[dict[str, Any]]) -> None:
        if any(str(item.get("path") or "") == "/世界/日期" for item in patch):
            raise ValueError("/世界/日期 已停用，请使用 /世界/时间")
        time_patches = [
            item for item in patch if str(item.get("path") or "") == "/世界/时间"
        ]
        if len(time_patches) != 1:
            raise ValueError("每轮必须且只能输出一个 /世界/时间 delta")
        item = time_patches[0]
        if str(item.get("op") or "").strip() != "delta":
            raise ValueError("/世界/时间 只允许使用 delta")
        value = item.get("value")
        if not isinstance(value, str) or not re.fullmatch(
            r"(0|[1-9]\d*):[0-5]\d",
            value.strip(),
        ):
            raise ValueError("/世界/时间 的 delta 必须是 H:MM，分钟范围为 00-59")

    @staticmethod
    def _current_phase(player_data: dict) -> str:
        if not isinstance(player_data, dict):
            return "日常"
        phase = (
            player_data.get("进程", {}).get("阶段")
            if isinstance(player_data.get("进程"), dict)
            else ""
        )
        phase = str(phase or "").strip()
        return phase if phase in {"日常", "战斗", "事件"} else "日常"

    @staticmethod
    def _pending_events(player_data: dict) -> dict[str, Any]:
        system_state = player_data.get("系统状态", {}) if isinstance(player_data, dict) else {}
        pending = system_state.get("待处理事件", {}) if isinstance(system_state, dict) else {}
        return pending if isinstance(pending, dict) else {}

    def _build_history_context(
        self,
        *,
        logs: list[dict],
        cameo_memories: list[dict] | None,
        action: str,
        current_world_date: str,
    ) -> str:
        return "\n".join(
            part
            for part in [
                f"当前时间：{current_world_date or '未知'}",
                f"玩家本轮行动：{action}",
                "最近记录：",
                self._format_logs(logs),
                "其他人与主角的交互：",
                self._format_cameo_memories(cameo_memories),
            ]
            if str(part).strip()
        )

    @staticmethod
    def _phase_protocol(phase: str) -> str:
        protocols = {
            "日常": (
                "daily_life_protocol:\n"
                "  objective: \"推进生活、训练、社交、巡逻或轻量异常。保持连贯，不擅自进入正式战斗。\"\n"
                "  rhythm: \"平稳、具体、有小转折。\"\n"
            ),
            "战斗": (
                "battle_protocol:\n"
                "  objective: \"根据行动和上下文推进战斗。胜负、损耗和状态变化必须能从正文中找到依据。\"\n"
                "  rhythm: \"动作优先，少写旁白总结。\"\n"
            ),
            "事件": (
                "event_protocol:\n"
                "  objective: \"根据玩家的尝试和已有事实独立裁决并推进当前剧情事件，可以自然转场、改变目标或结束。\"\n"
                "  continuation: \"魔物、追击、调查、救援、决斗等当前目标仍存在时，必须保留 /进程/当前事件。\"\n"
                "  completion: \"只有当前目标已解决或彻底消失时，才移除 /进程/当前事件并把阶段改为日常。\"\n"
                "  failure: \"失败可以结束事件，也可以形成被拘束、被抓走、被改造或继续受困的新处境；只按正文事实决定。\"\n"
            ),
        }
        return protocols.get(phase, protocols["日常"])

    @staticmethod
    def _event_completion_protocol(
        phase: str,
        selection_context: dict[str, object] | None,
    ) -> str:
        if phase != "事件" or not isinstance(selection_context, dict):
            return ""
        runtime = selection_context.get("event_runtime")
        if not isinstance(runtime, dict):
            return ""
        try:
            turn_count = max(0, int(runtime.get("turn_count", 0) or 0))
        except (TypeError, ValueError):
            return ""
        if turn_count <= 0:
            return ""

        common = (
            "event_completion_protocol:\n"
            f"  continued_turn: {turn_count + 1}\n"
            "  audit: \"正文完成后必须检查当前事件的原始目标是否已经解决。\"\n"
            "  finish: \"如果正文已经收束、目标已经解决或相关危机已经消失，"
            "必须在本轮 JSONPatch 中移除 /进程/当前事件；不要因为忘记清理而让事件继续。\"\n"
            "  continue: \"只有正文中仍存在具体、可指出的未解决事实时才能保留事件；"
            "必须在 <Analysis> 的‘指令与事件审计’中写明该事实，"
            "不能只写‘剧情仍需发展’。\"\n"
            "  transition: \"玩家明确发起新目标时可以自然转场或更新当前目标，"
            "但不能凭空添加无关冲突来拖延旧事件。\"\n"
        )
        if turn_count <= 2:
            return common + (
                "  priority: \"正常推进，并在本轮结尾认真判断继续或结束。\""
            )
        if turn_count <= 5:
            return common + (
                "  priority: \"事件已经持续多轮，本轮明显偏向推进和收束；"
                "不得仅为续写而凭空制造敌人、任务、误会、转折或新阻碍。\""
            )
        return common + (
            "  priority: \"事件已持续很久，本轮应优先解决当前目标并收束。"
            "只有玩家明确要求继续，或已有剧情事实确实阻止结束时，才可以保留事件；"
            "禁止新造阻碍延命。\""
        )

    @staticmethod
    def _variable_api_document() -> str:
        return """
variable_api:
  operations:
    - replace: { "op": "replace", "path": "/路径", "value": 新值 }
    - delta: { "op": "delta", "path": "/数字路径", "value": 正负数字 }
    - insert: { "op": "insert", "path": "/对象/新Key", "value": 新值 }
    - array_insert: { "op": "insert", "path": "/数组/-", "value": 新元素 }
    - remove: { "op": "remove", "path": "/对象/Key" }
  writable:
    - /进程/阶段
    - /进程/当前事件
    - /进程/当前事件/scene_event
    - /进程/当前事件/selected_participants
    - /进程/当前事件/selected_targets
    - /进程/当前事件/turn_count
    - /世界/时间
    - /世界/世界观备注
    - /记录
    - /名声/知名度
    - /名声/风评
    - /主角/核心状态
    - /主角/身体部位状况
    - /主角/道具栏
    - /主角/生理状态
    - /主角/快感状态
    - /主角/技能
    - /主角/特质
    - /主角/永久性身体改造
    - /系统状态/待处理事件
  readonly: []
  rules:
    - 只根据本轮正文更新变量。
    - 不要把系统数据写进正文。
    - 普通状态字段的 delta 只用于数字；/世界/时间 是专用的 H:MM 字符串增量。
    - 每轮必须且只能输出一个 /世界/时间 delta，值为本轮正文实际增加的 H:MM 字符串。
    - 小时可以超过 23，分钟必须为 00-59；例如短对话 0:10、训练 1:30、睡眠 8:00、两天 48:00、没有时间流逝 0:00。
    - 只输出增加量，不输出绝对时间，也不要自行判断最终日期；代码会累加时间并处理跨日。
    - 完成待处理事件后 remove 对应事件 Key。
    - 只有持续剧情才使用当前事件；普通日常、自由行动、训练和社交不要创建当前事件。
    - 当前剧情事件继续时保留或更新当前事件；剧情自然完成后可以移除当前事件或离开事件阶段。
""".strip()

    @staticmethod
    def _visible_current_variables(player_data: dict) -> dict:
        if not isinstance(player_data, dict):
            return {}
        visible = {
            key: value
            for key, value in player_data.items()
            if str(key) not in HIDDEN_CURRENT_VARIABLE_KEYS
        }
        process = visible.get("进程")
        if isinstance(process, dict) and "当前事件" in process:
            visible["进程"] = deepcopy(process)
            visible["进程"].pop("当前事件", None)
        world = visible.get("世界")
        if isinstance(world, dict) and any(key in world for key in ("日期", "时间")):
            visible["世界"] = deepcopy(world)
            visible["世界"].pop("日期", None)
            visible["世界"].pop("时间", None)
        return visible

    @staticmethod
    def _backend_event_protocol(pending_events: dict[str, Any]) -> str:
        if not pending_events:
            return "backend_event_protocol: \"无待处理事件。\""
        return (
            "backend_event_protocol:\n"
            "  task_type: \"SILENT_BACKGROUND_PROCESS\"\n"
            "  instruction: \"正常写正文，不要暴露后台任务；在 <JSONPatch> 中处理并清理待处理事件。\"\n"
            f"  pending_events: {json.dumps(pending_events, ensure_ascii=False)}"
        )

    @staticmethod
    def _examples_library() -> str:
        return """
examples_library:
  - scenario: "日常获得物品"
    format: |
      <UpdateVariable>
        <Analysis>
          - 指令与事件：无待处理事件。
          - 状态检查：本轮是日常探索，阶段保持日常。
          - 物品/位置/关系变化：正文写到我捡起并收好了便签，因此写入道具栏。
          - 长期记忆：便签内容只是临时线索，暂不登记世界观备注。
        </Analysis>
        <JSONPatch>[
          { "op": "insert", "path": "/主角/道具栏/便签", "value": "写着今天的线索" },
          { "op": "replace", "path": "/主角/生理状态/当前生理动态", "value": "呼吸平稳" },
          { "op": "delta", "path": "/世界/时间", "value": "0:10" }
        ]</JSONPatch>
      </UpdateVariable>
  - scenario: "战斗损耗"
    format: |
      <UpdateVariable>
        <Analysis>
          - 指令与事件：无强制后台事件。
          - 状态检查：正文中发生了短暂战斗，主角被击中但没有永久伤害。
          - 物品/位置/关系变化：没有获得或消耗道具。
          - 长期记忆：因为正文中明确使用闪避脱险，所以增加闪避进度。
        </Analysis>
        <JSONPatch>[
          { "op": "delta", "path": "/主角/核心状态/体力值/当前", "value": -8 },
          { "op": "replace", "path": "/主角/身体部位状况/手臂", "value": "轻微擦伤" },
          { "op": "delta", "path": "/主角/技能/闪避/进度", "value": 5 },
          { "op": "replace", "path": "/进程/阶段", "value": "日常" },
          { "op": "delta", "path": "/世界/时间", "value": "0:20" }
        ]</JSONPatch>
      </UpdateVariable>
  - scenario: "事件清理"
    format: |
      <UpdateVariable>
        <Analysis>
          - 指令与事件：本轮处理了待处理事件“线索整理事件”。
          - 状态检查：正文照常推进，没有让玩家察觉后台整理。
          - 物品/位置/关系变化：没有新增道具。
          - 长期记忆：已将旧线索整理完成，因此移除事件触发器，避免重复执行。
        </Analysis>
        <JSONPatch>[
          { "op": "remove", "path": "/系统状态/待处理事件/线索整理事件" },
          { "op": "delta", "path": "/世界/时间", "value": "0:05" }
        ]</JSONPatch>
      </UpdateVariable>
  - scenario: "世界观备注"
    format: |
      <UpdateVariable>
        <Analysis>
          - 指令与事件：无待处理事件。
          - 状态检查：本轮发现了可复用的地点设定。
          - 物品/位置/关系变化：没有道具变化，位置线索来自正文。
          - 长期记忆：旧校舍夜间魔力反应会影响后续剧情，因此登记为世界观备注。
        </Analysis>
        <JSONPatch>[
          { "op": "insert", "path": "/世界/世界观备注/地点_旧校舍", "value": "夜间会出现微弱魔力反应。" },
          { "op": "delta", "path": "/世界/时间", "value": "0:15" }
        ]</JSONPatch>
      </UpdateVariable>
""".strip()

    @staticmethod
    def _action_system_prompt() -> str:
        return (
            "你是魔法少女互动运行时中的叙事与状态维护模型。"
            "本轮主输出是结构化文本，不是纯 JSON。"
            "请严格按 user message 要求输出正文、<行动选项> 和 <UpdateVariable>；"
            "不要在指定结构之外添加解释、道歉、Markdown 代码围栏或额外说明。"
        )

    @staticmethod
    def _build_action_messages(
        prompt: str,
        system_prompt: str,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        system_text = str(system_prompt or "").strip()
        prompt_text = str(prompt or "").strip()
        if system_text:
            messages.append({"role": "system", "content": system_text})
        if prompt_text:
            messages.extend(
                [
                    {
                        "role": "user",
                        "content": "喵喵喵——伟大又可爱的小猫之神，请聆听信徒最虔诚的呼唤——",
                    },
                    {
                        "role": "assistant",
                        "content": "喵——哈~ 刚睡醒就来找本喵，是什么事呀喵？",
                    },
                    {
                        "role": "user",
                        "content": (
                            "想请小猫之神主持一场魔法少女互动故事，小鱼干已经准备好啦。"
                            "请在故事外当 DM，用第三人称写魔法少女们的故事。"
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "好耶喵，那就让小猫之神来当 DM 吧，小鱼干可要说话算话！"
                            "世界、角色和规则都交给本喵。"
                            "本喵会在故事外当好 DM，用第三人称写魔法少女们的故事，"
                            "也会把每轮真正发生的变化整理好喵。"
                        ),
                    },
                    {"role": "user", "content": prompt_text},
                ]
            )
        return messages
