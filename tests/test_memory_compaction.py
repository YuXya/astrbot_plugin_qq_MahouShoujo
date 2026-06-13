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
from src.domain.models.data_models import ActionTurnResult  # noqa: E402
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
                    "memory_text": "这段短记忆不应重复计数" * 20,
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
                    "memory_text": "旧交互摘要",
                    "world_date": "第二天",
                },
                {
                    "type": "interaction_memory",
                    "created_at": 4,
                    "memory_text": "最新交互摘要",
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
        self.assertEqual(cameo[0]["memory_text"], "最新交互摘要")

    def test_history_context_uses_summary_story_and_interaction_summary(self):
        logs = [
            {"type": "memory_summary", "summary": "长期摘要", "title": "过去"},
            {"type": "action_turn", "story_text": "旧正文", "title": "旧行动"},
            {
                "type": "action_turn",
                "story_text": "最新正文",
                "memory_text": "当前玩家自己的短事件记忆不应重复发送",
                "title": "新行动",
            },
        ]
        formatted = BattleDiaryAnalyzer._format_logs(logs)
        self.assertIn("长期摘要", formatted)
        self.assertIn("最新正文", formatted)
        self.assertNotIn("旧正文", formatted)
        self.assertNotIn("当前玩家自己的短事件记忆不应重复发送", formatted)

        cameo = BattleDiaryAnalyzer._format_cameo_memories(
            [{"type": "interaction_memory", "source_name": "甲", "memory_text": "共同处理事件"}]
        )
        self.assertIn("共同处理事件", cameo)
        self.assertNotIn("遭遇", cameo)
        self.assertNotIn("结算", cameo)

    def test_participant_memories_merge_and_sort_by_conversation_no(self):
        self._write_jsonl(
            "daily_memory.jsonl",
            [
                {
                    "type": "action_turn",
                    "conversation_no": 3,
                    "world_day_offset": 1,
                    "memory_text": "自己主动处理了第三个事件。",
                },
                {
                    "type": "action_turn",
                    "conversation_no": 1,
                    "world_day_offset": 0,
                    "memory_text": "自己主动处理了第一个事件。",
                },
            ],
        )
        self._write_jsonl(
            "cameo_memory.jsonl",
            [
                {
                    "type": "interaction_memory",
                    "conversation_no": 2,
                    "world_day_offset": 0,
                    "source_name": "另一名玩家",
                    "memory_text": "被卷入第二个事件。",
                }
            ],
        )

        records = self.repo._read_recent_participant_memories(self.user_dir, limit=2)

        self.assertEqual([item["conversation_no"] for item in records], [2, 3])
        self.assertEqual(
            [item["type"] for item in records],
            ["interaction_memory", "action_turn_memory"],
        )

    def test_body_participant_info_includes_recent_events(self):
        analyzer = BattleDiaryAnalyzer.__new__(BattleDiaryAnalyzer)
        analyzer.config_manager = types.SimpleNamespace(
            get_teammate_recent_record_count=lambda: 2
        )
        result = analyzer._format_teammate_info(
            [
                {
                    "主角": {"个人信息": {"姓名": "洛洛"}},
                    "最近事件": [
                        {
                            "conversation_no": 7,
                            "memory_text": "洛洛刚刚完成了调查。",
                        }
                    ],
                }
            ]
        )

        self.assertIn("最近事件", result["json"])
        self.assertIn("洛洛刚刚完成了调查", result["json"])

    def test_selection_projection_drops_recent_events(self):
        projected = BattleDiaryAnalyzer._prompt_protagonist_profiles(
            [
                {
                    "主角": {"个人信息": {"姓名": "洛洛"}},
                    "最近事件": [{"conversation_no": 7, "memory_text": "不应发送"}],
                }
            ]
        )

        self.assertEqual(projected, [{"个人信息": {"姓名": "洛洛"}}])

    def test_action_turn_saves_short_memory_with_conversation_no(self):
        (self.user_dir / "player_data_update.json").write_text(
            json.dumps({"进程": {"阶段": "日常"}, "主角": {}}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = ActionTurnResult(
            story_text="完整正文",
            memory_text="当前玩家的短事件记忆",
        )

        self.repo.save_action_turn_result(
            group_id="g1",
            user_id="u1",
            result=result,
        )
        saved = self.repo._read_recent_logs(
            self.user_dir / "daily_memory.jsonl",
            limit=1,
        )[0]

        self.assertEqual(result.conversation_no, 1)
        self.assertEqual(saved["conversation_no"], 1)
        self.assertEqual(saved["memory_text"], "当前玩家的短事件记忆")


class InteractionSummaryTests(unittest.TestCase):
    def test_current_player_memory_is_created_without_participants(self):
        analyzer = MemorySummaryAnalyzer(None, _Config(), _Editable())

        async def fake_call(prompt, *, umo, purpose):
            return json.dumps(
                {"current_player_memory": "主角独自完成了本轮调查。", "interactions": []},
                ensure_ascii=False,
            )

        analyzer._call = fake_call
        result = asyncio.run(
            analyzer.summarize_interactions(
                action="调查",
                story_text="主角独自完成调查。",
                world_date="今天",
                protagonist={},
                participants=[],
            )
        )

        self.assertEqual(result["current_player_memory"], "主角独自完成了本轮调查。")
        self.assertEqual(result["interactions"], [])

    def test_only_accepts_candidate_participants(self):
        analyzer = MemorySummaryAnalyzer(None, _Config(), _Editable())

        async def fake_call(prompt, *, umo, purpose):
            return json.dumps(
                {
                    "current_player_memory": "主角处理了本轮事件。",
                    "interactions": [
                        {"target": "小洛", "memory_text": "与主角共同处理了事件。"},
                        {"target": "路人", "memory_text": "不应写入。"},
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
        self.assertEqual(
            result,
            {
                "current_player_memory": "主角处理了本轮事件。",
                "interactions": [{"target": "洛洛", "memory_text": "与主角共同处理了事件。"}],
            },
        )

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
            service._summarize_turn_memories(
                player_data={"主角": {"个人信息": {"姓名": "主角"}}},
                result=types.SimpleNamespace(action="行动", story_text="正文", title="行动"),
                participants=[{"_user_id": "u2", "target_name": "洛洛"}],
                umo=None,
                world_date="今天",
            )
        )
        self.assertEqual(result, {"current_player_memory": "", "interactions": []})

    def test_interaction_memory_keeps_source_conversation_no(self):
        written = []

        class Repository:
            def append_interaction_memory(self, group_id, user_id, payload):
                written.append((group_id, user_id, payload))

        service = ActionTurnApplicationService.__new__(ActionTurnApplicationService)
        service.save_repository = Repository()
        affected = asyncio.run(
            service._append_interaction_memories(
                group_id="g1",
                user_id="u1",
                player_data={"主角": {"个人信息": {"姓名": "主角"}}},
                result=types.SimpleNamespace(title="行动", conversation_no=19),
                participants=[{"_user_id": "u2", "target_name": "洛洛"}],
                interactions=[{"target": "洛洛", "memory_text": "洛洛协助主角完成调查。"}],
                world_day_offset=3,
                world_date="第三天",
            )
        )

        self.assertEqual(affected, ["u2"])
        self.assertEqual(written[0][2]["conversation_no"], 19)
        self.assertEqual(written[0][2]["memory_text"], "洛洛协助主角完成调查。")


class ParticipantSelectionMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_selection_candidates_are_loaded_without_recent_events(self):
        calls = []

        class Repository:
            def build_city_teammate_candidates(self, group_id, user_id, *, recent_record_count):
                calls.append(recent_record_count)
                return []

            def build_city_magical_girl_candidates(
                self, group_id, user_id, *, recent_record_count
            ):
                calls.append(recent_record_count)
                return []

            def build_public_monster_candidates(self):
                return []

        class Analyzer:
            async def select_action_context(self, **kwargs):
                return {}

        service = ActionTurnApplicationService.__new__(ActionTurnApplicationService)
        service.save_repository = Repository()
        service.llm_analyzer = Analyzer()

        await service._select_context(
            group_id="g1",
            user_id="u1",
            player_data={},
            logs=[],
            cameo_memories=[],
            action_text="行动",
            umo=None,
        )

        self.assertEqual(calls, [0, 0])


if __name__ == "__main__":
    unittest.main()
