from __future__ import annotations

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


class PlayerMemoryWebViewerTests(unittest.TestCase):
    def setUp(self):
        self.viewer = SaveWebViewer.__new__(SaveWebViewer)
        self.viewer.public_path_prefix = ""

    def test_action_card_displays_story_short_memory_and_conversation_no(self):
        html = self.viewer._player_log_cards(
            "g1",
            "u1",
            [
                {
                    "type": "action_turn",
                    "title": "调查行动",
                    "story_text": "完整行动正文",
                    "memory_text": "约一百字的短事件记忆",
                    "conversation_no": 12,
                }
            ],
        )

        self.assertIn("完整行动正文", html)
        self.assertIn("短事件记忆：", html)
        self.assertIn("约一百字的短事件记忆", html)
        self.assertIn("事件序号 #12", html)

    def test_memory_summary_does_not_display_short_memory_label(self):
        html = self.viewer._player_log_cards(
            "g1",
            "u1",
            [
                {
                    "type": "memory_summary",
                    "summary": "长期记忆摘要",
                    "memory_text": "不应作为短事件显示",
                }
            ],
        )

        self.assertIn("长期记忆摘要", html)
        self.assertNotIn("短事件记忆：", html)
        self.assertNotIn("不应作为短事件显示", html)
        self.assertNotIn("事件序号 #", html)

    def test_action_card_omits_empty_short_memory_and_conversation_no(self):
        html = self.viewer._player_log_cards(
            "g1",
            "u1",
            [
                {
                    "type": "action_turn",
                    "story_text": "只有完整正文",
                    "conversation_no": "异常旧值",
                }
            ],
        )

        self.assertIn("只有完整正文", html)
        self.assertNotIn("短事件记忆：", html)
        self.assertNotIn("事件序号 #", html)

    def test_cameo_card_displays_memory_and_source_conversation_no(self):
        html = self.viewer._player_cameo_memory_cards(
            "g1",
            "u1",
            [
                {
                    "type": "interaction_memory",
                    "memory_text": "客串交互记忆",
                    "source_name": "另一位玩家",
                    "conversation_no": 9,
                }
            ],
        )

        self.assertIn("客串交互记忆", html)
        self.assertIn("另一位玩家", html)
        self.assertIn("事件序号 #9", html)

    def test_admin_delete_controls_remain_available(self):
        log_html = self.viewer._player_log_cards(
            "g1",
            "u1",
            [{"type": "action_turn", "_log_index": 3, "story_text": "正文"}],
            allow_delete=True,
        )
        cameo_html = self.viewer._player_cameo_memory_cards(
            "g1",
            "u1",
            [{"type": "interaction_memory", "_log_index": 4, "memory_text": "交互"}],
            allow_delete=True,
        )

        self.assertIn("/player/log/delete", log_html)
        self.assertIn("/player/cameo/delete", cameo_html)


if __name__ == "__main__":
    unittest.main()
