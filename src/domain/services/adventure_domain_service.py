from __future__ import annotations

from ..models.data_models import ReincarnationCard


class AdventureDomainService:
    def normalize_card(
        self,
        raw: dict,
    ) -> ReincarnationCard:
        """解析 LLM 输出的 { info: [...] } 格式，构建 ReincarnationCard。

        每个 info 项含 field / path / description，
        其中 path 形如 /主角/个人信息/姓名，description 为对应值。
        """
        info_list = raw.get("info")
        if isinstance(info_list, list):
            normalized_info: list[dict] = []
            for item in info_list:
                if not isinstance(item, dict):
                    continue
                field_name = self._clean_text(item.get("field"), "")
                path = self._clean_text(item.get("path"), "")
                description = self._clean_text(item.get("description"), "")
                if not path.startswith("/主角/"):
                    # 尝试修补：若 field 可用，自动补 /主角/ 前缀
                    if path and not path.startswith("/"):
                        path = f"/主角/{path}"
                    elif not path and field_name:
                        path = f"/主角/{field_name}"
                if not field_name:
                    # 从 path 推导 field
                    parts = path.strip("/").split("/")
                    field_name = parts[-1] if parts else ""
                normalized_info.append({
                    "field": field_name[:32],
                    "path": path[:120],
                    "description": description[:300],
                })
            return ReincarnationCard(info=normalized_info)

        # 兜底：如果 LLM 输出的不是 info 格式，生成最基础的卡
        return ReincarnationCard(info=[])

    def build_mock_card(
        self,
        theme: str,
        nickname: str | None = None,
    ) -> ReincarnationCard:
        target_name = nickname or "测试群友"
        return ReincarnationCard(info=[
            {"field": "姓名", "path": "/主角/个人信息/姓名", "description": target_name},
            {"field": "性格特质", "path": "/主角/个人信息/性格特质", "description": "表面一本正经，实际很容易被新鲜事吸引；喜欢吐槽，但关键时刻会认真帮大家收拾局面。"},
            {"field": "代表色", "path": "/主角/个人信息/代表色", "description": "樱粉色 (#FFB6C1)"},
            {"field": "核心能力", "path": "/主角/个人信息/核心能力", "description": "把群里的零碎话题炼成奇妙道具"},
            {"field": "使魔伙伴种类", "path": "/主角/个人信息/使魔伙伴种类", "description": "小狐"},
            {"field": "使魔伙伴与主角关系", "path": "/主角/个人信息/使魔伙伴与主角关系", "description": "信赖伙伴"},
            {"field": "年龄", "path": "/主角/个人信息/年龄", "description": "14"},
            {"field": "身份/职业", "path": "/主角/个人信息/身份&职业", "description": "初中生"},
            {"field": "魔法少女名", "path": "/主角/个人信息/魔法少女名", "description": "星樱"},
            {"field": "武装", "path": "/主角/个人信息/武装", "description": "星纹法杖"},
            {"field": "变身服", "path": "/主角/个人信息/变身服", "description": "转生后披着过大的星星斗篷，背着比本人还认真的小书包"},
            {"field": "脸型", "path": "/主角/相貌特征/脸型", "description": "瓜子脸"},
            {"field": "五官", "path": "/主角/相貌特征/五官", "description": "圆圆的眼睛，带着好奇的光"},
            {"field": "眼睛颜色", "path": "/主角/相貌特征/眼睛颜色", "description": "红瞳"},
            {"field": "发型与发色", "path": "/主角/相貌特征/发型与发色", "description": "白色长发双马尾"},
            {"field": "特殊记号", "path": "/主角/相貌特征/特殊记号", "description": "无"},
            {"field": "身高", "path": "/主角/身材细节/身高", "description": "142cm"},
            {"field": "三围", "path": "/主角/身材细节/三围", "description": "B78(A)/W55/H82"},
            {"field": "体态", "path": "/主角/身材细节/体态", "description": "娇小可爱"},
            {"field": "肌肉线条", "path": "/主角/身材细节/肌肉线条", "description": "纤细柔软"},
            {"field": "体脂率", "path": "/主角/身材细节/体脂率", "description": "22%"},
            {"field": "皮肤状态", "path": "/主角/身材细节/皮肤状态", "description": "白皙滑嫩"},
            {"field": "乳房形状", "path": "/主角/性器官特征/乳房形状", "description": "小巧圆润"},
            {"field": "乳晕与乳头颜色", "path": "/主角/性器官特征/乳晕与乳头颜色", "description": "粉嫩"},
            {"field": "小穴形态", "path": "/主角/性器官特征/小穴形态", "description": "紧致浅淡"},
            {"field": "体毛状况", "path": "/主角/性器官特征/体毛状况", "description": "稀疏浅淡"},
            {"field": "天生敏感度", "path": "/主角/性器官特征/天生敏感度", "description": "耳后和锁骨较敏感"},
        ])

    @staticmethod
    def _clean_text(value: object, default: str) -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
