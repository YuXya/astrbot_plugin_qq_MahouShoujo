from __future__ import annotations

import html
import hmac
import json
import re
import secrets
from typing import Any
from urllib.parse import quote

from aiohttp import web

from ...shared.levels import (
    ALL_VISIBLE_LEVELS,
    LEVEL_LABELS,
    level_label,
    normalize_visible_levels,
    visible_levels_label,
)
from ...utils.logger import logger
from ..storage.recent_llm_message_repository import RecentLLMMessageRepository
from ..storage.state_progress import (
    build_progress_sections,
    build_state_display_items,
    level_display,
    level_exp_percent,
)


ADMIN_LOGIN_CODE = "优夏酱世界第一可爱"
SESSION_COOKIE_NAME = "qq_mahoushoujo_session"
SESSION_ADMIN_ROLE = "admin"
SESSION_USER_ROLE = "user"


class SaveWebViewer:
    def __init__(
        self,
        repository,
        editable_manager,
        host: str = "0.0.0.0",
        port: int = 8501,
        public_path_prefix: str = "",
        recent_llm_messages: RecentLLMMessageRepository | None = None,
    ):
        self.repository = repository
        self.editable_manager = editable_manager
        self.host = host
        self.port = int(port)
        self.public_path_prefix = self._normalize_path_prefix(public_path_prefix)
        self.recent_llm_messages = recent_llm_messages or RecentLLMMessageRepository()
        self.token = ""
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def is_running(self) -> bool:
        return self._runner is not None

    async def start(self) -> str:
        if self.is_running:
            return self.token

        self.token = secrets.token_urlsafe(24)
        app = web.Application()
        self._add_route(app, "GET", "/login", self._login_page)
        self._add_route(app, "POST", "/login", self._login)
        self._add_route(app, "POST", "/logout", self._logout)
        self._add_route(app, "GET", "/", self._index)
        self._add_route(app, "GET", "/city", self._city_detail)
        self._add_route(app, "POST", "/city/name/save", self._city_name_save)
        self._add_route(app, "GET", "/player", self._player_detail)
        self._add_route(app, "POST", "/player/profile/save", self._player_profile_save)
        self._add_route(app, "POST", "/player/delete", self._player_delete)
        self._add_route(app, "POST", "/player/log/delete", self._player_log_delete)
        self._add_route(app, "POST", "/player/log/clear", self._player_log_clear)
        self._add_route(app, "POST", "/player/cameo/delete", self._player_cameo_delete)
        self._add_route(app, "POST", "/player/cameo/clear", self._player_cameo_clear)
        self._add_route(app, "POST", "/player/state/reset", self._player_state_reset)
        self._add_route(app, "GET", "/player/file/source", self._player_file_source)
        self._add_route(app, "POST", "/player/file/save", self._player_file_save)
        self._add_route(app, "GET", "/player/file/export", self._player_file_export)
        self._add_route(app, "POST", "/player/file/import", self._player_file_import)
        self._add_route(app, "GET", "/editable", self._editable_index)
        self._add_route(app, "GET", "/llm-messages", self._llm_messages)
        self._add_route(app, "POST", "/llm-messages/limit", self._llm_messages_limit)
        self._add_route(app, "POST", "/llm-messages/clear", self._llm_messages_clear)
        self._add_route(app, "GET", "/editable/file", self._editable_file)
        self._add_route(app, "POST", "/editable/save", self._editable_save)
        self._add_route(app, "POST", "/editable/reset", self._editable_reset)
        self._add_route(app, "GET", "/editable/source", self._editable_source)
        self._add_route(app, "POST", "/editable/source/save", self._editable_source_save)
        self._add_route(app, "GET", "/editable/export", self._editable_export)
        self._add_route(app, "GET", "/editable/export/key-info", self._editable_export_key_info)
        self._add_route(app, "POST", "/editable/import", self._editable_import)
        self._add_route(app, "GET", "/health", self._health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(f"魔法少女存档网页已启动: {self.host}:{self.port}")
        return self.token

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._runner = None
        self._site = None
        self.token = ""
        logger.info("魔法少女存档网页已关闭")

    def _add_route(self, app: web.Application, method: str, path: str, handler) -> None:
        app.router.add_route(method, path, handler)
        prefixed_path = self._url(path)
        if prefixed_path != path:
            app.router.add_route(method, prefixed_path, handler)

    async def _login_page(self, request: web.Request) -> web.Response:
        if self._is_authorized(request):
            raise self._redirect("/")
        return self._login_response()

    async def _login(self, request: web.Request) -> web.Response:
        data = await request.post()
        qq_id = str(data.get("qq_id", "")).strip()
        if qq_id == ADMIN_LOGIN_CODE:
            cookie_value = self._build_session_cookie(SESSION_ADMIN_ROLE)
        else:
            saves = self.repository.list_saves_by_user(qq_id)
            if not saves:
                return self._login_response("没有找到这个 QQ 号的魔法少女存档。", status=401)
            cookie_value = self._build_session_cookie(SESSION_USER_ROLE, self._safe_session_id(qq_id))

        response = self._redirect("/")
        response.set_cookie(
            SESSION_COOKIE_NAME,
            cookie_value,
            httponly=True,
            samesite="Lax",
            path=self._cookie_path(),
        )
        raise response

    async def _logout(self, request: web.Request) -> web.Response:
        response = self._redirect("/login")
        response.del_cookie(SESSION_COOKIE_NAME, path=self._cookie_path())
        raise response

    def _login_response(self, error: str = "", status: int = 200) -> web.Response:
        error_html = f"<p class=\"error\">{self._e(error)}</p>" if error else ""
        return self._html_response(
            "魔法少女登录",
            f"""
            <section class="login-shell" aria-label="魔法少女网页登录">
              <div class="sparkles" aria-hidden="true">
                <span></span><span></span><span></span><span></span><span></span><span></span>
              </div>
              <div class="login-panel">
                <div class="login-badge" aria-hidden="true">
                  <span class="badge-star">✦</span>
                </div>
                <p class="login-kicker">Mahou Shoujo Portal</p>
                <h1>魔法少女网页登录</h1>
                <p class="login-copy">输入 QQ 号，唤醒你的契约档案，让星光为你展开下一页冒险。</p>
                {error_html}
                <form method="post" action="{self._url('/login')}">
                  <label for="qq-id">契约者 QQ 号</label>
                  <div class="magic-input">
                    <span aria-hidden="true">☾</span>
                    <input id="qq-id" name="qq_id" type="text" autocomplete="username" autofocus placeholder="在星光中输入 QQ 号">
                  </div>
                  <div class="actions login-actions">
                    <button class="login-button" type="submit">开启魔法档案</button>
                  </div>
                </form>
                <div class="login-runes" aria-hidden="true">
                  <span>星愿</span><span>守护</span><span>契约</span>
                </div>
              </div>
              <div class="login-vision" aria-hidden="true">
                <div class="moon"></div>
                <div class="magic-circle">
                  <div class="circle-core">✧</div>
                  <span class="orbit orbit-one"></span>
                  <span class="orbit orbit-two"></span>
                  <span class="orbit orbit-three"></span>
                </div>
                <div class="wand">
                  <span class="wand-star">✦</span>
                  <span class="wand-stick"></span>
                </div>
              </div>
            </section>
            """,
            status=status,
            show_logout=False,
        )

    async def _health(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return self._forbidden()
        return web.json_response({"ok": True})

    async def _llm_messages(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        records = self.recent_llm_messages.list_records()
        record_cards = []
        for index, record in enumerate(records, start=1):
            created_at = self._format_time(record.get("created_at"))
            purpose = self._e(record.get("purpose") or "文本补全")
            provider_id = self._e(record.get("provider_id") or "未知")
            error = str(record.get("error") or "")
            error_html = (
                f'<p class="error">错误：{self._e(error)}</p>'
                if error
                else ""
            )
            record_cards.append(
                f"""
                <details class="log-card">
                  <summary class="log-card-summary">
                    <div class="log-card-head">
                      <div>
                        <span class="log-index">最近记录 #{index}</span>
                        <h3>{purpose}</h3>
                      </div>
                    </div>
                    <div class="log-meta summary-meta">
                      <span>{self._e(created_at)}</span>
                      <span>Provider：{provider_id}</span>
                    </div>
                  </summary>
                  <div class="log-card-body">
                    {error_html}
                    <h2>System Prompt</h2>
                    <pre>{self._e(record.get("system_prompt") or "（无）")}</pre>
                    <h2>发送给 AI 的完整消息</h2>
                    <pre>{self._e(record.get("prompt") or "")}</pre>
                    <h2>AI 原始回复</h2>
                    <pre>{self._e(record.get("response") or "（无回复）")}</pre>
                  </div>
                </details>
                """
            )
        records_html = "\n".join(record_cards) or (
            '<div class="empty-state">还没有文本补全消息记录。</div>'
        )
        limit = self.recent_llm_messages.get_limit()
        return self._html_response(
            "最近消息记录",
            f"""
            <h1>最近消息记录</h1>
            <p><a href="{self._url('/')}">返回存档列表</a></p>
            <p class="muted">管理员调试页面。记录文本补全请求的完整 System Prompt、消息正文和 AI 原始回复。</p>
            <div class="source-actions">
              <form class="inline-import-form" method="post" action="{self._url('/llm-messages/limit')}">
                <label class="inline-label" for="llm-message-limit">保留最近</label>
                <input id="llm-message-limit" name="limit" type="number" min="1" max="100" value="{limit}">
                <span>次</span>
                <button class="compact-button" type="submit">保存数量</button>
              </form>
              <form class="inline-form" method="post" action="{self._url('/llm-messages/clear')}" onsubmit="return confirm('确定清空最近消息记录？');">
                <button class="danger compact-button" type="submit">清空记录</button>
              </form>
            </div>
            <div class="log-list">{records_html}</div>
            """,
        )

    async def _llm_messages_limit(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()
        data = await request.post()
        self.recent_llm_messages.set_limit(data.get("limit"))
        raise self._redirect("/llm-messages")

    async def _llm_messages_clear(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()
        self.recent_llm_messages.clear()
        raise self._redirect("/llm-messages")

    async def _index(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if not session:
            return self._forbidden()
        if session["role"] == SESSION_USER_ROLE:
            return self._user_index(session["user_id"])

        cities = self.repository.list_cities()
        rows = []
        for city in cities:
            city_id = str(city.get("city_id") or "")
            city_name = str(city.get("city_name") or city_id)
            href = self._url(f"/city?city_id={quote(city_id, safe='')}")
            rows.append(
                "<tr>"
                f"<td><a href=\"{href}\">{self._e(city_name)}</a></td>"
                f"<td>{self._e(city_id)}</td>"
                f"<td>{self._e(city.get('player_count', 0))}</td>"
                f"<td>{self._format_time(city.get('updated_at'))}</td>"
                f"<td><a class=\"button-link compact-link\" href=\"{href}\">进入城市</a></td>"
                "</tr>"
            )

        body = "\n".join(rows) or "<tr><td colspan=\"5\">还没有任何城市存档。</td></tr>"
        return self._html_response(
            "魔法少女城市",
            f"""
            <h1>魔法少女城市</h1>
            <p class="muted">管理员页面。每个城市对应一个群，城市 ID 就是群号。</p>
            <p class="nav-actions">
              <a class="button-link" href="{self._url('/editable?category=world_background')}">编辑世界背景</a>
              <a class="button-link" href="{self._url('/editable?category=text_completion')}">编辑文本补全</a>
              <a class="button-link secondary-link" href="{self._url('/llm-messages')}">最近消息记录</a>
            </p>
            <table>
              <thead>
                <tr>
                  <th>城市名</th><th>城市 ID</th><th>魔法少女数量</th><th>最近更新</th><th>操作</th>
                </tr>
              </thead>
              <tbody>{body}</tbody>
            </table>
            """,
        )

    async def _city_detail(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        city_id = str(request.query.get("city_id", "")).strip()
        if not city_id:
            raise web.HTTPBadRequest(text="missing city_id")

        city_name = self.repository.get_city_name(city_id)
        saves = self.repository.list_saves_by_city(city_id)
        rows = []
        for item in saves:
            row = self._save_table_row(item)
            delete_form = (
                f"<form class=\"inline-form\" method=\"post\" action=\"{self._url('/player/delete')}\" "
                "onsubmit=\"return confirm('确定删除这个玩家存档？此操作不可恢复。');\">"
                f"<input type=\"hidden\" name=\"group_id\" value=\"{row['group_id']}\">"
                f"<input type=\"hidden\" name=\"user_id\" value=\"{row['user_id']}\">"
                "<button class=\"danger compact-button\" type=\"submit\">删除</button>"
                "</form>"
            )
            rows.append(
                "<tr>"
                f"<td>{row['user_id']}</td>"
                f"<td><a href=\"{row['href']}\">{row['nickname']}</a></td>"
                f"<td>{row['class_name']}</td>"
                f"<td>{row['level']}</td>"
                f"<td>{row['updated_at']}</td>"
                f"<td>{delete_form}</td>"
                "</tr>"
            )

        body = "\n".join(rows) or "<tr><td colspan=\"6\">这个城市还没有玩家存档。</td></tr>"
        return self._html_response(
            f"{city_name}魔法少女存档",
            f"""
            <p><a href="{self._url('/')}">返回城市列表</a></p>
            <h1>{self._e(city_name)}</h1>
            <p class="muted">城市 ID：{self._e(city_id)}</p>
            <section class="detail-panel city-editor-panel">
              <h2>城市档案</h2>
              <form class="inline-import-form city-name-form" method="post" action="{self._url('/city/name/save')}">
                <input type="hidden" name="city_id" value="{self._e(city_id)}">
                <label class="inline-label" for="city-name">城市名</label>
                <input id="city-name" name="city_name" type="text" value="{self._e(city_name)}">
                <button class="compact-button" type="submit">保存城市名</button>
              </form>
            </section>
            <table>
              <thead>
                <tr>
                  <th>用户</th><th>角色</th><th>职阶</th><th>等级</th><th>更新时间</th><th>操作</th>
                </tr>
              </thead>
              <tbody>{body}</tbody>
            </table>
            """,
        )

    async def _city_name_save(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()
        data = await request.post()
        city_id = str(data.get("city_id", "")).strip()
        if not city_id:
            raise web.HTTPBadRequest(text="missing city_id")
        self.repository.update_city_name(city_id, str(data.get("city_name", "")))
        raise self._redirect(f"/city?city_id={quote(city_id, safe='')}")

    def _user_index(self, user_id: str) -> web.Response:
        saves = self.repository.list_saves_by_user(user_id)
        city_cards = []
        for item in saves:
            row = self._save_table_row(item)
            meta_items = [row["nickname"], row["level"], row["updated_at"]]
            meta_html = "".join(
                f"<span>{value}</span>" for value in meta_items if str(value or "").strip()
            )
            city_cards.append(
                f"""
                <article class="player-city-card">
                  <div class="city-card-orb" aria-hidden="true">✦</div>
                  <div class="city-card-main">
                    <span class="city-card-label">城市档案</span>
                    <h2>{row['city_name']}</h2>
                    <p>城市 ID：{row['group_id']}</p>
                    <div class="city-card-meta">
                      {meta_html}
                    </div>
                  </div>
                  <a class="player-enter-link" href="{row['href']}">进入城市</a>
                </article>
                """
            )

        city_list = "\n".join(city_cards) or (
            """
            <section class="player-empty-state">
              <div aria-hidden="true">✧</div>
              <h2>还没有可进入城市</h2>
              <p>完成一次转生后，你的魔法少女个人档案会在这里生成，并按所在城市归档。</p>
            </section>
            """
        )
        return self._html_response(
            "魔法少女个人档案",
            f"""
            <section class="player-shell" aria-label="魔法少女个人档案">
              <div class="player-stars" aria-hidden="true">
                <span></span><span></span><span></span><span></span><span></span>
              </div>
              <header class="player-hero">
                <p class="player-kicker">Mahou Shoujo City Gate</p>
                <h1>魔法少女个人档案</h1>
                <p>契约者 {self._e(user_id)}，这里记录着你的魔法少女身份、所在城市与成长痕迹。</p>
              </header>
              <section class="player-city-section">
                <div class="player-section-head">
                  <div>
                    <span>Available Cities</span>
                    <h2>可进入城市</h2>
                  </div>
                </div>
                <div class="player-city-grid">{city_list}</div>
              </section>
            </section>
            """,
        )

    def _save_table_row(self, item: dict[str, Any]) -> dict[str, str]:
        group_id_raw = str(item.get("group_id", "") or "")
        user_id_raw = str(item.get("user_id", "") or "")
        href = self._url(
            f"/player?group_id={quote(group_id_raw, safe='')}&user_id={quote(user_id_raw, safe='')}"
        )
        return {
            "group_id": self._e(group_id_raw),
            "city_name": self._e(self.repository.get_city_name(group_id_raw)),
            "user_id": self._e(user_id_raw),
            "nickname": self._e(item.get("nickname") or item.get("target_name") or "未命名"),
            "class_name": self._e(self._rank_display(item.get("class_name", ""))),
            "level": self._e(self._rank_display(level_label(item.get("level", 1)))),
            "updated_at": self._format_time(item.get("updated_at")),
            "href": href,
        }

    @staticmethod
    def _rank_display(value: object) -> str:
        text = str(value or "").strip()
        if re.fullmatch(r"[A-FS]", text, flags=re.IGNORECASE):
            return f"{text.upper()}级"
        return text

    async def _editable_index(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        selected_category = request.query.get("category", "world_background")
        category_titles = {
            "world_background": "世界背景",
            "text_completion": "文本补全",
        }
        category_descriptions = {
            "world_background": "影响魔法少女公共设定、地点、魔物、种族和职业等背景内容。",
            "text_completion": "管理发给 AI 的 Prompt、System Prompt 和世界书注入话术。",
        }
        if selected_category not in category_titles:
            raise web.HTTPBadRequest(text="invalid editable category")

        items = self.editable_manager.list_editable_files()
        rows = self._editable_rows(items, selected_category)
        title = category_titles[selected_category]
        description = category_descriptions[selected_category]

        return self._html_response(
            title,
            f"""
            <h1>{self._e(title)}</h1>
            <p><a href="{self._url('/')}">返回存档列表</a></p>
            <p class="muted">保存时会自动备份旧文件。世界书、状态书、技能书、性癖书、事件书和魔物书 default.json 会先做 JSON 校验。</p>
            {self._editable_table(description, rows)}
            """,
        )

    async def _editable_file(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        file_id = request.query.get("id", "")
        if not self._is_editable_file(file_id):
            raise web.HTTPBadRequest(text="invalid editable file")
        back_category = self._editable_back_category(request.query.get("category"), file_id)
        content = self.editable_manager.read_text(file_id)
        note = self.editable_manager.read_note(file_id)
        meta = self._editable_file_meta(file_id)
        label = meta.get("label", file_id) if meta else file_id
        title = f"编辑 {label}"
        if self._is_structured_book_file(file_id):
            if file_id == "monster_book/default.json":
                return self._monster_book_file_response(
                    title,
                    file_id,
                    back_category,
                    note,
                    content,
                )
            if file_id == "event_book/default.json":
                return self._event_book_file_response(
                    title,
                    file_id,
                    back_category,
                    note,
                    content,
                )
            return self._world_book_file_response(
                title,
                file_id,
                back_category,
                note,
                content,
            )

        return self._plain_editable_file_response(
            title,
            file_id,
            back_category,
            note,
            content,
        )

    def _plain_editable_file_response(
        self,
        title: str,
        file_id: str,
        back_category: str,
        note: str,
        content: str,
        warning: str = "",
    ) -> web.Response:
        warning_html = (
            f"<p class=\"error\">{self._e(warning)}</p>"
            if warning
            else ""
        )
        return self._html_response(
            title,
            f"""
            <h1>{self._e(title)}</h1>
            <p><a href="{self._url(f'/editable?category={self._e(back_category)}')}">返回{self._e(self._editable_category_title(back_category))}</a></p>
            <p class="muted">{self._e(file_id)}</p>
            {warning_html}
            <form method="post" action="{self._url('/editable/save')}">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <label for="note">资源说明 / 注释</label>
              <textarea id="note" class="note-editor" name="note" spellcheck="false">{self._e(note)}</textarea>
              <label for="content">资源正文</label>
              <textarea id="content" class="content-editor" name="content" spellcheck="false">{self._e(content)}</textarea>
              <div class="actions">
                <button type="submit">保存</button>
              </div>
            </form>
            <form method="post" action="{self._url('/editable/reset')}" onsubmit="return confirm('确定恢复为当前代码内置默认内容？旧文件会先自动备份。');">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <button class="secondary" type="submit">恢复当前默认内容</button>
            </form>
            """,
        )

    def _world_book_file_response(
        self,
        title: str,
        file_id: str,
        back_category: str,
        note: str,
        content: str,
    ) -> web.Response:
        try:
            book = self._normalize_world_book(json.loads(content))
        except Exception as exc:
            return self._plain_editable_file_response(
                title,
                file_id,
                back_category,
                note,
                content,
                warning=f"世界书 JSON 解析失败，请先修复原始 JSON：{exc}",
            )

        book_json = self._json_script_data(book)
        is_change_book = file_id in {"skill_book/default.json", "fetish_book/default.json"}
        base_path_block = (
            f"""
              <div class="book-config-grid">
                <div>
                  <label for="book-display-name">展示名称</label>
                  <input id="book-display-name" type="text" value="{self._e(book.get('display_name') or self._default_book_display_name(file_id))}" spellcheck="false">
                </div>
                <div>
                  <label for="book-base-path">默认 change 基础路径</label>
                  <input id="book-base-path" type="text" value="{self._e(book.get('base_path') or '')}" spellcheck="false">
                </div>
              </div>
              <p class="muted">这个路径会发给 AI 作为 update.changes 的路径提示，不代表 JSON 文件实际存放路径。</p>
            """
            if is_change_book
            else ""
        )
        book_title = (
            "技能书条目"
            if file_id == "skill_book/default.json"
            else "性癖书条目"
            if file_id == "fetish_book/default.json"
            else "状态书条目"
            if file_id == "status_book/default.json"
            else "世界书条目"
        )
        book_hint = (
            "每个条目会在命中后作为技能说明注入 Prompt。可见等级用 F、E、D、C、B、A、S 多选控制，默认全部可见。"
            if file_id == "skill_book/default.json"
            else '条目标题代表可开发性癖；content 是简单介绍，Lv.1 到 Lv.Max 分别填写当前等级效果。已拥有性癖只注入简介和当前等级效果；"总是注入"的已拥有性癖每次都会注入，未拥有时会在待开发列表附带简单介绍。性癖最高 Lv.5；可见等级用 F、E、D、C、B、A、S 多选控制。'
            if file_id == "fetish_book/default.json"
            else "每个条目会在命中后作为状态补充设定注入 Prompt。可见等级用 F、E、D、C、B、A、S 多选控制，默认全部可见。"
            if file_id == "status_book/default.json"
            else "每个条目会在命中后作为世界背景补充注入 Prompt。可见等级用 F、E、D、C、B、A、S 多选控制，默认全部可见。"
        )
        storage_key = "qq_mahoushoujo:book:open_entries:" + file_id.replace("/", ":")
        source_url = self._url(
            f"/editable/source?id={quote(file_id, safe='')}&category={self._e(back_category)}"
        )
        export_url = self._url(f"/editable/export?id={quote(file_id, safe='')}")
        key_info_export_url = self._url(
            f"/editable/export/key-info?id={quote(file_id, safe='')}"
        )
        return self._html_response(
            title,
            f"""
            <h1>{self._e(title)}</h1>
            <p><a href="{self._url(f'/editable?category={self._e(back_category)}')}">返回{self._e(self._editable_category_title(back_category))}</a></p>
            <p class="muted">{self._e(file_id)}</p>
            <div class="source-actions">
              <a class="button-link secondary-link" href="{source_url}">编辑源码</a>
              <a class="button-link secondary-link" href="{export_url}">导出 JSON</a>
              <a class="button-link secondary-link" href="{key_info_export_url}">导出关键信息 TXT</a>
              <form class="inline-import-form" method="post" action="{self._url('/editable/import')}" enctype="multipart/form-data">
                <input type="hidden" name="id" value="{self._e(file_id)}">
                <input type="hidden" name="category" value="{self._e(back_category)}">
                <input name="import_file" type="file" accept=".json,application/json">
                <button class="secondary compact-button" type="submit">导入 JSON</button>
              </form>
            </div>
            <form id="world-book-form" method="post" action="{self._url('/editable/save')}">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <input id="world-book-content" type="hidden" name="content" value="">
              <label for="note">资源说明 / 注释</label>
              <textarea id="note" class="note-editor" name="note" spellcheck="false">{self._e(note)}</textarea>
              {base_path_block}

              <div class="world-book-toolbar">
                <div>
                  <h2>{self._e(book_title)}</h2>
                  <p class="muted">{self._e(book_hint)}</p>
                </div>
                <button id="add-entry" type="button">+ 添加条目</button>
              </div>
              <div id="world-book-entries"></div>
              <div class="actions">
                <button type="submit">保存</button>
              </div>
            </form>
            <form method="post" action="{self._url('/editable/reset')}" onsubmit="return confirm('确定恢复为当前代码内置默认内容？旧文件会先自动备份。');">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <button class="secondary" type="submit">恢复当前默认内容</button>
            </form>
            <script>
              const initialWorldBook = {book_json};
              const entriesEl = document.getElementById("world-book-entries");
              const addEntryButton = document.getElementById("add-entry");
              const form = document.getElementById("world-book-form");
              const contentInput = document.getElementById("world-book-content");
              const displayNameInput = document.getElementById("book-display-name");
              const basePathInput = document.getElementById("book-base-path");
              const isStatusBook = {str(file_id == "fetish_book/default.json").lower()};
              const levelOptions = [
                {{ value: 1, label: "F" }},
                {{ value: 2, label: "E" }},
                {{ value: 3, label: "D" }},
                {{ value: 4, label: "C" }},
                {{ value: 5, label: "B" }},
                {{ value: 6, label: "A" }},
                {{ value: 7, label: "S" }},
              ];
              const openStateStorageKey = "{self._e(storage_key)}";
              let draggingIndex = null;
              let openEntryKeys = new Set();
              let hasCapturedOpenState = false;

              const state = {{
                ...initialWorldBook,
                entries: Array.isArray(initialWorldBook.entries) ? initialWorldBook.entries : [],
              }};
              if (displayNameInput) {{
                state.display_name = String(initialWorldBook.display_name || displayNameInput.value || "");
              }}
              if (basePathInput) {{
                state.base_path = String(initialWorldBook.base_path || basePathInput.value || "");
              }}

              function entryDefaults(index) {{
                return {{
                  id: `entry_${{index + 1}}`,
                  title: "",
                  enabled: true,
                  recursive: true,
                  strategy: "keyword",
                  keys: [],
                  visible_levels: levelOptions.map((item) => item.value),
                  content: "",
                  level_descriptions: {{}},
                }};
              }}

              function normalizeEntry(entry, index) {{
                const keys = Array.isArray(entry.keys)
                  ? entry.keys
                  : (typeof entry.keys === "string" ? [entry.keys] : []);
                const rawLevelDescriptions = entry.level_descriptions && typeof entry.level_descriptions === "object"
                  ? entry.level_descriptions
                  : {{}};
                return {{
                  id: String(entry.id || `entry_${{index + 1}}`).trim(),
                  title: String(entry.title || ""),
                  enabled: entry.enabled !== false,
                  recursive: entry.recursive !== false,
                  strategy: entry.strategy === "always" ? "always" : "keyword",
                  keys: keys.map((key) => String(key).trim()).filter(Boolean),
                  visible_levels: normalizeVisibleLevels(entry.visible_levels, entry.min_level, entry.max_level),
                  content: String(entry.content || ""),
                  ...(isStatusBook ? {{
                    level_descriptions: Object.fromEntries(
                      [1, 2, 3, 4, 5].map((level) => [String(level), String(rawLevelDescriptions[String(level)] || "")])
                    ),
                  }} : {{}}),
                }};
              }}

              function splitKeys(value) {{
                return String(value || "")
                  .split(/[\\n,，]/)
                  .map((key) => key.trim())
                  .filter(Boolean);
              }}

              function normalizeVisibleLevels(raw, minLevel, maxLevel) {{
                let selected = [];
                if (Array.isArray(raw)) {{
                  selected = raw.map((level) => Number.parseInt(level, 10));
                }} else if (typeof raw === "string" && raw.trim()) {{
                  selected = raw.split(/[,，\\s]+/).map((level) => {{
                    const trimmed = String(level).trim().toUpperCase();
                    const byLabel = levelOptions.find((item) => item.label === trimmed);
                    return byLabel ? byLabel.value : Number.parseInt(trimmed, 10);
                  }});
                }} else {{
                  const minValue = Number.parseInt(minLevel, 10);
                  const maxValue = Number.parseInt(maxLevel, 10);
                  const low = Number.isFinite(minValue) ? Math.max(1, Math.min(7, minValue)) : 1;
                  const high = Number.isFinite(maxValue) ? Math.max(low, Math.min(7, maxValue)) : 7;
                  selected = levelOptions.map((item) => item.value).filter((level) => level >= low && level <= high);
                }}
                const clean = levelOptions
                  .map((item) => item.value)
                  .filter((level) => selected.includes(level));
                return clean.length ? clean : levelOptions.map((item) => item.value);
              }}

              function visibleLevelsLabel(levels) {{
                const normalized = normalizeVisibleLevels(levels);
                if (normalized.length === levelOptions.length) return "F-S";
                return normalized
                  .map((level) => levelOptions.find((item) => item.value === level)?.label || String(level))
                  .join("/");
              }}

              function visibleLevelInputs(levels) {{
                const selected = new Set(normalizeVisibleLevels(levels));
                return levelOptions.map((item) => `
                  <label class="summary-check level-choice">
                    <input data-field="visible_level" type="checkbox" value="${{item.value}}"${{selected.has(item.value) ? " checked" : ""}}> ${{item.label}}
                  </label>
                `).join("");
              }}

              function syncFromDom() {{
                if (displayNameInput) {{
                  state.display_name = displayNameInput.value;
                }}
                if (basePathInput) {{
                  state.base_path = basePathInput.value;
                }}
                state.entries = Array.from(entriesEl.querySelectorAll(".world-entry")).map((card, index) => normalizeEntry({{
                  id: card.querySelector("[data-field='id']").value,
                  title: card.querySelector("[data-field='title']").value,
                  enabled: card.querySelector("[data-field='enabled']").checked,
                  recursive: card.querySelector("[data-field='recursive']").checked,
                  strategy: card.querySelector("[data-field='strategy']").value,
                  keys: splitKeys(card.querySelector("[data-field='keys']").value),
                  visible_levels: Array.from(card.querySelectorAll("[data-field='visible_level']:checked")).map((input) => Number.parseInt(input.value, 10)),
                  content: card.querySelector("[data-field='content']").value,
                  level_descriptions: isStatusBook
                    ? Object.fromEntries(
                        [1, 2, 3, 4, 5].map((level) => [
                          String(level),
                          card.querySelector(`[data-field='level_description_${{level}}']`).value,
                        ])
                      )
                    : {{}},
                }}, index));
              }}

              function entryDomKey(entry, index) {{
                return String(entry.id || entry.title || `entry_${{index + 1}}`).trim();
              }}

              function captureOpenState() {{
                hasCapturedOpenState = true;
                openEntryKeys = new Set(
                  Array.from(entriesEl.querySelectorAll(".world-entry")).flatMap((card) => {{
                    const key = card.dataset.entryKey;
                    const details = card.querySelector("details");
                    return key && details && details.open ? [key] : [];
                  }})
                );
                persistOpenState();
              }}

              function loadOpenState() {{
                try {{
                  const raw = localStorage.getItem(openStateStorageKey);
                  if (!raw) {{
                    return;
                  }}
                  const data = JSON.parse(raw);
                  if (!data || !Array.isArray(data.openKeys)) {{
                    return;
                  }}
                  openEntryKeys = new Set(data.openKeys.map((key) => String(key)));
                  hasCapturedOpenState = true;
                }} catch (error) {{
                  console.warn("failed to load world book open state", error);
                }}
              }}

              function persistOpenState() {{
                try {{
                  localStorage.setItem(
                    openStateStorageKey,
                    JSON.stringify({{ openKeys: Array.from(openEntryKeys) }})
                  );
                }} catch (error) {{
                  console.warn("failed to save world book open state", error);
                }}
              }}

              function renderEntries() {{
                entriesEl.innerHTML = "";
                state.entries.forEach((entry, index) => {{
                  const normalized = normalizeEntry(entry, index);
                  const summaryTitle = normalized.title || normalized.id || `条目 ${{index + 1}}`;
                  const entryKey = entryDomKey(normalized, index);
                  const isOpen = hasCapturedOpenState && openEntryKeys.has(entryKey);
                  const card = document.createElement("section");
                  card.className = "world-entry";
                  card.dataset.entryKey = entryKey;
                  const statusLevelFields = isStatusBook
                    ? `
                        <div class="status-level-grid">
                          ${{[1, 2, 3, 4, 5].map((level) => `
                            <label class="block-field">${{level === 5 ? "Lv.Max（Lv.5）" : "Lv." + level}} 效果
                              <textarea data-field="level_description_${{level}}" class="entry-content-editor" spellcheck="false">${{escapeHtml(normalized.level_descriptions[String(level)] || "")}}</textarea>
                            </label>
                          `).join("")}}
                        </div>
                      `
                    : "";
                  card.innerHTML = `
                    <details${{isOpen ? " open" : ""}}>
                      <summary class="world-entry-head">
                        <button class="drag-handle" type="button" data-action="drag" draggable="true" title="拖动排序" aria-label="拖动排序">☰</button>
                        <span class="entry-title">${{escapeHtml(summaryTitle)}}</span>
                        <span class="muted" style="margin-left:4px">${{visibleLevelsLabel(normalized.visible_levels)}}</span>
                        <label class="summary-check"><input data-field="enabled" type="checkbox"${{normalized.enabled ? " checked" : ""}}> 启用</label>
                        <label class="summary-check"><input data-field="recursive" type="checkbox"${{normalized.recursive ? " checked" : ""}}> 允许递归</label>
                        <button class="danger" type="button" data-action="delete">删除</button>
                      </summary>
                      <div class="world-entry-body">
                        <div class="world-entry-grid">
                          <label class="compact-field"><span>ID</span><input data-field="id" type="text" value="${{escapeAttr(normalized.id)}}"></label>
                          <label class="compact-field title-field"><span>标题</span><input data-field="title" type="text" value="${{escapeAttr(normalized.title)}}"></label>
                          <div class="compact-field level-field"><span>可见等级</span><div class="level-choice-row">${{visibleLevelInputs(normalized.visible_levels)}}</div></div>
                          <label class="compact-field"><span>触发方式</span>
                            <select data-field="strategy">
                              <option value="keyword"${{normalized.strategy === "keyword" ? " selected" : ""}}>关键词命中</option>
                              <option value="always"${{normalized.strategy === "always" ? " selected" : ""}}>总是注入</option>
                            </select>
                          </label>
                        </div>
                        <label class="block-field">关键词（支持中文逗号、英文逗号或换行分隔；触发方式为"总是注入"时可留空）
                          <textarea data-field="keys" class="keys-editor" spellcheck="false">${{escapeHtml(normalized.keys.join("\\n"))}}</textarea>
                        </label>
                        <label class="block-field">${{isStatusBook ? "简单介绍" : "设定内容"}}
                          <textarea data-field="content" class="entry-content-editor" spellcheck="false">${{escapeHtml(normalized.content)}}</textarea>
                        </label>
                        ${{statusLevelFields}}
                      </div>
                    </details>
                  `;
                  const detailsEl = card.querySelector("details");
                  detailsEl.addEventListener("toggle", () => {{
                    if (detailsEl.open) {{
                      openEntryKeys.add(entryKey);
                    }} else {{
                      openEntryKeys.delete(entryKey);
                    }}
                    hasCapturedOpenState = true;
                    persistOpenState();
                  }});
                  card.querySelector(".summary-check").addEventListener("click", (event) => {{
                    event.stopPropagation();
                  }});
                  const dragHandle = card.querySelector("[data-action='drag']");
                  dragHandle.addEventListener("click", (event) => {{
                    event.preventDefault();
                    event.stopPropagation();
                  }});
                  dragHandle.addEventListener("dragstart", (event) => {{
                    syncFromDom();
                    draggingIndex = index;
                    card.classList.add("dragging");
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", String(index));
                  }});
                  dragHandle.addEventListener("dragend", () => {{
                    draggingIndex = null;
                    card.classList.remove("dragging");
                    entriesEl.querySelectorAll(".drag-over").forEach((item) => item.classList.remove("drag-over"));
                  }});
                  card.addEventListener("dragover", (event) => {{
                    if (draggingIndex === null || draggingIndex === index) {{
                      return;
                    }}
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                    card.classList.add("drag-over");
                  }});
                  card.addEventListener("dragleave", () => {{
                    card.classList.remove("drag-over");
                  }});
                  card.addEventListener("drop", (event) => {{
                    event.preventDefault();
                    card.classList.remove("drag-over");
                    if (draggingIndex === null || draggingIndex === index) {{
                      return;
                    }}
                    reorderEntries(draggingIndex, index);
                    draggingIndex = null;
                  }});
                  card.querySelector("[data-action='delete']").addEventListener("click", (event) => {{
                    event.preventDefault();
                    event.stopPropagation();
                    if (!confirm("确定删除这个世界书条目？")) {{
                      return;
                    }}
                    captureOpenState();
                    syncFromDom();
                    state.entries.splice(index, 1);
                    openEntryKeys.delete(entryKey);
                    persistOpenState();
                    renderEntries();
                  }});
                  const titleInput = card.querySelector("[data-field='title']");
                  const idInput = card.querySelector("[data-field='id']");
                  const titleEl = card.querySelector(".entry-title");
                  const refreshSummaryTitle = () => {{
                    titleEl.textContent = titleInput.value.trim() || idInput.value.trim() || `条目 ${{index + 1}}`;
                  }};
                  titleInput.addEventListener("input", refreshSummaryTitle);
                  idInput.addEventListener("input", refreshSummaryTitle);
                  entriesEl.appendChild(card);
                }});
              }}

              function reorderEntries(fromIndex, toIndex) {{
                captureOpenState();
                syncFromDom();
                const nextEntries = [...state.entries];
                const [moved] = nextEntries.splice(fromIndex, 1);
                nextEntries.splice(toIndex, 0, moved);
                state.entries = nextEntries;
                renderEntries();
              }}

              function escapeHtml(value) {{
                return String(value)
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
              }}

              function escapeAttr(value) {{
                return escapeHtml(value)
                  .replace(/"/g, "&quot;")
                  .replace(/'/g, "&#39;");
              }}

              addEntryButton.addEventListener("click", () => {{
                captureOpenState();
                syncFromDom();
                const newEntry = entryDefaults(state.entries.length);
                state.entries.push(newEntry);
                openEntryKeys.add(entryDomKey(newEntry, state.entries.length - 1));
                persistOpenState();
                renderEntries();
              }});

              form.addEventListener("submit", () => {{
                syncFromDom();
                contentInput.value = JSON.stringify(state, null, 2);
              }});

              state.entries = state.entries.map(normalizeEntry);
              loadOpenState();
              renderEntries();
            </script>
            """,
        )

    def _monster_book_file_response(
        self,
        title: str,
        file_id: str,
        back_category: str,
        note: str,
        content: str,
    ) -> web.Response:
        try:
            book = self._normalize_monster_book(json.loads(content))
        except Exception as exc:
            return self._plain_editable_file_response(
                title,
                file_id,
                back_category,
                note,
                content,
                warning=f"魔物书 JSON 解析失败，请先修复源码 JSON：{exc}",
            )

        book_json = self._json_script_data(book)
        storage_key = "qq_mahoushoujo:monster_book:open_entries"
        source_url = self._url(
            f"/editable/source?id={quote(file_id, safe='')}&category={self._e(back_category)}"
        )
        export_url = self._url(f"/editable/export?id={quote(file_id, safe='')}")
        return self._html_response(
            title,
            f"""
            <h1>{self._e(title)}</h1>
            <p><a href="{self._url(f'/editable?category={self._e(back_category)}')}">返回{self._e(self._editable_category_title(back_category))}</a></p>
            <p class="muted">{self._e(file_id)}</p>
            <p class="muted">魔物书只负责编辑和保存 JSON，不会自动参与当前项目的 Prompt 或战斗逻辑。等级覆盖设定留空时，读取方应使用通用设定。</p>
            <div class="source-actions">
              <a class="button-link secondary-link" href="{source_url}">编辑源码</a>
              <a class="button-link secondary-link" href="{export_url}">导出 JSON</a>
              <form class="inline-import-form" method="post" action="{self._url('/editable/import')}" enctype="multipart/form-data">
                <input type="hidden" name="id" value="{self._e(file_id)}">
                <input type="hidden" name="category" value="{self._e(back_category)}">
                <input name="import_file" type="file" accept=".json,application/json">
                <button class="secondary compact-button" type="submit">导入 JSON</button>
              </form>
            </div>
            <form id="monster-book-form" method="post" action="{self._url('/editable/save')}">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <input id="monster-book-content" type="hidden" name="content" value="">
              <label for="note">资源说明 / 注释</label>
              <textarea id="note" class="note-editor" name="note" spellcheck="false">{self._e(note)}</textarea>
              <div class="world-book-toolbar">
                <div>
                  <h2>魔物列表</h2>
                  <p class="muted">创建魔物，选择可见等级和魔物等级；每个魔物等级可填写单独的简单设定和详细设定。</p>
                </div>
                <button id="add-monster" type="button">+ 添加魔物</button>
              </div>
              <div id="monster-book-entries"></div>
              <div class="actions">
                <button type="submit">保存</button>
              </div>
            </form>
            <form method="post" action="{self._url('/editable/reset')}" onsubmit="return confirm('确定恢复为当前代码内置默认内容？旧文件会先自动备份。');">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <button class="secondary" type="submit">恢复当前默认内容</button>
            </form>
            <script>
              const initialMonsterBook = {book_json};
              const monsterEntriesEl = document.getElementById("monster-book-entries");
              const monsterForm = document.getElementById("monster-book-form");
              const monsterContentInput = document.getElementById("monster-book-content");
              const addMonsterButton = document.getElementById("add-monster");
              const monsterStorageKey = "{self._e(storage_key)}";
              const monsterLevelOptions = [
                {{ value: 1, label: "F" }},
                {{ value: 2, label: "E" }},
                {{ value: 3, label: "D" }},
                {{ value: 4, label: "C" }},
                {{ value: 5, label: "B" }},
                {{ value: 6, label: "A" }},
                {{ value: 7, label: "S" }},
              ];
              const monsterState = {{
                ...initialMonsterBook,
                entries: Array.isArray(initialMonsterBook.entries) ? initialMonsterBook.entries : [],
              }};
              let monsterOpenKeys = new Set();
              let monsterHasLoadedOpenState = false;

              function monsterDefaults(index) {{
                return {{
                  id: `monster_${{index + 1}}`,
                  name: "",
                  visible_levels: monsterLevelOptions.map((item) => item.value),
                  monster_levels: monsterLevelOptions.map((item) => item.value),
                  keys: [],
                  brief: "",
                  content: "",
                  level_settings: {{}},
                }};
              }}

              function monsterNormalizeEntry(entry, index) {{
                const keys = Array.isArray(entry.keys)
                  ? entry.keys
                  : (typeof entry.keys === "string" ? [entry.keys] : []);
                const rawSettings = entry.level_settings && typeof entry.level_settings === "object"
                  ? entry.level_settings
                  : {{}};
                const monsterLevels = monsterNormalizeLevels(entry.monster_levels, entry.min_monster_level, entry.max_monster_level);
                const levelSettings = {{}};
                monsterLevels.forEach((level) => {{
                  const raw = rawSettings[String(level)] && typeof rawSettings[String(level)] === "object"
                    ? rawSettings[String(level)]
                    : {{}};
                  levelSettings[String(level)] = {{
                    brief: String(raw.brief || ""),
                    content: String(raw.content || ""),
                  }};
                }});
                return {{
                  id: String(entry.id || `monster_${{index + 1}}`).trim(),
                  name: String(entry.name || entry.title || ""),
                  visible_levels: monsterNormalizeLevels(entry.visible_levels, entry.min_level, entry.max_level),
                  monster_levels: monsterLevels,
                  keys: keys.map((key) => String(key).trim()).filter(Boolean),
                  brief: String(entry.brief || entry.summary || ""),
                  content: String(entry.content || entry.detail || ""),
                  level_settings: levelSettings,
                }};
              }}

              function monsterNormalizeLevels(raw, minLevel, maxLevel) {{
                let selected = [];
                if (Array.isArray(raw)) {{
                  selected = raw.map((level) => Number.parseInt(level, 10));
                }} else if (typeof raw === "string" && raw.trim()) {{
                  selected = raw.split(/[,，、\\s]+/).map((level) => {{
                    const trimmed = String(level).trim().toUpperCase();
                    const byLabel = monsterLevelOptions.find((item) => item.label === trimmed);
                    return byLabel ? byLabel.value : Number.parseInt(trimmed, 10);
                  }});
                }} else {{
                  const minValue = Number.parseInt(minLevel, 10);
                  const maxValue = Number.parseInt(maxLevel, 10);
                  const low = Number.isFinite(minValue) ? Math.max(1, Math.min(7, minValue)) : 1;
                  const high = Number.isFinite(maxValue) ? Math.max(low, Math.min(7, maxValue)) : 7;
                  selected = monsterLevelOptions.map((item) => item.value).filter((level) => level >= low && level <= high);
                }}
                const clean = monsterLevelOptions.map((item) => item.value).filter((level) => selected.includes(level));
                return clean.length ? clean : monsterLevelOptions.map((item) => item.value);
              }}

              function monsterLevelsLabel(levels) {{
                const normalized = monsterNormalizeLevels(levels);
                if (normalized.length === monsterLevelOptions.length) return "F-S";
                return normalized.map((level) => monsterLevelOptions.find((item) => item.value === level)?.label || String(level)).join("/");
              }}

              function monsterLevelInputs(field, levels) {{
                const selected = new Set(monsterNormalizeLevels(levels));
                return monsterLevelOptions.map((item) => `
                  <label class="summary-check level-choice">
                    <input data-field="${{field}}" type="checkbox" value="${{item.value}}"${{selected.has(item.value) ? " checked" : ""}}> ${{item.label}}
                  </label>
                `).join("");
              }}

              function monsterSplitKeys(value) {{
                return String(value || "").split(/[\\n,，、]/).map((key) => key.trim()).filter(Boolean);
              }}

              function monsterSyncFromDom() {{
                monsterState.entries = Array.from(monsterEntriesEl.querySelectorAll(".monster-entry")).map((card, index) => {{
                  const monsterLevels = Array.from(card.querySelectorAll("[data-field='monster_level']:checked")).map((input) => Number.parseInt(input.value, 10));
                  const levelSettings = {{}};
                  monsterNormalizeLevels(monsterLevels).forEach((level) => {{
                    const briefInput = card.querySelector(`[data-field='level_brief_${{level}}']`);
                    const contentInput = card.querySelector(`[data-field='level_content_${{level}}']`);
                    levelSettings[String(level)] = {{
                      brief: briefInput ? briefInput.value : "",
                      content: contentInput ? contentInput.value : "",
                    }};
                  }});
                  return monsterNormalizeEntry({{
                    id: card.querySelector("[data-field='id']").value,
                    name: card.querySelector("[data-field='name']").value,
                    visible_levels: Array.from(card.querySelectorAll("[data-field='visible_level']:checked")).map((input) => Number.parseInt(input.value, 10)),
                    monster_levels: monsterLevels,
                    keys: monsterSplitKeys(card.querySelector("[data-field='keys']").value),
                    brief: card.querySelector("[data-field='brief']").value,
                    content: card.querySelector("[data-field='content']").value,
                    level_settings: levelSettings,
                  }}, index);
                }});
              }}

              function monsterEntryKey(entry, index) {{
                return String(entry.id || entry.name || `monster_${{index + 1}}`).trim();
              }}

              function monsterLoadOpenState() {{
                try {{
                  const raw = localStorage.getItem(monsterStorageKey);
                  if (!raw) return;
                  const data = JSON.parse(raw);
                  if (data && Array.isArray(data.openKeys)) {{
                    monsterOpenKeys = new Set(data.openKeys.map(String));
                    monsterHasLoadedOpenState = true;
                  }}
                }} catch (error) {{
                  console.warn("failed to load monster book open state", error);
                }}
              }}

              function monsterPersistOpenState() {{
                try {{
                  localStorage.setItem(monsterStorageKey, JSON.stringify({{ openKeys: Array.from(monsterOpenKeys) }}));
                }} catch (error) {{
                  console.warn("failed to save monster book open state", error);
                }}
              }}

              function monsterCaptureOpenState() {{
                monsterOpenKeys = new Set();
                monsterEntriesEl.querySelectorAll(".monster-entry").forEach((card) => {{
                  const details = card.querySelector("details");
                  if (card.dataset.entryKey && details && details.open) monsterOpenKeys.add(card.dataset.entryKey);
                }});
                monsterHasLoadedOpenState = true;
                monsterPersistOpenState();
              }}

              function monsterRenderEntries() {{
                monsterEntriesEl.innerHTML = "";
                monsterState.entries.forEach((entry, index) => {{
                  const normalized = monsterNormalizeEntry(entry, index);
                  const entryKey = monsterEntryKey(normalized, index);
                  const isOpen = monsterHasLoadedOpenState && monsterOpenKeys.has(entryKey);
                  const card = document.createElement("section");
                  card.className = "world-entry monster-entry";
                  card.dataset.entryKey = entryKey;
                  const levelFields = normalized.monster_levels.map((level) => {{
                    const label = monsterLevelOptions.find((item) => item.value === level)?.label || String(level);
                    const setting = normalized.level_settings[String(level)] || {{ brief: "", content: "" }};
                    return `
                      <div class="monster-level-block">
                        <h3>${{label}} 级魔物设定</h3>
                        <label class="block-field">简单设定（留空使用通用简单设定）
                          <textarea data-field="level_brief_${{level}}" class="entry-content-editor" spellcheck="false">${{monsterEscapeHtml(setting.brief || "")}}</textarea>
                        </label>
                        <label class="block-field">详细设定（留空使用通用详细设定）
                          <textarea data-field="level_content_${{level}}" class="entry-content-editor" spellcheck="false">${{monsterEscapeHtml(setting.content || "")}}</textarea>
                        </label>
                      </div>
                    `;
                  }}).join("");
                  const summaryTitle = normalized.name || normalized.id || `魔物 ${{index + 1}}`;
                  card.innerHTML = `
                    <details${{isOpen ? " open" : ""}}>
                      <summary class="world-entry-head">
                        <span class="entry-title">${{monsterEscapeHtml(summaryTitle)}}</span>
                        <span class="muted" style="margin-left:4px">可见 ${{monsterLevelsLabel(normalized.visible_levels)}} / 魔物 ${{monsterLevelsLabel(normalized.monster_levels)}}</span>
                        <button class="danger" type="button" data-action="delete">删除</button>
                      </summary>
                      <div class="world-entry-body">
                        <div class="world-entry-grid">
                          <label class="compact-field"><span>ID</span><input data-field="id" type="text" value="${{monsterEscapeAttr(normalized.id)}}"></label>
                          <label class="compact-field title-field"><span>魔物名</span><input data-field="name" type="text" value="${{monsterEscapeAttr(normalized.name)}}"></label>
                          <div class="compact-field level-field"><span>可见等级</span><div class="level-choice-row">${{monsterLevelInputs("visible_level", normalized.visible_levels)}}</div></div>
                          <div class="compact-field level-field"><span>魔物等级</span><div class="level-choice-row">${{monsterLevelInputs("monster_level", normalized.monster_levels)}}</div></div>
                        </div>
                        <label class="block-field">关键词（支持中文逗号、英文逗号、顿号或换行分隔）
                          <textarea data-field="keys" class="keys-editor" spellcheck="false">${{monsterEscapeHtml(normalized.keys.join("\\n"))}}</textarea>
                        </label>
                        <label class="block-field">通用简单设定
                          <textarea data-field="brief" class="entry-content-editor" spellcheck="false">${{monsterEscapeHtml(normalized.brief)}}</textarea>
                        </label>
                        <label class="block-field">通用详细设定
                          <textarea data-field="content" class="entry-content-editor" spellcheck="false">${{monsterEscapeHtml(normalized.content)}}</textarea>
                        </label>
                        <div class="monster-level-settings">${{levelFields}}</div>
                      </div>
                    </details>
                  `;
                  const detailsEl = card.querySelector("details");
                  detailsEl.addEventListener("toggle", () => {{
                    if (detailsEl.open) monsterOpenKeys.add(entryKey);
                    else monsterOpenKeys.delete(entryKey);
                    monsterHasLoadedOpenState = true;
                    monsterPersistOpenState();
                  }});
                  card.querySelector("[data-action='delete']").addEventListener("click", (event) => {{
                    event.preventDefault();
                    event.stopPropagation();
                    if (!confirm("确定删除这个魔物？")) return;
                    monsterCaptureOpenState();
                    monsterSyncFromDom();
                    monsterState.entries.splice(index, 1);
                    monsterOpenKeys.delete(entryKey);
                    monsterPersistOpenState();
                    monsterRenderEntries();
                  }});
                  const refreshTitle = () => {{
                    card.querySelector(".entry-title").textContent =
                      card.querySelector("[data-field='name']").value.trim()
                      || card.querySelector("[data-field='id']").value.trim()
                      || `魔物 ${{index + 1}}`;
                  }};
                  card.querySelector("[data-field='name']").addEventListener("input", refreshTitle);
                  card.querySelector("[data-field='id']").addEventListener("input", refreshTitle);
                  card.querySelectorAll("[data-field='monster_level']").forEach((input) => {{
                    input.addEventListener("change", () => {{
                      monsterCaptureOpenState();
                      monsterSyncFromDom();
                      monsterRenderEntries();
                    }});
                  }});
                  monsterEntriesEl.appendChild(card);
                }});
              }}

              function monsterEscapeHtml(value) {{
                return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
              }}

              function monsterEscapeAttr(value) {{
                return monsterEscapeHtml(value).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
              }}

              addMonsterButton.addEventListener("click", () => {{
                monsterCaptureOpenState();
                monsterSyncFromDom();
                const newEntry = monsterDefaults(monsterState.entries.length);
                monsterState.entries.push(newEntry);
                monsterOpenKeys.add(monsterEntryKey(newEntry, monsterState.entries.length - 1));
                monsterHasLoadedOpenState = true;
                monsterPersistOpenState();
                monsterRenderEntries();
              }});

              monsterForm.addEventListener("submit", () => {{
                monsterSyncFromDom();
                monsterContentInput.value = JSON.stringify(monsterState, null, 2);
              }});

              monsterState.entries = monsterState.entries.map(monsterNormalizeEntry);
              monsterLoadOpenState();
              monsterRenderEntries();
            </script>
            """,
        )

    def _event_book_file_response(
        self,
        title: str,
        file_id: str,
        back_category: str,
        note: str,
        content: str,
    ) -> web.Response:
        try:
            book = self._normalize_event_book(json.loads(content))
        except Exception as exc:
            return self._plain_editable_file_response(
                title,
                file_id,
                back_category,
                note,
                content,
                warning=f"事件书 JSON 解析失败，请先修复原始 JSON：{exc}",
            )

        book_json = self._json_script_data(book)
        storage_key = "qq_mahoushoujo:event_book:open_state"
        source_url = self._url(
            f"/editable/source?id={quote(file_id, safe='')}&category={self._e(back_category)}"
        )
        export_url = self._url(f"/editable/export?id={quote(file_id, safe='')}")
        key_info_export_url = self._url(
            f"/editable/export/key-info?id={quote(file_id, safe='')}"
        )
        return self._html_response(
            title,
            f"""
            <h1>{self._e(title)}</h1>
            <p><a href="{self._url(f'/editable?category={self._e(back_category)}')}">返回{self._e(self._editable_category_title(back_category))}</a></p>
            <p class="muted">{self._e(file_id)}</p>
            <p class="muted">事件书按当前命令分组。当前事件内关键词命中或 always 条目会注入详细介绍；其他事件只在关键词命中且简略介绍不为空时注入简略介绍。可见等级用 F、E、D、C、B、A、S 多选控制，默认全部可见。</p>
            <div class="source-actions">
              <a class="button-link secondary-link" href="{source_url}">编辑源码</a>
              <a class="button-link secondary-link" href="{export_url}">导出 JSON</a>
              <a class="button-link secondary-link" href="{key_info_export_url}">导出关键信息 TXT</a>
              <form class="inline-import-form" method="post" action="{self._url('/editable/import')}" enctype="multipart/form-data">
                <input type="hidden" name="id" value="{self._e(file_id)}">
                <input type="hidden" name="category" value="{self._e(back_category)}">
                <input name="import_file" type="file" accept=".json,application/json">
                <button class="secondary compact-button" type="submit">导入 JSON</button>
              </form>
            </div>
            <form id="event-book-form" method="post" action="{self._url('/editable/save')}">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <input id="event-book-content" type="hidden" name="content" value="">
              <label for="note">资源说明 / 注释</label>
              <textarea id="note" class="note-editor" name="note" spellcheck="false">{self._e(note)}</textarea>

              <div class="world-book-toolbar">
                <div>
                  <h2>事件列表</h2>
                </div>
              </div>
              <div id="event-book-events"></div>
              <div class="actions">
                <button type="submit">保存</button>
              </div>
            </form>
            <form method="post" action="{self._url('/editable/reset')}" onsubmit="return confirm('确定恢复为当前代码内置默认内容？旧文件会先自动备份。');">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <button class="secondary" type="submit">恢复当前默认内容</button>
            </form>
            <script>
              const initialEventBook = {book_json};
              const eventsEl = document.getElementById("event-book-events");
              const ebForm = document.getElementById("event-book-form");
              const ebContentInput = document.getElementById("event-book-content");
              const ebStorageKey = "{self._e(storage_key)}";
              const requiredEvents = [
                {{ id: "reincarnation", command: "/魔法少女转生", name: "魔法少女转生" }},
                {{ id: "battle", command: "/魔法少女战斗", name: "魔法少女战斗" }},
                {{ id: "daily", command: "/魔法少女日常", name: "魔法少女日常" }},
              ];
              const ebLevelOptions = [
                {{ value: 1, label: "F" }},
                {{ value: 2, label: "E" }},
                {{ value: 3, label: "D" }},
                {{ value: 4, label: "C" }},
                {{ value: 5, label: "B" }},
                {{ value: 6, label: "A" }},
                {{ value: 7, label: "S" }},
              ];

              const ebState = {{
                ...initialEventBook,
                events: ebNormalizeEvents(initialEventBook.events),
              }};

              let ebOpenState = {{ events: new Set(), entries: new Set() }};
              let ebHasCapturedOpenState = false;

              function ebEntryDefaults(index) {{
                return {{
                  id: `entry_${{index + 1}}`,
                  title: "",
                  enabled: true,
                  recursive: true,
                  strategy: "keyword",
                  keys: [],
                  visible_levels: ebLevelOptions.map((item) => item.value),
                  brief: "",
                  content: "",
                }};
              }}

              function ebNormalizeEvents(events) {{
                const source = Array.isArray(events) ? events : [];
                return requiredEvents.map((required) => {{
                  const found = source.find((event) => event && (event.id === required.id || event.command === required.command));
                  return ebNormalizeEvent({{ ...required, ...(found || {{}}), id: required.id, command: required.command, name: required.name }});
                }});
              }}

              function ebNormalizeEvent(event) {{
                return {{
                  id: String(event.id || "").trim(),
                  command: String(event.command || "").trim(),
                  name: String(event.name || ""),
                  entries: Array.isArray(event.entries) ? event.entries.map((e, ei) => ebNormalizeEntry(e, ei)) : [],
                }};
              }}

              function ebNormalizeEntry(entry, index) {{
                const keys = Array.isArray(entry.keys)
                  ? entry.keys
                  : (typeof entry.keys === "string" ? [entry.keys] : []);
                return {{
                  id: String(entry.id || `entry_${{index + 1}}`).trim(),
                  title: String(entry.title || ""),
                  enabled: entry.enabled !== false,
                  recursive: entry.recursive !== false,
                  strategy: entry.strategy === "always" ? "always" : "keyword",
                  keys: keys.map((key) => String(key).trim()).filter(Boolean),
                  visible_levels: ebNormalizeVisibleLevels(entry.visible_levels, entry.min_level, entry.max_level),
                  brief: String(entry.brief || ""),
                  content: String(entry.content || ""),
                }};
              }}

              function ebSplitKeys(value) {{
                return String(value || "")
                  .split(/[\\n,，]/)
                  .map((key) => key.trim())
                  .filter(Boolean);
              }}

              function ebNormalizeVisibleLevels(raw, minLevel, maxLevel) {{
                let selected = [];
                if (Array.isArray(raw)) {{
                  selected = raw.map((level) => Number.parseInt(level, 10));
                }} else if (typeof raw === "string" && raw.trim()) {{
                  selected = raw.split(/[,，\\s]+/).map((level) => {{
                    const trimmed = String(level).trim().toUpperCase();
                    const byLabel = ebLevelOptions.find((item) => item.label === trimmed);
                    return byLabel ? byLabel.value : Number.parseInt(trimmed, 10);
                  }});
                }} else {{
                  const minValue = Number.parseInt(minLevel, 10);
                  const maxValue = Number.parseInt(maxLevel, 10);
                  const low = Number.isFinite(minValue) ? Math.max(1, Math.min(7, minValue)) : 1;
                  const high = Number.isFinite(maxValue) ? Math.max(low, Math.min(7, maxValue)) : 7;
                  selected = ebLevelOptions.map((item) => item.value).filter((level) => level >= low && level <= high);
                }}
                const clean = ebLevelOptions
                  .map((item) => item.value)
                  .filter((level) => selected.includes(level));
                return clean.length ? clean : ebLevelOptions.map((item) => item.value);
              }}

              function ebVisibleLevelsLabel(levels) {{
                const normalized = ebNormalizeVisibleLevels(levels);
                if (normalized.length === ebLevelOptions.length) return "F-S";
                return normalized
                  .map((level) => ebLevelOptions.find((item) => item.value === level)?.label || String(level))
                  .join("/");
              }}

              function ebVisibleLevelInputs(levels) {{
                const selected = new Set(ebNormalizeVisibleLevels(levels));
                return ebLevelOptions.map((item) => `
                  <label class="summary-check level-choice">
                    <input data-field="visible_level" type="checkbox" value="${{item.value}}"${{selected.has(item.value) ? " checked" : ""}}> ${{item.label}}
                  </label>
                `).join("");
              }}

              function ebEscapeHtml(value) {{
                return String(value)
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
              }}

              function ebEscapeAttr(value) {{
                return ebEscapeHtml(value)
                  .replace(/"/g, "&quot;")
                  .replace(/'/g, "&#39;");
              }}

              function ebEntryKey(entry, eventIdx, entryIdx) {{
                return `${{eventIdx}}_${{String(entry.id || entry.title || `entry_${{entryIdx + 1}}`).trim()}}`;
              }}

              function ebCaptureOpenState() {{
                ebHasCapturedOpenState = true;
                ebOpenState.events = new Set();
                ebOpenState.entries = new Set();
                eventsEl.querySelectorAll(".event-block").forEach((block) => {{
                  const eventKey = block.dataset.eventKey;
                  const eventDetails = block.querySelector(":scope > details");
                  if (eventKey && eventDetails && eventDetails.open) ebOpenState.events.add(eventKey);
                  block.querySelectorAll(".eb-entry").forEach((card) => {{
                    const entryKey = card.dataset.entryKey;
                    const entryDetails = card.querySelector("details");
                    if (entryKey && entryDetails && entryDetails.open) ebOpenState.entries.add(entryKey);
                  }});
                }});
                ebPersistOpenState();
              }}

              function ebLoadOpenState() {{
                try {{
                  const raw = localStorage.getItem(ebStorageKey);
                  if (!raw) return;
                  const data = JSON.parse(raw);
                  if (!data) return;
                  if (Array.isArray(data.openEvents)) ebOpenState.events = new Set(data.openEvents.map(String));
                  if (Array.isArray(data.openEntries)) ebOpenState.entries = new Set(data.openEntries.map(String));
                  ebHasCapturedOpenState = true;
                }} catch (error) {{
                  console.warn("failed to load event book open state", error);
                }}
              }}

              function ebPersistOpenState() {{
                try {{
                  localStorage.setItem(ebStorageKey, JSON.stringify({{
                    openEvents: Array.from(ebOpenState.events),
                    openEntries: Array.from(ebOpenState.entries),
                  }}));
                }} catch (error) {{
                  console.warn("failed to save event book open state", error);
                }}
              }}

              function ebSyncFromDom() {{
                ebState.events = Array.from(eventsEl.querySelectorAll(".event-block")).map((block, eventIdx) => {{
                  const current = ebNormalizeEvent(ebState.events[eventIdx] || requiredEvents[eventIdx]);
                  current.entries = Array.from(block.querySelectorAll(".eb-entry")).map((card, entryIdx) => ebNormalizeEntry({{
                    id: card.querySelector("[data-field='id']").value,
                    title: card.querySelector("[data-field='title']").value,
                    enabled: card.querySelector("[data-field='enabled']").checked,
                    recursive: card.querySelector("[data-field='recursive']").checked,
                    strategy: card.querySelector("[data-field='strategy']").value,
                    keys: ebSplitKeys(card.querySelector("[data-field='keys']").value),
                    visible_levels: Array.from(card.querySelectorAll("[data-field='visible_level']:checked")).map((input) => Number.parseInt(input.value, 10)),
                    brief: card.querySelector("[data-field='brief']").value,
                    content: card.querySelector("[data-field='content']").value,
                  }}, entryIdx));
                  return current;
                }});
              }}

              function ebRenderEvents() {{
                eventsEl.innerHTML = "";
                ebState.events.forEach((eventItem, eventIdx) => {{
                  const norm = ebNormalizeEvent(eventItem);
                  const eventKey = norm.command || norm.id;
                  const eventIsOpen = ebHasCapturedOpenState && ebOpenState.events.has(eventKey);
                  const eventEl = document.createElement("section");
                  eventEl.className = "event-block";
                  eventEl.dataset.eventKey = eventKey;
                  const entryCount = norm.entries.length;

                  let entriesHtml = "";
                  norm.entries.forEach((entry, entryIdx) => {{
                    const eNorm = ebNormalizeEntry(entry, entryIdx);
                    const entryKey = ebEntryKey(eNorm, eventIdx, entryIdx);
                    const entryIsOpen = ebHasCapturedOpenState && ebOpenState.entries.has(entryKey);
                    const entrySummary = eNorm.title || eNorm.id || `条目 ${{entryIdx + 1}}`;
                    entriesHtml += `
                      <div class="eb-entry rb-entry" data-entry-key="${{ebEscapeAttr(entryKey)}}" data-entry-index="${{entryIdx}}">
                        <details${{entryIsOpen ? " open" : ""}}>
                          <summary class="world-entry-head">
                            <span class="entry-title">${{ebEscapeHtml(entrySummary)}}</span>
                            <label class="summary-check"><input data-field="enabled" type="checkbox"${{eNorm.enabled ? " checked" : ""}}> 启用</label>
                            <label class="summary-check"><input data-field="recursive" type="checkbox"${{eNorm.recursive ? " checked" : ""}}> 允许递归</label>
                            <span class="muted" style="margin-left:4px">${{ebVisibleLevelsLabel(eNorm.visible_levels)}}</span>
                            <button class="danger" type="button" data-action="delete-entry">删除</button>
                          </summary>
                          <div class="world-entry-body">
                            <div class="world-entry-grid">
                              <label class="compact-field"><span>ID</span><input data-field="id" type="text" value="${{ebEscapeAttr(eNorm.id)}}"></label>
                              <label class="compact-field title-field"><span>标题</span><input data-field="title" type="text" value="${{ebEscapeAttr(eNorm.title)}}"></label>
                              <div class="compact-field level-field"><span>可见等级</span><div class="level-choice-row">${{ebVisibleLevelInputs(eNorm.visible_levels)}}</div></div>
                              <label class="compact-field"><span>触发方式</span>
                                <select data-field="strategy">
                                  <option value="keyword"${{eNorm.strategy === "keyword" ? " selected" : ""}}>关键词命中</option>
                                  <option value="always"${{eNorm.strategy === "always" ? " selected" : ""}}>总是注入（仅当前事件）</option>
                                </select>
                              </label>
                            </div>
                            <label class="block-field">关键词（支持中文逗号、英文逗号或换行分隔；always 只在本事件生效，其他事件不会因 always 命中）
                              <textarea data-field="keys" class="keys-editor" spellcheck="false">${{ebEscapeHtml(eNorm.keys.join("\\n"))}}</textarea>
                            </label>
                            <label class="block-field">简略介绍（其他事件关键词命中时注入；为空则视为未命中）
                              <textarea data-field="brief" class="entry-content-editor" spellcheck="false" style="height:60px">${{ebEscapeHtml(eNorm.brief)}}</textarea>
                            </label>
                            <label class="block-field">详细介绍（当前事件关键词命中或当前事件 always 时注入）
                              <textarea data-field="content" class="entry-content-editor" spellcheck="false">${{ebEscapeHtml(eNorm.content)}}</textarea>
                            </label>
                          </div>
                        </details>
                      </div>
                    `;
                  }});

                  eventEl.innerHTML = `
                    <details${{eventIsOpen ? " open" : ""}}>
                      <summary class="world-entry-head region-head">
                        <span class="entry-title">${{ebEscapeHtml(norm.command)}} · ${{ebEscapeHtml(norm.name)}}</span>
                        <span class="muted" style="margin-left:4px">(${{entryCount}} 个条目)</span>
                      </summary>
                      <div class="region-body">
                        <div class="world-entry-grid">
                          <label class="compact-field"><span>事件 ID</span><input type="text" value="${{ebEscapeAttr(norm.id)}}" disabled></label>
                          <label class="compact-field"><span>命令</span><input type="text" value="${{ebEscapeAttr(norm.command)}}" disabled></label>
                          <label class="compact-field"><span>事件名称</span><input type="text" value="${{ebEscapeAttr(norm.name)}}" disabled></label>
                        </div>
                        <div class="world-book-toolbar" style="margin-top:8px">
                          <button class="secondary" type="button" data-action="add-entry">+ 添加条目</button>
                        </div>
                        <div class="region-entries">${{entriesHtml}}</div>
                      </div>
                    </details>
                  `;

                  const eventDetails = eventEl.querySelector(":scope > details");
                  eventDetails.addEventListener("toggle", () => {{
                    if (eventDetails.open) ebOpenState.events.add(eventKey);
                    else ebOpenState.events.delete(eventKey);
                    ebHasCapturedOpenState = true;
                    ebPersistOpenState();
                  }});

                  eventEl.querySelector("[data-action='add-entry']").addEventListener("click", (clickEvent) => {{
                    clickEvent.preventDefault();
                    clickEvent.stopPropagation();
                    ebCaptureOpenState();
                    ebSyncFromDom();
                    const newEntry = ebEntryDefaults(ebState.events[eventIdx].entries.length);
                    ebState.events[eventIdx].entries.push(newEntry);
                    const entryKey = ebEntryKey(newEntry, eventIdx, ebState.events[eventIdx].entries.length - 1);
                    ebOpenState.entries.add(entryKey);
                    ebOpenState.events.add(eventKey);
                    ebPersistOpenState();
                    ebRenderEvents();
                  }});

                  eventEl.querySelectorAll(".eb-entry").forEach((entryCard, entryIdx) => {{
                    const entryDetails = entryCard.querySelector("details");
                    const entryKey = entryCard.dataset.entryKey;
                    entryDetails.addEventListener("toggle", () => {{
                      if (entryDetails.open) ebOpenState.entries.add(entryKey);
                      else ebOpenState.entries.delete(entryKey);
                      ebHasCapturedOpenState = true;
                      ebPersistOpenState();
                    }});

                    entryCard.querySelector(".summary-check").addEventListener("click", (clickEvent) => {{
                      clickEvent.stopPropagation();
                    }});

                    entryCard.querySelector("[data-action='delete-entry']").addEventListener("click", (clickEvent) => {{
                      clickEvent.preventDefault();
                      clickEvent.stopPropagation();
                      if (!confirm("确定删除这个条目？")) return;
                      ebCaptureOpenState();
                      ebSyncFromDom();
                      ebState.events[eventIdx].entries.splice(entryIdx, 1);
                      ebOpenState.entries.delete(entryKey);
                      ebPersistOpenState();
                      ebRenderEvents();
                    }});

                    const titleInput = entryCard.querySelector("[data-field='title']");
                    const idInput = entryCard.querySelector("[data-field='id']");
                    const entryTitleEl = entryCard.querySelector(".entry-title");
                    const refreshEntryTitle = () => {{
                      entryTitleEl.textContent = titleInput.value.trim() || idInput.value.trim() || `条目 ${{entryIdx + 1}}`;
                    }};
                    titleInput.addEventListener("input", refreshEntryTitle);
                    idInput.addEventListener("input", refreshEntryTitle);
                  }});

                  eventsEl.appendChild(eventEl);
                }});
              }}

              ebForm.addEventListener("submit", () => {{
                ebSyncFromDom();
                ebState.events = ebNormalizeEvents(ebState.events);
                ebContentInput.value = JSON.stringify(ebState, null, 2);
              }});

              ebLoadOpenState();
              ebRenderEvents();
            </script>
            """,
        )

    @classmethod
    def _normalize_event_book(cls, raw: object) -> dict:
        book = dict(raw) if isinstance(raw, dict) else {}
        required_events = [
            {
                "id": "reincarnation",
                "command": "/魔法少女转生",
                "name": "魔法少女转生",
            },
            {
                "id": "battle",
                "command": "/魔法少女战斗",
                "name": "魔法少女战斗",
            },
            {
                "id": "daily",
                "command": "/魔法少女日常",
                "name": "魔法少女日常",
            },
        ]
        raw_events = book.get("events", [])
        if not isinstance(raw_events, list):
            raw_events = []

        normalized_events = []
        for required in required_events:
            raw_event = next(
                (
                    event
                    for event in raw_events
                    if isinstance(event, dict)
                    and (
                        str(event.get("id") or "").strip() == required["id"]
                        or str(event.get("command") or "").strip()
                        == required["command"]
                    )
                ),
                {},
            )

            raw_entries = raw_event.get("entries", []) if isinstance(raw_event, dict) else []
            if not isinstance(raw_entries, list):
                raw_entries = []

            normalized_entries = []
            for e_idx, raw_entry in enumerate(raw_entries):
                if not isinstance(raw_entry, dict):
                    continue
                keys = raw_entry.get("keys", [])
                if isinstance(keys, str):
                    keys = [keys]
                if not isinstance(keys, list):
                    keys = []
                normalized_entries.append({
                    "id": str(raw_entry.get("id") or f"entry_{e_idx + 1}").strip(),
                    "title": str(raw_entry.get("title") or ""),
                    "enabled": raw_entry.get("enabled", True) is not False,
                    "recursive": raw_entry.get("recursive", True) is not False,
                    "strategy": (
                        "always"
                        if str(raw_entry.get("strategy") or "keyword").strip().lower()
                        == "always"
                        else "keyword"
                    ),
                    "keys": [str(key).strip() for key in keys if str(key).strip()],
                    "visible_levels": list(cls._normalize_visible_levels(raw_entry)),
                    "brief": str(raw_entry.get("brief") or ""),
                    "content": str(raw_entry.get("content") or ""),
                })

            normalized_events.append({
                **required,
                "entries": normalized_entries,
            })

        book["version"] = book.get("version", 1)
        book["events"] = normalized_events
        return book

    @classmethod
    def _normalize_monster_book(cls, raw: object) -> dict:
        book = dict(raw) if isinstance(raw, dict) else {}
        entries = book.get("entries", [])
        if isinstance(entries, dict):
            iterable = entries.items()
        elif isinstance(entries, list):
            iterable = enumerate(entries)
        else:
            iterable = []

        normalized_entries = []
        for fallback_id, entry in iterable:
            if not isinstance(entry, dict):
                continue
            keys = entry.get("keys", [])
            if isinstance(keys, str):
                keys = [keys]
            if not isinstance(keys, list):
                keys = []

            monster_levels = normalize_visible_levels(
                entry.get("monster_levels"),
                min_level=entry.get("min_monster_level", 1),
                max_level=entry.get("max_monster_level", 7),
            )
            raw_level_settings = (
                entry.get("level_settings")
                if isinstance(entry.get("level_settings"), dict)
                else {}
            )
            level_settings: dict[str, dict[str, str]] = {}
            for level in monster_levels:
                raw_setting = raw_level_settings.get(str(level), {})
                if not isinstance(raw_setting, dict):
                    raw_setting = {}
                level_settings[str(level)] = {
                    "brief": str(raw_setting.get("brief") or ""),
                    "content": str(raw_setting.get("content") or ""),
                }

            normalized_entries.append(
                {
                    "id": str(entry.get("id") or fallback_id).strip(),
                    "name": str(entry.get("name") or entry.get("title") or ""),
                    "visible_levels": list(cls._normalize_visible_levels(entry)),
                    "monster_levels": list(monster_levels),
                    "keys": [str(key).strip() for key in keys if str(key).strip()],
                    "brief": str(entry.get("brief") or entry.get("summary") or ""),
                    "content": str(entry.get("content") or entry.get("detail") or ""),
                    "level_settings": level_settings,
                }
            )

        book["version"] = book.get("version", 1)
        book["entries"] = normalized_entries
        return book

    @classmethod
    def _normalize_world_book(cls, raw: object) -> dict:
        book = dict(raw) if isinstance(raw, dict) else {}
        entries = book.get("entries", [])
        if isinstance(entries, dict):
            iterable = entries.items()
        elif isinstance(entries, list):
            iterable = enumerate(entries)
        else:
            iterable = []

        normalized_entries = []
        for fallback_id, entry in iterable:
            if not isinstance(entry, dict):
                continue
            keys = entry.get("keys", [])
            if isinstance(keys, str):
                keys = [keys]
            if not isinstance(keys, list):
                keys = []
            normalized_entry = {
                    "id": str(entry.get("id") or fallback_id).strip(),
                    "title": str(entry.get("title") or ""),
                    "enabled": entry.get("enabled", True) is not False,
                    "recursive": entry.get("recursive", True) is not False,
                    "strategy": (
                        "always"
                        if str(entry.get("strategy") or "keyword").strip().lower()
                        == "always"
                        else "keyword"
                    ),
                    "keys": [str(key).strip() for key in keys if str(key).strip()],
                    "visible_levels": list(cls._normalize_visible_levels(entry)),
                    "content": str(entry.get("content") or ""),
                }
            if isinstance(entry.get("level_descriptions"), dict):
                normalized_entry["level_descriptions"] = {
                        str(level): str(
                            (entry.get("level_descriptions") or {}).get(str(level)) or ""
                        )
                        for level in range(1, 6)
                }
            normalized_entries.append(normalized_entry)

        book["version"] = book.get("version", 1)
        if "display_name" in book:
            book["display_name"] = str(book.get("display_name") or "")
        if "base_path" in book:
            book["base_path"] = str(book.get("base_path") or "")
        book["entries"] = normalized_entries
        return book

    @classmethod
    def _format_book_key_info(cls, raw: object, *, file_id: str = "") -> str:
        if file_id == "event_book/default.json" or (
            isinstance(raw, dict) and "events" in raw
        ):
            return cls._format_event_book_key_info(raw)
        if file_id == "monster_book/default.json":
            return cls._format_monster_book_key_info(raw)

        book = cls._normalize_world_book(raw)
        entries = book.get("entries", [])
        if not isinstance(entries, list):
            return ""

        blocks: list[str] = []
        indexed_entries = [
            (index, entry)
            for index, entry in enumerate(entries)
            if isinstance(entry, dict)
        ]
        for index, entry in sorted(
            indexed_entries,
            key=lambda item: (min(cls._normalize_visible_levels(item[1])), item[0]),
        ):
            title = cls._single_line_text(
                entry.get("title") or entry.get("id") or f"条目{index + 1}"
            )
            # 保留内容中的换行，只去除首尾空白
            content = str(entry.get("content") or "").strip()
            level_descriptions = (
                entry.get("level_descriptions")
                if isinstance(entry.get("level_descriptions"), dict)
                else {}
            )
            if not content and not level_descriptions:
                continue
            if file_id == "fetish_book/default.json":
                lines = [title]
                if content:
                    lines.append(f"简介：{content}")
                for level in range(1, 6):
                    description = str(level_descriptions.get(str(level)) or "").strip()
                    if description:
                        label = "Lv.Max（Lv.5）" if level == 5 else f"Lv.{level}"
                        lines.append(f"{label}：{description}")
                blocks.append("\n".join(lines))
            elif content:
                # 标题一行，内容从下一行开始，内容中的回车保留为换行
                blocks.append(f"{title}\n{content}")
        # 每个条目之间用分隔线隔开
        return "\n\n-----------------------------------------------\n\n".join(blocks) + ("\n" if blocks else "")

    @classmethod
    def _format_event_book_key_info(cls, raw: object) -> str:
        book = cls._normalize_event_book(raw)
        events = book.get("events", [])
        if not isinstance(events, list):
            return ""

        event_blocks: list[str] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_name = str(
                event.get("command") or event.get("name") or event.get("id") or "未知事件"
            )
            entries = event.get("entries", [])
            if not isinstance(entries, list):
                continue

            entry_lines: list[str] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                title = cls._single_line_text(
                    entry.get("title") or entry.get("id") or "条目"
                )
                strategy = str(entry.get("strategy") or "keyword")
                brief = str(entry.get("brief") or "").strip()
                content = str(entry.get("content") or "").strip()
                level_text = visible_levels_label(entry.get("visible_levels"))
                entry_lines.append(f"  [{title}] ({level_text}, {strategy})")
                if brief:
                    entry_lines.append(f"    简略: {brief}")
                if content:
                    entry_lines.append(f"    详细: {content}")

            if entry_lines:
                event_blocks.append(f"【{event_name}】\n" + "\n".join(entry_lines))

        return "\n\n-----------------------------------------------\n\n".join(event_blocks) + ("\n" if event_blocks else "")

    @classmethod
    def _format_monster_book_key_info(cls, raw: object) -> str:
        book = cls._normalize_monster_book(raw)
        entries = book.get("entries", [])
        if not isinstance(entries, list):
            return ""

        blocks: list[str] = []
        indexed_entries = [
            (index, entry)
            for index, entry in enumerate(entries)
            if isinstance(entry, dict)
        ]
        for index, entry in sorted(
            indexed_entries,
            key=lambda item: (min(cls._normalize_visible_levels(item[1])), item[0]),
        ):
            name = cls._single_line_text(
                entry.get("name") or entry.get("id") or f"魔物{index + 1}"
            )
            lines = [
                name,
                f"ID: {cls._single_line_text(entry.get('id'))}",
                f"可见等级: {visible_levels_label(entry.get('visible_levels'))}",
                f"魔物等级: {visible_levels_label(entry.get('monster_levels'))}",
            ]
            keys = entry.get("keys") if isinstance(entry.get("keys"), list) else []
            if keys:
                lines.append("关键词: " + "、".join(str(key) for key in keys))

            brief = str(entry.get("brief") or "").strip()
            content = str(entry.get("content") or "").strip()
            if brief:
                lines.append(f"通用简单设定: {brief}")
            if content:
                lines.append(f"通用详细设定: {content}")

            level_settings = (
                entry.get("level_settings")
                if isinstance(entry.get("level_settings"), dict)
                else {}
            )
            for level in normalize_visible_levels(entry.get("monster_levels")):
                setting = level_settings.get(str(level), {})
                if not isinstance(setting, dict):
                    continue
                level_brief = str(setting.get("brief") or "").strip()
                level_content = str(setting.get("content") or "").strip()
                if not level_brief and not level_content:
                    continue
                lines.append(f"{level_label(level)} 级覆盖:")
                if level_brief:
                    lines.append(f"  简单设定: {level_brief}")
                if level_content:
                    lines.append(f"  详细设定: {level_content}")

            blocks.append("\n".join(lines))

        return "\n\n-----------------------------------------------\n\n".join(blocks) + ("\n" if blocks else "")

    @staticmethod
    def _single_line_text(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _normalize_visible_levels(entry: dict) -> tuple[int, ...]:
        if not isinstance(entry, dict):
            return ALL_VISIBLE_LEVELS
        return normalize_visible_levels(
            entry.get("visible_levels"),
            min_level=entry.get("min_level", 1),
            max_level=entry.get("max_level", 7),
        )

    @staticmethod
    def _default_book_display_name(file_id: str) -> str:
        if file_id == "skill_book/default.json":
            return "技能&熟练度"
        if file_id == "fetish_book/default.json":
            return "性癖开发"
        return ""

    @staticmethod
    def _json_script_data(value: object) -> str:
        return (
            json.dumps(value, ensure_ascii=False)
            .replace("</", "<\\/")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )

    async def _editable_source(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        file_id = request.query.get("id", "")
        if not self._is_structured_book_file(file_id):
            raise web.HTTPBadRequest(text="invalid editable source file")
        category = self._editable_back_category(request.query.get("category"), file_id)
        content = self.editable_manager.read_text(file_id)
        return self._source_editor_response(
            title=f"编辑源码 - {file_id}",
            back_url=self._url(
                f"/editable/file?id={quote(file_id, safe='')}&category={self._e(category)}"
            ),
            save_url=self._url("/editable/source/save"),
            hidden_fields={
                "id": file_id,
                "category": category,
            },
            content=content,
            file_name=file_id,
            import_url=self._url("/editable/import"),
            export_url=self._url(f"/editable/export?id={quote(file_id, safe='')}"),
            accept=".json,application/json",
        )

    async def _editable_source_save(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        data = await request.post()
        file_id = str(data.get("id", ""))
        category = self._editable_back_category(str(data.get("category", "")), file_id)
        content = str(data.get("content", ""))
        if not self._is_structured_book_file(file_id):
            raise web.HTTPBadRequest(text="invalid editable source file")
        try:
            self.editable_manager.write_json_book(file_id, content)
        except Exception as exc:
            return self._source_editor_response(
                title=f"编辑源码 - {file_id}",
                back_url=self._url(
                    f"/editable/file?id={quote(file_id, safe='')}&category={self._e(category)}"
                ),
                save_url=self._url("/editable/source/save"),
                hidden_fields={"id": file_id, "category": category},
                content=content,
                file_name=file_id,
                import_url=self._url("/editable/import"),
                export_url=self._url(f"/editable/export?id={quote(file_id, safe='')}"),
                accept=".json,application/json",
                warning=str(exc),
            )
        raise web.HTTPFound(
            self._url(f"/editable/file?id={quote(file_id, safe='')}&category={self._e(category)}")
        )

    async def _editable_export(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        file_id = request.query.get("id", "")
        if not self._is_structured_book_file(file_id):
            raise web.HTTPBadRequest(text="invalid editable export file")
        content = self.editable_manager.read_text(file_id)
        return self._download_response(file_id.replace("/", "_"), content, "application/json")

    async def _editable_export_key_info(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        file_id = request.query.get("id", "")
        if not self._is_structured_book_file(file_id):
            raise web.HTTPBadRequest(text="invalid editable key info export file")
        content = self.editable_manager.read_text(file_id)
        try:
            key_info = self._format_book_key_info(json.loads(content), file_id=file_id)
        except Exception as exc:
            raise web.HTTPBadRequest(text=f"invalid editable book JSON: {exc}") from exc

        filename = file_id.replace("/", "_").replace(".json", "_key_info.txt")
        return self._download_response(filename, key_info, "text/plain")

    async def _editable_import(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        data = await request.post()
        file_id = str(data.get("id", ""))
        category = self._editable_back_category(str(data.get("category", "")), file_id)
        if not self._is_structured_book_file(file_id):
            raise web.HTTPBadRequest(text="invalid editable import file")
        content = await self._form_text_content(data)
        try:
            self.editable_manager.write_json_book(file_id, content)
        except Exception as exc:
            return self._source_editor_response(
                title=f"导入失败 - {file_id}",
                back_url=self._url(
                    f"/editable/file?id={quote(file_id, safe='')}&category={self._e(category)}"
                ),
                save_url=self._url("/editable/source/save"),
                hidden_fields={"id": file_id, "category": category},
                content=content,
                file_name=file_id,
                import_url=self._url("/editable/import"),
                export_url=self._url(f"/editable/export?id={quote(file_id, safe='')}"),
                accept=".json,application/json",
                warning=str(exc),
            )
        raise web.HTTPFound(
            self._url(f"/editable/file?id={quote(file_id, safe='')}&category={self._e(category)}")
        )

    async def _editable_save(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        data = await request.post()
        file_id = str(data.get("id", ""))
        category = self._editable_back_category(str(data.get("category", "")), file_id)
        note = str(data.get("note", ""))
        content = str(data.get("content", ""))
        if not self._is_editable_file(file_id):
            raise web.HTTPBadRequest(text="invalid editable file")

        try:
            if file_id == "world_book/default.json":
                json.loads(content)
                self.editable_manager.write_world_book(content)
            elif file_id in {
                "status_book/default.json",
                "skill_book/default.json",
                "fetish_book/default.json",
                "event_book/default.json",
                "monster_book/default.json",
            }:
                self.editable_manager.write_json_book(file_id, content)
            else:
                self.editable_manager.write_text(file_id, content)
            self.editable_manager.write_note(file_id, note)
        except Exception as exc:
            return self._html_response(
                "保存失败",
                f"""
                <h1>保存失败</h1>
                <p class="error">{self._e(exc)}</p>
                <p><a href="{self._url(f'/editable/file?id={quote(file_id, safe="")}&category={self._e(category)}')}">返回编辑</a></p>
                """,
                status=400,
            )

        raise web.HTTPFound(
            self._url(f"/editable/file?id={quote(file_id, safe='')}&category={self._e(category)}")
        )

    async def _editable_reset(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        data = await request.post()
        file_id = str(data.get("id", ""))
        category = self._editable_back_category(str(data.get("category", "")), file_id)
        if not self._is_editable_file(file_id):
            raise web.HTTPBadRequest(text="invalid editable file")

        try:
            self.editable_manager.reset_to_default(file_id)
            self.editable_manager.reset_note_to_default(file_id)
        except Exception as exc:
            return self._html_response(
                "恢复默认失败",
                f"""
                <h1>恢复默认失败</h1>
                <p class="error">{self._e(exc)}</p>
                <p><a href="{self._url(f'/editable/file?id={quote(file_id, safe="")}&category={self._e(category)}')}">返回编辑</a></p>
                """,
                status=400,
            )

        raise web.HTTPFound(
            self._url(f"/editable/file?id={quote(file_id, safe='')}&category={self._e(category)}")
        )

    async def _player_file_source(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        group_id = request.query.get("group_id", "")
        user_id = request.query.get("user_id", "")
        file_name = request.query.get("file", "")
        content = self.repository.read_player_source_file(group_id, user_id, file_name)
        back_url = self._url(
            f"/player?group_id={self._e(group_id)}&user_id={self._e(user_id)}"
        )
        query = (
            f"group_id={quote(group_id, safe='')}&user_id={quote(user_id, safe='')}"
            f"&file={quote(file_name, safe='')}"
        )
        return self._source_editor_response(
            title=f"编辑存档源码 - {file_name}",
            back_url=back_url,
            save_url=self._url("/player/file/save"),
            hidden_fields={
                "group_id": group_id,
                "user_id": user_id,
                "file": file_name,
            },
            content=content,
            file_name=file_name,
            import_url=self._url("/player/file/import"),
            export_url=self._url(f"/player/file/export?{query}"),
            accept=".jsonl" if file_name.endswith(".jsonl") else ".json,application/json",
        )

    async def _player_file_save(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        data = await request.post()
        group_id = str(data.get("group_id", ""))
        user_id = str(data.get("user_id", ""))
        file_name = str(data.get("file", ""))
        content = str(data.get("content", ""))
        back_url = self._url(
            f"/player?group_id={self._e(group_id)}&user_id={self._e(user_id)}"
        )
        try:
            self.repository.write_player_source_file(group_id, user_id, file_name, content)
        except Exception as exc:
            return self._source_editor_response(
                title=f"编辑存档源码 - {file_name}",
                back_url=back_url,
                save_url=self._url("/player/file/save"),
                hidden_fields={"group_id": group_id, "user_id": user_id, "file": file_name},
                content=content,
                file_name=file_name,
                import_url=self._url("/player/file/import"),
                export_url=self._url(
                    f"/player/file/export?group_id={quote(group_id, safe='')}&user_id={quote(user_id, safe='')}&file={quote(file_name, safe='')}"
                ),
                accept=".jsonl" if file_name.endswith(".jsonl") else ".json,application/json",
                warning=str(exc),
            )
        raise web.HTTPFound(back_url)

    async def _player_file_export(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        group_id = request.query.get("group_id", "")
        user_id = request.query.get("user_id", "")
        file_name = request.query.get("file", "")
        content = self.repository.read_player_source_file(group_id, user_id, file_name)
        return self._download_response(file_name, content, "application/x-ndjson" if file_name.endswith(".jsonl") else "application/json")

    async def _player_file_import(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        data = await request.post()
        group_id = str(data.get("group_id", ""))
        user_id = str(data.get("user_id", ""))
        file_name = str(data.get("file", ""))
        content = await self._form_text_content(data)
        back_url = self._url(
            f"/player?group_id={self._e(group_id)}&user_id={self._e(user_id)}"
        )
        try:
            self.repository.write_player_source_file(group_id, user_id, file_name, content)
        except Exception as exc:
            return self._source_editor_response(
                title=f"导入存档源码失败 - {file_name}",
                back_url=back_url,
                save_url=self._url("/player/file/save"),
                hidden_fields={"group_id": group_id, "user_id": user_id, "file": file_name},
                content=content,
                file_name=file_name,
                import_url=self._url("/player/file/import"),
                export_url=self._url(
                    f"/player/file/export?group_id={quote(group_id, safe='')}&user_id={quote(user_id, safe='')}&file={quote(file_name, safe='')}"
                ),
                accept=".jsonl" if file_name.endswith(".jsonl") else ".json,application/json",
                warning=str(exc),
            )
        raise web.HTTPFound(back_url)

    async def _player_detail(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if not session:
            return self._forbidden()

        group_id = request.query.get("group_id", "")
        user_id = request.query.get("user_id", "")
        is_admin = session["role"] == SESSION_ADMIN_ROLE
        if not self._can_access_player(session, user_id):
            return self._forbidden()
        detail = self.repository.read_save_detail(group_id, user_id)
        if detail is None:
            raise web.HTTPNotFound(text="save not found")

        player_data = detail.get("player_data", {})
        logs = detail.get("logs", [])
        cameo_memories = detail.get("cameo_memories", [])
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        title_name = self._get_nested(protagonist, ["个人信息", "姓名"], player_data.get("nickname", "")) or user_id
        if not is_admin:
            return self._player_site_detail_response(
                group_id,
                user_id,
                player_data,
                logs,
                cameo_memories,
                title_name,
            )

        # 读取初始状态用于对比展示
        user_dir = self.repository.get_user_dir(group_id, user_id)
        player_data_base = self.repository._read_json(user_dir / "player_data.json")
        summary = self._player_summary_html(
            group_id,
            user_id,
            player_data,
            can_edit=True,
        )
        log_cards = self._player_log_cards(group_id, user_id, logs, allow_delete=is_admin)
        cameo_cards = self._player_cameo_memory_cards(
            group_id, user_id, cameo_memories, allow_delete=is_admin
        )
        source_file_panel = (
            self._player_source_file_panel(group_id, user_id)
            if is_admin
            else ""
        )
        progress_overview = self._progress_overview_html(player_data)
        log_note = (
            "删除单条记录只会移除 daily_memory.jsonl 中对应一行，不会回滚当前状态。"
            if is_admin
            else "这里展示该存档最近的战斗记录。"
        )
        cameo_note = (
            "删除单条记录只会移除 cameo_memory.jsonl 中对应一行。"
            if is_admin
            else "这里展示其他玩家日记里明确提到该角色的遭遇和结算。"
        )
        log_clear_button = (
            self._player_clear_form(
                group_id,
                user_id,
                "/player/log/clear",
                "删除全部战斗记录",
                "确定删除该玩家的全部战斗记录？当前状态不会自动回滚。",
            )
            if is_admin
            else ""
        )
        cameo_clear_button = (
            self._player_clear_form(
                group_id,
                user_id,
                "/player/cameo/clear",
                "删除全部交互",
                "确定删除该玩家的全部交互记录？",
            )
            if is_admin
            else ""
        )
        state_reset_button = (
            self._player_clear_form(
                group_id,
                user_id,
                "/player/state/reset",
                "刷新状态",
                "确定刷新状态？player_data_update.json 会恢复为 player_data.json 的初始状态。",
            )
            if is_admin
            else ""
        )
        danger_zone = (
            f"""
            <form class="danger-zone" method="post" action="{self._url('/player/delete')}" onsubmit="return confirm('确定删除这个玩家存档？此操作不可恢复。');">
              <input type="hidden" name="group_id" value="{self._e(group_id)}">
              <input type="hidden" name="user_id" value="{self._e(user_id)}">
              <strong>危险操作</strong>
              <span>删除该玩家的 player_data 和 daily_memory。</span>
              <button class="danger" type="submit">删除玩家存档</button>
            </form>
            """
            if is_admin
            else ""
        )

        return self._html_response(
            f"玩家存档 - {title_name}",
            f"""
            <h1>{self._e(title_name)}</h1>
            <p class="nav-actions">
              <a class="button-link secondary-link" href="{self._url('/')}">返回列表</a>
            </p>
            {summary}
            {progress_overview}
            <section class="detail-panel">
              <div class="section-head">
                <div>
                  <h2>战斗记录</h2>
                  <p class="muted">{self._e(log_note)}</p>
                </div>
                {log_clear_button}
              </div>
              <div class="log-list">{log_cards}</div>
            </section>
            <section class="detail-panel">
              <div class="section-head">
                <div>
                  <h2>其他人与主角的交互</h2>
                  <p class="muted">{self._e(cameo_note)}</p>
                </div>
                {cameo_clear_button}
              </div>
              <div class="log-list">{cameo_cards}</div>
            </section>
            <section class="detail-grid raw-grid">
              <details class="raw-panel">
                <summary>查看当前状态（player_data_update.json）</summary>
                <pre>{self._e_json(player_data)}</pre>
              </details>
              <details class="raw-panel">
                <summary>查看初始状态（player_data.json）</summary>
                <pre>{self._e_json(player_data_base)}</pre>
              </details>
            </section>
            {self._state_overview_html(player_data, state_reset_button)}
            {source_file_panel}
            {danger_zone}
            """,
        )

    def _player_site_detail_response(
        self,
        group_id: str,
        user_id: str,
        player_data: dict[str, Any],
        logs: list[dict[str, Any]],
        cameo_memories: list[dict[str, Any]],
        title_name: str,
    ) -> web.Response:
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        city_name = self.repository.get_city_name(group_id)
        magical_name = self._get_nested(protagonist, ["个人信息", "魔法少女名"], "")
        class_name = self._rank_display(self._get_nested(protagonist, ["个人信息", "身份&职业"], "未知职阶"))
        color = self._get_nested(protagonist, ["个人信息", "代表色"], "星光色")
        ability = self._get_nested(protagonist, ["个人信息", "核心能力"], "未记录")
        weapon = self._get_nested(protagonist, ["个人信息", "武装"], "未记录")
        outfit = self._get_nested(protagonist, ["个人信息", "变身服"], "未记录")
        familiar = self._get_nested(protagonist, ["个人信息", "使魔伙伴种类"], "")
        familiar_bond = self._get_nested(protagonist, ["个人信息", "使魔伙伴与主角关系"], "")
        display_name = magical_name or title_name
        page_name = f"魔法少女 {display_name}"

        progress_html = self._progress_overview_html(player_data)
        logs_html = self._player_log_cards(group_id, user_id, logs[:8], allow_delete=False)
        cameo_html = self._player_cameo_memory_cards(
            group_id, user_id, cameo_memories[:8], allow_delete=False
        )
        top_items = [
            ("姓名", title_name),
            ("年龄", self._get_nested(protagonist, ["个人信息", "年龄"], "未记录")),
            ("身高", self._get_nested(protagonist, ["身材细节", "身高"], "未记录")),
        ]
        personal_items = [
            ("魔法少女名", magical_name or "未记录"),
            ("所在城市", city_name),
            ("城市 ID", group_id),
            ("职阶", class_name),
            ("等级", self._rank_display(level_display(player_data))),
            ("等级经验", f"{level_exp_percent(player_data)}%"),
            ("代表色", color),
            ("核心能力", ability),
            ("使魔伙伴", familiar or "未记录"),
            ("使魔关系", familiar_bond or "未记录"),
            ("武装", weapon),
            ("变身服", outfit),
            ("性格特质", self._get_nested(protagonist, ["个人信息", "性格特质"], "未记录")),
        ]
        appearance_items = self._player_site_collect_fields(protagonist, [
            ("脸型", ["相貌特征", "脸型"]),
            ("五官", ["相貌特征", "五官"]),
            ("眼睛颜色", ["相貌特征", "眼睛颜色"]),
            ("发型与发色", ["相貌特征", "发型与发色"]),
            ("特殊记号", ["相貌特征", "特殊记号"]),
        ])
        body_items = self._player_site_collect_fields(protagonist, [
            ("三围", ["身材细节", "三围"]),
            ("体态", ["身材细节", "体态"]),
            ("肌肉线条", ["身材细节", "肌肉线条"]),
            ("体脂率", ["身材细节", "体脂率"]),
            ("皮肤状态", ["身材细节", "皮肤状态"]),
        ])
        sex_items = self._player_site_collect_fields(protagonist, [
            ("乳房形状", ["性器官特征", "乳房形状"]),
            ("乳晕与乳头颜色", ["性器官特征", "乳晕与乳头颜色"]),
            ("小穴形态", ["性器官特征", "小穴形态"]),
            ("体毛状况", ["性器官特征", "体毛状况"]),
            ("天生敏感度", ["性器官特征", "天生敏感度"]),
        ])

        return self._html_response(
            page_name,
            f"""
            <section class="player-detail-shell" aria-label="魔法少女个人档案">
              <div class="player-stars" aria-hidden="true">
                <span></span><span></span><span></span><span></span><span></span>
              </div>
              <header class="player-detail-hero">
                <a class="player-back-link" href="{self._url('/')}">返回个人档案</a>
                <div class="player-detail-emblem" aria-hidden="true">✦</div>
                <p class="player-kicker">Mahou Shoujo Profile</p>
                <h1>{self._e(page_name)}</h1>
                <p>{self._e(city_name)}记录中的魔法少女档案。这里汇总了你的身份、外观、装备、成长进度与最近的冒险痕迹。</p>
                <div class="player-hero-tags">
                  <span>{self._e(self._rank_display(level_display(player_data)))}</span>
                  <span>{self._e(class_name)}</span>
                  <span>{self._e(color)}</span>
                </div>
                <section class="player-top-grid">
                  {self._player_site_top_grid(top_items)}
                </section>
              </header>

              <section class="player-detail-flow">
                <section class="player-profile-triad">
                  <div class="player-side-stack">
                    {self._player_site_profile_card("Appearance", "相貌特征", appearance_items)}
                    {self._player_site_profile_card("Body", "身材细节", body_items)}
                  </div>
                  <div class="player-profile-main">
                    {self._player_site_profile_card("Personal", "个人信息", personal_items)}
                  </div>
                  <div class="player-profile-side">
                    {self._player_site_profile_card("Body Detail", "性器官特征", sex_items)}
                  </div>
                </section>

                <article class="player-site-section player-memory-row">
                  <div class="profile-card-head">
                    <span>Diary</span>
                    <h2>最近冒险记录</h2>
                  </div>
                  <div class="log-list">{logs_html}</div>
                </article>
                <article class="player-site-section player-memory-row">
                  <div class="profile-card-head">
                    <span>Connections</span>
                    <h2>城市中的交互</h2>
                  </div>
                  <div class="log-list">{cameo_html}</div>
                </article>

                <section class="player-site-section">
                  <div class="profile-card-head">
                    <span>Growth</span>
                    <h2>成长进度</h2>
                  </div>
                  {progress_html or '<p class="player-site-empty">暂无成长进度记录。</p>'}
                </section>
              </section>
            </section>
            """,
        )

    def _player_site_collect_fields(
        self,
        protagonist: dict[str, Any],
        fields: list[tuple[str, list[str]]],
    ) -> list[tuple[str, object]]:
        return [
            (label, self._get_nested(protagonist, path, "未记录"))
            for label, path in fields
        ]

    def _player_site_top_grid(self, items: list[tuple[str, object]]) -> str:
        return "".join(
            f"""
            <article class="player-top-item">
              <span>{self._e(label)}</span>
              <strong>{self._e(value or "未记录")}</strong>
            </article>
            """
            for label, value in items
        )

    def _player_site_profile_card(
        self,
        kicker: str,
        title: str,
        items: list[tuple[str, object]],
    ) -> str:
        return f"""
            <article class="player-profile-card">
              <div class="profile-card-head">
                <span>{self._e(kicker)}</span>
                <h2>{self._e(title)}</h2>
              </div>
              {self._player_site_info_grid(items)}
            </article>
        """

    def _player_site_info_grid(self, items: list[tuple[str, object]]) -> str:
        rows = []
        for label, value in items:
            text = str(value or "").strip()
            if not text:
                continue
            rows.append(
                f"""
                <div class="player-info-item">
                  <span>{self._e(label)}</span>
                  <strong>{self._e(text)}</strong>
                </div>
                """
            )
        return f'<div class="player-info-grid">{"".join(rows)}</div>'

    def _player_site_profile_sections(self, protagonist: dict[str, Any]) -> str:
        groups = [
            ("Personal", "个人信息", [
                ("性格特质", ["个人信息", "性格特质"]),
                ("年龄", ["个人信息", "年龄"]),
                ("核心能力", ["个人信息", "核心能力"]),
                ("使魔伙伴种类", ["个人信息", "使魔伙伴种类"]),
                ("使魔伙伴与主角关系", ["个人信息", "使魔伙伴与主角关系"]),
            ]),
            ("Appearance", "相貌特征", [
                ("脸型", ["相貌特征", "脸型"]),
                ("五官", ["相貌特征", "五官"]),
                ("眼睛颜色", ["相貌特征", "眼睛颜色"]),
                ("发型与发色", ["相貌特征", "发型与发色"]),
                ("特殊记号", ["相貌特征", "特殊记号"]),
            ]),
            ("Body", "身体记录", [
                ("身高", ["身材细节", "身高"]),
                ("三围", ["身材细节", "三围"]),
                ("体态", ["身材细节", "体态"]),
                ("肌肉线条", ["身材细节", "肌肉线条"]),
                ("体脂率", ["身材细节", "体脂率"]),
                ("皮肤状态", ["身材细节", "皮肤状态"]),
            ]),
            ("Equipment", "魔法装备", [
                ("武装", ["个人信息", "武装"]),
                ("变身服", ["个人信息", "变身服"]),
                ("代表色", ["个人信息", "代表色"]),
            ]),
        ]
        cards = []
        for kicker, title, fields in groups:
            items = []
            for label, path in fields:
                value = self._get_nested(protagonist, path, "")
                if value:
                    items.append((label, value))
            if not items:
                continue
            cards.append(
                f"""
                <article class="player-profile-card">
                  <div class="profile-card-head">
                    <span>{self._e(kicker)}</span>
                    <h2>{self._e(title)}</h2>
                  </div>
                  {self._player_site_info_grid(items)}
                </article>
                """
            )
        return "\n".join(cards)

    def _player_site_state_html(self, state: dict[str, Any]) -> str:
        items = build_state_display_items(state, limit=36)
        if not items:
            return ""
        return (
            '<div class="player-state-grid">'
            + "".join(
                f"""
                <div class="player-state-item">
                  <span>{self._e(label)}</span>
                  <strong>{self._e(value)}</strong>
                </div>
                """
                for label, value in items
            )
            + "</div>"
        )

    async def _player_profile_save(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if not session:
            return self._forbidden()

        data = await request.post()
        group_id = str(data.get("group_id", "")).strip()
        user_id = str(data.get("user_id", "")).strip()
        if not group_id or not user_id:
            raise web.HTTPBadRequest(text="missing group_id or user_id")
        if not self._can_access_player(session, user_id):
            return self._forbidden()

        fields = [
            "姓名",
            "性格特质",
            "代表色",
            "核心能力",
            "使魔伙伴种类",
            "使魔伙伴与主角关系",
            "年龄",
            "身份&职业",
            "魔法少女名",
            "武装",
            "变身服",
            "脸型",
            "五官",
            "眼睛颜色",
            "发型与发色",
            "特殊记号",
            "身高",
            "三围",
            "体态",
            "肌肉线条",
            "体脂率",
            "皮肤状态",
            "乳房形状",
            "乳晕与乳头颜色",
            "小穴形态",
            "体毛状况",
            "天生敏感度",
        ]
        updates: dict[str, Any] = {
            key: str(data.get(key, ""))
            for key in fields
        }

        try:
            self.repository.update_profile_card(group_id, user_id, updates)
        except Exception as exc:
            return self._html_response(
                "保存角色档案失败",
                f"""
                <h1>保存角色档案失败</h1>
                <p class="error">{self._e(exc)}</p>
                <p><a class="button-link secondary-link" href="{self._url(f'/player?group_id={quote(group_id, safe="")}&user_id={quote(user_id, safe="")}')}">返回角色档案</a></p>
                """,
            )

        raise self._redirect(
            f"/player?group_id={quote(group_id, safe='')}&user_id={quote(user_id, safe='')}"
        )

    def _player_source_file_panel(self, group_id: str, user_id: str) -> str:
        rows = []
        for item in self.repository.list_player_source_files(group_id, user_id):
            file_name = str(item.get("name", ""))
            exists_text = "已存在" if item.get("exists") else "未创建"
            accept = ".jsonl" if file_name.endswith(".jsonl") else ".json,application/json"
            source_url = self._url(
                f"/player/file/source?group_id={quote(group_id, safe='')}&user_id={quote(user_id, safe='')}&file={quote(file_name, safe='')}"
            )
            export_url = self._url(
                f"/player/file/export?group_id={quote(group_id, safe='')}&user_id={quote(user_id, safe='')}&file={quote(file_name, safe='')}"
            )
            rows.append(
                f"""
                <tr>
                  <td>{self._e(file_name)}</td>
                  <td>{self._e(exists_text)}</td>
                  <td>
                    <a class="button-link secondary-link compact-link" href="{source_url}">编辑源码</a>
                    <a class="button-link secondary-link compact-link" href="{export_url}">导出</a>
                    <form class="inline-import-form" method="post" action="{self._url('/player/file/import')}" enctype="multipart/form-data">
                      <input type="hidden" name="group_id" value="{self._e(group_id)}">
                      <input type="hidden" name="user_id" value="{self._e(user_id)}">
                      <input type="hidden" name="file" value="{self._e(file_name)}">
                      <input name="import_file" type="file" accept="{self._e(accept)}">
                      <button class="secondary compact-button" type="submit">导入</button>
                    </form>
                  </td>
                </tr>
                """
            )
        body = "\n".join(rows)
        return f"""
            <section class="detail-panel">
              <div class="section-head">
                <div>
                  <h2>存档源码</h2>
                  <p class="muted">管理员可编辑、导出或导入该玩家目录下的 JSON / JSONL 源文件。保存前会校验格式。</p>
                </div>
              </div>
              <table class="source-table">
                <thead><tr><th>文件</th><th>状态</th><th>操作</th></tr></thead>
                <tbody>{body}</tbody>
              </table>
            </section>
        """

    def _player_summary_html(
        self,
        group_id: str,
        user_id: str,
        player_data: dict[str, Any],
        can_edit: bool = False,
    ) -> str:
        avatar_html = "<span>転</span>"
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        created_at = player_data.get("created_at", "")
        updated_at = player_data.get("updated_at", "")

        # 构建可编辑的人物卡字段
        profile_sections = self._build_profile_edit_sections(protagonist, can_edit)
        class_name = self._rank_display(self._get_nested(protagonist, ["个人信息", "身份&职业"], "未知职阶"))
        target_name = self._get_nested(protagonist, ["个人信息", "姓名"], player_data.get("nickname", ""))

        profile_panel = (
            f"""
              <form class="detail-panel profile-edit-panel" method="post" action="{self._url('/player/profile/save')}">
                <input type="hidden" name="group_id" value="{self._e(group_id)}">
                <input type="hidden" name="user_id" value="{self._e(user_id)}">
                <div class="section-head profile-edit-head">
                  <h2>角色档案</h2>
                  <button class="compact-button" type="submit">保存</button>
                </div>
                {profile_sections}
              </form>
            """
            if can_edit
            else f"""
              <article class="detail-panel">
                <h2>角色档案</h2>
                {profile_sections}
              </article>
            """
        )
        return f"""
            <section class="hero-card">
              <div class="avatar-large">{avatar_html}</div>
              <div class="hero-main">
                <div class="kicker">城市 {self._e(self.repository.get_city_name(group_id))} / 城市 ID {self._e(group_id)} / 用户 {self._e(user_id)}</div>
                <h2>{self._e(target_name)}</h2>
                <div class="identity-line">
                  <span>{self._e(self._rank_display(level_display(player_data)))}</span>
                  <span>等级经验 {self._e(level_exp_percent(player_data))}%</span>
                  <span>{self._e(class_name)}</span>
                </div>
              </div>
            </section>
            <section class="detail-grid">
              {profile_panel}
              <article class="detail-panel">
                <h2>当前状态</h2>
                <div class="meta-list">
                  <div><span>等级经验</span><strong>{self._e(level_exp_percent(player_data))}%</strong></div>
                  <div><span>创建</span><strong>{self._e(created_at)}</strong></div>
                  <div><span>更新</span><strong>{self._e(updated_at)}</strong></div>
                </div>
              </article>
            </section>
        """

    def _build_profile_edit_sections(
        self,
        protagonist: dict[str, Any],
        can_edit: bool = False,
    ) -> str:
        """根据主角树生成可编辑/只读的人物卡字段。"""
        sections_html = []
        field_groups = [
            ("个人信息", [
                ("姓名", ["个人信息", "姓名"]),
                ("性格特质", ["个人信息", "性格特质"]),
                ("代表色", ["个人信息", "代表色"]),
                ("核心能力", ["个人信息", "核心能力"]),
                ("使魔伙伴种类", ["个人信息", "使魔伙伴种类"]),
                ("使魔伙伴与主角关系", ["个人信息", "使魔伙伴与主角关系"]),
                ("年龄", ["个人信息", "年龄"]),
                ("身份&职业", ["个人信息", "身份&职业"]),
                ("魔法少女名", ["个人信息", "魔法少女名"]),
                ("武装", ["个人信息", "武装"]),
                ("变身服", ["个人信息", "变身服"]),
            ]),
            ("相貌特征", [
                ("脸型", ["相貌特征", "脸型"]),
                ("五官", ["相貌特征", "五官"]),
                ("眼睛颜色", ["相貌特征", "眼睛颜色"]),
                ("发型与发色", ["相貌特征", "发型与发色"]),
                ("特殊记号", ["相貌特征", "特殊记号"]),
            ]),
            ("身材细节", [
                ("身高", ["身材细节", "身高"]),
                ("三围", ["身材细节", "三围"]),
                ("体态", ["身材细节", "体态"]),
                ("肌肉线条", ["身材细节", "肌肉线条"]),
                ("体脂率", ["身材细节", "体脂率"]),
                ("皮肤状态", ["身材细节", "皮肤状态"]),
            ]),
            ("性器官特征", [
                ("乳房形状", ["性器官特征", "乳房形状"]),
                ("乳晕与乳头颜色", ["性器官特征", "乳晕与乳头颜色"]),
                ("小穴形态", ["性器官特征", "小穴形态"]),
                ("体毛状况", ["性器官特征", "体毛状况"]),
                ("天生敏感度", ["性器官特征", "天生敏感度"]),
            ]),
        ]

        for section_name, fields in field_groups:
            field_items = []
            for label, path in fields:
                value = self._get_nested(protagonist, path, "")
                if can_edit:
                    field_items.append(self._profile_edit_field(label, label, value))
                else:
                    if value:
                        field_items.append(self._profile_field(label, value))
            if field_items:
                sections_html.append(
                    f'<div class="section-title">{self._e(section_name)}</div>'
                    + "".join(field_items)
                )

        return "\n".join(sections_html)

    @staticmethod
    def _get_nested(data: dict, keys: list[str], default: str = "") -> str:
        current = data
        for key in keys:
            if not isinstance(current, dict):
                return default
            current = current.get(key)
            if current is None:
                return default
        return str(current) if current is not None else default

    def _profile_edit_field(
        self,
        label: str,
        name: str,
        value: object,
        multiline: bool = False,
        monospace: bool = False,
    ) -> str:
        value_text = self._e(value or "")
        class_name = "profile-edit-textarea"
        if monospace:
            class_name += " monospace-editor"
        control = (
            f"<textarea class=\"{class_name}\" name=\"{self._e(name)}\">{value_text}</textarea>"
            if multiline
            else f"<input type=\"text\" name=\"{self._e(name)}\" value=\"{value_text}\">"
        )
        return f"""
            <label class="profile-edit-field">
              <span>{self._e(label)}</span>
              {control}
            </label>
        """

    def _profile_field(self, label: str, value: object) -> str:
        return f"""
            <div class="profile-field">
              <span>{self._e(label)}</span>
              <p>{self._e(value or "未记录")}</p>
            </div>
        """

    def _player_log_cards(
        self,
        group_id: str,
        user_id: str,
        logs: list[dict[str, Any]],
        allow_delete: bool = False,
    ) -> str:
        if not logs:
            return "<p class=\"muted empty-state\">还没有战斗记录。</p>"

        cards: list[str] = []
        for display_index, log in enumerate(logs, start=1):
            log_index = int(log.get("_log_index", -1))
            raw_log_type = str(log.get("type", "log"))
            log_type = self._e(raw_log_type)
            title = self._e(log.get("title") or log.get("message") or "战斗记录")
            action = self._e(log.get("action") or "")
            level_change = self._e(log.get("level_change") or "")
            diary = self._e(log.get("diary") or "")
            encounter = self._e(log.get("encounter") or "")
            result = self._e(log.get("result") or log.get("message") or "")
            changes = log.get("changes")
            if not isinstance(changes, list):
                changes = log.get("rewards") if isinstance(log.get("rewards"), list) else []
            change_html = "".join(f"<span class=\"tag\">{self._e(item)}</span>" for item in changes)
            created_at = self._format_time(log.get("created_at"))
            world_date = self._world_date_display(log)
            world_date_html = f"<span>{self._e(world_date)}</span>" if world_date else ""
            level_html = f"<span>{level_change}</span>" if level_change else ""
            action_html = f"<p class=\"log-action\">{action}</p>" if action else ""
            diary_html = f"<p class=\"log-result\">{diary}</p>" if diary else ""
            encounter_html = (
                f"<p class=\"log-action\">遭遇：{encounter}</p>"
                if encounter
                else ""
            )
            changes_block = (
                f"<div class=\"tag-row\">{change_html}</div>"
                if change_html
                else ""
            )
            delete_button = ""
            if allow_delete and log_index >= 0 and raw_log_type == "battle_diary":
                delete_button = f"""
                  <form class="inline-form" method="post" action="{self._url('/player/log/delete')}" onsubmit="return confirm('确定删除这条战斗记录？当前 state 不会自动回滚。');">
                    <input type="hidden" name="group_id" value="{self._e(group_id)}">
                    <input type="hidden" name="user_id" value="{self._e(user_id)}">
                    <input type="hidden" name="log_index" value="{log_index}">
                    <button class="danger compact-button" type="submit">删除记录</button>
                  </form>
                """
            cards.append(
                f"""
                <details class="log-card">
                  <summary class="log-card-summary">
                    <div class="log-card-head">
                      <div>
                        <span class="log-index">#{display_index}</span>
                        <h3>{title}</h3>
                      </div>
                      {delete_button}
                    </div>
                    <div class="log-meta summary-meta">
                      <span>{self._e(created_at)}</span>
                      {world_date_html}
                      <span>{log_type}</span>
                      {level_html}
                    </div>
                  </summary>
                  <div class="log-card-body">
                    <div class="log-meta">
                      <span>{self._e(created_at)}</span>
                      {world_date_html}
                      <span>{log_type}</span>
                      {level_html}
                    </div>
                    {action_html}
                    {diary_html}
                    {encounter_html}
                    <p class="log-result">{result}</p>
                    {changes_block}
                  </div>
                </details>
                """
            )
        return "\n".join(cards)

    def _player_cameo_memory_cards(
        self,
        group_id: str,
        user_id: str,
        memories: list[dict[str, Any]],
        allow_delete: bool = False,
    ) -> str:
        if not memories:
            return "<p class=\"muted empty-state\">还没有其他人与主角的交互。</p>"

        cards: list[str] = []
        for display_index, memory in enumerate(memories, start=1):
            title = self._e(memory.get("title") or "其他人与主角的交互")
            source_name = self._e(
                "多条交互摘要"
                if memory.get("type") == "cameo_summary"
                else memory.get("source_target_name") or "未知角色"
            )
            encounter = self._e(memory.get("encounter") or "")
            result = self._e(memory.get("result") or "")
            created_at = self._format_time(memory.get("created_at"))
            world_date = self._world_date_display(memory)
            world_date_html = f"<span>{self._e(world_date)}</span>" if world_date else ""
            encounter_html = (
                f"<p class=\"log-action\">遭遇：{encounter}</p>"
                if encounter
                else ""
            )
            delete_button = ""
            if allow_delete and memory.get("_log_index") is not None:
                log_index = memory["_log_index"]
                delete_button = f"""
                  <form class="inline-form" method="post" action="{self._url('/player/cameo/delete')}" onsubmit="return confirm('确定删除这条交互记录？');">
                    <input type="hidden" name="group_id" value="{self._e(group_id)}">
                    <input type="hidden" name="user_id" value="{self._e(user_id)}">
                    <input type="hidden" name="log_index" value="{log_index}">
                    <button class="danger compact-button" type="submit">删除记录</button>
                  </form>
                """
            cards.append(
                f"""
                <details class="log-card cameo-card">
                  <summary class="log-card-summary">
                    <div class="log-card-head">
                      <div>
                        <span class="log-index">#{display_index}</span>
                        <h3>{title}</h3>
                      </div>
                      {delete_button}
                    </div>
                    <div class="log-meta summary-meta">
                      <span>{self._e(created_at)}</span>
                      {world_date_html}
                      <span>来源：{source_name}</span>
                    </div>
                  </summary>
                  <div class="log-card-body">
                    <div class="log-meta">
                      <span>{self._e(created_at)}</span>
                      {world_date_html}
                      <span>来源：{source_name}</span>
                    </div>
                    {encounter_html}
                    <p class="log-result">{result}</p>
                  </div>
                </details>
                """
            )
        return "\n".join(cards)

    def _progress_overview_html(self, state: dict[str, Any]) -> str:
        sections = build_progress_sections(
            state,
            self.editable_manager.read_book_base_path(
                "skill_book/default.json",
                "/主角/技能/",
            ),
            self.editable_manager.read_book_base_path(
                "fetish_book/default.json",
                "/主角/快感状态/性癖/",
            ),
            limit=16,
        )
        skill_title = self.editable_manager.read_book_display_name(
            "skill_book/default.json",
            "技能&熟练度",
        )
        status_title = self.editable_manager.read_book_display_name(
            "fetish_book/default.json",
            "性癖开发",
        )
        return (
            self._progress_panel_html(skill_title, sections.skill_items)
            + self._progress_panel_html(status_title, sections.status_items)
        )

    def _progress_panel_html(self, title: str, items: list[Any]) -> str:
        if not items:
            return f"""
            <section class="detail-panel progress-overview-panel">
              <h2>{self._e(title)}</h2>
              <p class="muted empty-state">暂无可展示的经验进度。</p>
            </section>
            """
        rows = "".join(
            f"""
            <div class="progress-row">
              <div class="progress-head">
                <div class="progress-name">
                  <span>{self._e(item.label)}</span>
                  <span class="progress-level">{'Lv.Max' if item.is_max else f'Lv.{self._e(item.level)}'}</span>
                </div>
                <div class="progress-xp">{'MAX' if item.is_max else f'{self._e(item.value)} <small>/ 100</small>'}</div>
              </div>
              <div class="progress-track">
                <div class="progress-fill" style="width: {self._e(item.percent)}%;"></div>
              </div>
            </div>
            """
            for item in items
        )
        return f"""
            <section class="detail-panel progress-overview-panel">
              <h2>{self._e(title)}</h2>
              <div class="progress-list">{rows}</div>
            </section>
        """

    def _state_overview_html(self, state: dict[str, Any], action_html: str = "") -> str:
        items = build_state_display_items(state, limit=40)
        if not items and not action_html:
            return ""
        item_html = "".join(
            f"""
            <div class="state-item">
              <span>{self._e(label)}</span>
              <strong>{self._e(value)}</strong>
            </div>
            """
            for label, value in items
        ) or '<p class="muted empty-state">暂无可展示的状态项。</p>'
        return f"""
            <section class="detail-panel state-overview-panel">
              <div class="section-head">
                <div>
                  <h2>完整状态</h2>
                  <p class="muted">包含当前 state 中除经验进度外的状态项；原始 JSON 可在上方展开查看。</p>
                </div>
                {action_html}
              </div>
              <div class="state-overview-grid">{item_html}</div>
            </section>
        """

    def _player_clear_form(
        self,
        group_id: str,
        user_id: str,
        action: str,
        label: str,
        confirmation: str,
    ) -> str:
        return f"""
            <form class="inline-form" method="post" action="{self._url(action)}" onsubmit="return confirm('{self._e(confirmation)}');">
              <input type="hidden" name="group_id" value="{self._e(group_id)}">
              <input type="hidden" name="user_id" value="{self._e(user_id)}">
              <button class="danger compact-button" type="submit">{self._e(label)}</button>
            </form>
        """

    async def _player_log_delete(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        data = await request.post()
        group_id = str(data.get("group_id", ""))
        user_id = str(data.get("user_id", ""))
        try:
            log_index = int(data.get("log_index", -1))
        except (TypeError, ValueError):
            log_index = -1
        if not group_id or not user_id or log_index < 0:
            raise web.HTTPBadRequest(text="missing group_id, user_id or log_index")

        self.repository.delete_battle_log(group_id, user_id, log_index)
        raise web.HTTPFound(
            self._url(f"/player?group_id={self._e(group_id)}&user_id={self._e(user_id)}")
        )

    async def _player_log_clear(self, request: web.Request) -> web.Response:
        group_id, user_id = await self._admin_player_action_ids(request)
        self.repository.clear_battle_logs(group_id, user_id)
        raise self._player_detail_redirect(group_id, user_id)

    async def _player_cameo_delete(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        data = await request.post()
        group_id = str(data.get("group_id", ""))
        user_id = str(data.get("user_id", ""))
        try:
            log_index = int(data.get("log_index", -1))
        except (TypeError, ValueError):
            log_index = -1
        if not group_id or not user_id or log_index < 0:
            raise web.HTTPBadRequest(text="missing group_id, user_id or log_index")

        self.repository.delete_cameo_memory(group_id, user_id, log_index)
        raise web.HTTPFound(
            self._url(f"/player?group_id={self._e(group_id)}&user_id={self._e(user_id)}")
        )

    async def _player_cameo_clear(self, request: web.Request) -> web.Response:
        group_id, user_id = await self._admin_player_action_ids(request)
        self.repository.clear_cameo_memories(group_id, user_id)
        raise self._player_detail_redirect(group_id, user_id)

    async def _player_state_reset(self, request: web.Request) -> web.Response:
        group_id, user_id = await self._admin_player_action_ids(request)
        self.repository.reset_player_state(group_id, user_id)
        raise self._player_detail_redirect(group_id, user_id)

    async def _admin_player_action_ids(self, request: web.Request) -> tuple[str, str]:
        if not self._is_admin(request):
            raise web.HTTPForbidden(text="forbidden")
        data = await request.post()
        group_id = str(data.get("group_id", "")).strip()
        user_id = str(data.get("user_id", "")).strip()
        if not group_id or not user_id:
            raise web.HTTPBadRequest(text="missing group_id or user_id")
        return group_id, user_id

    def _player_detail_redirect(self, group_id: str, user_id: str) -> web.HTTPFound:
        return web.HTTPFound(
            self._url(
                f"/player?group_id={quote(group_id, safe='')}&user_id={quote(user_id, safe='')}"
            )
        )

    async def _player_delete(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return self._forbidden()

        data = await request.post()
        group_id = str(data.get("group_id", ""))
        user_id = str(data.get("user_id", ""))
        if not group_id or not user_id:
            raise web.HTTPBadRequest(text="missing group_id or user_id")

        self.repository.delete_player_save(group_id, user_id)
        raise self._redirect("/")

    def _is_authorized(self, request: web.Request) -> bool:
        return self._session(request) is not None

    def _is_admin(self, request: web.Request) -> bool:
        session = self._session(request)
        return bool(session and session["role"] == SESSION_ADMIN_ROLE)

    def _can_access_player(self, session: dict[str, str], user_id: str) -> bool:
        if session["role"] == SESSION_ADMIN_ROLE:
            return True
        return session["user_id"] == self._safe_session_id(user_id)

    def _session(self, request: web.Request) -> dict[str, str] | None:
        raw = request.cookies.get(SESSION_COOKIE_NAME, "")
        if not raw or not self.token:
            return None
        parts = raw.split(":")
        if len(parts) == 2 and parts[0] == SESSION_ADMIN_ROLE:
            role, signature = parts
            payload = role
            if hmac.compare_digest(signature, self._session_signature(payload)):
                return {"role": role, "user_id": ""}
            return None
        if len(parts) == 3 and parts[0] == SESSION_USER_ROLE:
            role, user_id, signature = parts
            payload = f"{role}:{user_id}"
            if hmac.compare_digest(signature, self._session_signature(payload)):
                return {"role": role, "user_id": user_id}
        return None

    def _build_session_cookie(self, role: str, user_id: str = "") -> str:
        payload = role if role == SESSION_ADMIN_ROLE else f"{role}:{user_id}"
        return f"{payload}:{self._session_signature(payload)}"

    def _session_signature(self, payload: str) -> str:
        return hmac.new(
            self.token.encode("utf-8"),
            payload.encode("utf-8"),
            "sha256",
        ).hexdigest()

    @staticmethod
    def _safe_session_id(value: object) -> str:
        text = str(value or "unknown").strip()
        text = re.sub(r"[^0-9A-Za-z_.-]+", "_", text)
        return text[:80] or "unknown"

    def _forbidden(self) -> web.Response:
        raise self._redirect("/login")

    def _redirect(self, path: str) -> web.HTTPFound:
        return web.HTTPFound(self._url(path))

    def _url(self, path: str) -> str:
        if not path:
            path = "/"
        if not path.startswith("/"):
            path = "/" + path
        if not self.public_path_prefix:
            return path
        if path == "/":
            return self.public_path_prefix + "/"
        return self.public_path_prefix + path

    def _cookie_path(self) -> str:
        return self.public_path_prefix or "/"

    @staticmethod
    def _normalize_path_prefix(prefix: str) -> str:
        text = str(prefix or "").strip()
        if not text or text == "/":
            return ""
        return "/" + text.strip("/")

    def _source_editor_response(
        self,
        *,
        title: str,
        back_url: str,
        save_url: str,
        hidden_fields: dict[str, str],
        content: str,
        file_name: str,
        import_url: str,
        export_url: str,
        accept: str,
        warning: str = "",
    ) -> web.Response:
        hidden_html = "\n".join(
            f'<input type="hidden" name="{self._e(key)}" value="{self._e(value)}">'
            for key, value in hidden_fields.items()
        )
        warning_html = f'<p class="error">{self._e(warning)}</p>' if warning else ""
        return self._html_response(
            title,
            f"""
            <h1>{self._e(title)}</h1>
            <p><a href="{back_url}">返回</a></p>
            <p class="muted">{self._e(file_name)}</p>
            {warning_html}
            <div class="source-actions">
              <a class="button-link secondary-link" href="{export_url}">导出</a>
              <form class="inline-import-form" method="post" action="{import_url}" enctype="multipart/form-data">
                {hidden_html}
                <input name="import_file" type="file" accept="{self._e(accept)}">
                <button class="secondary compact-button" type="submit">导入</button>
              </form>
            </div>
            <form method="post" action="{save_url}">
              {hidden_html}
              <label for="source-content">源码内容</label>
              <textarea id="source-content" class="source-editor" name="content" spellcheck="false">{self._e(content)}</textarea>
              <div class="actions">
                <button type="submit">保存源码</button>
              </div>
            </form>
            """,
        )

    async def _form_text_content(self, data: Any) -> str:
        file_field = data.get("import_file")
        if file_field is not None and getattr(file_field, "filename", ""):
            raw = file_field.file.read()
            if isinstance(raw, str):
                return raw
            return raw.decode("utf-8-sig")
        return str(data.get("content") or data.get("import_content") or "")

    def _download_response(
        self,
        filename: str,
        content: str,
        content_type: str,
    ) -> web.Response:
        safe_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", filename).strip("._") or "download.json"
        response = web.Response(
            text=str(content),
            content_type=content_type,
            charset="utf-8",
        )
        response.headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        return response

    def _html_response(
        self,
        title: str,
        content: str,
        status: int = 200,
        show_logout: bool = True,
    ) -> web.Response:
        logout_html = (
            f"""
            <form class="logout-form" method="post" action="{self._url('/logout')}">
              <button class="secondary compact-button" type="submit">退出</button>
            </form>
            """
            if show_logout
            else ""
        )
        return web.Response(
            status=status,
            text=f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{self._e(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: linear-gradient(180deg, #f3f6fb 0%, #eef3f8 42%, #f8fafc 100%); color: #20242a; -webkit-font-smoothing: antialiased; }}
    main {{ max-width: 1160px; margin: 0 auto; padding: 30px 20px 52px; }}
    .topbar {{ display: flex; justify-content: flex-end; min-height: 36px; margin-bottom: 8px; }}
    .logout-form {{ margin: 0; }}
    h1 {{ margin: 0 0 12px; font-size: 30px; letter-spacing: 0; }}
    h2 {{ margin: 24px 0 10px; font-size: 18px; }}
    .muted {{ color: #68707d; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #dde2ea; border-radius: 8px; overflow: hidden; box-shadow: 0 10px 24px rgba(31, 41, 55, 0.06); }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #e6eaf0; text-align: left; font-size: 14px; }}
    th {{ background: #eef2f6; color: #3a4350; }}
    a {{ color: #1f6feb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .nav-actions {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 14px 0 18px; }}
    .button-link {{ display: inline-flex; align-items: center; justify-content: center; padding: 9px 16px; border-radius: 6px; background: #1f6feb; color: #fff; font-weight: 700; }}
    .button-link:hover {{ text-decoration: none; background: #1a5fc9; }}
    .secondary-link {{ background: #59636e; }}
    .secondary-link:hover {{ background: #46515d; }}
    .compact-link {{ padding: 6px 10px; font-size: 13px; }}
    .inline-form {{ display: inline; margin: 0; }}
    .inline-import-form {{ display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 0; }}
    .source-actions {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 12px 0 18px; }}
    .source-table td:last-child {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .compact-button {{ margin: 0; padding: 6px 10px; font-size: 13px; }}
    .city-editor-panel {{ margin: 16px 0; }}
    .city-name-form input[type="text"] {{ width: min(360px, 100%); }}
    main:has(.player-shell) {{ width: 100%; max-width: none; min-height: 100vh; box-sizing: border-box; padding: 0; overflow: hidden; }}
    main:has(.player-shell) .topbar {{ position: absolute; top: 18px; right: 22px; z-index: 5; margin: 0; min-height: 0; }}
    main:has(.player-shell) .topbar button {{ border: 1px solid rgba(255,255,255,.68); background: rgba(112, 72, 156, .72); box-shadow: 0 12px 28px rgba(108, 53, 133, .18); backdrop-filter: blur(10px); }}
    .player-shell {{ position: relative; min-height: 100vh; padding: 72px clamp(18px, 5vw, 72px) 58px; box-sizing: border-box; overflow: hidden; background: radial-gradient(circle at 14% 14%, rgba(255, 241, 151, .82) 0 7%, transparent 21%), radial-gradient(circle at 78% 20%, rgba(139, 229, 255, .72) 0 8%, transparent 22%), radial-gradient(circle at 82% 78%, rgba(255, 139, 200, .52) 0 11%, transparent 25%), linear-gradient(135deg, #fff5fb 0%, #f7ddff 30%, #dff7ff 66%, #fff6c7 100%); color: #42233f; isolation: isolate; }}
    .player-shell::before {{ content: ""; position: absolute; inset: -20%; z-index: -2; background-image: linear-gradient(rgba(255,255,255,.48) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.42) 1px, transparent 1px); background-size: 42px 42px; transform: rotate(-7deg); }}
    .player-shell::after {{ content: ""; position: absolute; inset: 0; z-index: -1; background: radial-gradient(circle at 62% 28%, transparent 0 23%, rgba(255,255,255,.32) 24%, transparent 25%), radial-gradient(circle at 62% 28%, transparent 0 38%, rgba(255,255,255,.24) 39%, transparent 40%); }}
    .player-stars {{ position: absolute; inset: 0; pointer-events: none; }}
    .player-stars span {{ position: absolute; width: 9px; height: 9px; background: #fff; clip-path: polygon(50% 0, 63% 36%, 100% 50%, 63% 64%, 50% 100%, 37% 64%, 0 50%, 37% 36%); filter: drop-shadow(0 0 8px rgba(255, 94, 178, .72)); opacity: .9; animation: twinkle 3s ease-in-out infinite; }}
    .player-stars span:nth-child(1) {{ left: 9%; top: 22%; transform: scale(1.5); }}
    .player-stars span:nth-child(2) {{ left: 38%; top: 15%; animation-delay: .5s; }}
    .player-stars span:nth-child(3) {{ left: 81%; top: 18%; transform: scale(1.8); animation-delay: .9s; }}
    .player-stars span:nth-child(4) {{ left: 71%; top: 72%; animation-delay: 1.3s; }}
    .player-stars span:nth-child(5) {{ left: 17%; top: 78%; transform: scale(1.2); animation-delay: 1.8s; }}
    .player-hero {{ position: relative; max-width: 760px; margin: 0 auto 34px; padding: 12px 108px 0; text-align: center; }}
    .player-hero::before {{ content: "✦"; position: absolute; top: 0; right: 0; display: grid; place-items: center; width: 78px; height: 78px; border-radius: 50%; border: 2px solid rgba(255,255,255,.8); background: conic-gradient(from 20deg, #ff73b7, #ffd66b, #8fe8ff, #b896ff, #ff73b7); color: #fff; font-size: 34px; box-shadow: 0 16px 36px rgba(188, 80, 166, .24), inset 0 0 0 8px rgba(255,255,255,.48); }}
    .player-kicker, .player-section-head span, .city-card-label {{ display: block; margin: 0 0 8px; color: #c54793; font-size: 12px; font-weight: 900; text-transform: uppercase; letter-spacing: 0; }}
    .player-hero h1 {{ margin: 0 0 12px; color: #64204f; font-size: clamp(34px, 5vw, 58px); line-height: 1.05; text-shadow: 0 2px 0 #fff, 0 18px 40px rgba(204, 70, 157, .18); }}
    .player-hero p {{ margin: 0 auto; max-width: 45em; color: #67425e; line-height: 1.75; }}
    .player-city-section {{ position: relative; max-width: 1080px; margin: 0 auto; padding: 22px; border: 1px solid rgba(255,255,255,.72); border-radius: 8px; background: rgba(255,255,255,.5); box-shadow: 0 24px 70px rgba(141, 76, 146, .18), inset 0 0 0 1px rgba(255,255,255,.4); backdrop-filter: blur(14px); }}
    .player-section-head {{ display: flex; justify-content: space-between; align-items: end; gap: 18px; margin-bottom: 16px; }}
    .player-section-head h2 {{ margin: 0; color: #652052; font-size: 24px; }}
    .player-city-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
    .player-city-card {{ position: relative; min-height: 188px; display: grid; grid-template-columns: 62px 1fr; grid-template-rows: 1fr auto; gap: 12px; padding: 18px; border: 1px solid rgba(221, 91, 169, .28); border-radius: 8px; background: linear-gradient(180deg, rgba(255,255,255,.9), rgba(255,247,252,.78)); box-shadow: 0 14px 36px rgba(175, 74, 151, .13); overflow: hidden; }}
    .player-city-card::after {{ content: ""; position: absolute; right: -36px; top: -44px; width: 130px; height: 130px; border-radius: 50%; background: radial-gradient(circle, rgba(255, 221, 113, .7), rgba(122, 222, 248, .35) 54%, transparent 56%); }}
    .city-card-orb {{ position: relative; z-index: 1; width: 54px; height: 54px; display: grid; place-items: center; border-radius: 50%; background: linear-gradient(135deg, #ff6bb3, #8ee8ff); color: #fff; font-size: 25px; box-shadow: 0 12px 24px rgba(192, 80, 169, .22); }}
    .city-card-main {{ position: relative; z-index: 1; min-width: 0; }}
    .city-card-main h2 {{ margin: 0 0 6px; color: #5a214e; font-size: 22px; overflow-wrap: anywhere; }}
    .city-card-main p {{ margin: 0; color: #76506c; font-size: 13px; font-weight: 800; overflow-wrap: anywhere; }}
    .city-card-meta {{ display: flex; flex-wrap: wrap; gap: 7px; margin-top: 14px; }}
    .city-card-meta span {{ min-height: 24px; display: inline-flex; align-items: center; padding: 3px 8px; border: 1px solid rgba(211, 91, 165, .24); border-radius: 999px; background: rgba(255,255,255,.72); color: #744160; font-size: 12px; font-weight: 800; }}
    .player-enter-link {{ position: relative; z-index: 1; grid-column: 1 / -1; display: inline-flex; align-items: center; justify-content: center; min-height: 42px; padding: 0 14px; border-radius: 8px; background: linear-gradient(135deg, #ff5fae, #b56bff 52%, #45c9ee); color: #fff; font-weight: 900; box-shadow: 0 14px 28px rgba(180, 70, 176, .22); }}
    .player-enter-link:hover {{ text-decoration: none; filter: brightness(1.04); }}
    .player-empty-state {{ padding: 34px 18px; text-align: center; border: 1px dashed rgba(207, 84, 161, .42); border-radius: 8px; background: rgba(255,255,255,.64); }}
    .player-empty-state div {{ color: #ff62ad; font-size: 44px; }}
    .player-empty-state h2 {{ margin: 4px 0 8px; color: #652052; }}
    .player-empty-state p {{ max-width: 34em; margin: 0 auto; color: #76506c; line-height: 1.7; }}
    main:has(.player-detail-shell) {{ width: 100%; max-width: none; min-height: 100vh; box-sizing: border-box; padding: 0; overflow: hidden; }}
    main:has(.player-detail-shell) .topbar {{ position: absolute; top: 18px; right: 22px; z-index: 5; margin: 0; min-height: 0; }}
    main:has(.player-detail-shell) .topbar button {{ border: 1px solid rgba(255,255,255,.68); background: rgba(112, 72, 156, .72); box-shadow: 0 12px 28px rgba(108, 53, 133, .18); backdrop-filter: blur(10px); }}
    .player-detail-shell {{ --player-detail-width: 1180px; position: relative; min-height: 100vh; padding: 70px clamp(18px, 5vw, 72px) 58px; box-sizing: border-box; overflow: hidden; background: radial-gradient(circle at 13% 16%, rgba(255, 241, 151, .82) 0 7%, transparent 21%), radial-gradient(circle at 82% 18%, rgba(139, 229, 255, .72) 0 8%, transparent 22%), radial-gradient(circle at 80% 82%, rgba(255, 139, 200, .52) 0 11%, transparent 25%), linear-gradient(135deg, #fff5fb 0%, #f7ddff 30%, #dff7ff 66%, #fff6c7 100%); color: #42233f; isolation: isolate; }}
    .player-detail-shell::before {{ content: ""; position: absolute; inset: -20%; z-index: -2; background-image: linear-gradient(rgba(255,255,255,.48) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.42) 1px, transparent 1px); background-size: 42px 42px; transform: rotate(-7deg); }}
    .player-detail-shell::after {{ content: ""; position: absolute; inset: 0; z-index: -1; background: radial-gradient(circle at 50% 18%, transparent 0 22%, rgba(255,255,255,.32) 23%, transparent 24%), radial-gradient(circle at 50% 18%, transparent 0 39%, rgba(255,255,255,.23) 40%, transparent 41%); }}
    .player-detail-hero {{ position: relative; width: 100%; max-width: var(--player-detail-width); box-sizing: border-box; margin: 0 auto 24px; padding: 32px 128px 30px; border: 1px solid rgba(255,255,255,.72); border-radius: 8px; background: rgba(255,255,255,.48); box-shadow: 0 24px 70px rgba(141, 76, 146, .16), inset 0 0 0 1px rgba(255,255,255,.42); backdrop-filter: blur(14px); text-align: center; }}
    .player-back-link {{ position: absolute; left: 18px; top: 18px; display: inline-flex; align-items: center; min-height: 32px; padding: 0 12px; border: 1px solid rgba(212, 93, 166, .28); border-radius: 999px; background: rgba(255,255,255,.68); color: #8d3975; font-size: 13px; font-weight: 900; }}
    .player-back-link:hover {{ text-decoration: none; background: rgba(255,255,255,.9); }}
    .player-detail-emblem {{ position: absolute; top: 18px; right: 24px; width: 86px; height: 86px; display: grid; place-items: center; border-radius: 50%; border: 2px solid rgba(255,255,255,.82); background: conic-gradient(from 20deg, #ff73b7, #ffd66b, #8fe8ff, #b896ff, #ff73b7); color: #fff; font-size: 38px; box-shadow: 0 16px 36px rgba(188, 80, 166, .24), inset 0 0 0 8px rgba(255,255,255,.48); }}
    .player-detail-hero h1 {{ margin: 0 0 12px; color: #64204f; font-size: clamp(34px, 5vw, 62px); line-height: 1.05; text-shadow: 0 2px 0 #fff, 0 18px 40px rgba(204, 70, 157, .18); overflow-wrap: anywhere; }}
    .player-detail-hero p {{ margin: 0 auto; max-width: 50em; color: #67425e; line-height: 1.75; }}
    .player-hero-tags {{ display: flex; justify-content: center; flex-wrap: wrap; gap: 8px; margin-top: 18px; }}
    .player-hero-tags span {{ min-height: 28px; display: inline-flex; align-items: center; padding: 3px 10px; border: 1px solid rgba(211, 91, 165, .26); border-radius: 999px; background: rgba(255,255,255,.72); color: #744160; font-size: 13px; font-weight: 900; }}
    .player-detail-hero .player-top-grid {{ max-width: 760px; margin: 22px auto 0; }}
    .player-detail-hero .player-top-item {{ min-height: 82px; background: rgba(255,255,255,.58); box-shadow: inset 0 0 0 1px rgba(255,255,255,.42); text-align: left; }}
    .player-detail-layout {{ width: 100%; max-width: var(--player-detail-width); margin: 0 auto; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .player-detail-flow {{ width: 100%; max-width: var(--player-detail-width); margin: 0 auto; display: grid; gap: 14px; }}
    .player-top-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .player-top-item {{ min-height: 96px; padding: 18px 20px; border: 1px solid rgba(221, 91, 169, .28); border-radius: 8px; background: linear-gradient(180deg, rgba(255,255,255,.92), rgba(255,247,252,.8)); box-shadow: 0 14px 36px rgba(175, 74, 151, .12); }}
    .player-top-item span {{ display: block; color: #c54793; font-size: 12px; font-weight: 900; }}
    .player-top-item strong {{ display: block; margin-top: 8px; color: #4b2447; font-size: clamp(20px, 2.5vw, 28px); line-height: 1.18; overflow-wrap: anywhere; }}
    .player-split-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .player-profile-triad {{ width: 100%; box-sizing: border-box; display: grid; grid-template-columns: minmax(230px, .92fr) minmax(360px, 1.28fr) minmax(230px, .92fr); gap: 14px; align-items: stretch; }}
    .player-side-stack {{ display: grid; grid-template-rows: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; align-self: stretch; }}
    .player-profile-main, .player-profile-side {{ display: grid; align-self: stretch; }}
    .player-profile-triad .player-profile-card {{ min-height: 0; padding: 16px; }}
    .player-profile-triad .profile-card-head {{ margin-bottom: 10px; }}
    .player-profile-triad .profile-card-head h2 {{ font-size: 21px; }}
    .player-profile-triad .player-info-grid {{ gap: 8px; }}
    .player-side-stack .player-info-grid, .player-profile-side .player-info-grid {{ grid-template-columns: 1fr; }}
    .player-profile-main .player-info-grid {{ grid-template-columns: 1fr; }}
    .player-profile-triad .player-info-item {{ min-height: 50px; padding: 8px 10px; }}
    .player-profile-triad .player-info-item span {{ font-size: 11px; }}
    .player-profile-triad .player-info-item strong {{ margin-top: 3px; font-size: 14px; line-height: 1.35; }}
    .player-profile-card, .player-site-section {{ position: relative; box-sizing: border-box; padding: 20px; border: 1px solid rgba(221, 91, 169, .28); border-radius: 8px; background: linear-gradient(180deg, rgba(255,255,255,.9), rgba(255,247,252,.78)); box-shadow: 0 14px 36px rgba(175, 74, 151, .13); overflow: hidden; }}
    .primary-profile-card {{ grid-row: span 2; }}
    .profile-card-head {{ margin-bottom: 14px; }}
    .profile-card-head span {{ display: block; margin: 0 0 6px; color: #c54793; font-size: 12px; font-weight: 900; text-transform: uppercase; letter-spacing: 0; }}
    .profile-card-head h2 {{ margin: 0; color: #652052; font-size: 24px; }}
    .player-info-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .player-info-item {{ min-height: 64px; padding: 11px 12px; border: 1px solid rgba(211, 91, 165, .22); border-radius: 8px; background: rgba(255,255,255,.62); }}
    .player-info-item span, .player-state-item span {{ display: block; color: #9b477d; font-size: 12px; font-weight: 900; }}
    .player-info-item strong, .player-state-item strong {{ display: block; margin-top: 5px; color: #4b2447; font-size: 15px; line-height: 1.45; overflow-wrap: anywhere; }}
    .player-site-section {{ max-width: 1180px; margin: 14px auto 0; }}
    .player-site-section .detail-panel {{ border-color: rgba(221, 91, 169, .22); background: rgba(255,255,255,.58); box-shadow: none; }}
    .player-site-section .progress-name {{ color: #5a214e; }}
    .player-site-section .progress-name::before {{ border-color: #ffd8eb; border-top-color: #ff5fae; }}
    .player-site-section .progress-fill {{ background: linear-gradient(90deg, #ff5fae, #45c9ee); box-shadow: 0 0 10px rgba(255, 95, 174, .22); }}
    .player-state-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    .player-state-item {{ min-height: 64px; padding: 11px 12px; border: 1px solid rgba(211, 91, 165, .22); border-radius: 8px; background: rgba(255,255,255,.62); }}
    .player-memory-grid {{ max-width: 1180px; margin: 14px auto 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .player-memory-grid .player-site-section {{ margin: 0; }}
    .player-detail-flow > .player-site-section {{ width: 100%; max-width: none; margin: 0; }}
    .player-memory-row {{ max-width: none; margin: 0; }}
    .player-memory-row .log-card-summary {{ padding: 12px 14px; }}
    .player-memory-row .log-card-body {{ padding: 0 14px 14px; }}
    .player-detail-shell .log-card {{ border-color: rgba(221, 91, 169, .22); background: rgba(255,255,255,.72); box-shadow: 0 8px 22px rgba(175, 74, 151, .08); }}
    .player-detail-shell .log-card-summary, .player-detail-shell .log-card[open] .log-card-summary {{ background: rgba(255,255,255,.62); }}
    .player-site-empty {{ margin: 0; padding: 18px; border: 1px dashed rgba(207, 84, 161, .42); border-radius: 8px; background: rgba(255,255,255,.64); color: #76506c; }}
    label {{ display: block; margin: 18px 0 8px; font-weight: 700; color: #303846; }}
    input[type="text"] {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #c8d0dc; border-radius: 7px; font: inherit; background: #fbfdff; }}
    input[type="number"] {{ width: 76px; box-sizing: border-box; padding: 7px 9px; border: 1px solid #c8d0dc; border-radius: 7px; font: inherit; background: #fbfdff; }}
    textarea {{ width: 100%; resize: vertical; padding: 12px; border: 1px solid #c8d0dc; border-radius: 7px; font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; font-size: 13px; line-height: 1.5; box-sizing: border-box; background: #fbfdff; transition: border-color .15s ease, box-shadow .15s ease; }}
    textarea:focus, input:focus, select:focus {{ outline: none; border-color: #1f6feb !important; box-shadow: 0 0 0 3px rgba(31, 111, 235, 0.13); }}
    textarea.note-editor {{ min-height: 132px; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    textarea.content-editor {{ min-height: 58vh; }}
    textarea.source-editor {{ min-height: 62vh; }}
    button {{ margin-top: 12px; padding: 9px 16px; border: 0; border-radius: 6px; background: #1f6feb; color: #fff; font-weight: 700; cursor: pointer; }}
    button.secondary {{ background: #59636e; }}
    button.danger {{ margin-top: 0; background: #b42318; }}
    button.danger:hover {{ background: #931f15; }}
    .actions {{ display: flex; gap: 10px; align-items: center; }}
    .inline-label {{ display: inline; margin: 0; }}
    .error {{ color: #b42318; font-weight: 700; }}
    main:has(.login-shell) {{ width: 100%; max-width: none; min-height: 100vh; box-sizing: border-box; padding: 0; overflow: hidden; }}
    main:has(.login-shell) .topbar {{ display: none; }}
    .login-shell {{ position: relative; min-height: 100vh; display: grid; grid-template-columns: minmax(320px, 480px) minmax(320px, 1fr); align-items: center; gap: 42px; padding: 46px clamp(22px, 6vw, 86px); box-sizing: border-box; overflow: hidden; background: radial-gradient(circle at 18% 16%, rgba(255, 247, 177, .96) 0 6%, transparent 19%), radial-gradient(circle at 82% 22%, rgba(169, 236, 255, .76) 0 7%, transparent 20%), radial-gradient(circle at 74% 76%, rgba(255, 156, 208, .5) 0 10%, transparent 24%), linear-gradient(135deg, #fff5fb 0%, #fee3f2 28%, #dff6ff 62%, #fff8c9 100%); color: #42233f; isolation: isolate; }}
    .login-shell::before {{ content: ""; position: absolute; inset: -18%; z-index: -2; background-image: linear-gradient(rgba(255,255,255,.58) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.46) 1px, transparent 1px); background-size: 44px 44px; transform: rotate(-9deg); mask-image: radial-gradient(circle at 50% 50%, #000 0 58%, transparent 76%); }}
    .login-shell::after {{ content: ""; position: absolute; inset: 0; z-index: -1; background: radial-gradient(circle at 50% 42%, transparent 0 30%, rgba(255,255,255,.34) 31%, transparent 32%), radial-gradient(circle at 50% 42%, transparent 0 43%, rgba(255,255,255,.3) 44%, transparent 45%), radial-gradient(circle at 50% 42%, transparent 0 55%, rgba(255,255,255,.22) 56%, transparent 57%); opacity: .9; }}
    .sparkles {{ position: absolute; inset: 0; pointer-events: none; }}
    .sparkles span {{ position: absolute; width: 8px; height: 8px; background: #fff; clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); filter: drop-shadow(0 0 8px rgba(255, 106, 180, .72)); opacity: .9; animation: twinkle 2.8s ease-in-out infinite; }}
    .sparkles span:nth-child(1) {{ left: 10%; top: 18%; transform: scale(1.6); }}
    .sparkles span:nth-child(2) {{ left: 42%; top: 12%; animation-delay: .4s; }}
    .sparkles span:nth-child(3) {{ left: 83%; top: 15%; transform: scale(2); animation-delay: .9s; }}
    .sparkles span:nth-child(4) {{ left: 64%; top: 72%; transform: scale(1.4); animation-delay: 1.2s; }}
    .sparkles span:nth-child(5) {{ left: 17%; top: 78%; transform: scale(1.1); animation-delay: 1.7s; }}
    .sparkles span:nth-child(6) {{ left: 92%; top: 58%; transform: scale(1.3); animation-delay: 2.1s; }}
    .login-panel {{ position: relative; z-index: 2; max-width: 480px; margin: 0; padding: 34px; border: 2px solid rgba(255, 255, 255, .76); border-radius: 8px; background: linear-gradient(180deg, rgba(255,255,255,.9), rgba(255,246,252,.84)); box-shadow: 0 28px 70px rgba(167, 76, 146, .24), inset 0 0 0 1px rgba(255, 156, 208, .25); backdrop-filter: blur(18px); }}
    @media (min-width: 861px) {{ .login-panel {{ grid-column: 2; justify-self: start; }} .login-vision {{ grid-column: 1; grid-row: 1; justify-self: end; }} }}
    .login-panel::before {{ content: ""; position: absolute; inset: 10px; border: 1px dashed rgba(231, 104, 178, .45); border-radius: 8px; pointer-events: none; }}
    .login-badge {{ width: 82px; height: 82px; display: grid; place-items: center; margin-bottom: 18px; border: 2px solid rgba(255,255,255,.82); border-radius: 50%; background: conic-gradient(from 18deg, #ff73b7, #ffd66b, #8fe8ff, #b896ff, #ff73b7); box-shadow: 0 14px 30px rgba(255, 104, 181, .34), inset 0 0 0 8px rgba(255,255,255,.5); color: #fff; font-size: 38px; text-shadow: 0 2px 10px rgba(128, 35, 119, .42); }}
    .badge-star {{ display: inline-block; transform: translateY(-1px); }}
    .login-kicker {{ margin: 0 0 7px; color: #cc4e98; font-size: 12px; font-weight: 900; text-transform: uppercase; letter-spacing: .14em; }}
    .login-panel h1 {{ margin: 0 0 10px; color: #652052; font-size: clamp(31px, 4.6vw, 48px); line-height: 1.05; text-shadow: 0 2px 0 #fff, 0 12px 28px rgba(209, 70, 150, .18); }}
    .login-copy {{ max-width: 35em; margin: 0 0 20px; color: #694260; line-height: 1.75; }}
    .login-panel label {{ margin: 18px 0 9px; color: #743063; font-size: 14px; }}
    .magic-input {{ display: grid; grid-template-columns: 42px 1fr; align-items: center; border: 1px solid rgba(219, 83, 165, .42); border-radius: 8px; background: rgba(255,255,255,.84); box-shadow: inset 0 0 0 1px rgba(255,255,255,.7), 0 12px 28px rgba(196, 77, 156, .12); overflow: hidden; }}
    .magic-input span {{ display: grid; place-items: center; min-height: 48px; color: #d854a2; font-size: 22px; background: linear-gradient(180deg, rgba(255, 238, 248, .9), rgba(255, 255, 255, .45)); }}
    .magic-input input[type="text"] {{ min-height: 48px; padding: 12px 14px 12px 0; border: 0; border-radius: 0; background: transparent; color: #4b2847; }}
    .magic-input:focus-within {{ border-color: #ff66b3; box-shadow: 0 0 0 4px rgba(255, 102, 179, .18), 0 18px 36px rgba(196, 77, 156, .18); }}
    .magic-input:focus-within input {{ box-shadow: none !important; }}
    .login-actions {{ margin-top: 18px; }}
    button.login-button {{ width: 100%; min-height: 50px; margin-top: 0; border: 1px solid rgba(255,255,255,.72); border-radius: 8px; background: linear-gradient(135deg, #ff5fae, #b56bff 52%, #45c9ee); box-shadow: 0 16px 32px rgba(180, 70, 176, .28), inset 0 1px 0 rgba(255,255,255,.42); color: #fff; font-size: 16px; text-shadow: 0 1px 8px rgba(89, 31, 116, .35); }}
    button.login-button:hover {{ filter: brightness(1.04); transform: translateY(-1px); }}
    .login-runes {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }}
    .login-runes span {{ padding: 6px 10px; border: 1px solid rgba(209, 84, 162, .28); border-radius: 999px; background: rgba(255,255,255,.54); color: #9b3d82; font-size: 12px; font-weight: 800; }}
    .login-panel .error {{ margin: 14px 0 0; padding: 10px 12px; border: 1px solid rgba(225, 71, 111, .3); border-radius: 8px; background: rgba(255, 239, 245, .86); color: #b42360; }}
    .login-vision {{ position: relative; z-index: 1; min-height: 560px; display: grid; place-items: center; }}
    .moon {{ position: absolute; right: 11%; top: 11%; width: 120px; height: 120px; border-radius: 50%; background: #fff9c9; box-shadow: 0 0 42px rgba(255, 247, 166, .9), inset -22px -8px 0 rgba(255, 210, 237, .75); }}
    .magic-circle {{ position: relative; width: min(52vw, 520px); aspect-ratio: 1; display: grid; place-items: center; border-radius: 50%; background: radial-gradient(circle, rgba(255,255,255,.74) 0 16%, transparent 17%), conic-gradient(from 0deg, rgba(255,102,179,.2), rgba(96,211,242,.24), rgba(255,216,103,.24), rgba(181,107,255,.22), rgba(255,102,179,.2)); box-shadow: 0 0 0 2px rgba(255,255,255,.72), 0 0 52px rgba(255, 93, 174, .33); }}
    .magic-circle::before, .magic-circle::after {{ content: ""; position: absolute; inset: 12%; border-radius: 50%; border: 2px solid rgba(255,255,255,.75); box-shadow: inset 0 0 30px rgba(255, 255, 255, .35); }}
    .magic-circle::after {{ inset: 25%; border-style: dashed; transform: rotate(24deg); }}
    .circle-core {{ position: relative; z-index: 2; width: 132px; height: 132px; display: grid; place-items: center; border-radius: 50%; background: linear-gradient(135deg, #fff, #ffe4f2); color: #ff5fae; font-size: 68px; box-shadow: 0 20px 42px rgba(167, 76, 146, .24); }}
    .orbit {{ position: absolute; border: 2px solid rgba(255,255,255,.62); border-radius: 50%; transform: rotate(var(--tilt)); }}
    .orbit::before {{ content: "✦"; position: absolute; top: -14px; left: 50%; color: #fff; font-size: 22px; filter: drop-shadow(0 0 8px #ff69b4); }}
    .orbit-one {{ --tilt: 18deg; width: 82%; height: 36%; }}
    .orbit-two {{ --tilt: -31deg; width: 90%; height: 42%; }}
    .orbit-three {{ --tilt: 68deg; width: 78%; height: 32%; }}
    .wand {{ position: absolute; right: 7%; bottom: 16%; width: 250px; height: 250px; transform: rotate(-26deg); }}
    .wand-star {{ position: absolute; right: 38px; top: 4px; color: #fff; font-size: 86px; line-height: 1; text-shadow: 0 0 18px #ff55af, 0 0 38px rgba(255, 214, 107, .9); }}
    .wand-stick {{ position: absolute; right: 86px; top: 78px; width: 18px; height: 190px; border-radius: 999px; background: linear-gradient(180deg, #fff, #ffb8dc 48%, #8ee7ff); box-shadow: 0 12px 28px rgba(113, 66, 158, .22); }}
    @keyframes twinkle {{ 0%, 100% {{ opacity: .4; transform: scale(.72) rotate(0deg); }} 50% {{ opacity: 1; transform: scale(1.35) rotate(28deg); }} }}
    .danger-zone {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 16px 0 22px; padding: 14px 16px; border: 1px solid #f0b8b0; border-radius: 8px; background: #fff5f3; color: #6f1d15; }}
    .danger-zone span {{ color: #7a3b34; }}
    .world-book-toolbar {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin: 16px 0 8px; padding: 10px 12px; border: 1px solid #d9e1eb; border-radius: 8px; background: rgba(255,255,255,0.86); box-shadow: 0 6px 14px rgba(31, 41, 55, 0.05); }}
    .world-book-toolbar h2 {{ margin: 0 0 3px; }}
    .world-book-toolbar p {{ margin: 0; }}
    .book-config-grid {{ display: grid; grid-template-columns: minmax(180px, .72fr) minmax(260px, 1.28fr); gap: 12px; align-items: start; }}
    #world-book-entries {{ display: grid; gap: 6px; }}
    #monster-book-entries {{ display: grid; gap: 6px; }}
    .world-entry {{ margin: 0; background: #fff; border: 1px solid #dde2ea; border-radius: 8px; box-shadow: 0 3px 10px rgba(31, 41, 55, 0.04); transition: transform .14s ease, box-shadow .14s ease, border-color .14s ease, opacity .14s ease; }}
    .world-entry.dragging {{ opacity: .45; transform: scale(.995); }}
    .world-entry.drag-over {{ border-color: #1f6feb; box-shadow: 0 0 0 3px rgba(31, 111, 235, 0.14), 0 12px 28px rgba(31, 41, 55, 0.1); }}
    .region-block {{ margin: 0 0 12px; background: #fff; border: 2px solid #c8d6e5; border-radius: 10px; box-shadow: 0 3px 10px rgba(31, 41, 55, 0.06); transition: transform .14s ease, box-shadow .14s ease, border-color .14s ease, opacity .14s ease; }}
    .region-block.dragging {{ opacity: .45; transform: scale(.995); }}
    .region-block.drag-over {{ border-color: #1f6feb; box-shadow: 0 0 0 3px rgba(31, 111, 235, 0.14), 0 12px 28px rgba(31, 41, 55, 0.1); }}
    .region-block > details {{ padding: 0; }}
    .region-head {{ background: linear-gradient(180deg, #eef4fa, #dde6f0); }}
    .region-body {{ padding: 10px; }}
    .region-entries {{ margin-top: 8px; }}
    .region-entries .rb-entry {{ margin-bottom: 6px; }}
    .world-entry details {{ padding: 0; }}
    .world-entry-head {{ display: flex; gap: 8px; align-items: center; padding: 7px 10px; cursor: pointer; background: linear-gradient(180deg, #f8fafc, #eef4fa); border-radius: 8px; }}
    .world-entry details[open] .world-entry-head {{ border-bottom: 1px solid #dde2ea; border-radius: 8px 8px 0 0; }}
    .world-entry-head .entry-title {{ font-weight: 800; margin-right: auto; color: #172033; }}
    .drag-handle {{ flex: 0 0 auto; width: 26px; height: 26px; margin: 0; padding: 0; border-radius: 6px; border: 1px solid #c8d0dc; background: #fff; color: #536172; cursor: grab; font-size: 15px; line-height: 1; }}
    .drag-handle:active {{ cursor: grabbing; }}
    .summary-check {{ display: inline-flex; align-items: center; gap: 5px; margin: 0; font-size: 13px; font-weight: 700; cursor: default; }}
    .summary-check input {{ width: 15px; height: 15px; }}
    .world-entry-body {{ padding: 10px; background: #fff; border-radius: 0 0 8px 8px; }}
    .world-entry-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }}
    .title-field {{ grid-column: span 1; }}
    .level-field {{ grid-column: span 2; }}
    .level-choice-row {{ display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; flex: 1 1 auto; min-width: 0; padding: 8px 10px; border: 1px solid #d9e1eb; border-radius: 8px; background: #f8fafc; }}
    .level-choice {{ display: inline-flex; align-items: center; gap: 4px; margin: 0; white-space: nowrap; }}
    .compact-field {{ display: flex; align-items: center; gap: 6px; margin: 0; }}
    .compact-field span {{ flex: 0 0 auto; color: #3a4350; }}
    .world-entry input[type="text"], .world-entry input[type="number"], .world-entry select {{ width: 100%; min-width: 0; box-sizing: border-box; padding: 6px 8px; border: 1px solid #c8d0dc; border-radius: 7px; font: inherit; background: #fbfdff; }}
    .block-field {{ margin-top: 12px; }}
    textarea.keys-editor {{ min-height: 48px; font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }}
    textarea.entry-content-editor {{ min-height: 78px; }}
    .status-level-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; }}
    .monster-level-settings {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; margin-top: 12px; }}
    .monster-level-block {{ padding: 10px; border: 1px solid #dde2ea; border-radius: 8px; background: #f8fafc; }}
    .monster-level-block h3 {{ margin: 0 0 6px; font-size: 15px; }}
    .hero-card {{ display: flex; gap: 20px; align-items: center; margin: 18px 0 18px; padding: 20px; border: 1px solid #d9e1eb; border-radius: 8px; background: #fff; box-shadow: 0 10px 24px rgba(31, 41, 55, 0.06); }}
    .avatar-large {{ width: 92px; height: 92px; flex: 0 0 auto; display: grid; place-items: center; overflow: hidden; border-radius: 8px; border: 1px solid #d8e0eb; background: #f0f4f8; color: #59636e; font-size: 34px; font-weight: 900; }}
    .avatar-large img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
    .hero-main h2 {{ margin: 4px 0 6px; font-size: 22px; }}
    .kicker {{ color: #68707d; font-size: 13px; }}
    .subtitle {{ margin: 0 0 12px; color: #3a4350; }}
    .identity-line {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .identity-line span, .tag {{ display: inline-flex; align-items: center; min-height: 26px; padding: 3px 9px; border-radius: 6px; background: #eef4fa; border: 1px solid #d8e0eb; color: #263241; font-size: 13px; font-weight: 700; }}
    .detail-grid {{ display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr); gap: 16px; margin: 16px 0; }}
    .detail-panel, .raw-panel, .log-card {{ border: 1px solid #dde2ea; border-radius: 8px; background: #fff; box-shadow: 0 8px 22px rgba(31, 41, 55, 0.05); }}
    .detail-panel {{ padding: 18px; }}
    .detail-panel h2 {{ margin-top: 0; }}
    .profile-field {{ padding: 12px 0; border-top: 1px solid #edf1f5; }}
    .profile-field:first-of-type {{ border-top: 0; }}
    .profile-field span {{ display: block; margin-bottom: 5px; color: #68707d; font-size: 13px; font-weight: 800; }}
    .profile-field p {{ margin: 0; line-height: 1.7; }}
    .profile-edit-panel {{ align-self: start; }}
    .profile-edit-head {{ margin-bottom: 8px; align-items: center; }}
    .profile-edit-head h2 {{ margin: 0; }}
    .profile-edit-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 12px; }}
    .profile-edit-field {{ margin: 10px 0 0; }}
    .profile-edit-field span {{ display: block; margin-bottom: 5px; color: #68707d; font-size: 13px; font-weight: 800; }}
    .profile-edit-field input[type="text"] {{ padding: 8px 10px; min-height: 38px; }}
    textarea.profile-edit-textarea {{ min-height: 72px; resize: vertical; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    textarea.profile-edit-textarea.monospace-editor {{ min-height: 96px; font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }}
    .meta-list span {{ display: block; color: #68707d; font-size: 12px; font-weight: 800; }}
    .tag-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }}
    .meta-list {{ display: grid; gap: 8px; margin-top: 16px; }}
    .meta-list div {{ display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-top: 1px solid #edf1f5; }}
    .state-overview-panel {{ margin-top: 16px; }}
    .state-overview-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    .state-item {{ min-height: 64px; padding: 11px 12px; border: 1px solid #dde2ea; border-radius: 8px; background: #f8fafc; }}
    .state-item span {{ display: block; color: #68707d; font-size: 12px; font-weight: 800; overflow-wrap: anywhere; }}
    .state-item strong {{ display: block; margin-top: 4px; color: #172033; font-size: 17px; overflow-wrap: anywhere; }}
    .progress-overview-panel {{ margin: 16px 0; }}
    .progress-overview-panel h2 {{ color: #172033; }}
    .progress-list {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 15px 22px; }}
    .progress-row {{ min-width: 0; }}
    .progress-head {{ display: grid; grid-template-columns: 1fr auto; gap: 14px; align-items: baseline; margin-bottom: 7px; }}
    .progress-name {{ display: flex; align-items: baseline; gap: 9px; min-width: 0; color: #172033; font-size: 18px; font-weight: 900; overflow-wrap: anywhere; }}
    .progress-name::before {{ content: ""; width: 16px; height: 16px; flex: 0 0 auto; border: 3px solid #c8d0dc; border-top-color: #1f6feb; border-radius: 50%; }}
    .progress-level {{ color: #68707d; font-size: 12px; font-weight: 800; white-space: nowrap; }}
    .progress-xp {{ color: #1f6feb; font-size: 15px; font-weight: 900; white-space: nowrap; }}
    .progress-xp small {{ color: #68707d; font-size: 12px; }}
    .progress-track {{ width: 100%; height: 7px; overflow: hidden; background: #e7edf5; border-radius: 999px; }}
    .progress-fill {{ height: 100%; border-radius: inherit; background: #1f6feb; box-shadow: 0 0 8px rgba(31, 111, 235, 0.22); }}
    .section-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }}
    .section-head h2 {{ margin-bottom: 4px; }}
    .log-list {{ display: grid; gap: 12px; }}
    .log-card {{ padding: 0; overflow: hidden; }}
    .log-card-summary {{ display: block; padding: 15px; cursor: pointer; background: #fff; }}
    .log-card-summary::-webkit-details-marker {{ display: none; }}
    .log-card-summary::marker {{ content: ""; }}
    .log-card[open] .log-card-summary {{ border-bottom: 1px solid #edf1f5; background: #f8fafc; }}
    .log-card-body {{ padding: 0 15px 15px; }}
    .log-card-body .log-meta {{ margin-top: 12px; }}
    .log-card-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }}
    .log-card h3 {{ margin: 2px 0 0; font-size: 17px; }}
    .log-index {{ color: #68707d; font-size: 12px; font-weight: 800; }}
    .log-meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; color: #59636e; font-size: 13px; }}
    .summary-meta {{ margin-bottom: 0; }}
    .log-meta span {{ padding: 3px 8px; border-radius: 6px; background: #f3f6fb; border: 1px solid #e1e7ef; }}
    .log-action {{ margin: 10px 0 6px; color: #303846; font-weight: 700; }}
    .log-result {{ margin: 0; line-height: 1.7; color: #263241; }}
    .raw-grid {{ grid-template-columns: 1fr 1fr; }}
    .raw-panel {{ padding: 0; overflow: hidden; }}
    .raw-panel-head {{ display: flex; justify-content: space-between; gap: 10px; align-items: center; padding: 10px 15px; border-bottom: 1px solid #edf1f5; }}
    .raw-panel-wrapper details summary {{ border-top: 0; }}
    .raw-panel summary {{ padding: 13px 15px; cursor: pointer; font-weight: 800; background: #f8fafc; }}
    .raw-panel pre {{ margin: 0; border-radius: 0; }}
    .empty-state {{ padding: 18px; background: #fff; border: 1px dashed #c8d0dc; border-radius: 8px; }}
    @media (max-width: 900px) {{ .world-entry-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .level-field {{ grid-column: 1 / -1; }} }}
    @media (max-width: 900px) {{ .state-overview-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 900px) {{ .detail-grid, .raw-grid {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 960px) {{ .player-detail-layout, .player-profile-triad, .player-split-grid, .player-memory-grid {{ grid-template-columns: 1fr; }} .player-profile-main {{ order: -1; }} .player-side-stack {{ grid-template-rows: auto; }} .player-profile-main .player-info-grid {{ grid-template-columns: 1fr; }} .primary-profile-card {{ grid-row: auto; }} .player-state-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 720px) {{ .player-shell {{ padding: 76px 16px 34px; }} .player-hero {{ padding: 6px 74px 0 0; text-align: left; margin-bottom: 22px; }} .player-hero::before {{ width: 60px; height: 60px; font-size: 27px; }} .player-city-section {{ padding: 16px; }} .player-section-head {{ display: block; }} .player-city-card {{ grid-template-columns: 52px 1fr; padding: 15px; }} .city-card-orb {{ width: 46px; height: 46px; }} }}
    @media (max-width: 720px) {{ .player-detail-shell {{ padding: 76px 16px 34px; }} .player-detail-hero {{ padding: 58px 82px 22px 18px; text-align: left; }} .player-detail-emblem {{ top: 16px; right: 16px; width: 58px; height: 58px; font-size: 27px; }} .player-back-link {{ left: 14px; top: 14px; }} .player-hero-tags {{ justify-content: flex-start; }} .player-detail-hero .player-top-grid {{ grid-template-columns: 1fr; }} .player-info-grid, .player-state-grid {{ grid-template-columns: 1fr; }} .player-profile-card, .player-site-section {{ padding: 16px; }} }}
    @media (max-width: 560px) {{ .state-overview-grid {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 560px) {{ .progress-list {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 560px) {{ .profile-edit-grid {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 720px) {{ .world-entry-grid, .book-config-grid {{ grid-template-columns: 1fr; }} .world-book-toolbar {{ flex-direction: column; }} .world-entry-head {{ flex-wrap: wrap; }} .hero-card {{ align-items: flex-start; }} .log-card-head {{ flex-direction: column; }} }}
    pre {{ overflow: auto; padding: 14px; background: #111827; color: #d1e7dd; border-radius: 6px; line-height: 1.45; }}
  </style>
</head>
<body><main><div class="topbar">{logout_html}</div>{content}</main></body>
</html>""",
            content_type="text/html",
        )

    @staticmethod
    def _e(value: object) -> str:
        return html.escape(str(value or ""), quote=True)

    def _e_json(self, value: Any) -> str:
        import json

        return self._e(json.dumps(value, ensure_ascii=False, indent=2))

    @staticmethod
    def _format_time(value: object) -> str:
        try:
            import datetime as dt

            timestamp = int(value or 0) / 1000
            if timestamp <= 0:
                return ""
            return dt.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""

    @staticmethod
    def _world_date_display(item: dict[str, Any]) -> str:
        start = str(item.get("world_date_from") or "").strip()
        end = str(item.get("world_date_to") or "").strip()
        if start and end:
            return start if start == end else f"{start} 到 {end}"
        value = str(item.get("world_date") or "").strip()
        if value:
            return value
        if item.get("world_date_unknown"):
            return "历史日期未知"
        return ""

    def _is_editable_file(self, file_id: str) -> bool:
        return file_id in {
            item["id"] for item in self.editable_manager.list_editable_files()
        }

    @staticmethod
    def _is_structured_book_file(file_id: str) -> bool:
        return file_id in {
            "world_book/default.json",
            "status_book/default.json",
            "skill_book/default.json",
            "fetish_book/default.json",
            "event_book/default.json",
            "monster_book/default.json",
        }

    def _editable_rows(self, items: list[dict[str, str]], category: str) -> list[str]:
        rows = []
        for item in items:
            item_category = item.get("category") or "other"
            if item_category not in {"world_background", "text_completion"}:
                item_category = "other"
            if item_category != category:
                continue

            raw_file_id = item["id"]
            file_id = self._e(raw_file_id)
            label = self._e(item["label"])
            file_type = self._e(item["type"])
            note_preview = self._e(item.get("note_preview", ""))
            href = self._url(f"/editable/file?id={quote(raw_file_id, safe='')}")
            rows.append(
                "<tr>"
                f"<td><a href=\"{href}\">{label}</a></td>"
                f"<td>{file_id}</td>"
                f"<td>{file_type}</td>"
                f"<td>{note_preview}</td>"
                "</tr>"
            )
        return rows

    def _editable_table(
        self,
        description: str,
        rows: list[str],
    ) -> str:
        body = "".join(rows) or "<tr><td colspan=\"4\">没有可编辑资源。</td></tr>"
        return f"""
              <p class="muted">{self._e(description)}</p>
              <table>
                <thead><tr><th>名称</th><th>文件</th><th>类型</th><th>说明</th></tr></thead>
                <tbody>{body}</tbody>
              </table>
        """

    def _editable_file_meta(self, file_id: str) -> dict[str, str] | None:
        for item in self.editable_manager.list_editable_files():
            if item["id"] == file_id:
                return item
        return None

    def _editable_back_category(self, category: str | None, file_id: str) -> str:
        if category in {"world_background", "text_completion"}:
            return str(category)
        meta = self._editable_file_meta(file_id)
        if meta and meta.get("category") in {"world_background", "text_completion"}:
            return str(meta["category"])
        return "world_background"

    @staticmethod
    def _editable_category_title(category: str) -> str:
        if category == "text_completion":
            return "文本补全"
        return "世界背景"
