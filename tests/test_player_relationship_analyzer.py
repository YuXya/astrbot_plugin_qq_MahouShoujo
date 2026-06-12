from __future__ import annotations

import json
import sys
import types
import unittest
from types import SimpleNamespace


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


astrbot = types.ModuleType("astrbot")
astrbot_api = types.ModuleType("astrbot.api")
astrbot_star = types.ModuleType("astrbot.api.star")
astrbot_api.logger = _Logger()
astrbot_star.StarTools = type("StarTools", (), {})
sys.modules.setdefault("astrbot", astrbot)
sys.modules.setdefault("astrbot.api", astrbot_api)
sys.modules.setdefault("astrbot.api.star", astrbot_star)

from src.infrastructure.analysis.analyzers.player_relationship_analyzer import (  # noqa: E402
    PlayerRelationshipAnalyzer,
)


class _EditableManager:
    def __init__(self, resources: dict[str, str], template: str | None = None):
        self.resources = resources
        self.template = template or (
            "events={{event_book_json}}\nmonsters={{monster_book_json}}"
        )

    def read_text(self, relative_path: str) -> str:
        return self.resources.get(relative_path, "")

    def get_prompt(self, name: str) -> str:
        return self.template

    @staticmethod
    def render_text(text: str, variables: dict[str, object]) -> str:
        for key, value in variables.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text


class PlayerRelationshipAnalyzerBookContextTests(unittest.TestCase):
    def _build_prompt(self, event_book: object, monster_book: object) -> str:
        manager = _EditableManager(
            {
                "event_book/default.json": json.dumps(event_book, ensure_ascii=False),
                "monster_book/default.json": json.dumps(monster_book, ensure_ascii=False),
            }
        )
        analyzer = PlayerRelationshipAnalyzer(None, None, manager)
        return analyzer.build_relationship_prompt(
            card=SimpleNamespace(),
            participants_context={},
            world_date="",
        )

    def test_prompt_contains_only_selected_book_fields(self):
        prompt = self._build_prompt(
            {
                "version": 3,
                "categories": [
                    {
                        "id": "category-secret",
                        "events": [
                            {
                                "id": "event-secret",
                                "name": "调查传闻",
                                "keys": ["传闻"],
                                "location_tags": ["学校"],
                                "compatible_monsters": ["魅魔"],
                                "content": "事件正文",
                                "enabled": True,
                                "strategy": "keyword",
                                "opening_hook": "开场秘密",
                            }
                        ],
                    },
                    {"events": ["ignore", {"name": "第二事件", "content": "正文二"}]},
                ],
            },
            {
                "version": 1,
                "entries": [
                    {
                        "id": "monster-secret",
                        "name": "魅魔",
                        "keys": ["诱惑"],
                        "content": "魔物正文",
                        "battle_gimmicks": ["机制秘密"],
                    },
                    None,
                ],
            },
        )

        for expected in (
            "调查传闻",
            "传闻",
            "学校",
            "魅魔",
            "事件正文",
            "第二事件",
            "正文二",
            "诱惑",
            "魔物正文",
        ):
            self.assertIn(expected, prompt)
        for excluded in (
            "category-secret",
            "event-secret",
            "monster-secret",
            "enabled",
            "strategy",
            "开场秘密",
            "battle_gimmicks",
            "机制秘密",
            "version",
        ):
            self.assertNotIn(excluded, prompt)

    def test_invalid_books_fall_back_to_empty_lists(self):
        manager = _EditableManager(
            {
                "event_book/default.json": "not-json",
                "monster_book/default.json": json.dumps({"entries": "invalid"}),
            }
        )
        analyzer = PlayerRelationshipAnalyzer(None, None, manager)

        prompt = analyzer.build_relationship_prompt(
            card=SimpleNamespace(),
            participants_context={},
            world_date="",
        )

        self.assertEqual(prompt, "events=[]\nmonsters=[]")

    def test_legacy_prompt_without_placeholders_still_gets_book_context(self):
        manager = _EditableManager(
            {
                "event_book/default.json": json.dumps(
                    {"categories": [{"events": [{"name": "旧模板事件"}]}]}
                ),
                "monster_book/default.json": json.dumps(
                    {"entries": [{"name": "旧模板魔物"}]}
                ),
            },
            template="自定义旧关系总结模板",
        )
        analyzer = PlayerRelationshipAnalyzer(None, None, manager)

        prompt = analyzer.build_relationship_prompt(
            card=SimpleNamespace(),
            participants_context={},
            world_date="",
        )

        self.assertIn("自定义旧关系总结模板", prompt)
        self.assertIn("旧模板事件", prompt)
        self.assertIn("旧模板魔物", prompt)


if __name__ == "__main__":
    unittest.main()
