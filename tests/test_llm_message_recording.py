from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    info = debug
    warning = debug
    error = debug


astrbot = types.ModuleType("astrbot")
astrbot_api = types.ModuleType("astrbot.api")
astrbot_star = types.ModuleType("astrbot.api.star")
astrbot_api.logger = _Logger()
astrbot_star.StarTools = type("StarTools", (), {})
sys.modules.setdefault("astrbot", astrbot)
sys.modules.setdefault("astrbot.api", astrbot_api)
sys.modules.setdefault("astrbot.api.star", astrbot_star)

from src.infrastructure.analysis.utils import llm_utils  # noqa: E402
from src.infrastructure.analysis.analyzers.action_turn_analyzer import (  # noqa: E402
    ActionTurnAnalyzer,
)
from src.infrastructure.storage.recent_llm_message_repository import (  # noqa: E402
    RecentLLMMessageRepository,
)


class _Config:
    @staticmethod
    def get_llm_retries() -> int:
        return 1

    @staticmethod
    def get_llm_backoff() -> int:
        return 1

    @staticmethod
    def get_llm_provider_id() -> str:
        return "provider"


class _Recorder:
    def __init__(self):
        self.records = []

    def append(self, **record):
        self.records.append(record)


class _Context:
    def __init__(self, *, reject_contexts: bool = False):
        self.reject_contexts = reject_contexts
        self.calls = []

    @staticmethod
    def get_provider_by_id(provider_id):
        return object() if provider_id == "provider" else None

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_contexts and "contexts" in kwargs:
            raise TypeError("contexts is not supported")
        return SimpleNamespace(completion_text="ok")


class LLMMessageRecordingTests(unittest.IsolatedAsyncioTestCase):
    def test_recent_message_repository_defaults_to_twelve_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RecentLLMMessageRepository(Path(temp_dir))

            self.assertEqual(repository.get_limit(), 12)

            for index in range(14):
                repository.append(
                    purpose="测试",
                    provider_id="provider",
                    prompt=f"prompt-{index}",
                    system_prompt="system",
                    response=f"response-{index}",
                )

            records = repository.list_records()
            self.assertEqual(len(records), 12)
            self.assertEqual(records[0]["prompt"], "prompt-13")
            self.assertEqual(records[-1]["prompt"], "prompt-2")

    async def test_contexts_record_contains_runtime_prompt_once(self):
        prompt = "完整 action_turn_prompt"
        messages = ActionTurnAnalyzer._build_action_messages(prompt, "系统规则")
        recorder = _Recorder()
        context = _Context()

        with patch.object(llm_utils, "_recent_llm_message_repository", return_value=recorder):
            await llm_utils.call_provider_with_retry(
                context,
                _Config(),
                prompt=prompt,
                system_prompt="系统规则",
                messages=messages,
            )

        self.assertEqual(len(context.calls), 1)
        self.assertIn("contexts", context.calls[0])
        recorded_prompt = recorder.records[0]["prompt"]
        self.assertEqual(recorded_prompt.count(prompt), 1)
        self.assertNotIn("fallback_prompt", recorded_prompt)
        self.assertEqual(json.loads(recorded_prompt), {"messages": messages})

    async def test_fallback_record_contains_actual_prompt(self):
        prompt = "完整 action_turn_prompt"
        messages = ActionTurnAnalyzer._build_action_messages(prompt, "系统规则")
        recorder = _Recorder()
        context = _Context(reject_contexts=True)

        with patch.object(llm_utils, "_recent_llm_message_repository", return_value=recorder):
            await llm_utils.call_provider_with_retry(
                context,
                _Config(),
                prompt=prompt,
                system_prompt="系统规则",
                messages=messages,
            )

        self.assertEqual(len(context.calls), 2)
        self.assertIn("contexts", context.calls[0])
        self.assertEqual(
            context.calls[1],
            {
                "chat_provider_id": "provider",
                "prompt": prompt,
                "system_prompt": "系统规则",
            },
        )
        self.assertEqual(recorder.records[0]["prompt"], prompt)

    def test_action_persona_messages_wrap_runtime_prompt_once(self):
        messages = ActionTurnAnalyzer._build_action_messages("运行时 Prompt", "系统规则")

        self.assertEqual(
            [message["role"] for message in messages],
            [
                "system",
                "user",
                "assistant",
                "user",
                "assistant",
                "user",
                "assistant",
                "user",
                "assistant",
                "user",
            ],
        )
        self.assertEqual(messages[-1]["content"], "运行时 Prompt")
        self.assertEqual(
            sum(message["content"].count("运行时 Prompt") for message in messages),
            1,
        )


if __name__ == "__main__":
    unittest.main()
