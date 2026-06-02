from __future__ import annotations

from typing import Any

from ...utils.logger import logger


class ChatHistoryReader:
    def __init__(self, context, max_history_messages: int = 120):
        self.context = context
        self.max_history_messages = max(20, min(max_history_messages, 500))

    async def read_player_messages(
        self,
        event,
        group_id: str,
        user_id: str | None,
    ) -> list[str]:
        if not user_id:
            return []

        bot = self._find_onebot_client(event)
        if bot is not None and hasattr(bot, "call_action"):
            messages = await self._read_onebot_group_history(bot, group_id, user_id)
            if messages:
                return messages

        return []

    async def _read_onebot_group_history(
        self,
        bot: Any,
        group_id: str,
        user_id: str,
    ) -> list[str]:
        try:
            result = await bot.call_action(
                "get_group_msg_history",
                group_id=int(group_id),
                count=self.max_history_messages,
                reverseOrder=True,
            )
        except TypeError:
            try:
                result = await bot.call_action(
                    "get_group_msg_history",
                    group_id=int(group_id),
                    count=self.max_history_messages,
                )
            except Exception as exc:
                logger.debug(f"读取 OneBot 群历史失败: {exc}")
                return []
        except Exception as exc:
            logger.debug(f"读取 OneBot 群历史失败: {exc}")
            return []

        raw_messages = result.get("messages", []) if isinstance(result, dict) else []
        player_messages: list[str] = []
        for raw in raw_messages:
            sender = raw.get("sender", {}) if isinstance(raw, dict) else {}
            sender_id = str(sender.get("user_id", "")).strip()
            if sender_id != str(user_id):
                continue
            text = self._extract_text(raw.get("message"))
            if text:
                player_messages.append(text)

        return player_messages[-30:]

    def _find_onebot_client(self, event) -> Any | None:
        candidates = [
            getattr(event, "bot", None),
            getattr(getattr(event, "message_obj", None), "bot", None),
            getattr(getattr(event, "message_obj", None), "client", None),
        ]

        platform_id = self._safe_call(event, "get_platform_id")
        platform_mgr = getattr(self.context, "platform_manager", None)
        if platform_mgr is not None:
            for attr in ("platform_insts", "platforms", "_platforms"):
                platforms = getattr(platform_mgr, attr, None)
                if isinstance(platforms, dict):
                    platform = platforms.get(platform_id) if platform_id else None
                    if platform is None and len(platforms) == 1:
                        platform = next(iter(platforms.values()))
                    if platform is not None:
                        candidates.extend(
                            [
                                getattr(platform, "bot", None),
                                getattr(platform, "client", None),
                                getattr(platform, "lark_api", None),
                            ]
                        )
                        get_client = getattr(platform, "get_client", None)
                        if callable(get_client):
                            try:
                                candidates.append(get_client())
                            except Exception:
                                pass

        for candidate in candidates:
            if candidate is not None and hasattr(candidate, "call_action"):
                return candidate
        return None

    @staticmethod
    def _extract_text(message: Any) -> str:
        if isinstance(message, str):
            return message.strip()
        if not isinstance(message, list):
            return ""

        parts: list[str] = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type")
            data = seg.get("data", {})
            if seg_type in {"text", "Plain", "plain"}:
                text = data.get("text") if isinstance(data, dict) else ""
                if text:
                    parts.append(str(text))
        return " ".join(parts).strip()

    @staticmethod
    def _safe_call(obj: Any, name: str) -> str | None:
        method = getattr(obj, name, None)
        if callable(method):
            try:
                value = method()
                return str(value) if value else None
            except Exception:
                return None
        return None
