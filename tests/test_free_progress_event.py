from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


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


class MagicalBattlePromptCandidateTests(unittest.TestCase):
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
        compact = BattleDiaryAnalyzer._compact_json_dump(
            {"scene_events": scene_events, "monsters": monsters}
        )
        self.assertNotIn("event_gimmick", compact)
        self.assertNotIn("battle_gimmick", compact)
        self.assertNotIn("\n", compact)


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

    def test_threshold_50_is_success_and_49_is_obstacle(self):
        success = self._service()._active_event_context(_runtime(ai=50, desire=50))
        obstacle = self._service()._active_event_context(_runtime(ai=49, desire=49))
        self.assertEqual(success["event_outcome"]["result"], "success")
        self.assertEqual(obstacle["event_outcome"]["result"], "obstacle")
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

    def _save(self, patch, *, runtime=None):
        return self.repo.save_action_turn_result(
            group_id="g1",
            user_id="u1",
            result=ActionTurnResult(story_text="测试正文", json_patch=patch),
            event_runtime=runtime,
        )

    def test_start_continue_and_end_event(self):
        started = self._save([], runtime=_runtime(turn_count=0))
        self.assertEqual(started["进程"]["阶段"], "事件")
        self.assertEqual(started["进程"]["当前事件"]["turn_count"], 1)
        self.assertEqual(
            started["进程"]["当前事件"]["selected_targets"][0]["id"],
            "monster",
        )

        continued = self._save([])
        self.assertEqual(continued["进程"]["当前事件"]["turn_count"], 2)
        self.assertEqual(
            continued["进程"]["当前事件"]["selected_targets"][0]["id"],
            "monster",
        )

        ended = self._save(
            [
                {"op": "remove", "path": "/进程/当前事件"},
                {"op": "replace", "path": "/进程/阶段", "value": "日常"},
            ]
        )
        self.assertEqual(ended["进程"]["阶段"], "日常")
        self.assertNotIn("当前事件", ended["进程"])

    def test_allows_event_swap_and_normalizes_unpaired_removal(self):
        self._save([], runtime=_runtime(turn_count=0))
        changed = self._save(
            [
                {
                    "op": "replace",
                    "path": "/进程/当前事件/scene_event/id",
                    "value": "other",
                },
                {
                    "op": "replace",
                    "path": "/进程/当前事件/ai_win_rate",
                    "value": 100,
                },
            ]
        )
        self.assertEqual(changed["进程"]["当前事件"]["scene_event"]["id"], "other")
        self.assertEqual(changed["进程"]["当前事件"]["ai_win_rate"], 100)

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

    def save_action_turn_result(self, **kwargs):
        self.saved = kwargs
        return kwargs["result"].state_snapshot


class _ActiveEventAnalyzer:
    async def select_daily_context(self, **kwargs):
        raise AssertionError("活动事件不应重新选择上下文")

    async def select_magical_battle_context(self, **kwargs):
        raise AssertionError("活动事件不应重新选择上下文")

    async def analyze_action_turn(self, **kwargs):
        self.selection_context = kwargs["selection_context"]
        return SimpleNamespace(
            result=ActionTurnResult(story_text="继续事件", json_patch=[]),
            raw_response="继续事件",
        )


class ActiveEventFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_event_skips_context_selection(self):
        service = ActionTurnApplicationService.__new__(ActionTurnApplicationService)
        service.config_manager = SimpleNamespace()
        service.llm_analyzer = _ActiveEventAnalyzer()
        service.save_repository = _ActiveEventRepository()
        service.card_generator = None
        service.event_book_engine = SimpleNamespace(get_scene_event=lambda event_id: None)

        result = await service.execute_action_turn(
            group_id="g1",
            user_id="u1",
            nickname="测试",
            action_text="尝试逃跑",
            umo=None,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            service.llm_analyzer.selection_context["scene_event"]["id"],
            "capture",
        )
        self.assertEqual(
            service.llm_analyzer.selection_context["selected_targets"][0]["id"],
            "monster",
        )
        self.assertEqual(result.result.selected_targets[0]["name"], "测试魔物")


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


if __name__ == "__main__":
    unittest.main()
