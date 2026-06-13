from __future__ import annotations

from ...utils.logger import logger


class ConfigManager:
    def __init__(self, config):
        self.config = config

    def _get_group(self, name: str) -> dict:
        try:
            value = self.config.get(name, {})
        except AttributeError:
            value = getattr(self.config, name, {})
        return value if isinstance(value, dict) else {}

    def _get_battle_group(self) -> dict:
        return self._get_group("battle")

    # ========== 群聊权限 ==========

    def get_group_list_mode(self) -> str:
        """获取群组列表模式 (whitelist/blacklist/none)"""
        return self._get_group("basic").get("group_list_mode", "none")

    def get_group_list(self) -> list[str]:
        """获取群组列表（用于黑白名单）"""
        return self._get_group("basic").get("group_list", [])

    def is_group_allowed(self, group_id_or_umo: str) -> bool:
        """
        根据配置的白/黑名单判断是否允许在该群聊中使用。
        支持传入 simple group_id 或 UMO (Unified Message Origin)。
        """
        mode = self.get_group_list_mode().lower()
        if mode not in ("whitelist", "blacklist", "none"):
            mode = "none"

        if mode == "none":
            return True

        glist = [str(g).strip() for g in self.get_group_list()]
        target = str(group_id_or_umo).strip()

        is_in_list = any(self._is_group_match(target, item) for item in glist)

        if mode == "whitelist":
            return is_in_list
        if mode == "blacklist":
            return not is_in_list

        return True

    def _is_group_match(self, target: str, item: str) -> bool:
        """
        核心匹配逻辑：判断名单中的 item 是否匹配目标的 target (UMO 或纯 ID)。
        支持完整会话ID、纯群号、以及 Telegram 话题(#)/隔离(_) 的穿透匹配。
        """
        if item == target:
            return True

        # 分解目标 UMO 的前缀和 ID 部分
        if ":" in target:
            target_prefix, target_id = target.rsplit(":", 1)
        else:
            target_prefix, target_id = "", target

        # 生成目标 ID 的所有"穿透"候选
        candidates = {target_id}
        if "#" in target_id:
            candidates.add(target_id.split("#", 1)[0])
        if "_" in target_id:
            for part in target_id.split("_"):
                candidates.add(part)

        # 检查名单项的格式
        if ":" in item:
            i_prefix, i_id = item.rsplit(":", 1)
            if target_prefix and i_prefix != target_prefix:
                return False
        else:
            i_id = item

        # 名单项 ID 也可能包含复合形式，需要拆解匹配
        item_variants = {i_id}
        if "#" in i_id:
            item_variants.add(i_id.split("#", 1)[0])
        if "_" in i_id:
            for part in i_id.split("_"):
                item_variants.add(part)

        return not item_variants.isdisjoint(candidates)

    # ========== LLM 设置 ==========

    def get_llm_provider_id(self) -> str:
        return str(self._get_group("llm").get("llm_provider_id", "")).strip()

    def get_subtask_llm_provider_id(self) -> str:
        return str(self._get_group("llm").get("subtask_llm_provider_id", "")).strip()

    def get_llm_retries(self) -> int:
        return int(self._get_group("llm").get("llm_retries", 2) or 2)

    def get_llm_backoff(self) -> int:
        return int(self._get_group("llm").get("llm_backoff", 2) or 2)

    def get_interaction_memory_target_chars(self) -> int:
        value = int(self._get_battle_group().get("interaction_memory_target_chars", 100) or 100)
        return max(50, value)

    def get_memory_compaction_threshold_chars(self) -> int:
        value = int(self._get_battle_group().get("memory_compaction_threshold_chars", 20000) or 0)
        return max(0, value)

    def get_memory_compaction_target_chars(self) -> int:
        value = int(self._get_battle_group().get("memory_compaction_target_chars", 2000) or 2000)
        return max(500, value)

    def get_teammate_recent_record_count(self) -> int:
        value = int(self._get_battle_group().get("teammate_recent_record_count", 1) or 1)
        return max(1, min(value, 5))

    def get_debug_mode(self) -> bool:
        return bool(self._get_battle_group().get("debug_mode", False))

    def get_use_mock_data(self) -> bool:
        return bool(self._get_battle_group().get("use_mock_data", False))

    def get_t2i_rendering_strategies(self) -> list[dict]:
        group = self._get_group("t2i_rendering")
        return [
            {
                "full_page": True,
                "viewport_width": 900,
                "viewport_height": 720,
                "type": group.get("t2i_r1_type", "png"),
                "quality": group.get("t2i_r1_quality", 100),
                "device_scale_factor_level": group.get(
                    "t2i_r1_device_scale", "ultra"
                ),
                "timeout": group.get("t2i_r1_timeout", 50000),
            },
            {
                "full_page": True,
                "viewport_width": 900,
                "viewport_height": 720,
                "type": group.get("t2i_r2_type", "jpeg"),
                "quality": group.get("t2i_r2_quality", 80),
                "device_scale_factor_level": group.get("t2i_r2_device_scale", "high"),
                "timeout": group.get("t2i_r2_timeout", 100000),
            },
        ]

    def get_t2i_max_concurrent(self) -> int:
        return int(self._get_group("performance").get("max_concurrent_t2i", 1) or 1)

    def get_web_host(self) -> str:
        return str(self._get_group("web_viewer").get("host", "0.0.0.0") or "0.0.0.0")

    def get_web_port(self) -> int:
        return int(self._get_group("web_viewer").get("port", 8501) or 8501)

    def get_web_public_base_url(self) -> str:
        return str(self._get_group("web_viewer").get("public_base_url", "")).strip()

    def get_web_public_path_prefix(self) -> str:
        prefix = str(self._get_group("web_viewer").get("public_path_prefix", "")).strip()
        if not prefix or prefix == "/":
            return ""
        return "/" + prefix.strip("/")
