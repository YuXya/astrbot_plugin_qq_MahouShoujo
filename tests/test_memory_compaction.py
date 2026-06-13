from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


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

from src.infrastructure.analysis.analyzers.battle_diary_analyzer import (  # noqa: E402
    BattleDiaryAnalyzer,
)
from src.infrastructure.analysis.analyzers.memory_summary_analyzer import (  # noqa: E402
    MemorySummaryAnalyzer,
)
from src.application.services.action_turn_application_service import (  # noqa: E402
    ActionTurnApplicationService,
)
from src.infrastructure.storage.player_save_repository import (  # noqa: E402
    PlayerSaveRepository,
)


class _Config:
    def get_interaction_memory_target_chars(self):
        return 100

    def get_memory_compaction_target_chars(self):
        return 2000

    def get_debug_mode(self):
        return False

    def get_subtask_llm_provider_id(self):
        return ""


class _Editable:
    def render_prompt(self, name, values):
        return f"{name}:{json.dumps(values, ensure_ascii=False)}"

    def get_prompt(self, name):
        return ""


class MemoryCompactionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.repo = PlayerSaveRepository.__new__(PlayerSaveRepository)
        self.repo.root_dir = self.root
        self.repo.editable_manager = None
        self.user_dir = self.root / "groups" / "g1" / "users" / "u1"
        self.user_dir.mkdir(parents=True)

    def _write_jsonl(self, name: str, records: list[dict]):
        text = "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n"
        (self.user_dir / name).write_text(text, encoding="utf-8")

    def test_compaction_counts_only_memory_text_and_keeps_latest_records(self):
        self._write_jsonl(
            "daily_memory.jsonl",
            [
                {
                    "type": "action_turn",
                    "created_at": 1,
                    "story_text": "旧行动正文",
                    "action_options": ["这段很长但不应计数" * 20],
                    "world_date": "第一天",
                },
                {
                    "type": "action_turn",
                    "created_at": 3,
                    "story_text": "最新行动正文",
                    "world_date": "第三天",
                },
            ],
        )
        self._write_jsonl(
            "cameo_memory.jsonl",
            [
                {
                    "type": "interaction_memory",
                    "created_at": 2,
                    "summary": "旧交互摘要",
                    "world_date": "第二天",
                },
                {
                    "type": "interaction_memory",
                    "created_at": 4,
                    "summary": "最新交互摘要",
                    "world_date": "第四天",
                },
            ],
        )

        self.assertIsNone(
            self.repo.prepare_memory_compaction("g1", "u1", threshold_chars=100)
        )
        prepared = self.repo.prepare_memory_compaction("g1", "u1", threshold_chars=1)
        self.assertEqual(len(prepared["records"]), 2)
        self.assertEqual(
            [item["text"] for item in prepared["records"]],
            ["旧行动正文", "旧交互摘要"],
        )

        self.repo.apply_memory_compaction(
            "g1",
            "u1",
            prepared=prepared,
            summary_text="长期客观摘要",
        )
        daily = self.repo._read_recent_logs(self.user_dir / "daily_memory.jsonl", limit=0)
        cameo = self.repo._read_recent_logs(self.user_dir / "cameo_memory.jsonl", limit=0)
        self.assertEqual([item["type"] for item in daily], ["memory_summary", "action_turn"])
        self.assertEqual(daily[0]["summary"], "长期客观摘要")
        self.assertEqual(daily[1]["story_text"], "最新行动正文")
        self.assertEqual(len(cameo), 1)
        self.assertEqual(cameo[0]["summary"], "最新交互摘要")

    def test_history_context_uses_summary_story_and_interaction_summary(self):
        logs = [
            {"type": "memory_summary", "summary": "长期摘要", "title": "过去"},
            {"type": "action_turn", "story_text": "旧正文", "title": "旧行动"},
            {"type": "action_turn", "story_text": "最新正文", "title": "新行动"},
        ]
        formatted = BattleDiaryAnalyzer._format_logs(logs)
        self.assertIn("长期摘要", formatted)
        self.assertIn("最新正文", formatted)
        self.assertNotIn("旧正文", formatted)

        cameo = BattleDiaryAnalyzer._format_cameo_memories(
            [{"type": "interaction_memory", "source_name": "甲", "summary": "共同处理事件"}]
        )
        self.assertIn("共同处理事件", cameo)
        self.assertNotIn("遭遇", cameo)
        self.assertNotIn("结算", cameo)


class InteractionSummaryTests(unittest.TestCase):
    def test_only_accepts_candidate_participants(self):
        analyzer = MemorySummaryAnalyzer(None, _Config(), _Editable())

        async def fake_call(prompt, *, umo, purpose):
            return json.dumps(
                {
                    "interactions": [
                        {"target": "小洛", "summary": "与主角共同处理了事件。"},
                        {"target": "路人", "summary": "不应写入。"},
                    ]
                },
                ensure_ascii=False,
            )

        analyzer._call = fake_call
        result = asyncio.run(
            analyzer.summarize_interactions(
                action="行动",
                story_text="正文",
                world_date="今天",
                protagonist={},
                participants=[{"target_name": "洛洛", "魔法少女名": "小洛"}],
            )
        )
        self.assertEqual(result, [{"target": "洛洛", "summary": "与主角共同处理了事件。"}])

    def test_summary_failure_skips_interaction_write(self):
        class FailingAnalyzer:
            async def summarize_interactions(self, **kwargs):
                raise ValueError("子任务失败")

        class Repository:
            def append_interaction_memory(self, *args, **kwargs):
                raise AssertionError("失败时不应写入")

        service = ActionTurnApplicationService.__new__(ActionTurnApplicationService)
        service.memory_summary_analyzer = FailingAnalyzer()
        service.save_repository = Repository()
        result = asyncio.run(
            service._append_interaction_memories(
                group_id="g1",
                user_id="u1",
                player_data={"主角": {"个人信息": {"姓名": "主角"}}},
                result=types.SimpleNamespace(action="行动", story_text="正文", title="行动"),
                participants=[{"_user_id": "u2", "target_name": "洛洛"}],
                umo=None,
                world_day_offset=0,
                world_date="今天",
            )
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
