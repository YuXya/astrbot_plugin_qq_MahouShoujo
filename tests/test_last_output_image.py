from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
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

from src.infrastructure.storage.player_save_repository import PlayerSaveRepository  # noqa: E402


class LastOutputImageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.repo = PlayerSaveRepository.__new__(PlayerSaveRepository)
        self.repo.root_dir = self.root
        self.repo.editable_manager = None
        self.user_dir = self.repo.get_user_dir("g1", "u1")
        self.user_dir.mkdir(parents=True)
        (self.user_dir / "player_data.json").write_text(
            json.dumps({"user_id": "u1"}),
            encoding="utf-8",
        )

    def _source(self, name: str, content: bytes) -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_saves_png_and_reads_it_back(self):
        saved = self.repo.save_last_output_image("g1", "u1", self._source("one.png", b"png"))

        self.assertEqual(saved, str(self.user_dir / "last_output.png"))
        self.assertEqual((self.user_dir / "last_output.png").read_bytes(), b"png")
        self.assertEqual(self.repo.get_last_output_image("g1", "u1"), saved)

    def test_switching_format_overwrites_and_keeps_only_one_image(self):
        self.repo.save_last_output_image("g1", "u1", self._source("one.png", b"png"))
        saved = self.repo.save_last_output_image("g1", "u1", self._source("two.jpeg", b"jpg"))

        self.assertEqual(saved, str(self.user_dir / "last_output.jpg"))
        self.assertFalse((self.user_dir / "last_output.png").exists())
        self.assertEqual((self.user_dir / "last_output.jpg").read_bytes(), b"jpg")

    def test_failed_copy_preserves_previous_image(self):
        old_path = self.repo.save_last_output_image(
            "g1", "u1", self._source("old.png", b"old")
        )
        with patch("shutil.copyfile", side_effect=OSError("copy failed")):
            saved = self.repo.save_last_output_image(
                "g1", "u1", self._source("new.jpg", b"new")
            )

        self.assertIsNone(saved)
        self.assertEqual(self.repo.get_last_output_image("g1", "u1"), old_path)
        self.assertEqual((self.user_dir / "last_output.png").read_bytes(), b"old")

    def test_missing_source_preserves_previous_image(self):
        old_path = self.repo.save_last_output_image(
            "g1", "u1", self._source("old.jpg", b"old")
        )

        saved = self.repo.save_last_output_image(
            "g1", "u1", self.root / "missing.png"
        )

        self.assertIsNone(saved)
        self.assertEqual(self.repo.get_last_output_image("g1", "u1"), old_path)
        self.assertEqual((self.user_dir / "last_output.jpg").read_bytes(), b"old")

    def test_images_are_isolated_by_group_and_user(self):
        first = self.repo.save_last_output_image("g1", "u1", self._source("a.png", b"a"))
        second = self.repo.save_last_output_image("g2", "u1", self._source("b.jpg", b"b"))

        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), b"a")
        self.assertEqual(Path(second).read_bytes(), b"b")

    def test_delete_player_save_removes_last_output_image(self):
        self.repo.save_last_output_image("g1", "u1", self._source("one.png", b"png"))

        self.assertTrue(self.repo.delete_player_save("g1", "u1"))
        self.assertFalse(self.user_dir.exists())
        self.assertIsNone(self.repo.get_last_output_image("g1", "u1"))


if __name__ == "__main__":
    unittest.main()
