from __future__ import annotations

import json
import sys
import types
import unittest


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

from src.infrastructure.web.save_web_viewer import SaveWebViewer


class FetishBookWebEditorTests(unittest.TestCase):
    def test_normalizes_current_percentage_schema(self):
        raw = {
            "version": 1,
            "base_path": "/主角/快感状态/性癖/",
            "entries": [
                {
                    "id": "entry_1",
                    "title": "测试",
                    "enabled": True,
                    "recursive": False,
                    "strategy": "always",
                    "keys": ["测试", "触发"],
                    "content": "简介",
                    "percentage_descriptions": {
                        "0-20": "初始",
                        "21-40": "发展",
                        "41-60": "深入",
                        "61-80": "沉迷",
                        "81-100": "完全",
                    },
                }
            ],
        }

        normalized = SaveWebViewer._normalize_fetish_book(raw)

        self.assertEqual(normalized, raw)
        self.assertNotIn("level_descriptions", normalized["entries"][0])

    def test_normalizes_string_keys_and_missing_fields(self):
        normalized = SaveWebViewer._normalize_fetish_book(
            {"entries": [{"keys": "一, 二\n三", "strategy": "invalid"}]}
        )
        entry = normalized["entries"][0]

        self.assertEqual(entry["id"], "entry_1")
        self.assertEqual(entry["keys"], ["一", "二", "三"])
        self.assertEqual(entry["strategy"], "keyword")
        self.assertFalse(entry["recursive"])
        self.assertEqual(
            list(entry["percentage_descriptions"]),
            ["0-20", "21-40", "41-60", "61-80", "81-100"],
        )

    def test_editor_response_contains_visual_controls(self):
        viewer = SaveWebViewer.__new__(SaveWebViewer)
        viewer.public_path_prefix = ""
        response = viewer._fetish_book_file_response(
            "编辑 性癖书 default.json",
            "fetish_book/default.json",
            "world_background",
            "说明",
            json.dumps({"version": 1, "entries": []}, ensure_ascii=False),
        )
        body = response.text

        self.assertIn('id="fetish-book-form"', body)
        self.assertIn('id="fetish-book-base-path"', body)
        self.assertIn("percentage_descriptions", body)
        self.assertIn("81-100", body)
        self.assertIn("编辑源码", body)
        self.assertIn("导入 JSON", body)

    def test_invalid_json_falls_back_to_plain_editor(self):
        viewer = SaveWebViewer.__new__(SaveWebViewer)
        viewer.public_path_prefix = ""
        response = viewer._fetish_book_file_response(
            "编辑 性癖书 default.json",
            "fetish_book/default.json",
            "world_background",
            "",
            "{invalid",
        )

        self.assertIn("性癖书 JSON 解析失败", response.text)
        self.assertIn('class="content-editor"', response.text)


if __name__ == "__main__":
    unittest.main()
