from __future__ import annotations

from ..models.data_models import ReincarnationCard


class AdventureDomainService:
    def normalize_card(
        self,
        raw: dict,
    ) -> ReincarnationCard:
        title = self._clean_text(raw.get("title"), "魔法少女转生人物卡")
        subtitle = self._clean_text(raw.get("subtitle"), "新的命运正在发芽")
        target_name = self._clean_text(raw.get("target_name"), "神秘群友")
        class_name = self._clean_text(raw.get("class_name"), "见习冒险者")
        appearance = self._clean_text(
            raw.get("appearance"),
            "软乎乎的短发和圆圆眼睛被保留下来，披上过大的星纹斗篷，像刚从童话书里跑出来。",
        )
        personality = self._clean_text(raw.get("personality"), "好奇、嘴硬、容易被夸奖哄开心")
        talent = self._clean_text(raw.get("talent"), "能把平平无奇的话题变成热闹的小事件")
        birth_description = self._clean_text(
            raw.get("birth_description"),
            "她直接在魔法少女醒来，身边只有陌生的风声和一点能遮身的小行李。",
        )
        quote = self._clean_text(raw.get("quote"), "哼，我才不是迷路了，只是在巡视新世界！")
        footer = self._clean_text(raw.get("footer"), "魔法少女转生人物卡，仅供娱乐。")

        return ReincarnationCard(
            title=title[:32],
            subtitle=subtitle[:48],
            target_name=target_name[:32],
            class_name=class_name[:24],
            appearance=appearance[:260],
            personality=personality[:180],
            talent=talent[:120],
            birth_description=birth_description[:180],
            likes=self._normalize_likes(raw.get("likes")),
            quote=quote[:80],
            footer=footer[:80],
        )

    def build_mock_card(
        self,
        theme: str,
        nickname: str | None = None,
    ) -> ReincarnationCard:
        target_name = nickname or "测试群友"
        return ReincarnationCard(
            title="魔法少女转生人物卡",
            subtitle=f"{target_name} 的今日新身份",
            target_name=target_name,
            class_name="迷你咒语收藏家",
            appearance="转生后披着过大的星星斗篷，背着比本人还认真的小书包，整个人可爱得像会发光的棉花糖。",
            personality="表面一本正经，实际很容易被新鲜事吸引；喜欢吐槽，但关键时刻会认真帮大家收拾局面。",
            talent="把群里的零碎话题炼成奇妙道具，并用一句吐槽点亮全场。",
            birth_description="她直接在薄雾森林的溪边醒来，斗篷边沾着露水，附近有苔石、小蘑菇和一条通向村落的窄路。",
            likes=["热闹话题", "甜点", "被认真回应"],
            quote="才、才不是特地来救你的，只是顺路而已！",
            footer="玩具模式：未调用人物卡 LLM。",
        )

    @staticmethod
    def _normalize_likes(raw_likes: object) -> list[str]:
        if not isinstance(raw_likes, list):
            return ["甜点", "冒险", "被夸奖"]
        likes = [str(item).strip()[:16] for item in raw_likes if str(item).strip()]
        return likes[:4] or ["甜点", "冒险", "被夸奖"]

    @staticmethod
    def _clean_text(value: object, default: str) -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
