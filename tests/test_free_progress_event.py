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
from src.domain.models.data_models import (  # noqa: E402
    ActionTurnResult,
    BattleDiaryCard,
    ReincarnationCard,
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


def _runtime(*, turn_count: int = 1) -> dict:
    return {
        "scene_event": {"id": "capture", "title": "抓捕事件", "reason": "测试"},
        "selected_participants": [],
        "selected_targets": [{"id": "monster", "name": "测试魔物"}],
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
        self.assertNotIn("ai_win_rate", context)
        self.assertNotIn("desire_win_rate", context)
        self.assertNotIn("battle_odds", context)
        self.assertNotIn("event_outcome", context)

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
        self.assertNotIn("ai_win_rate", context)
        self.assertNotIn("desire_win_rate", context)
        self.assertNotIn("battle_odds", context)

    async def test_continuous_event_only_selects_context(self):
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
        for key in ("ai_win_rate", "desire_win_rate", "battle_odds", "event_outcome"):
            self.assertNotIn(key, context)

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

    def test_active_context_exposes_event_references_without_prejudging_result(self):
        context = self._service()._active_event_context(_runtime())

        self.assertEqual(context["scene_event"]["content"], "最新版正文")
        self.assertEqual(context["scene_event"]["success_ending"], "成功")
        self.assertEqual(context["scene_event"]["obstacle_ending"], "受阻")
        for key in ("ai_win_rate", "desire_win_rate", "battle_odds", "event_outcome"):
            self.assertNotIn(key, context)

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
    def test_action_prompt_treats_player_input_as_an_attempt(self):
        prompt = (
            Path(__file__).resolve().parents[1]
            / "prompts"
            / "magical_girl"
            / "action_turn_prompt.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("本喵也不会偏袒她", prompt)
        self.assertIn("玩家输入只描述她想做什么和如何尝试", prompt)
        self.assertIn("本喵会根据已有事实自然决定结果", prompt)
        self.assertIn("本喵的爪子一次只按住一件当前事件", prompt)
        self.assertIn("每轮过去了多久也要给本喵算清楚", prompt)
        self.assertNotIn("dice_roll", prompt)
        self.assertNotIn("battle_odds", prompt)

    def test_action_prompt_uses_cat_god_voice_naturally(self):
        prompt = (
            Path(__file__).resolve().parents[1]
            / "prompts"
            / "magical_girl"
            / "action_turn_prompt.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("本喵只在故事外主持", prompt)
        self.assertIn("本喵只认刚才正文里真正发生的变化", prompt)
        self.assertIn("<JSONPatch> 也给本喵写得干干净净", prompt)

    def test_selection_prompt_only_selects_context(self):
        prompt = (
            Path(__file__).resolve().parents[1]
            / "prompts"
            / "magical_girl"
            / "action_context_selection_prompt.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("只负责筛选本轮相关资料", prompt)
        self.assertNotIn("ai_win_rate", prompt)
        self.assertNotIn("desire_win_rate", prompt)

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

    def test_action_messages_establish_cat_god_without_repeating_runtime_rules(self):
        prompt = "完整运行时 Prompt"
        messages = ActionTurnAnalyzer._build_action_messages(prompt, "系统规则")

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
        assistant_history = "\n".join(
            message["content"]
            for message in messages
            if message["role"] == "assistant"
        )
        self.assertIn("小猫之神", messages[1]["content"])
        self.assertIn("货真价实的神", assistant_history)
        self.assertIn("小鱼干", assistant_history)
        self.assertIn("分魂", assistant_history)
        self.assertIn("DM", assistant_history)
        self.assertNotIn("第三人称", assistant_history)
        self.assertNotIn("不偏袒", assistant_history)
        for item in ("时间", "状态", "物品", "关系", "JSONPatch"):
            self.assertNotIn(item, assistant_history)
        self.assertEqual(
            sum(message["content"].count(prompt) for message in messages),
            1,
        )
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
            json.dumps(
                {
                    "player_clock": {"day_offset": 0, "minute_of_day": 8 * 60},
                    "进程": {"阶段": "日常"},
                    "主角": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _save(
        self,
        patch,
        *,
        runtime=None,
        ensure_time=True,
        player_minute_of_day=None,
    ):
        patch = list(patch)
        if ensure_time and not any(
            isinstance(item, dict) and str(item.get("path") or "").startswith("/世界/")
            for item in patch
        ):
            patch.append({"op": "delta", "path": "/世界/时间", "value": "0:00"})
        return self.repo.save_action_turn_result(
            group_id="g1",
            user_id="u1",
            result=ActionTurnResult(story_text="测试正文", json_patch=patch),
            player_minute_of_day=player_minute_of_day,
            event_runtime=runtime,
        )

    @staticmethod
    def _battle_card() -> BattleDiaryCard:
        return BattleDiaryCard(
            title="测试战斗",
            subtitle="",
            target_name="测试角色",
            action="测试行动",
            date_label="",
            diary="测试日记",
            encounter="测试遭遇",
            result="测试结果",
        )

    def test_legacy_battle_save_does_not_advance_world_date(self):
        self.repo.save_battle_result("g1", "u1", self._battle_card())

        self.assertEqual(self.repo.get_current_world_day_offset("g1"), 0)
        logs = self.repo._read_recent_logs(
            self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl",
            limit=0,
        )
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["world_day_offset"], 0)

    def test_legacy_battle_save_without_player_data_does_not_advance_world_date(self):
        card = self._battle_card()

        self.repo.save_battle_result("g1", "missing", card)

        self.assertEqual(self.repo.get_current_world_day_offset("g1"), 0)
        self.assertEqual(card.date_label, "公元2020年4月1日")
        self.assertFalse(
            (self.repo.get_user_dir("g1", "missing") / "daily_memory.jsonl").exists()
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
            [{"op": "delta", "path": "/世界/时间", "value": "72:00"}],
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
            {"op": "delta", "path": "/世界/时间", "value": "72:00"},
        )
        self.assertEqual(log["time_advanced_minutes"], 72 * 60)
        self.assertEqual(log["world_time"], "公元2020年4月1日 8:00")
        self.assertEqual(log["world_time_end"], "公元2020年4月4日 8:00")
        self.assertNotIn("日期", state.get("世界", {}))

    def test_event_end_uses_explicit_day_advance_without_extra_day(self):
        self._save([], runtime=_runtime(turn_count=0))
        self._save(
            [
                {"op": "delta", "path": "/世界/时间", "value": "72:00"},
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

    def test_zero_world_time_delta_keeps_clock_and_saves_turn(self):
        self._save(
            [{"op": "delta", "path": "/世界/时间", "value": "0:00"}],
            runtime=_runtime(turn_count=0),
        )

        self.assertEqual(self.repo.get_current_world_day_offset("g1"), 0)
        self.assertEqual(self.repo.get_current_conversation_no("g1"), 2)
        log = self.repo._read_recent_logs(
            self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl",
            limit=1,
        )[0]
        self.assertFalse(log["day_advanced"])
        self.assertEqual(log["days_advanced"], 0)
        self.assertIn(
            {"op": "delta", "path": "/世界/时间", "value": "0:00"},
            log["json_patch"],
        )
        self.assertEqual(log["world_time_end"], "公元2020年4月1日 8:00")

    def test_invalid_world_time_patch_does_not_consume_conversation(self):
        invalid_patches = [
            [{"op": "replace", "path": "/世界/日期", "value": "2020-04-02"}],
            [{"op": "replace", "path": "/世界/时间", "value": "1:00"}],
            [{"op": "delta", "path": "/世界/时间", "value": "-1:00"}],
            [{"op": "delta", "path": "/世界/时间", "value": "1:60"}],
            [{"op": "delta", "path": "/世界/时间", "value": 90}],
            [],
            [
                {"op": "delta", "path": "/世界/时间", "value": "1:00"},
                {"op": "delta", "path": "/世界/时间", "value": "0:30"},
            ],
        ]
        for invalid_patch in invalid_patches:
            with self.subTest(patch=invalid_patch):
                with self.assertRaises(ValueError):
                    self._save(
                        invalid_patch,
                        runtime=_runtime(turn_count=0),
                        ensure_time=False,
                    )
                self.assertEqual(self.repo.get_current_conversation_no("g1"), 1)
                self.assertEqual(self.repo.get_current_world_day_offset("g1"), 0)
                self.assertFalse(
                    (self.repo.get_user_dir("g1", "u1") / "daily_memory.jsonl").exists()
                )

    def test_world_time_crosses_midnight_and_keeps_remainder(self):
        self.repo._atomic_write_json(
            self.repo._world_clock_path("g1"),
            {
                "schema_version": 3,
                "next_day_offset": 0,
                "next_minute_of_day": 23 * 60,
                "next_conversation_no": 1,
            },
        )
        user_dir = self.repo.get_user_dir("g1", "u1")
        player_data = self.repo._load_current_player_data(user_dir)
        player_data["player_clock"] = {"day_offset": 0, "minute_of_day": 23 * 60}
        self.repo._save_current_player_data(user_dir, player_data)
        self._save(
            [{"op": "delta", "path": "/世界/时间", "value": "1:30"}],
            player_minute_of_day=23 * 60,
        )

        clock = self.repo._read_json(self.repo._world_clock_path("g1"))
        self.assertEqual(clock["next_day_offset"], 1)
        self.assertEqual(clock["next_minute_of_day"], 30)
        self.assertEqual(self.repo.get_current_world_datetime("g1"), "公元2020年4月2日 0:30")

    def test_world_time_advances_more_than_two_days(self):
        self._save([{"op": "delta", "path": "/世界/时间", "value": "49:30"}])

        self.assertEqual(self.repo.get_current_world_day_offset("g1"), 2)
        self.assertEqual(self.repo.get_current_world_minute_of_day("g1"), 9 * 60 + 30)
        self.assertEqual(self.repo.get_current_world_datetime("g1"), "公元2020年4月3日 9:30")

    def test_stale_world_minute_does_not_consume_conversation(self):
        self.repo.get_current_world_datetime("g1")

        with self.assertRaisesRegex(ValueError, "玩家个人时间已变化"):
            self._save(
                [{"op": "delta", "path": "/世界/时间", "value": "0:10"}],
                player_minute_of_day=9 * 60,
            )

        self.assertEqual(self.repo.get_current_conversation_no("g1"), 1)

    def test_multiple_players_share_one_conversation_sequence(self):
        second_user_dir = self.repo.get_user_dir("g1", "u2")
        second_user_dir.mkdir(parents=True)
        (second_user_dir / "player_data_update.json").write_text(
            json.dumps(
                {
                    "player_clock": {"day_offset": 0, "minute_of_day": 8 * 60},
                    "进程": {"阶段": "日常"},
                    "主角": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self._save([], runtime=_runtime(turn_count=0))
        self.repo.save_action_turn_result(
            group_id="g1",
            user_id="u2",
            result=ActionTurnResult(
                story_text="第二名玩家",
                json_patch=[{"op": "delta", "path": "/世界/时间", "value": "0:00"}],
            ),
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

    def test_linked_players_each_advance_from_their_own_clock(self):
        second_dir = self.repo.get_user_dir("g1", "u2")
        second_dir.mkdir(parents=True)
        self.repo._atomic_write_json(
            second_dir / "player_data_update.json",
            {
                "player_clock": {"day_offset": 1, "minute_of_day": 10 * 60},
                "进程": {"阶段": "日常"},
                "主角": {},
            },
        )

        self.repo.save_action_turn_result(
            group_id="g1",
            user_id="u1",
            result=ActionTurnResult(
                story_text="联动正文",
                json_patch=[{"op": "delta", "path": "/世界/时间", "value": "1:30"}],
            ),
            linked_user_ids=["u2"],
        )

        self.assertEqual(self.repo.get_player_clock("g1", "u1"), (0, 9 * 60 + 30))
        self.assertEqual(self.repo.get_player_clock("g1", "u2"), (1, 11 * 60 + 30))
        self.assertEqual(self.repo.get_current_world_datetime("g1"), "公元2020年4月2日 11:30")

    def test_lagging_player_does_not_move_world_high_water_backward(self):
        self.repo._atomic_write_json(
            self.repo._world_clock_path("g1"),
            {
                "schema_version": 3,
                "next_day_offset": 3,
                "next_minute_of_day": 12 * 60,
                "next_conversation_no": 1,
            },
        )

        self._save([{"op": "delta", "path": "/世界/时间", "value": "1:00"}])

        self.assertEqual(self.repo.get_player_clock("g1", "u1"), (0, 9 * 60))
        self.assertEqual(self.repo.get_current_world_datetime("g1"), "公元2020年4月4日 12:00")

    def test_broken_linked_clock_is_skipped(self):
        broken_dir = self.repo.get_user_dir("g1", "broken")
        broken_dir.mkdir(parents=True)
        self.repo._atomic_write_json(
            broken_dir / "player_data_update.json",
            {"进程": {"阶段": "日常"}, "主角": {}},
        )

        self.repo.save_action_turn_result(
            group_id="g1",
            user_id="u1",
            result=ActionTurnResult(
                story_text="联动正文",
                json_patch=[{"op": "delta", "path": "/世界/时间", "value": "0:20"}],
            ),
            linked_user_ids=["broken"],
        )

        self.assertEqual(self.repo.get_player_clock("g1", "u1"), (0, 8 * 60 + 20))
        self.assertNotIn(
            "player_clock",
            self.repo._load_current_player_data(broken_dir),
        )

    def test_missing_caller_clock_fails_action(self):
        user_dir = self.repo.get_user_dir("g1", "u1")
        player_data = self.repo._load_current_player_data(user_dir)
        player_data.pop("player_clock")
        self.repo._save_current_player_data(user_dir, player_data)

        with self.assertRaisesRegex(ValueError, "缺少 player_clock"):
            self._save([])

    def test_validation_failure_does_not_consume_conversation_number(self):
        with self.assertRaises(ValueError):
            self.repo.save_action_turn_result(
                group_id="g1",
                user_id="u1",
                result=ActionTurnResult(story_text="失败", json_patch=[]),
                player_day_offset=3,
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
        self.assertNotIn("ai_win_rate", changed["进程"]["当前事件"])
        self.assertNotIn("desire_win_rate", changed["进程"]["当前事件"])
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

    def test_log_does_not_record_structured_outcome(self):
        with patch.object(self.repo, "append_log") as append_log:
            self._save([], runtime=_runtime(turn_count=0))

        log_entry = append_log.call_args.args[2]
        self.assertNotIn("event_win_rate", log_entry)
        self.assertNotIn("event_dice_roll", log_entry)
        self.assertNotIn("event_outcome", log_entry)
        self.assertEqual(log_entry["conversation_no"], 1)
        self.assertTrue(log_entry["event_started"])
        self.assertFalse(log_entry["day_advanced"])

    def test_legacy_outcome_fields_are_removed_on_next_save(self):
        legacy_runtime = {
            **_runtime(turn_count=3),
            "ai_win_rate": 80,
            "desire_win_rate": 100,
            "battle_odds": {"dice_roll": 20},
            "event_outcome": {"result": "success"},
        }
        user_dir = self.repo.get_user_dir("g1", "u1")
        player_data = self.repo._load_current_player_data(user_dir)
        player_data["进程"] = {"阶段": "事件", "当前事件": legacy_runtime}
        self.repo._save_current_player_data(user_dir, player_data)

        state = self._save([])

        for key in ("ai_win_rate", "desire_win_rate", "battle_odds", "event_outcome"):
            self.assertNotIn(key, state["进程"]["当前事件"])
        self.assertEqual(state["进程"]["当前事件"]["turn_count"], 4)

    def test_main_llm_event_patch_controls_success_failure_and_continuation(self):
        for label, patch, should_continue in (
            ("成功结束", [{"op": "remove", "path": "/进程/当前事件"}], False),
            ("失败结束", [{"op": "remove", "path": "/进程/当前事件"}], False),
            ("受挫继续", [], True),
            (
                "被捕继续",
                [
                    {
                        "op": "replace",
                        "path": "/主角/核心状态/当前状态",
                        "value": "被捕",
                    }
                ],
                True,
            ),
        ):
            with self.subTest(label=label):
                self.setUp()
                state = self._save(patch, runtime=_runtime(turn_count=0))
                self.assertEqual("当前事件" in state["进程"], should_continue)


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
        self.assertEqual(self.repo.get_current_world_minute_of_day("g1"), 8 * 60)
        migrated_clock = self.repo._read_json(group_dir / "world_clock.json")
        self.assertEqual(migrated_clock["schema_version"], 3)
        self.assertEqual(migrated_clock["next_minute_of_day"], 8 * 60)

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


class PlayerClockLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.repo = PlayerSaveRepository.__new__(PlayerSaveRepository)
        self.repo.root_dir = self.root
        self.repo.editable_manager = None

    @staticmethod
    def _card(name="测试角色"):
        return ReincarnationCard(
            info=[
                {
                    "field": "姓名",
                    "path": "/主角/个人信息/姓名",
                    "description": name,
                }
            ]
        )

    def test_reincarnation_starts_at_world_high_water(self):
        self.repo._atomic_write_json(
            self.repo._world_clock_path("g1"),
            {
                "schema_version": 3,
                "next_day_offset": 2,
                "next_minute_of_day": 13 * 60 + 15,
                "next_conversation_no": 1,
            },
        )

        self.repo.save_reincarnation("g1", "u1", self._card())
        player_data = self.repo._load_current_player_data(
            self.repo.get_user_dir("g1", "u1")
        )

        self.assertEqual(self.repo._player_clock(player_data), (2, 13 * 60 + 15))
        self.assertEqual(
            player_data["主角"]["时间信息"]["成为魔法少女时间"],
            "公元2020年4月3日 13:15",
        )

    def test_reset_catches_up_but_preserves_transformation_time(self):
        self.repo.save_reincarnation("g1", "u1", self._card())
        user_dir = self.repo.get_user_dir("g1", "u1")
        current = self.repo._load_current_player_data(user_dir)
        current["主角"]["时间信息"]["成为魔法少女时间"] = "公元2020年4月1日 8:00"
        self.repo._save_current_player_data(user_dir, current)
        self.repo._atomic_write_json(
            self.repo._world_clock_path("g1"),
            {
                "schema_version": 3,
                "next_day_offset": 4,
                "next_minute_of_day": 17 * 60,
                "next_conversation_no": 1,
            },
        )

        self.repo.reset_player_state("g1", "u1")
        refreshed = self.repo._load_current_player_data(user_dir)

        self.assertEqual(self.repo._player_clock(refreshed), (4, 17 * 60))
        self.assertEqual(
            refreshed["主角"]["时间信息"]["成为魔法少女时间"],
            "公元2020年4月1日 8:00",
        )

    def test_player_clock_and_transformation_time_are_readonly(self):
        state = {
            "player_clock": {"day_offset": 0, "minute_of_day": 480},
            "主角": {"时间信息": {"成为魔法少女时间": "公元2020年4月1日 8:00"}},
        }
        for path in (
            "/player_clock/minute_of_day",
            "/主角/时间信息/成为魔法少女时间",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "禁止修改"):
                self.repo.apply_json_patch(
                    state,
                    [{"op": "replace", "path": path, "value": "非法修改"}],
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
            },
            current_world_date="公元2020年4月1日",
        )

        self.assertIsNone(daily)
        self.assertEqual(continuous["scene_event"]["id"], "capture")
        self.assertNotIn("ai_win_rate", continuous)
        self.assertNotIn("desire_win_rate", continuous)

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
        legacy_runtime = {
            **_runtime(),
            "ai_win_rate": 90,
            "desire_win_rate": 100,
            "battle_odds": {"dice_roll": 20},
            "event_outcome": {"result": "success"},
        }
        return {
            "player_data": {
                "player_clock": {"day_offset": 0, "minute_of_day": 8 * 60},
                "进程": {"阶段": "事件", "当前事件": legacy_runtime},
            },
            "logs": [],
            "cameo_memories": [],
        }

    def get_player_clock(self, group_id, user_id):
        return 0, 8 * 60

    def format_world_datetime(self, offset, minute_of_day):
        return "公元2020年4月1日 8:00"

    def format_world_datetime_for_display(self, offset, minute_of_day):
        return "display-time-with-weekday"

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
        }

    async def analyze_action_turn(self, **kwargs):
        self.selection_context = kwargs["selection_context"]
        self.current_world_date = kwargs["current_world_date"]
        return SimpleNamespace(
            result=ActionTurnResult(
                story_text="继续事件",
                json_patch=[{"op": "delta", "path": "/世界/时间", "value": "0:10"}],
            ),
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
        self.assertEqual(service.llm_analyzer.current_world_date, "display-time-with-weekday")
        self.assertEqual(result.result.date_label, "display-time-with-weekday")
        self.assertEqual(
            service.llm_analyzer.selection_input["current_event"]["scene_event"]["id"],
            "capture",
        )
        self.assertEqual(
            service.llm_analyzer.selection_context["current_event"]["scene_event"]["id"],
            "capture",
        )
        for key in ("battle_odds", "event_outcome", "ai_win_rate", "desire_win_rate"):
            self.assertNotIn(key, service.llm_analyzer.selection_input["current_event"])
            self.assertNotIn(key, service.llm_analyzer.selection_context["current_event"])
        self.assertEqual(
            service.llm_analyzer.selection_context["proposed_event"]["scene_event"]["id"],
            "tentacle_event",
        )
        self.assertEqual(result.result.selected_targets[0]["name"], "触手怪")
        for key in ("battle_odds", "event_outcome", "ai_win_rate", "desire_win_rate"):
            self.assertNotIn(key, service.llm_analyzer.selection_context)
        self.assertNotIn("battle_odds", service.save_repository.saved)


class WorldTimeDisplayTests(unittest.TestCase):
    def test_display_time_includes_calculated_weekday_without_changing_storage_format(self):
        self.assertEqual(
            PlayerSaveRepository.format_world_datetime_for_display(0, 8 * 60),
            "公元2020年4月1日 星期三 8:00",
        )
        self.assertEqual(
            PlayerSaveRepository.format_world_datetime_for_display(3, 9 * 60 + 5),
            "公元2020年4月4日 星期六 9:05",
        )
        self.assertEqual(
            PlayerSaveRepository.format_world_datetime(0, 8 * 60),
            "公元2020年4月1日 8:00",
        )


class ActionTurnCardTests(unittest.TestCase):
    def test_action_text_places_current_time_before_story(self):
        result = ActionTurnResult(
            story_text="测试正文",
            date_label="公元2020年4月1日 8:00",
        )

        text = result.to_text()

        self.assertTrue(text.startswith("当前时间：公元2020年4月1日 8:00\n测试正文"))

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
            date_label="公元2020年4月1日 8:00",
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
        self.assertIn("当前时间：公元2020年4月1日 8:00", html)
        self.assertLess(
            html.index("当前时间：公元2020年4月1日 8:00"),
            html.index("测试正文"),
        )
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

    def test_action_card_displays_skills_fetishes_and_inventory_in_order(self):
        generator = ReportGenerator.__new__(ReportGenerator)
        skills = {
            f"技能{index}": {"进度": progress}
            for index, progress in enumerate(
                [0, 20, 21, 40, 41, 60, 61, 80, 81, 100, 55, 75, 95],
                start=1,
            )
        }
        result = ActionTurnResult(
            story_text="测试正文",
            json_patch=[
                {"op": "replace", "path": "/主角/临时状态", "value": "专注"},
            ],
            state_snapshot={
                "主角": {
                    "技能": skills,
                    "快感状态": {
                        "性癖": {
                            "恶堕": {"进度": 18},
                        },
                    },
                    "道具栏": {
                        "药水": 1,
                        "空药瓶": 0,
                        "半瓶药剂": 1.5,
                        "便签": "写着今天的线索",
                        "护符": True,
                        "失效护符": False,
                        "空盒": None,
                        "工具箱": {
                            "状态": "损坏",
                            "零件": ["扳手"],
                        },
                    },
                },
            },
        )

        html = generator._render_action_turn_html(result)

        self.assertIn('<div class="section-title">技能&amp;性癖&amp;道具</div>', html)
        self.assertLess(html.index("状态变化"), html.index("技能&amp;性癖&amp;道具"))
        self.assertLess(html.index("技能&amp;性癖&amp;道具"), html.index("行动记录已写入存档。"))
        self.assertIn('<span class="patch-chip">技能1：入门</span>', html)
        self.assertIn('<span class="patch-chip">技能2：入门</span>', html)
        self.assertIn('<span class="patch-chip">技能3：熟练</span>', html)
        self.assertIn('<span class="patch-chip">技能4：熟练</span>', html)
        self.assertIn('<span class="patch-chip">技能5：进阶</span>', html)
        self.assertIn('<span class="patch-chip">技能6：进阶</span>', html)
        self.assertIn('<span class="patch-chip">技能7：精通</span>', html)
        self.assertIn('<span class="patch-chip">技能8：精通</span>', html)
        self.assertIn('<span class="patch-chip">技能9：大师</span>', html)
        self.assertIn('<span class="patch-chip">技能10：大师</span>', html)
        self.assertIn('<span class="patch-chip">技能13：大师</span>', html)
        self.assertIn('<span class="patch-chip">恶堕：入门</span>', html)
        self.assertIn('<span class="patch-chip">1个药水</span>', html)
        self.assertIn('<span class="patch-chip">0个空药瓶</span>', html)
        self.assertIn('<span class="patch-chip">1.5个半瓶药剂</span>', html)
        self.assertIn('<span class="patch-chip">便签：写着今天的线索</span>', html)
        self.assertIn('<span class="patch-chip">护符：是</span>', html)
        self.assertIn('<span class="patch-chip">失效护符：否</span>', html)
        self.assertIn('<span class="patch-chip">空盒：无</span>', html)
        self.assertIn(
            '<span class="patch-chip">工具箱：{&quot;状态&quot;:&quot;损坏&quot;,'
            '&quot;零件&quot;:[&quot;扳手&quot;]}</span>',
            html,
        )
        self.assertLess(html.index("技能13：大师"), html.index("恶堕：入门"))
        self.assertLess(html.index("恶堕：入门"), html.index("1个药水"))
        self.assertNotIn("技能1：0", html)
        self.assertNotIn("恶堕：18", html)

    def test_action_card_hides_collection_section_when_empty(self):
        generator = ReportGenerator.__new__(ReportGenerator)
        result = ActionTurnResult(
            story_text="测试正文",
            state_snapshot={
                "主角": {
                    "技能": {},
                    "快感状态": {"性癖": {}},
                    "道具栏": {},
                },
            },
        )

        html = generator._render_action_turn_html(result)

        self.assertNotIn('<div class="section-title">技能&amp;性癖&amp;道具</div>', html)

    def test_action_card_displays_collection_section_with_inventory_only(self):
        generator = ReportGenerator.__new__(ReportGenerator)
        result = ActionTurnResult(
            story_text="测试正文",
            state_snapshot={
                "主角": {
                    "技能": {},
                    "快感状态": {"性癖": {}},
                    "道具栏": {"钥匙": 2},
                },
            },
        )

        html = generator._render_action_turn_html(result)

        self.assertIn('<div class="section-title">技能&amp;性癖&amp;道具</div>', html)
        self.assertIn('<span class="patch-chip">2个钥匙</span>', html)

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
                [
                    {"op": "replace", "path": "/主角/标签", "value": value},
                    {"op": "delta", "path": "/世界/时间", "value": "0:05"},
                ],
                ensure_ascii=False,
            )
            + "</JSONPatch></UpdateVariable>"
        )

        result = ActionTurnAnalyzer.parse_action_turn_response(response)
        state_patch, elapsed_minutes, elapsed_label = repo._extract_world_time_advance(
            result.json_patch
        )
        applied = repo.apply_json_patch(state, state_patch)

        self.assertEqual(state["主角"]["标签"], value)
        self.assertNotIn("旧字段", state["主角"]["标签"])
        self.assertEqual(applied[0]["value"], value)
        self.assertEqual((elapsed_minutes, elapsed_label), (5, "0:05"))

    def test_parser_rejects_missing_world_time_patch(self):
        response = (
            "测试正文\n"
            "<行动选项>继续观察</行动选项>\n"
            "<UpdateVariable><Analysis>测试</Analysis>"
            "<JSONPatch>[]</JSONPatch></UpdateVariable>"
        )

        with self.assertRaisesRegex(ValueError, "必须且只能输出一个 /世界/时间"):
            ActionTurnAnalyzer.parse_action_turn_response(response)

    def test_visible_variables_hide_legacy_world_clock_fields(self):
        visible = ActionTurnAnalyzer._visible_current_variables(
            {
                "player_clock": {"day_offset": 9, "minute_of_day": 600},
                "世界": {
                    "日期": "旧日期",
                    "时间": "旧时间",
                    "世界观备注": {"地点": "保留"},
                }
            }
        )

        self.assertEqual(visible["世界"], {"世界观备注": {"地点": "保留"}})
        self.assertNotIn("player_clock", visible)

    def test_actual_interactions_choose_linked_player_ids(self):
        linked = ActionTurnApplicationService._interaction_user_ids(
            [
                {"_user_id": "u2", "target_name": "洛洛"},
                {"_user_id": "u3", "target_name": "小夏"},
            ],
            [{"target": "洛洛", "memory_text": "实际互动"}],
        )

        self.assertEqual(linked, ["u2"])


    def test_interaction_without_memory_does_not_link_player_clock(self):
        linked = ActionTurnApplicationService._interaction_user_ids(
            [{"_user_id": "u2", "target_name": "player-two"}],
            [{"target": "player-two", "memory_text": "  "}],
        )

        self.assertEqual(linked, [])


if __name__ == "__main__":
    unittest.main()
