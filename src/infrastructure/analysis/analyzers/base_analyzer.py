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
    ) -> str:
        pass

    @abstractmethod
    def create_data_object(
        self,
        data: dict,
    ) -> TDataObject:
        pass

    async def analyze(
        self,
        theme: str,
        user_id: str | None = None,
        nickname: str | None = None,
        umo: str | None = None,
    ) -> tuple[TDataObject | None, TokenUsage, str]:
        prompt = self.build_prompt(
            theme,
            user_id,
            nickname,
        )
        system_prompt = self.editable_manager.get_prompt("card_system_prompt")
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
            self.create_data_object(parsed),
            token_usage,
            result_text,
        )

    def _save_debug_file(self, suffix: str, content: str):
        try:
            debug_dir = StarTools.get_data_dir() / "debug_data"
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = debug_dir / f"adventure_{suffix}.txt"
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"保存调试文件失败: {exc}")
