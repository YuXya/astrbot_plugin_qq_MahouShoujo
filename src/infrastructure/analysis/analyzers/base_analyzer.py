from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from astrbot.api.star import StarTools

from ....domain.models.data_models import TokenUsage
from ...editable_resources import EditableResourceManager
from ....utils.logger import logger
from ..utils.json_utils import parse_json_object_response
from ..utils.llm_utils import (
    call_provider_with_retry,
    extract_response_text,
    extract_token_usage,
)

TDataObject = TypeVar("TDataObject")


class BaseAnalyzer(ABC, Generic[TDataObject]):
    def __init__(
        self,
        context,
        config_manager,
        editable_manager: EditableResourceManager | None = None,
    ):
        self.context = context
        self.config_manager = config_manager
        self.editable_manager = editable_manager or EditableResourceManager()

    @abstractmethod
    def get_data_type(self) -> str:
        pass

    @abstractmethod
    def build_prompt(
        self,
        theme: str,
        user_id: str | None,
        nickname: str | None,
        player_messages: list[str] | None = None,
        avatar_caption: str | None = None,
    ) -> str:
        pass

    @abstractmethod
    def create_data_object(
        self,
        data: dict,
        avatar_url: str | None = None,
        avatar_caption: str | None = None,
    ) -> TDataObject:
        pass

    async def analyze(
        self,
        theme: str,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
        player_messages: list[str] | None = None,
        avatar_url: str | None = None,
        avatar_caption: str | None = None,
    ) -> tuple[TDataObject | None, TokenUsage, str]:
        prompt = self.build_prompt(
            theme,
            user_id,
            nickname,
            player_messages,
            avatar_caption,
        )
        system_prompt = await self._build_system_prompt(umo)
        prompt = self._apply_persona_reinforcement(prompt, system_prompt)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("prompt", prompt)

        response = await call_provider_with_retry(
            self.context,
            self.config_manager,
            prompt=prompt,
            umo=umo,
            system_prompt=system_prompt,
            purpose=self.get_data_type(),
        )
        result_text = extract_response_text(response)
        if self.config_manager.get_debug_mode():
            self._save_debug_file("response", result_text)

        usage_dict = extract_token_usage(response)
        token_usage = TokenUsage(
            prompt_tokens=usage_dict["prompt_tokens"],
            completion_tokens=usage_dict["completion_tokens"],
            total_tokens=usage_dict["total_tokens"],
        )

        success, parsed, error = parse_json_object_response(result_text)
        if not success or not parsed:
            logger.error(f"{self.get_data_type()} JSON 解析失败: {error}")
            return None, token_usage, result_text

        return (
            self.create_data_object(parsed, avatar_url, avatar_caption),
            token_usage,
            result_text,
        )

    def _apply_persona_reinforcement(self, prompt: str, system_prompt: str | None) -> str:
        if not system_prompt or not system_prompt.strip():
            return prompt

        persona_content = system_prompt.strip()
        return self.editable_manager.render_prompt(
            "persona_reinforcement",
            {
                "system_prompt": persona_content,
                "data_type": self.get_data_type(),
                "prompt": prompt,
            },
        )

    def _save_debug_file(self, suffix: str, content: str):
        try:
            debug_dir = StarTools.get_data_dir() / "debug_data"
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = debug_dir / f"adventure_{suffix}.txt"
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"保存调试文件失败: {exc}")

    async def _build_system_prompt(self, umo: str | None) -> str:
        default_prompt = self.editable_manager.get_prompt("default_system_prompt")
        persona_mgr = getattr(self.context, "persona_manager", None)
        if persona_mgr is None:
            return default_prompt

        persona_prompt = None
        use_specific = self.config_manager.get_use_plugin_specific_persona()
        specific_id = self.config_manager.get_plugin_specific_persona_id()
        keep_original = self.config_manager.get_keep_original_persona()

        if use_specific and specific_id:
            try:
                persona_obj = await persona_mgr.get_persona(specific_id)
                persona_prompt = getattr(persona_obj, "system_prompt", None)
                if persona_prompt:
                    logger.debug(f"已应用插件指定人格: {specific_id}")
            except Exception as exc:
                logger.warning(f"获取插件指定人格失败 (ID: {specific_id}): {exc}")

        if not persona_prompt and keep_original and umo:
            persona_prompt = await self._resolve_session_persona_prompt(persona_mgr, umo)

        if not persona_prompt:
            try:
                personality = await persona_mgr.get_default_persona_v3(umo)
                if isinstance(personality, dict):
                    persona_prompt = personality.get("prompt")
                else:
                    persona_prompt = getattr(personality, "prompt", None)
            except Exception as exc:
                logger.debug(f"获取全局默认人格失败: {exc}")

        if isinstance(persona_prompt, str) and persona_prompt.strip():
            return (
                f"{persona_prompt.strip()}\n\n"
                f"请在保持上述人格风格的同时，严格完成{self.get_data_type()}生成任务。"
                "最终输出仍必须是纯 JSON，不要添加 Markdown 或解释。"
            )

        return default_prompt

    async def _resolve_session_persona_prompt(self, persona_mgr, umo: str) -> str | None:
        try:
            from astrbot.api import sp

            session_service_config = await sp.get_async(
                scope="umo",
                scope_id=str(umo),
                key="session_service_config",
                default={},
            )
            persona_id = (
                session_service_config.get("persona_id")
                if session_service_config
                else None
            )
            if persona_id and persona_id != "[%None]":
                persona_obj = await persona_mgr.get_persona(persona_id)
                prompt = getattr(persona_obj, "system_prompt", None)
                if prompt:
                    logger.debug(f"继承到会话选定人格: {persona_id}")
                    return prompt

            conv_mgr = getattr(self.context, "conversation_manager", None)
            if conv_mgr:
                curr_conv_id = await conv_mgr.get_curr_conversation_id(umo)
                if curr_conv_id:
                    conv_obj = await conv_mgr.get_conversation(umo, curr_conv_id)
                    conv_persona_id = getattr(conv_obj, "persona_id", None)
                    if conv_persona_id and conv_persona_id != "[%None]":
                        persona_obj = await persona_mgr.get_persona(conv_persona_id)
                        prompt = getattr(persona_obj, "system_prompt", None)
                        if prompt:
                            logger.debug(f"继承到对话人格: {conv_persona_id}")
                            return prompt
        except Exception as exc:
            logger.warning(f"识别会话人格失败 (umo: {umo}): {exc}")

        return None
