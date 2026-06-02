from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from astrbot.api.star import StarTools

from ...utils.logger import logger


class RecentLLMMessageRepository:
    DEFAULT_LIMIT = 3
    MAX_LIMIT = 100
    PLUGIN_NAME = "astrbot_plugin_qq_adventurer"
    _lock = threading.Lock()

    def __init__(self, root_dir: Path | None = None):
        self.root_dir = root_dir or (
            StarTools.get_data_dir(self.PLUGIN_NAME) / "debug_data"
        )
        self.path = self.root_dir / "recent_llm_messages.json"

    def list_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._read_data()["records"]))

    def get_limit(self) -> int:
        with self._lock:
            return self._read_data()["limit"]

    def set_limit(self, value: object) -> int:
        limit = self._normalize_limit(value)
        with self._lock:
            data = self._read_data()
            data["limit"] = limit
            data["records"] = data["records"][-limit:]
            self._write_data(data)
        return limit

    def clear(self) -> None:
        with self._lock:
            data = self._read_data()
            data["records"] = []
            self._write_data(data)

    def append(
        self,
        *,
        purpose: str,
        provider_id: str,
        prompt: str,
        system_prompt: str | None,
        response: str,
        error: str = "",
    ) -> None:
        record = {
            "created_at": int(time.time() * 1000),
            "purpose": str(purpose or "文本补全"),
            "provider_id": str(provider_id or ""),
            "system_prompt": str(system_prompt or ""),
            "prompt": str(prompt or ""),
            "response": str(response or ""),
            "error": str(error or ""),
        }
        try:
            with self._lock:
                data = self._read_data()
                data["records"].append(record)
                data["records"] = data["records"][-data["limit"]:]
                self._write_data(data)
        except Exception as exc:
            logger.warning(f"保存最近 LLM 消息失败: {exc}")

    def _read_data(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"limit": self.DEFAULT_LIMIT, "records": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"读取最近 LLM 消息失败，将使用空记录: {exc}")
            return {"limit": self.DEFAULT_LIMIT, "records": []}
        if not isinstance(raw, dict):
            return {"limit": self.DEFAULT_LIMIT, "records": []}
        records = raw.get("records")
        return {
            "limit": self._normalize_limit(raw.get("limit")),
            "records": [item for item in records if isinstance(item, dict)]
            if isinstance(records, list)
            else [],
        }

    def _write_data(self, data: dict[str, Any]) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

    @classmethod
    def _normalize_limit(cls, value: object) -> int:
        try:
            return max(1, min(int(value or cls.DEFAULT_LIMIT), cls.MAX_LIMIT))
        except Exception:
            return cls.DEFAULT_LIMIT
