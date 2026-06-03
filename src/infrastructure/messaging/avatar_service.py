from __future__ import annotations

from ...utils.logger import logger


class QQAvatarService:
    """根据 QQ 号构建头像 URL。

    QQ 头像是公开资源，只需 user_id 即可拼出高清头像地址，
    无需额外 API 调用。
    """

    USER_AVATAR_HD_TEMPLATE = (
        "https://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640&img_type=jpg"
    )

    def build_avatar_url(self, user_id: str | None) -> str:
        user_id = str(user_id or "").strip()
        if not user_id or not user_id.isdigit():
            return ""
        return self.USER_AVATAR_HD_TEMPLATE.format(user_id=user_id)
