from __future__ import annotations


class ConfigManager:
    def __init__(self, config):
        self.config = config

    def _get_group(self, name: str) -> dict:
        try:
            value = self.config.get(name, {})
        except AttributeError:
            value = getattr(self.config, name, {})
        return value if isinstance(value, dict) else {}

    def get_llm_provider_id(self) -> str:
        return str(self._get_group("llm").get("llm_provider_id", "")).strip()

    def get_enable_avatar_caption(self) -> bool:
        return bool(self._get_group("vision").get("enable_avatar_caption", False))

    def get_vision_provider_id(self) -> str:
        return str(self._get_group("vision").get("vision_provider_id", "")).strip()

    def get_avatar_caption_prompt(self) -> str:
        return str(
            self._get_group("vision").get(
                "avatar_caption_prompt",
                "请用中文简短描述这个 QQ 头像中的外貌特征，只描述头像画面，不判断真人身份。",
            )
        ).strip()

    def get_keep_original_persona(self) -> bool:
        return bool(
            self._get_group("analysis_features").get("keep_original_persona", False)
        )

    def get_use_plugin_specific_persona(self) -> bool:
        return bool(
            self._get_group("analysis_features").get(
                "use_plugin_specific_persona", False
            )
        )

    def get_plugin_specific_persona_id(self) -> str:
        return str(
            self._get_group("analysis_features").get("plugin_specific_persona_id", "")
        ).strip()

    def get_llm_retries(self) -> int:
        return int(self._get_group("llm").get("llm_retries", 2) or 2)

    def get_llm_backoff(self) -> int:
        return int(self._get_group("llm").get("llm_backoff", 2) or 2)

    def get_max_history_messages(self) -> int:
        return int(self._get_group("adventure").get("max_history_messages", 120) or 120)

    def get_diary_compress_interval(self) -> int:
        value = int(self._get_group("adventure").get("diary_compress_interval", 10) or 0)
        return max(0, value)

    def get_diary_compress_count(self) -> int:
        value = int(self._get_group("adventure").get("diary_compress_count", 6) or 0)
        return max(0, value)

    def get_cameo_compress_interval(self) -> int:
        value = int(self._get_group("adventure").get("cameo_compress_interval", 10) or 0)
        return max(0, value)

    def get_cameo_compress_count(self) -> int:
        value = int(self._get_group("adventure").get("cameo_compress_count", 6) or 0)
        return max(0, value)

    def get_debug_mode(self) -> bool:
        return bool(self._get_group("adventure").get("debug_mode", False))

    def get_use_mock_data(self) -> bool:
        return bool(self._get_group("adventure").get("use_mock_data", False))

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
