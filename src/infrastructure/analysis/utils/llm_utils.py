from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import asdict, is_dataclass
from typing import Any

from ....utils.logger import logger
from ...storage.recent_llm_message_repository import RecentLLMMessageRepository


_recent_llm_messages: RecentLLMMessageRepository | None = None


def _recent_llm_message_repository() -> RecentLLMMessageRepository:
    global _recent_llm_messages
    if _recent_llm_messages is None:
        _recent_llm_messages = RecentLLMMessageRepository()
    return _recent_llm_messages


def mark_latest_llm_error(error: str) -> None:
    _recent_llm_message_repository().mark_latest_error(error)


async def get_provider_id_with_fallback(
    context,
    config_manager,
    umo: str | None = None,
    provider_id_override: str | None = None,
) -> str | None:
    configured_ids: list[str] = []
    for candidate in (
        provider_id_override,
        config_manager.get_llm_provider_id(),
    ):
        configured = str(candidate or "").strip()
        if configured and configured not in configured_ids:
            configured_ids.append(configured)

    for configured in configured_ids:
        try:
            provider = context.get_provider_by_id(provider_id=configured)
            if provider:
                return configured
        except TypeError:
            try:
                provider = context.get_provider_by_id(configured)
                if provider:
                    return configured
            except Exception as exc:
                logger.warning(f"配置的 Provider 不可用: {configured}, {exc}")
        except Exception as exc:
            logger.warning(f"配置的 Provider 不可用: {configured}, {exc}")

    if umo:
        try:
            provider_id = await context.get_current_chat_provider_id(umo=umo)
            if provider_id:
                return provider_id
        except Exception as exc:
            logger.warning(f"获取当前会话 Provider 失败: {exc}")

    try:
        providers = context.get_all_providers()
        if providers:
            meta = providers[0].meta()
            provider_id = getattr(meta, "id", None)
            if provider_id:
                return provider_id
    except Exception as exc:
        logger.warning(f"获取可用 Provider 失败: {exc}")

    return None


async def call_provider_with_retry(
    context,
    config_manager,
    prompt: str,
    umo: str | None = None,
    system_prompt: str | None = None,
    messages: list[dict[str, str]] | None = None,
    purpose: str = "文本补全",
    provider_id_override: str | None = None,
):
    retries = max(1, config_manager.get_llm_retries())
    backoff = max(1, config_manager.get_llm_backoff())
    last_exc: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            provider_id = await get_provider_id_with_fallback(
                context,
                config_manager,
                umo,
                provider_id_override=provider_id_override,
            )
            if not provider_id:
                raise RuntimeError("没有可用的 LLM Provider")

            kwargs = {"chat_provider_id": provider_id, "prompt": prompt}
            if system_prompt:
                kwargs["system_prompt"] = system_prompt
            if messages:
                kwargs = {"chat_provider_id": provider_id, "messages": messages}

            logger.info(
                f"[LLM] 调用 Provider={provider_id}, attempt={attempt}, prompt_len={len(prompt)}, messages={bool(messages)}"
            )
            try:
                response = await context.llm_generate(**kwargs)
            except TypeError as exc:
                if not messages:
                    raise
                logger.warning(
                    f"LLM Provider 不支持 messages 参数，回退到 prompt/system_prompt: {exc}"
                )
                fallback_kwargs = {"chat_provider_id": provider_id, "prompt": prompt}
                if system_prompt:
                    fallback_kwargs["system_prompt"] = system_prompt
                response = await context.llm_generate(**fallback_kwargs)
            _recent_llm_message_repository().append(
                purpose=purpose,
                provider_id=provider_id,
                prompt=_format_prompt_record(prompt, messages),
                system_prompt=system_prompt,
                response=extract_response_text(response),
                raw_response=extract_response_debug_json(response),
            )
            return response
        except Exception as exc:
            last_exc = exc
            logger.warning(f"LLM 调用失败: attempt={attempt}, error={exc}")
            if attempt < retries:
                await asyncio.sleep(backoff * attempt)

    logger.error(f"LLM 调用全部失败: {last_exc}")
    _recent_llm_message_repository().append(
        purpose=purpose,
        provider_id=provider_id if "provider_id" in locals() else "",
        prompt=_format_prompt_record(prompt, messages),
        system_prompt=system_prompt,
        response="",
        error=format_exception_detail(last_exc),
    )
    return None


def _format_prompt_record(
    prompt: str,
    messages: list[dict[str, str]] | None = None,
) -> str:
    if not messages:
        return prompt
    return json.dumps(
        {
            "messages": messages,
            "fallback_prompt": prompt,
        },
        ensure_ascii=False,
        indent=2,
    )


def extract_response_text(response) -> str:
    if response is None:
        return ""
    text = getattr(response, "completion_text", None)
    return str(text if text is not None else response).strip()


def extract_response_debug_json(response) -> str:
    if response is None:
        return ""
    return json.dumps(
        _to_jsonable(response),
        ensure_ascii=False,
        indent=2,
    )


def format_exception_detail(exc: Exception | None) -> str:
    if exc is None:
        return ""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
    return detail or f"{type(exc).__name__}: {exc}"


def _to_jsonable(value: Any, seen: set[int] | None = None) -> Any:
    if seen is None:
        seen = set()

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    value_id = id(value)
    if value_id in seen:
        return f"<circular {type(value).__name__}>"
    seen.add(value_id)

    try:
        if isinstance(value, dict):
            return {str(key): _to_jsonable(item, seen) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_to_jsonable(item, seen) for item in value]
        if is_dataclass(value):
            return _to_jsonable(asdict(value), seen)
        if hasattr(value, "model_dump"):
            return _to_jsonable(value.model_dump(), seen)
        if hasattr(value, "dict") and callable(value.dict):
            return _to_jsonable(value.dict(), seen)
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return _to_jsonable(value.to_dict(), seen)
        if hasattr(value, "__dict__"):
            return {
                key: _to_jsonable(item, seen)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        return repr(value)
    except Exception as exc:
        return {
            "type": type(value).__name__,
            "repr": repr(value),
            "serialization_error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        seen.discard(value_id)


def extract_token_usage(response) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None and hasattr(response, "raw_completion"):
        usage = getattr(response.raw_completion, "usage", None)

    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    if isinstance(usage, dict):
        return {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }

    prompt_tokens = getattr(usage, "input", None)
    completion_tokens = getattr(usage, "output", None)
    total_tokens = getattr(usage, "total", None)
    if prompt_tokens is None:
        prompt_tokens = getattr(usage, "prompt_tokens", 0)
    if completion_tokens is None:
        completion_tokens = getattr(usage, "completion_tokens", 0)
    if total_tokens is None:
        total_tokens = getattr(usage, "total_tokens", 0)

    return {
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int(total_tokens or 0),
    }
