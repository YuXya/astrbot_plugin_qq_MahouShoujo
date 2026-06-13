from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


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

from src.application.services.action_turn_application_service import (  # noqa: E402
    ActionTurnApplicationService,
)
from src.domain.models.data_models import ActionTurnResult  # noqa: E402
from src.domain.services.battle_outcome_service import (  # noqa: E402
    resolve_battle_outcome,
)
from src.infrastructure.event_book.engine import EventBookEngine  # noqa: E402
from src.infrastructure.analysis.analyzers.battle_diary_analyzer import (  # noqa: E402
    BattleDiaryAnalyzer,
)
from src.infrastructure.analysis.analyzers.action_turn_analyzer import (  # noqa: E402
    ActionTurnAnalyzer,
)
from src.infrastructure.reporting.generators import ReportGenerator  # noqa: E402
from src.infrastructure.storage.player_save_repository import (  # noqa: E402
    PlayerSaveRepository,
)


def _runtime(*, ai: int = 50, desire: int = 50, turn_count: int = 1) -> dict:
    return {
        "scene_event": {"id": "capture", "title": "抓捕事件", "reason": "测试"},
        "selected_participants": [],
        "selected_targets": [{"id": "monster", "name": "测试魔物"}],
        "ai_win_rate": ai,
        "desire_win_rate": desire,
        "started_at": "公元2020年4月1日",
        "turn_count": turn_count,
    }


class EventBookLookupTests(unittest.TestCase):
    def test_get_scene_event_returns_latest_full_entry(self):
        root = Path(tempfile.mkdtemp())
        path = root / "event_book.json"
        path.write_text(
            json.dumps(
                {
                    "categories": [
                        {
                            "id": "monster_enemy",
                            "name": "目标是魔物",
                            "events": [
                                {
                                    "id": "capture",
                                    "name": "抓捕事件",
                                    "content": "事件正文",
                                    "event_gimmick": "事件机制",
                                    "success_ending": "顺利指导",
                                    "obstacle_ending": "受阻指导",
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        event = EventBookEngine(book_path=path).get_scene_event("capture")
        self.assertEqual(event["title"], "抓捕事件")
        self.assertEqual(event["content"], "事件正文")
        self.assertEqual(event["obstacle_ending"], "受阻指导")

    def test_candidates_only_filter_by_enabled_state_and_command(self):
        root = Path(tempfile.mkdtemp())
        path = root / "event_book.json"
        path.write_text(
            json.dumps(
                {
                    "categories": [
                        {
                            "id": "monster_enemy",
                            "events": [
                                {
                                    "id": "keyword_miss",
                                    "name": "关键词未命中也保留",
                                    "enabled": True,
                                    "command": "/魔法少女行动",
                                    "keys": ["逛街"],
                                },
                                {
                                    "id": "disabled",
                                    "enabled": False,
                                    "command": "/魔法少女行动",
                                },
                                {
                                    "id": "other_command",
                                    "enabled": True,
                                    "command": "/魔法少女战斗",
                                },
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        candidates = EventBookEngine(book_path=path).build_scene_event_candidates(
            current_event="/魔法少女行动"
        )

        self.assertEqual([candidate["id"] for candidate in candidates], ["keyword_miss"])
        self.assertEqual(candidates[0]["keys"], ["逛街"])


class SelectionPromptCandidateTests(unittest.TestCase):
    def test_candidate_views_only_keep_selection_fields(self):
        scene_events = BattleDiaryAnalyzer._prompt_scene_event_candidates(
            [
                {
                    "id": "event-1",
                    "title": "事件名称",
                    "keys": ["关键词"],
                    "location_tags": ["地点"],
                    "compatible_monsters": ["魔物"],
                    "content": "事件正文",
                    "event_gimmick": "不应传入",
                    "success_ending": "不应传入",
                    "category_name": "不应传入",
                }
            ]
        )
        monsters = BattleDiaryAnalyzer._prompt_monster_candidates(
            [
                {
                    "id": "monster-1",
                    "name": "魔物名称",
                    "keys": ["关键词"],
                    "content": "魔物正文",
                    "battle_gimmick": "不应传入",
                    "victory_ending": "不应传入",
                }
            ]
        )

        self.assertEqual(
            set(scene_events[0]),
            {"id", "title", "keys", "location_tags", "compatible_monsters", "content"},
        )
        self.assertEqual(set(monsters[0]), {"id", "name", "keys", "content"})
        prompt_json = BattleDiaryAnalyzer._json_dump(
            {"scene_events": scene_events, "monsters": monsters}
        )
        self.assertNotIn("event_gimmick", prompt_json)
        self.assertNotIn("battle_gimmick", prompt_json)
        self.assertIn("\n", prompt_json)

    def test_action_context_prompt_uses_filtered_candidate_views(self):
        template = "candidates={{candidates_json}}"

        def render_prompt(name, variables):
            text = template
            for key, value in variables.items():
                text = text.replace("{{" + key + "}}", str(value))
            return text

        analyzer = BattleDiaryAnalyzer.__new__(BattleDiaryAnalyzer)
        analyzer.editable_manager = SimpleNamespace(render_prompt=render_prompt)
        prompt = analyzer.build_action_context_prompt(
            action_text="自由行动",
            player_data={},
            logs=[],
            cameo_memories=[],
            participant_candidates=[],
            magical_girl_candidates=[],
            scene_event_candidates=[
                {
                    "id": "event-1",
                    "title": "事件名称",
                    "keys": ["关键词"],
                    "location_tags": ["地点"],
                    "compatible_monsters": ["魔物"],
                    "content": "事件正文",
                    "event_gimmick": "不应传入",
                }
            ],
            monster_candidates=[
                {
                    "id": "monster-1",
                    "name": "魔物名称",
                    "keys": ["关键词"],
                    "content": "魔物正文",
                    "battle_gimmick": "不应传入",
                }
            ],
        )

        self.assertNotIn("event_gimmick", prompt)
        self.assertNotIn("battle_gimmick", prompt)
        self.assertIn('"title": "事件名称"', prompt)
        self.assertIn('"name": "魔物名称"', prompt)


class PureLlmSelectionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _analyzer(scene_events):
        analyzer = BattleDiaryAnalyzer.__new__(BattleDiaryAnalyzer)
        analyzer.context = None
        analyzer.event_book_engine = SimpleNamespace(
            build_scene_event_candidates=lambda **kwargs: scene_events
        )
        analyzer.editable_manager = SimpleNamespace(
            render_prompt=lambda *args, **kwargs: "",
            get_prompt=lambda *args, **kwargs: "",
        )
        analyzer.config_manager = SimpleNamespace(
            get_debug_mode=lambda: False,
            get_subtask_llm_provider_id=lambda: None,
        )
        return analyzer

    async def test_daily_empty_llm_selection_stays_empty_even_when_keys_match(self):
        analyzer = self._analyzer(
            [{"id": "shopping", "title": "逛街魔物", "keys": ["逛街"]}]
        )
        response = SimpleNamespace(
            completion_text=json.dumps(
                {
                    "action_target": {"type": "daily_life", "target": "逛街"},
                    "participant_names": [],
                    "scene_event": None,
                    "selected_targets": [],
                },
                ensure_ascii=False,
            )
        )

        with patch(
            "src.infrastructure.analysis.analyzers.battle_diary_analyzer.call_provider_with_retry",
            new=AsyncMock(return_value=response),
        ):
            context = await analyzer.select_action_context(
                action_text="逛街",
                player_data={},
                logs=[],
                cameo_memories=[],
                participant_candidates=[],
                magical_girl_candidates=[],
                monster_candidates=[
                    {"id": "monster", "name": "逛街魔物", "keys": ["逛街"]}
                ],
                event_command="/魔法少女行动",
            )

        self.assertIsNone(context["scene_event"])
        self.assertEqual(context["selected_targets"], [])
        self.assertEqual(context["action_target"]["type"], "daily_life")
        self.assertEqual(context["ai_win_rate"], 50)
        self.assertEqual(context["desire_win_rate"], 50)
        self.assertNotIn("battle_odds", context)

    async def test_daily_explicit_llm_selection_resolves_candidates(self):
        analyzer = self._analyzer([{"id": "shopping", "title": "逛街事件"}])
        response = SimpleNamespace(
            completion_text=json.dumps(
                {
                    "participant_names": [],
                    "scene_event": {"id": "shopping", "reason": "LLM 选择"},
                    "selected_targets": [{"id": "monster", "reason": "LLM 选择"}],
                    "is_continuous_event": False,
                    "ai_win_rate": 90,
                    "desire_win_rate": 90,
                },
                ensure_ascii=False,
            )
        )

        with patch(
            "src.infrastructure.analysis.analyzers.battle_diary_analyzer.call_provider_with_retry",
            new=AsyncMock(return_value=response),
        ):
            context = await analyzer.select_action_context(
                action_text="普通日常",
                player_data={},
                logs=[],
                cameo_memories=[],
                participant_candidates=[],
                magical_girl_candidates=[],
                monster_candidates=[{"id": "monster", "name": "测试魔物"}],
                event_command="/魔法少女行动",
            )

        self.assertEqual(context["scene_event"]["id"], "shopping")
        self.assertEqual(context["selected_targets"][0]["id"], "monster")
        self.assertEqual(context["ai_win_rate"], 50)
        self.assertNotIn("battle_odds", context)

    async def test_continuous_event_builds_battle_odds(self):
        analyzer = self._analyzer([{"id": "capture", "title": "抓捕事件"}])
        response = SimpleNamespace(
            completion_text=json.dumps(
                {
                    "action_target": {"type": "战斗", "target": "测试魔物"},
                    "is_continuous_event": True,
                    "selected_participants": [],
                    "scene_event": {"id": "capture", "reason": "持续追击"},
                    "selected_targets": [{"id": "monster"}],
                    "ai_win_rate": 70,
                    "desire_win_rate": 80,
                },
                ensure_ascii=False,
            )
        )

        with patch(
            "src.infrastructure.analysis.analyzers.battle_diary_analyzer.call_provider_with_retry",
            new=AsyncMock(return_value=response),
        ):
            with patch(
                "src.domain.services.battle_outcome_service.random.randint",
                return_value=50,
            ):
                context = await analyzer.select_action_context(
                    action_text="追击魔物",
                    player_data={},
                    logs=[],
                    cameo_memories=[],
                    participant_candidates=[],
                    magical_girl_candidates=[],
                    monster_candidates=[{"id": "monster", "name": "测试魔物"}],
                )

        self.assertTrue(context["is_continuous_event"])
        self.assertEqual(context["battle_odds"]["player_win_rate"], 75)

    async def test_invalid_json_falls_back_to_daily_without_roll(self):
        analyzer = self._analyzer([])
        response = SimpleNamespace(completion_text="not json")

        with patch(
            "src.infrastructure.analysis.analyzers.battle_diary_analyzer.call_provider_with_retry",
            new=AsyncMock(return_value=response),
        ), patch(
            "src.infrastructure.analysis.analyzers.battle_diary_analyzer.mark_latest_llm_error"
        ):
            context = await analyzer.select_action_context(
                action_text="自由行动",
                player_data={},
                logs=[],
                cameo_memories=[],
                participant_candidates=[],
                magical_girl_candidates=[],
                monster_candidates=[],
            )

        self.assertEqual(context["action_target"]["type"], "日常")
        self.assertFalse(context["is_continuous_event"])
        self.assertNotIn("battle_odds", context)


class EventContextTests(unittest.TestCase):
    def _service(self):
        service = ActionTurnApplicationService.__new__(ActionTurnApplicationService)
        service.event_book_engine = SimpleNamespace(
            get_scene_event=lambda event_id: {
                "id": event_id,
                "title": "最新版事件",
                "content": "最新版正文",
                "success_ending": "成功",
                "obstacle_ending": "受阻",
            }
        )
        return service

    def test_dice_equal_to_rate_is_success_and_higher_dice_is_obstacle(self):
        success = self._service()._active_event_context(
            _runtime(ai=70, desire=70),
            battle_odds={"dice_roll": 70},
        )
        obstacle = self._service()._active_event_context(
            _runtime(ai=70, desire=70),
            battle_odds={"dice_roll": 71},
        )
        self.assertEqual(success["event_outcome"]["result"], "success")
        self.assertEqual(obstacle["event_outcome"]["result"], "obstacle")
        self.assertEqual(success["battle_odds"]["dice_roll"], 70)
        self.assertEqual(obstacle["battle_odds"]["dice_roll"], 71)
        self.assertEqual(success["scene_event"]["content"], "最新版正文")

    def test_active_context_only_exposes_full_target_once(self):
        runtime = _runtime(turn_count=3)
        context = self._service()._active_event_context(runtime)
        visible = ActionTurnAnalyzer._visible_current_variables(
            {"进程": {"阶段": "事件", "当前事件": runtime}}
        )

        self.assertEqual(context["selected_targets"][0]["id"], "monster")
        self.assertEqual(
            context["event_runtime"],
            {"started_at": "公元2020年4月1日", "turn_count": 3},
        )
        self.assertNotIn("selected_targets", context["event_runtime"])
        self.assertNotIn("selected_participants", context["event_runtime"])
        self.assertNotIn("scene_event", context["event_runtime"])
        llm_payload = json.dumps(
            {"selection_context": context, "current_variables": visible},
            ensure_ascii=False,
        )
        self.assertEqual(llm_payload.count('"id": "monster"'), 1)


class ActionPromptProjectionTests(unittest.TestCase):
    def test_current_event_is_hidden_from_prompt_snapshot_without_mutating_save(self):
        player_data = {
            "进程": {"阶段": "事件", "当前事件": _runtime()},
            "主角": {"姓名": "测试主角"},
        }

        visible = ActionTurnAnalyzer._visible_current_variables(player_data)

        self.assertEqual(visible["进程"], {"阶段": "事件"})
        self.assertEqual(
            player_data["进程"]["当前事件"]["selected_targets"][0]["id"],
            "monster",
        )

    def test_action_messages_establish_cat_god_dm_before_runtime_prompt(self):
        prompt = "完整运行时 Prompt"
        messages = ActionTurnAnalyzer._build_action_messages(prompt, "系统规则")

        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "user", "assistant", "user"],
        )
        assistant_history = "\n".join(
            message["content"]
            for message in messages
            if message["role"] == "assistant"
        )
        self.assertIn("小猫之神", messages[1]["content"])
        self.assertIn("小猫之神", assistant_history)
        self.assertIn("小鱼干", assistant_history)
        self.assertIn("DM", assistant_history)
        self.assertIn("第三人称", assistant_history)
        self.assertEqual(messages[-1], {"role": "user", "content": prompt})

    def test_action_messages_without_prompt_do_not_create_persona_history(self):
        self.assertEqual(
            ActionTurnAnalyzer._build_action_messages("", "系统规则"),
            [{"role": "system", "content": "系统规则"}],
        )

    def test_event_completion_protocol_is_hidden_for_new_or_non_event_turns(self):
        self.assertEqual(
            ActionTurnAnalyzer._event_completion_protocol(
                "事件",
                {"event_runtime": {"turn_count": 0}},
            ),
            "",
        )
        self.assertEqual(
            ActionTurnAnalyzer._event_completion_protocol(
                "日常",
                {"event_runtime": {"turn_count": 4}},
            ),
            "",
        )
        self.assertEqual(
            ActionTurnAnalyzer._event_completion_protocol("事件", {}),
            "",
        )

    def test_event_completion_protocol_strengthens_with_turn_count(self):
        early = ActionTurnAnalyzer._event_completion_protocol(
            "事件",
            {"event_runtime": {"turn_count": 1}},
        )
        middle = ActionTurnAnalyzer._event_completion_protocol(
            "事件",
            {"event_runtime": {"turn_count": 3}},
        )
        late = ActionTurnAnalyzer._event_completion_protocol(
            "事件",
            {"event_runtime": {"turn_count": 6}},
        )

        self.assertIn("正文已经收束", early)
        self.assertIn("移除 /进程/当前事件", early)
        self.assertIn("具体、可指出的未解决事实", early)
        self.assertIn("不得仅为续写", middle)
        self.assertIn("凭空制造敌人", middle)
        self.assertIn("本轮应优先解决当前目标", late)
        self.assertIn("禁止新造阻碍延命", late)
        self.assertNotEqual(early, middle)
        self.assertNotEqual(middle, late)


class EventSaveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.repo = PlayerSaveRepository.__new__(PlayerSaveRepository)
        self.repo.root_dir = self.root
        self.repo.editable_manager = None
        user_dir = self.repo.get_user_dir("g1", "u1")
        user_dir.mkdir(parents=True)
        (user_dir / "player_data_update.json").write_text(
            json.dumps({"进程": {"阶段": "日常"}, "主角": {}}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save(self, patch, *, runtime=None, battle_odds=None):
        return self.repo.save_action_turn_result(
            group_id="g1",
            user_id="u1",
            result=ActionTurnResult(story_text="测试正文", json_patch=patch),
            event_runtime=runtime,
            battle_odds=battle_odds,
        )

    def test_start_continue_and_end_event(self):
        started = self._save([], runtime=_runtime(turn_count=0))
        self.assertEqual(started["进程"]["阶段"], "事件")
        self.assertEqual(started["进程"]["当前事件"]["turn_count"], 1)
        self.assertEqual(
            started["进程"]["当前事件"]["selected_targets"][0]["id"],
            "monster",
        )
        clock = self.repo._read_json(self.repo._world_clock_path("g1"))
        self.assertEqual(clock["next_conversation_no"], 2)
        self.assertEqual(clock["next_day_offset"], 0)

        continued = self._save([])
        self.assertEqual(continued["进程"]["当前事件"]["turn_count"], 2)
        self.assertEqual(
            continued["进程"]["当前事件"]["selected_targets"][0]["id"],
            "monster",
        )
        clock = self.repo._read_json(self.repo._world_clock_path("g1"))
        self.assertEqual(clock["next_conversation_no"], 3)
        self.assertEqual(clock["next_day_offset"], 0)

        ended = self._save(
            [
                {"op": "remove", "path": "/进程/当前事件"},
                {"op": "replace", "path": "/进程/阶段", "value": "日常"},
            ]
        )
        self.assertEqual(ended["进程"]["阶段"], "日常")
        self.assertNotIn("当前事件", ended["进程"])
        clock = self.repo._read_json(self.repo._world_clock_path("g1"))
        self.assertEqual(clock["next_conversation_no"], 4)
        self.assertEqual(clock["next_day_offset"], 0)

        logs = self.repo._read_recent_logs(
            self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl",
            limit=0,
        )
        self.assertEqual([item["conversation_no"] for item in logs], [1, 2, 3])
        self.assertEqual([item["event_started"] for item in logs], [True, False, False])
        self.assertEqual([item["event_ended"] for item in logs], [False, False, True])
        self.assertEqual([item["day_advanced"] for item in logs], [False, False, False])
        self.assertEqual({item["world_day_offset"] for item in logs}, {0})

    def test_event_can_start_and_end_in_same_turn(self):
        ended = self._save(
            [{"op": "remove", "path": "/进程/当前事件"}],
            runtime=_runtime(turn_count=0),
        )

        self.assertEqual(ended["进程"]["阶段"], "日常")
        clock = self.repo._read_json(self.repo._world_clock_path("g1"))
        self.assertEqual(clock["next_conversation_no"], 2)
        self.assertEqual(clock["next_day_offset"], 0)
        log = self.repo._read_recent_logs(
            self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl",
            limit=1,
        )[0]
        self.assertTrue(log["event_started"])
        self.assertTrue(log["event_ended"])
        self.assertFalse(log["day_advanced"])
        self.assertEqual(log["days_advanced"], 0)
        self.assertEqual(log["conversation_no"], 1)

    def test_active_event_can_advance_multiple_days_without_ending(self):
        state = self._save(
            [{"op": "delta", "path": "/世界/日期", "value": 3}],
            runtime=_runtime(turn_count=0),
        )

        self.assertIn("当前事件", state["进程"])
        self.assertEqual(self.repo.get_current_world_day_offset("g1"), 3)
        self.assertEqual(self.repo.get_current_conversation_no("g1"), 2)
        log = self.repo._read_recent_logs(
            self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl",
            limit=1,
        )[0]
        self.assertFalse(log["event_ended"])
        self.assertTrue(log["day_advanced"])
        self.assertEqual(log["days_advanced"], 3)
        self.assertEqual(
            log["json_patch"][-1],
            {"op": "delta", "path": "/世界/日期", "value": 3},
        )
        self.assertNotIn("日期", state.get("世界", {}))

    def test_event_end_uses_explicit_day_advance_without_extra_day(self):
        self._save([], runtime=_runtime(turn_count=0))
        self._save(
            [
                {"op": "delta", "path": "/世界/日期", "value": 3},
                {"op": "remove", "path": "/进程/当前事件"},
            ]
        )

        self.assertEqual(self.repo.get_current_world_day_offset("g1"), 3)
        log = self.repo._read_recent_logs(
            self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl",
            limit=1,
        )[0]
        self.assertTrue(log["event_ended"])
        self.assertEqual(log["days_advanced"], 3)

    def test_invalid_world_date_patch_does_not_consume_conversation(self):
        invalid_patches = [
            [{"op": "replace", "path": "/世界/日期", "value": "2020-04-02"}],
            [{"op": "delta", "path": "/世界/日期", "value": 0}],
            [{"op": "delta", "path": "/世界/日期", "value": -1}],
            [{"op": "delta", "path": "/世界/日期", "value": 1.5}],
        ]
        for invalid_patch in invalid_patches:
            with self.subTest(patch=invalid_patch):
                with self.assertRaises(ValueError):
                    self._save(invalid_patch, runtime=_runtime(turn_count=0))
                self.assertEqual(self.repo.get_current_conversation_no("g1"), 1)
                self.assertEqual(self.repo.get_current_world_day_offset("g1"), 0)
                self.assertFalse(
                    (self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl").exists()
                )

    def test_multiple_players_share_one_conversation_sequence(self):
        second_user_dir = self.repo.get_user_dir("g1", "u2")
        second_user_dir.mkdir(parents=True)
        (second_user_dir / "player_data_update.json").write_text(
            json.dumps({"进程": {"阶段": "日常"}, "主角": {}}, ensure_ascii=False),
            encoding="utf-8",
        )

        self._save([], runtime=_runtime(turn_count=0))
        self.repo.save_action_turn_result(
            group_id="g1",
            user_id="u2",
            result=ActionTurnResult(story_text="第二名玩家", json_patch=[]),
            event_runtime=_runtime(turn_count=0),
        )
        self._save([])

        u1_logs = self.repo._read_recent_logs(
            self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl",
            limit=0,
        )
        u2_logs = self.repo._read_recent_logs(
            second_user_dir / "daily_memory.jsonl",
            limit=0,
        )
        self.assertEqual([item["conversation_no"] for item in u1_logs], [1, 3])
        self.assertEqual([item["conversation_no"] for item in u2_logs], [2])
        self.assertEqual(self.repo.get_current_conversation_no("g1"), 4)
        self.assertEqual(self.repo.get_current_world_day_offset("g1"), 0)

    def test_validation_failure_does_not_consume_conversation_number(self):
        with self.assertRaises(ValueError):
            self.repo.save_action_turn_result(
                group_id="g1",
                user_id="u1",
                result=ActionTurnResult(story_text="失败", json_patch=[]),
                world_day_offset=3,
                event_runtime=_runtime(turn_count=0),
            )

        self.assertEqual(self.repo.get_current_conversation_no("g1"), 1)
        self.assertEqual(self.repo.get_current_world_day_offset("g1"), 0)
        self.assertFalse(
            (self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl").exists()
        )

    def test_event_swap_requires_whole_runtime_and_normalizes_unpaired_removal(self):
        self._save([], runtime=_runtime(turn_count=0))
        with self.assertRaisesRegex(ValueError, "切换事件必须整体替换"):
            self._save(
                [
                    {
                        "op": "replace",
                        "path": "/进程/当前事件/scene_event/id",
                        "value": "other",
                    }
                ]
            )

        next_runtime = {
            "scene_event": {"id": "other", "title": "新事件", "reason": "切换"},
            "selected_participants": [],
            "selected_targets": [{"id": "monster-2", "name": "新魔物"}],
            "ai_win_rate": 100,
            "desire_win_rate": 100,
            "started_at": "公元2020年4月1日",
            "turn_count": 99,
        }
        changed = self._save(
            [
                {
                    "op": "replace",
                    "path": "/进程/当前事件",
                    "value": next_runtime,
                }
            ]
        )
        self.assertEqual(changed["进程"]["当前事件"]["scene_event"]["id"], "other")
        self.assertEqual(changed["进程"]["当前事件"]["ai_win_rate"], 100)
        self.assertEqual(changed["进程"]["当前事件"]["turn_count"], 1)
        self.assertEqual(
            changed["进程"]["当前事件"]["selected_targets"],
            [{"id": "monster-2", "name": "新魔物"}],
        )

        replaced_target = self._save(
            [
                {
                    "op": "replace",
                    "path": "/进程/当前事件/selected_targets",
                    "value": [{"id": "monster-2", "name": "新魔物"}],
                }
            ]
        )
        service = ActionTurnApplicationService.__new__(ActionTurnApplicationService)
        service.event_book_engine = SimpleNamespace(get_scene_event=lambda event_id: None)
        context = service._active_event_context(replaced_target["进程"]["当前事件"])
        self.assertEqual(context["selected_targets"][0]["id"], "monster-2")

        ended = self._save([{"op": "remove", "path": "/进程/当前事件"}])
        self.assertEqual(ended["进程"]["阶段"], "日常")
        self.assertNotIn("当前事件", ended["进程"])

    def test_log_uses_supplied_battle_odds(self):
        odds = {
            "player_win_rate": 70,
            "dice_roll": 71,
            "outcome": "player_lose",
        }
        with patch.object(self.repo, "append_log") as append_log:
            self._save([], runtime=_runtime(turn_count=0), battle_odds=odds)

        log_entry = append_log.call_args.args[2]
        self.assertEqual(log_entry["event_win_rate"], 70)
        self.assertEqual(log_entry["event_dice_roll"], 71)
        self.assertEqual(log_entry["event_outcome"], "obstacle")
        self.assertEqual(log_entry["conversation_no"], 1)
        self.assertTrue(log_entry["event_started"])
        self.assertFalse(log_entry["day_advanced"])


class ConversationSequenceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.repo = PlayerSaveRepository.__new__(PlayerSaveRepository)
        self.repo.root_dir = self.root
        self.repo.editable_manager = None

    def test_backfills_group_sequence_across_players_once(self):
        group_dir = self.root / "groups" / "g1"
        (group_dir / "users" / "u1").mkdir(parents=True)
        (group_dir / "users" / "u2").mkdir(parents=True)
        (group_dir / "world_clock.json").write_text(
            json.dumps({"schema_version": 1, "next_day_offset": 4}),
            encoding="utf-8",
        )
        (group_dir / "users" / "u1" / "daily_memory.jsonl").write_text(
            json.dumps({"type": "action_turn", "created_at": 20}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (group_dir / "users" / "u2" / "daily_memory.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"type": "action_turn", "created_at": 10}, ensure_ascii=False),
                    json.dumps({"type": "action_turn", "created_at": 30}, ensure_ascii=False),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        self.assertEqual(self.repo.get_current_conversation_no("g1"), 4)
        self.assertEqual(self.repo.get_current_conversation_no("g1"), 4)
        self.assertEqual(self.repo.get_current_world_day_offset("g1"), 4)

        u1_logs = self.repo._read_recent_logs(
            group_dir / "users" / "u1" / "daily_memory.jsonl",
            limit=0,
        )
        u2_logs = self.repo._read_recent_logs(
            group_dir / "users" / "u2" / "daily_memory.jsonl",
            limit=0,
        )
        self.assertEqual(u1_logs[0]["conversation_no"], 2)
        self.assertEqual(
            [item["conversation_no"] for item in u2_logs],
            [1, 3],
        )
        self.assertNotIn(
            "_log_index",
            (group_dir / "users" / "u1" / "daily_memory.jsonl").read_text(
                encoding="utf-8"
            ),
        )


class EventFallbackTests(unittest.TestCase):
    def test_only_continuous_selection_builds_event_runtime(self):
        service = ActionTurnApplicationService.__new__(ActionTurnApplicationService)
        daily = service._build_event_runtime(
            {
                "is_continuous_event": False,
                "scene_event": {"id": "shopping", "title": "逛街"},
            },
            current_world_date="公元2020年4月1日",
        )
        continuous = service._build_event_runtime(
            {
                "is_continuous_event": True,
                "scene_event": {"id": "capture", "title": "抓捕事件"},
                "battle_odds": {"ai_win_rate": 60, "desire_win_rate": 70},
            },
            current_world_date="公元2020年4月1日",
        )

        self.assertIsNone(daily)
        self.assertEqual(continuous["scene_event"]["id"], "capture")

    def test_legacy_free_action_event_is_detected_for_cleanup(self):
        self.assertTrue(
            ActionTurnApplicationService._is_legacy_free_action_event(
                {"scene_event": {"id": "free_action_old"}}
            )
        )
        self.assertFalse(
            ActionTurnApplicationService._is_legacy_free_action_event(_runtime())
        )


class _ActiveEventRepository:
    def __init__(self):
        self.saved = None

    def load_player_save(self, group_id, user_id):
        return {
            "player_data": {"进程": {"阶段": "事件", "当前事件": _runtime()}},
            "logs": [],
            "cameo_memories": [],
        }

    def get_current_world_day_offset(self, group_id):
        return 0

    def format_world_date(self, offset):
        return "公元2020年4月1日"

    def build_city_teammate_candidates(self, *args, **kwargs):
        return []

    def build_city_magical_girl_candidates(self, *args, **kwargs):
        return []

    def build_public_monster_candidates(self):
        return [{"id": "tentacle", "name": "触手怪"}]

    def find_participant_npcs(self, *args, **kwargs):
        return []

    def save_action_turn_result(self, **kwargs):
        self.saved = kwargs
        return kwargs["result"].state_snapshot


class _ActiveEventAnalyzer:
    async def select_action_context(self, **kwargs):
        self.selection_input = kwargs
        return {
            "action_target": {"type": "战斗", "target": "触手怪"},
            "is_continuous_event": True,
            "scene_event": {"id": "tentacle_event", "title": "触手怪事件"},
            "selected_participants": [],
            "selected_targets": [{"id": "tentacle", "name": "触手怪"}],
            "battle_odds": {
                "ai_win_rate": 10,
                "desire_win_rate": 100,
                "player_win_rate": 55,
                "dice_roll": 70,
                "outcome": "player_lose",
            },
            "event_outcome": {
                "result": "obstacle",
                "battle_result": "player_lose",
                "guidance": "未能脱离",
            },
        }

    async def analyze_action_turn(self, **kwargs):
        self.selection_context = kwargs["selection_context"]
        return SimpleNamespace(
            result=ActionTurnResult(story_text="继续事件", json_patch=[]),
            raw_response="继续事件",
        )


class ActiveEventFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_event_selects_one_candidate_and_keeps_current_event(self):
        service = ActionTurnApplicationService.__new__(ActionTurnApplicationService)
        service.config_manager = SimpleNamespace(
            get_teammate_recent_record_count=lambda: 0
        )
        service.llm_analyzer = _ActiveEventAnalyzer()
        service.save_repository = _ActiveEventRepository()
        service.card_generator = None
        service.event_book_engine = SimpleNamespace(get_scene_event=lambda event_id: None)

        result = await service.execute_action_turn(
            group_id="g1",
            user_id="u1",
            nickname="测试",
            action_text="挑战触手怪",
            umo=None,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            service.llm_analyzer.selection_input["current_event"]["scene_event"]["id"],
            "capture",
        )
        self.assertEqual(
            service.llm_analyzer.selection_context["current_event"]["scene_event"]["id"],
            "capture",
        )
        self.assertEqual(
            service.llm_analyzer.selection_context["proposed_event"]["scene_event"]["id"],
            "tentacle_event",
        )
        self.assertEqual(result.result.selected_targets[0]["name"], "触手怪")
        self.assertIs(
            service.save_repository.saved["battle_odds"],
            service.llm_analyzer.selection_context["battle_odds"],
        )


class BattleOutcomeTests(unittest.TestCase):
    def test_roll_boundaries_and_upset_results(self):
        with patch(
            "src.domain.services.battle_outcome_service.random.randint",
            side_effect=[20, 80],
        ) as randint:
            low_rate_win = resolve_battle_outcome(30, 30)
            high_rate_loss = resolve_battle_outcome(70, 70)

        self.assertEqual(randint.call_args_list[0].args, (20, 80))
        self.assertEqual(low_rate_win["dice_roll"], 20)
        self.assertEqual(low_rate_win["outcome"], "player_win")
        self.assertEqual(high_rate_loss["dice_roll"], 80)
        self.assertEqual(high_rate_loss["outcome"], "player_lose")

    def test_force_lose_ignores_favorable_roll(self):
        odds = resolve_battle_outcome(100, 100, dice_roll=20, force_lose=True)

        self.assertEqual(odds["player_win_rate"], 100)
        self.assertEqual(odds["dice_roll"], 20)
        self.assertEqual(odds["outcome"], "player_lose")

    def test_first_event_context_preserves_forced_loss(self):
        service = EventContextTests()._service()
        context = service._active_event_context(
            _runtime(ai=100, desire=100),
            battle_odds={"dice_roll": 20, "outcome": "player_lose"},
        )

        self.assertEqual(context["battle_odds"]["dice_roll"], 20)
        self.assertEqual(context["event_outcome"]["result"], "obstacle")

    def test_active_event_rerolls_once_per_context(self):
        service = EventContextTests()._service()
        with patch(
            "src.domain.services.battle_outcome_service.random.randint",
            side_effect=[20, 80],
        ) as randint:
            first = service._active_event_context(_runtime(ai=50, desire=50))
            second = service._active_event_context(_runtime(ai=50, desire=50))

        self.assertEqual(randint.call_count, 2)
        self.assertEqual(first["battle_odds"]["outcome"], "player_win")
        self.assertEqual(second["battle_odds"]["outcome"], "player_lose")


class ActionTurnCardTests(unittest.TestCase):
    def test_action_card_uses_selected_targets_and_player_action_row(self):
        generator = ReportGenerator.__new__(ReportGenerator)
        result = ActionTurnResult(
            story_text="测试正文",
            action="尝试逃跑",
            selected_targets=[
                {"id": "monster", "name": "测试魔物"},
                {"target_name": "小夏", "magical_girl_name": "夏光"},
            ],
            phase="事件",
            date_label="2020年4月1日",
            state_snapshot={
                "主角": {
                    "个人信息": {
                        "姓名": "小明",
                        "魔法少女名": "星辉",
                        "身份&职业": "见习调查员",
                    }
                }
            },
        )

        html = generator._render_action_turn_html(result)

        self.assertIn("测试魔物、小夏", html)
        self.assertIn(">身份</span>", html)
        self.assertIn(">见习调查员</span>", html)
        self.assertIn(">玩家行动</span>", html)
        self.assertIn(">尝试逃跑</span>", html)
        self.assertNotIn(">时间</span>", html)
        self.assertIn("max-width: 900px", html)
        self.assertIn("font-size: 21px", html)
        self.assertIn("font-size: 17px", html)

    def test_action_card_hides_empty_insert_containers(self):
        generator = ReportGenerator.__new__(ReportGenerator)
        result = ActionTurnResult(
            story_text="测试正文",
            json_patch=[
                {"op": "insert", "path": "/主角/身体部位状况", "value": {}},
                {
                    "op": "insert",
                    "path": "/主角/身体部位状况/手腕",
                    "value": "淡红勒痕",
                },
                {"op": "insert", "path": "/主角/快感状态/性癖", "value": []},
                {"op": "insert", "path": "/主角/快感状态/性癖/恶堕", "value": 1},
                {
                    "op": "insert",
                    "path": "/主角/标签",
                    "value": {
                        "状态": "警觉",
                        "详情": {"来源": "直觉"},
                    },
                },
                {"op": "replace", "path": "/主角/临时状态", "value": {}},
                {"op": "replace", "path": "/主角/效果列表", "value": ["警觉", "专注"]},
            ],
        )

        html = generator._render_action_turn_html(result)

        self.assertIn('<div class="section-title">状态变化</div>', html)
        self.assertNotIn('<div class="section-title">变量更新</div>', html)
        self.assertNotIn("身体部位状况：{}", html)
        self.assertNotIn("性癖：[]", html)
        self.assertIn("手腕：淡红勒痕", html)
        self.assertIn("恶堕：1", html)
        self.assertIn("状态：警觉", html)
        self.assertIn('详情：{&quot;来源&quot;:&quot;直觉&quot;}', html)
        self.assertNotIn("标签：", html)
        self.assertIn("临时状态：{}", html)
        self.assertIn('效果列表：[&quot;警觉&quot;,&quot;专注&quot;]', html)

    def test_action_card_expands_object_patch_without_display_limit(self):
        generator = ReportGenerator.__new__(ReportGenerator)
        body_parts = {
            "嘴": "状态1",
            "小穴": "状态2",
            "屁穴": "状态3",
            "胸部": "状态4",
            "小腹": "状态5",
            "皮肤": "状态6",
        }
        result = ActionTurnResult(
            story_text="测试正文",
            json_patch=[
                {"op": "replace", "path": "/主角/普通字段1", "value": "值1"},
                {"op": "replace", "path": "/主角/普通字段2", "value": "值2"},
                {"op": "replace", "path": "/主角/普通字段3", "value": "值3"},
                {
                    "op": "replace",
                    "path": "/主角/身体部位状况",
                    "value": body_parts,
                },
                {"op": "replace", "path": "/主角/额外状态", "value": "仍然显示"},
            ],
        )

        html = generator._render_action_turn_html(result)

        for label, value in body_parts.items():
            self.assertIn(f"{label}：{value}", html)
        self.assertNotIn("身体部位状况：", html)
        self.assertIn("额外状态：仍然显示", html)

    def test_action_card_formats_numeric_delta_as_change_message(self):
        generator = ReportGenerator.__new__(ReportGenerator)
        result = ActionTurnResult(
            story_text="测试正文",
            json_patch=[
                {
                    "op": "delta",
                    "path": "/主角/快感状态/性癖/触手play/进度",
                    "value": 10,
                },
                {
                    "op": "delta",
                    "path": "/主角/技能/闪避/经验",
                    "value": -5,
                },
                {
                    "op": "delta",
                    "path": "/主角/核心状态/体力值/当前",
                    "value": -8,
                },
                {"op": "delta", "path": "/主角/名声", "value": 0},
                {"op": "replace", "path": "/主角/等级", "value": 10},
            ],
        )

        html = generator._render_action_turn_html(result)

        self.assertIn("触手play经验增加了！", html)
        self.assertIn("闪避经验减少了！", html)
        self.assertIn("体力值减少了！", html)
        self.assertIn("名声没有变化。", html)
        self.assertIn("等级：10", html)
        self.assertNotIn("进度：10", html)

    def test_action_card_only_displays_current_event_reason(self):
        generator = ReportGenerator.__new__(ReportGenerator)
        reason = "优夏主动要求更刺激的玩法，小百将她带到无人的家政教室进行暴露与忍耐测试"
        result = ActionTurnResult(
            story_text="测试正文",
            json_patch=[
                {
                    "op": "replace",
                    "path": "/进程/当前事件/scene_event",
                    "value": {
                        "id": "free_action_760a5d15a7ac400eb73a9a43dc15e3da",
                        "title": "家政教室的暴露调教——小百的忍耐测试",
                        "reason": reason,
                    },
                },
                {"op": "delta", "path": "/进程/当前事件/turn_count", "value": 1},
                {
                    "op": "replace",
                    "path": "/进程/当前事件/selected_targets",
                    "value": [{"id": "target", "name": "后台目标"}],
                },
                {"op": "replace", "path": "/进程/阶段", "value": "事件"},
                {"op": "replace", "path": "/主角/临时状态", "value": "紧张"},
            ],
        )

        html = generator._render_action_turn_html(result)

        self.assertIn(reason, html)
        self.assertNotIn(f"reason：{reason}", html)
        self.assertNotIn("家政教室的暴露调教", html)
        self.assertNotIn("free_action_760a5d15a7ac400eb73a9a43dc15e3da", html)
        self.assertNotIn("turn_count", html)
        self.assertNotIn("后台目标", html)
        self.assertIn("阶段：事件", html)
        self.assertIn("临时状态：紧张", html)

    def test_action_card_supports_nested_current_event_reason_and_hides_empty_reason(self):
        generator = ReportGenerator.__new__(ReportGenerator)
        displayed = ReportGenerator._action_patch_display_items(
            [
                {
                    "op": "replace",
                    "path": "/进程/当前事件",
                    "value": {
                        "scene_event": {
                            "id": "event",
                            "title": "后台标题",
                            "reason": "整体事件原因",
                        },
                        "turn_count": 2,
                    },
                },
                {
                    "op": "replace",
                    "path": "/进程/当前事件/scene_event/reason",
                    "value": "直接原因",
                },
                {
                    "op": "replace",
                    "path": "/进程/当前事件/scene_event",
                    "value": {"id": "empty", "reason": "  "},
                },
                {"op": "replace", "path": "/进程/当前事件/scene_event/id", "value": "hidden"},
            ]
        )

        self.assertEqual(
            [generator._format_action_patch(item) for item in displayed],
            ["整体事件原因", "直接原因"],
        )

    def test_object_patch_is_saved_as_one_object(self):
        repo = PlayerSaveRepository.__new__(PlayerSaveRepository)
        state = {"主角": {"标签": {"旧字段": "旧值"}}}
        value = {
            "状态": "警觉",
            "详情": {"来源": "直觉"},
        }
        response = (
            "测试正文\n"
            "<行动选项>继续观察</行动选项>\n"
            "<UpdateVariable><Analysis>测试</Analysis><JSONPatch>"
            + json.dumps(
                [{"op": "replace", "path": "/主角/标签", "value": value}],
                ensure_ascii=False,
            )
            + "</JSONPatch></UpdateVariable>"
        )

        result = ActionTurnAnalyzer.parse_action_turn_response(response)

        applied = repo.apply_json_patch(state, result.json_patch)

        self.assertEqual(state["主角"]["标签"], value)
        self.assertNotIn("旧字段", state["主角"]["标签"])
        self.assertEqual(applied[0]["value"], value)


if __name__ == "__main__":
    unittest.main()
