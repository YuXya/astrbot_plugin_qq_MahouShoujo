from __future__ import annotations

import html
import hmac
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import quote

from aiohttp import web

from ...utils.logger import logger
from ..storage.recent_llm_message_repository import RecentLLMMessageRepository
from ..storage.state_progress import (
    build_progress_sections,
    build_state_display_items,
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
        self._add_route(app, "GET", "/player/relationships", self._player_relationships)
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
            raw_response = str(record.get("raw_response") or "")
            raw_response_html = (
                f"""
                    <h2>AI 原始响应对象</h2>
                    <pre>{self._llm_record_pre(raw_response)}</pre>
                """
                if raw_response
                else ""
            )
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
                    <pre>{self._llm_record_pre(record.get("system_prompt") or "（无）")}</pre>
                    <h2>发送给 AI 的完整消息</h2>
                    <pre>{self._llm_record_pre(record.get("prompt") or "")}</pre>
                    <h2>AI 文本回复</h2>
                    <pre>{self._llm_record_pre(record.get("response") or "（无回复）")}</pre>
                    {raw_response_html}
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
                f"<form class=\"inline-form player-delete-form\" method=\"post\" action=\"{self._url('/player/delete')}\" "
                f"data-player-label=\"{row['nickname']} / {row['user_id']}\">"
                f"<input type=\"hidden\" name=\"group_id\" value=\"{row['group_id']}\">"
                f"<input type=\"hidden\" name=\"user_id\" value=\"{row['user_id']}\">"
                "<button class=\"danger compact-button\" type=\"submit\">删除</button>"
                "</form>"
            )
            rows.append(
                "<tr>"
                f"<td>{row['user_id']}</td>"
                f"<td><a href=\"{row['href']}\">{row['nickname']}</a></td>"
                f"<td>{row['player_identity']}</td>"
                f"<td>{row['updated_at']}</td>"
                f"<td>{delete_form}</td>"
                "</tr>"
            )

        body = "\n".join(rows) or "<tr><td colspan=\"5\">这个城市还没有玩家存档。</td></tr>"
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
                  <th>用户</th><th>角色</th><th>玩家身份</th><th>更新时间</th><th>操作</th>
                </tr>
              </thead>
              <tbody>{body}</tbody>
            </table>
            <div id="player-delete-modal" class="delete-modal" hidden>
              <div class="delete-modal-backdrop" data-action="close-delete-modal"></div>
              <section class="delete-modal-panel" role="dialog" aria-modal="true" aria-labelledby="player-delete-modal-title">
                <button class="delete-modal-close" type="button" data-action="close-delete-modal" aria-label="关闭">×</button>
                <h2 id="player-delete-modal-title">删除玩家存档</h2>
                <p class="muted">确定删除 <strong id="delete-player-label"></strong> 的玩家存档？此操作不可恢复。</p>
                <div class="actions">
                  <button class="secondary compact-button" type="button" data-action="close-delete-modal">取消</button>
                  <button id="confirm-player-delete" class="danger compact-button" type="button">删除存档</button>
                </div>
              </section>
            </div>
            <script>
              const deleteModal = document.getElementById("player-delete-modal");
              const deleteLabel = document.getElementById("delete-player-label");
              const confirmPlayerDelete = document.getElementById("confirm-player-delete");
              let pendingDeleteForm = null;

              function closeDeleteModal() {{
                pendingDeleteForm = null;
                deleteModal.hidden = true;
              }}

              document.querySelectorAll(".player-delete-form").forEach((form) => {{
                form.addEventListener("submit", (event) => {{
                  event.preventDefault();
                  pendingDeleteForm = form;
                  deleteLabel.textContent = form.dataset.playerLabel || "";
                  deleteModal.hidden = false;
                }});
              }});

              deleteModal.querySelectorAll("[data-action='close-delete-modal']").forEach((button) => {{
                button.addEventListener("click", closeDeleteModal);
              }});

              confirmPlayerDelete.addEventListener("click", () => {{
                if (!pendingDeleteForm) return;
                pendingDeleteForm.submit();
              }});
            </script>
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
            meta_items = [row["nickname"], row["updated_at"]]
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
            "player_identity": self._e(item.get("faction") or "魔法少女"),
            "updated_at": self._format_time(item.get("updated_at")),
            "href": href,
        }

    @staticmethod
    def _rank_display(value: object) -> str:
        return str(value or "").strip()

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
            if file_id == "fetish_book/default.json":
                return self._fetish_book_file_response(
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
        return self._plain_editable_file_response(
            title, file_id, back_category, note, content
        )

    def _fetish_book_file_response(
        self,
        title: str,
        file_id: str,
        back_category: str,
        note: str,
        content: str,
    ) -> web.Response:
        try:
            book = self._normalize_fetish_book(self._loads_json_text(content))
        except Exception as exc:
            return self._plain_editable_file_response(
                title,
                file_id,
                back_category,
                note,
                content,
                warning=f"性癖书 JSON 解析失败，请先修复源码 JSON：{exc}",
            )

        book_json = self._json_script_data(book)
        source_url = self._url(
            f"/editable/source?id={quote(file_id, safe='')}&category={self._e(back_category)}"
        )
        export_url = self._url(f"/editable/export?id={quote(file_id, safe='')}")
        storage_key = "qq_mahoushoujo:fetish_book:open_entries"
        return self._html_response(
            title,
            f"""
            <h1>{self._e(title)}</h1>
            <p><a href="{self._url(f'/editable?category={self._e(back_category)}')}">返回{self._e(self._editable_category_title(back_category))}</a></p>
            <p class="muted">{self._e(file_id)}</p>
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
            <form id="fetish-book-form" method="post" action="{self._url('/editable/save')}">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <input id="fetish-book-content" type="hidden" name="content" value="">
              <label for="note">资源说明 / 注释</label>
              <textarea id="note" class="note-editor" name="note" spellcheck="false">{self._e(note)}</textarea>
              <div class="book-config-grid">
                <div>
                  <label for="fetish-book-base-path">默认 change 基础路径</label>
                  <input id="fetish-book-base-path" type="text" value="{self._e(book.get('base_path') or '')}" spellcheck="false">
                </div>
              </div>
              <p class="muted">这个路径会发给 AI 作为 update.changes 的路径提示，不代表 JSON 文件实际存放路径。</p>
              <div class="world-book-toolbar">
                <div>
                  <h2>性癖书条目</h2>
                  <p class="muted">编辑简介与五个百分比进度区间效果。已拥有性癖会按当前进度注入对应效果。</p>
                </div>
                <button id="add-fetish-entry" type="button">+ 添加条目</button>
              </div>
              <div id="fetish-book-entries"></div>
              <div class="actions"><button type="submit">保存</button></div>
            </form>
            <form method="post" action="{self._url('/editable/reset')}" onsubmit="return confirm('确定恢复为当前代码内置默认内容？旧文件会先自动备份。');">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <button class="secondary" type="submit">恢复当前默认内容</button>
            </form>
            <script>
              const initialFetishBook = {book_json};
              const fetishRanges = ["0-20", "21-40", "41-60", "61-80", "81-100"];
              const fetishEntriesEl = document.getElementById("fetish-book-entries");
              const fetishForm = document.getElementById("fetish-book-form");
              const fetishContentInput = document.getElementById("fetish-book-content");
              const fetishBasePathInput = document.getElementById("fetish-book-base-path");
              const fetishStorageKey = "{self._e(storage_key)}";
              let fetishOpenKeys = new Set();
              let fetishDraggingIndex = null;
              const fetishState = {{
                version: Number(initialFetishBook.version || 1),
                base_path: String(initialFetishBook.base_path || ""),
                entries: Array.isArray(initialFetishBook.entries) ? initialFetishBook.entries : [],
              }};

              function fetishSplitKeys(value) {{
                const raw = Array.isArray(value) ? value.join("\\n") : String(value || "");
                return raw.split(/[\\n,，、]/).map((item) => item.trim()).filter(Boolean);
              }}

              function fetishDefaults(index) {{
                return {{
                  id: `entry_${{index + 1}}`, title: "", enabled: true, recursive: false,
                  strategy: "keyword", keys: [], content: "",
                  percentage_descriptions: Object.fromEntries(fetishRanges.map((range) => [range, ""])),
                }};
              }}

              function fetishNormalizeEntry(entry, index) {{
                const descriptions = entry.percentage_descriptions && typeof entry.percentage_descriptions === "object"
                  ? entry.percentage_descriptions : {{}};
                return {{
                  id: String(entry.id || `entry_${{index + 1}}`).trim(),
                  title: String(entry.title || ""),
                  enabled: entry.enabled !== false,
                  recursive: entry.recursive !== false,
                  strategy: entry.strategy === "always" ? "always" : "keyword",
                  keys: fetishSplitKeys(entry.keys),
                  content: String(entry.content || ""),
                  percentage_descriptions: Object.fromEntries(
                    fetishRanges.map((range) => [range, String(descriptions[range] || "")])
                  ),
                }};
              }}

              function fetishEscapeHtml(value) {{
                return String(value || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
              }}

              function fetishEscapeAttr(value) {{
                return fetishEscapeHtml(value).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
              }}

              function fetishEntryKey(entry, index) {{
                return String(entry.id || entry.title || `entry_${{index + 1}}`).trim();
              }}

              function fetishLoadOpenState() {{
                try {{
                  const raw = localStorage.getItem(fetishStorageKey);
                  if (raw) fetishOpenKeys = new Set(JSON.parse(raw).map(String));
                }} catch (error) {{ console.warn("failed to load fetish book open state", error); }}
              }}

              function fetishPersistOpenState() {{
                try {{ localStorage.setItem(fetishStorageKey, JSON.stringify(Array.from(fetishOpenKeys))); }}
                catch (error) {{ console.warn("failed to save fetish book open state", error); }}
              }}

              function fetishSyncFromDom() {{
                fetishState.base_path = fetishBasePathInput.value;
                fetishState.entries = Array.from(fetishEntriesEl.querySelectorAll(".fetish-entry")).map((card, index) => fetishNormalizeEntry({{
                  id: card.querySelector("[data-field='id']").value,
                  title: card.querySelector("[data-field='title']").value,
                  enabled: card.querySelector("[data-field='enabled']").checked,
                  recursive: card.querySelector("[data-field='recursive']").checked,
                  strategy: card.querySelector("[data-field='strategy']").value,
                  keys: card.querySelector("[data-field='keys']").value,
                  content: card.querySelector("[data-field='content']").value,
                  percentage_descriptions: Object.fromEntries(fetishRanges.map((range) => [
                    range, card.querySelector(`[data-range="${{range}}"]`).value,
                  ])),
                }}, index));
              }}

              function fetishRender() {{
                fetishEntriesEl.innerHTML = "";
                fetishState.entries = fetishState.entries.map(fetishNormalizeEntry);
                fetishState.entries.forEach((entry, index) => {{
                  const key = fetishEntryKey(entry, index);
                  const card = document.createElement("article");
                  card.className = "world-entry fetish-entry";
                  card.draggable = false;
                  card.innerHTML = `
                    <details ${{fetishOpenKeys.has(key) ? "open" : ""}}>
                      <summary class="world-entry-head">
                        <button class="drag-handle" type="button" draggable="true" data-action="drag" title="拖拽排序">↕</button>
                        <span class="entry-title">${{fetishEscapeHtml(entry.title || entry.id || `条目 ${{index + 1}}`)}}</span>
                        <span class="tag">${{fetishEscapeHtml(entry.strategy)}}</span>
                        <label class="summary-check"><input data-field="enabled" type="checkbox"${{entry.enabled ? " checked" : ""}}> 启用</label>
                        <button class="danger compact-button" type="button" data-action="delete">删除</button>
                      </summary>
                      <div class="world-entry-body">
                        <div class="world-entry-grid">
                          <label class="title-field">ID<input data-field="id" type="text" value="${{fetishEscapeAttr(entry.id)}}" spellcheck="false"></label>
                          <label class="title-field">标题<input data-field="title" type="text" value="${{fetishEscapeAttr(entry.title)}}" spellcheck="false"></label>
                          <label class="compact-field"><span>策略</span><select data-field="strategy"><option value="keyword"${{entry.strategy !== "always" ? " selected" : ""}}>keyword</option><option value="always"${{entry.strategy === "always" ? " selected" : ""}}>always</option></select></label>
                          <label class="summary-check"><input data-field="recursive" type="checkbox"${{entry.recursive ? " checked" : ""}}> 递归关键词</label>
                        </div>
                        <div class="block-field"><label>关键词</label><textarea data-field="keys" class="keys-editor" spellcheck="false">${{fetishEscapeHtml(entry.keys.join("\\n"))}}</textarea></div>
                        <div class="block-field"><label>简介</label><textarea data-field="content" class="entry-content-editor" spellcheck="false">${{fetishEscapeHtml(entry.content)}}</textarea></div>
                        <div class="fetish-range-grid">${{fetishRanges.map((range) => `
                          <div class="block-field"><label>${{range}}%</label><textarea data-range="${{range}}" class="entry-content-editor" spellcheck="false">${{fetishEscapeHtml(entry.percentage_descriptions[range])}}</textarea></div>
                        `).join("")}}</div>
                      </div>
                    </details>`;
                  card.querySelector("details").addEventListener("toggle", (event) => {{
                    if (event.currentTarget.open) fetishOpenKeys.add(key); else fetishOpenKeys.delete(key);
                    fetishPersistOpenState();
                  }});
                  card.querySelectorAll(".summary-check").forEach((control) => control.addEventListener("click", (event) => event.stopPropagation()));
                  card.querySelector("[data-action='delete']").addEventListener("click", (event) => {{
                    event.preventDefault(); event.stopPropagation();
                    if (!confirm("确定删除这个性癖书条目？")) return;
                    fetishSyncFromDom(); fetishState.entries.splice(index, 1); fetishOpenKeys.delete(key); fetishRender();
                  }});
                  const dragHandle = card.querySelector("[data-action='drag']");
                  dragHandle.addEventListener("click", (event) => {{ event.preventDefault(); event.stopPropagation(); }});
                  dragHandle.addEventListener("dragstart", (event) => {{
                    fetishSyncFromDom(); fetishDraggingIndex = index; card.classList.add("dragging");
                    event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", String(index));
                  }});
                  dragHandle.addEventListener("dragend", () => {{ fetishDraggingIndex = null; card.classList.remove("dragging"); }});
                  card.addEventListener("dragover", (event) => {{ if (fetishDraggingIndex !== null && fetishDraggingIndex !== index) {{ event.preventDefault(); card.classList.add("drag-over"); }} }});
                  card.addEventListener("dragleave", () => card.classList.remove("drag-over"));
                  card.addEventListener("drop", (event) => {{
                    event.preventDefault(); card.classList.remove("drag-over");
                    if (fetishDraggingIndex === null || fetishDraggingIndex === index) return;
                    const [moved] = fetishState.entries.splice(fetishDraggingIndex, 1);
                    fetishState.entries.splice(index, 0, moved); fetishDraggingIndex = null; fetishRender();
                  }});
                  const refreshTitle = () => {{ card.querySelector(".entry-title").textContent = card.querySelector("[data-field='title']").value.trim() || card.querySelector("[data-field='id']").value.trim() || `条目 ${{index + 1}}`; }};
                  card.querySelector("[data-field='title']").addEventListener("input", refreshTitle);
                  card.querySelector("[data-field='id']").addEventListener("input", refreshTitle);
                  fetishEntriesEl.appendChild(card);
                }});
              }}

              document.getElementById("add-fetish-entry").addEventListener("click", () => {{
                fetishSyncFromDom();
                const entry = fetishDefaults(fetishState.entries.length);
                fetishState.entries.push(entry); fetishOpenKeys.add(fetishEntryKey(entry, fetishState.entries.length - 1)); fetishRender();
              }});
              fetishForm.addEventListener("submit", () => {{ fetishSyncFromDom(); fetishContentInput.value = JSON.stringify(fetishState, null, 2); }});
              fetishState.entries = fetishState.entries.map(fetishNormalizeEntry);
              fetishLoadOpenState();
              fetishRender();
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
            book = self._normalize_monster_book(self._loads_json_text(content))
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
                  <p class="muted">编辑公共魔物字段：关键词、正文、战斗机制，以及胜利/失败结尾表现。</p>
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
              let monsterOpenKeys = new Set();

              const monsterState = {{
                version: Number(initialMonsterBook.version || 1),
                base_path: String(initialMonsterBook.base_path || "/魔物图鉴/"),
                entries: Array.isArray(initialMonsterBook.entries) ? initialMonsterBook.entries : [],
              }};

              function monsterDefaults(index) {{
                return {{
                  id: `monster_${{index + 1}}`,
                  name: "",
                  keys: [],
                  content: "",
                  battle_gimmick: "",
                  victory_ending: "",
                  defeat_ending: "",
                }};
              }}

              function monsterSplitList(value) {{
                const raw = Array.isArray(value) ? value.join("\\n") : String(value || "");
                return raw.split(/[\\n,，、]/).map((item) => item.trim()).filter(Boolean);
              }}

              function monsterNormalizeEntry(entry, index) {{
                return {{
                  id: String(entry.id || `monster_${{index + 1}}`).trim(),
                  name: String(entry.name || "").trim(),
                  keys: monsterSplitList(entry.keys),
                  content: String(entry.content || ""),
                  battle_gimmick: String(entry.battle_gimmick || ""),
                  victory_ending: String(entry.victory_ending || ""),
                  defeat_ending: String(entry.defeat_ending || ""),
                }};
              }}

              function monsterEscapeHtml(value) {{
                return String(value || "")
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
              }}

              function monsterEscapeAttr(value) {{
                return monsterEscapeHtml(value).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
              }}

              function monsterEntryKey(entry, index) {{
                return String(entry.id || entry.name || `monster_${{index + 1}}`).trim();
              }}

              function monsterLoadOpenState() {{
                try {{
                  const raw = localStorage.getItem(monsterStorageKey);
                  if (raw) monsterOpenKeys = new Set(JSON.parse(raw).map(String));
                }} catch (error) {{
                  console.warn("failed to load monster book open state", error);
                }}
              }}

              function monsterPersistOpenState() {{
                try {{
                  localStorage.setItem(monsterStorageKey, JSON.stringify(Array.from(monsterOpenKeys)));
                }} catch (error) {{
                  console.warn("failed to save monster book open state", error);
                }}
              }}

              function monsterSyncFromDom() {{
                monsterState.entries = Array.from(monsterEntriesEl.querySelectorAll(".monster-entry")).map((card, index) => monsterNormalizeEntry({{
                  id: card.querySelector("[data-field='id']").value,
                  name: card.querySelector("[data-field='name']").value,
                  keys: card.querySelector("[data-field='keys']").value,
                  content: card.querySelector("[data-field='content']").value,
                  battle_gimmick: card.querySelector("[data-field='battle_gimmick']").value,
                  victory_ending: card.querySelector("[data-field='victory_ending']").value,
                  defeat_ending: card.querySelector("[data-field='defeat_ending']").value,
                }}, index));
              }}

              function monsterTextarea(name, value, label) {{
                return `
                  <div class="block-field">
                    <label>${{monsterEscapeHtml(label)}}</label>
                    <textarea data-field="${{monsterEscapeAttr(name)}}" class="entry-content-editor" spellcheck="false">${{monsterEscapeHtml(Array.isArray(value) ? value.join("\\n") : value)}}</textarea>
                  </div>
                `;
              }}

              function monsterRender() {{
                monsterEntriesEl.innerHTML = "";
                monsterState.entries = monsterState.entries.map(monsterNormalizeEntry);
                monsterState.entries.forEach((entry, index) => {{
                  const key = monsterEntryKey(entry, index);
                  const card = document.createElement("article");
                  card.className = "world-entry monster-entry";
                  card.dataset.entryKey = key;
                  card.innerHTML = `
                    <details ${{monsterOpenKeys.has(key) ? "open" : ""}}>
                      <summary class="world-entry-head">
                        <span class="entry-title">${{monsterEscapeHtml(entry.name || entry.id || `魔物 ${{index + 1}}`)}}</span>
                        <span class="tag">${{monsterEscapeHtml(entry.id)}}</span>
                        <button class="danger compact-button" type="button" data-action="delete">删除</button>
                      </summary>
                      <div class="world-entry-body">
                        <div class="world-entry-grid">
                          <label class="title-field">ID<input data-field="id" type="text" value="${{monsterEscapeAttr(entry.id)}}" spellcheck="false"></label>
                          <label class="title-field">名称<input data-field="name" type="text" value="${{monsterEscapeAttr(entry.name)}}" spellcheck="false"></label>
                        </div>
                        <div class="block-field">
                          <label>关键词</label>
                          <textarea data-field="keys" class="keys-editor" spellcheck="false">${{monsterEscapeHtml(entry.keys.join("\\n"))}}</textarea>
                        </div>
                        ${{monsterTextarea("content", entry.content, "正文")}}
                        ${{monsterTextarea("battle_gimmick", entry.battle_gimmick, "战斗机制")}}
                        <div class="monster-ending-grid">
                          ${{monsterTextarea("victory_ending", entry.victory_ending, "战斗胜利结尾")}}
                          ${{monsterTextarea("defeat_ending", entry.defeat_ending, "战斗失败结尾")}}
                        </div>
                      </div>
                    </details>
                  `;
                  card.querySelector("details").addEventListener("toggle", (event) => {{
                    if (event.currentTarget.open) monsterOpenKeys.add(key);
                    else monsterOpenKeys.delete(key);
                    monsterPersistOpenState();
                  }});
                  card.querySelector("[data-action='delete']").addEventListener("click", () => {{
                    monsterSyncFromDom();
                    monsterState.entries.splice(index, 1);
                    monsterOpenKeys.delete(key);
                    monsterRender();
                  }});
                  card.addEventListener("input", monsterSyncFromDom);
                  monsterEntriesEl.appendChild(card);
                }});
              }}

              addMonsterButton.addEventListener("click", () => {{
                monsterSyncFromDom();
                const entry = monsterDefaults(monsterState.entries.length);
                monsterState.entries.push(entry);
                monsterOpenKeys.add(monsterEntryKey(entry, monsterState.entries.length - 1));
                monsterRender();
              }});

              monsterForm.addEventListener("submit", () => {{
                monsterSyncFromDom();
                monsterContentInput.value = JSON.stringify(monsterState, null, 2);
              }});

              monsterLoadOpenState();
              monsterRender();
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
            book = self._normalize_event_book(self._loads_json_text(content))
        except Exception as exc:
            return self._plain_editable_file_response(
                title,
                file_id,
                back_category,
                note,
                content,
                warning=f"事件书 JSON 解析失败，请先修复原始 JSON：{exc}",
            )

        try:
            monster_book = self._normalize_monster_book(
                self._loads_json_text(self.editable_manager.read_text("monster_book/default.json"))
            )
        except Exception:
            monster_book = {"version": 1, "entries": []}

        book_json = self._json_script_data(book)
        monster_book_json = self._json_script_data(monster_book)
        storage_key = "qq_mahoushoujo:event_book:open_events"
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
            <div id="event-monster-picker-modal" class="monster-modal" hidden>
              <div class="monster-modal-backdrop" data-action="close-event-monster-picker"></div>
              <section class="monster-modal-panel" role="dialog" aria-modal="true" aria-labelledby="event-monster-picker-title">
                <div class="monster-modal-head">
                  <div>
                    <span>Monster Picker</span>
                    <h2 id="event-monster-picker-title">选择兼容魔物</h2>
                  </div>
                  <button class="secondary compact-button" type="button" data-action="close-event-monster-picker">关闭</button>
                </div>
                <div class="monster-choice-grid" id="event-monster-picker-options"></div>
                <div class="monster-modal-actions">
                  <button class="secondary" type="button" data-action="close-event-monster-picker">取消</button>
                  <button type="button" data-action="confirm-event-monster-picker">确定</button>
                </div>
              </section>
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
                  <p class="muted">按分类编辑事件的正文、事件机制、顺利进行和受到阻碍；魔物事件通过魔物名称选择 compatible_monsters。</p>
                </div>
                <button id="add-event" type="button">+ 添加事件</button>
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
              const eventMonsterBook = {monster_book_json};
              const eventEntriesEl = document.getElementById("event-book-events");
              const eventForm = document.getElementById("event-book-form");
              const eventContentInput = document.getElementById("event-book-content");
              const addEventButton = document.getElementById("add-event");
              const eventStorageKey = "{self._e(storage_key)}";
              const eventDefaultCategories = [
                {{ id: "monster_enemy", name: "目标是魔物", events: [] }},
                {{ id: "character_enemy", name: "目标是魔法少女", events: [] }},
              ];
              const eventMonsterOptions = (Array.isArray(eventMonsterBook.entries) ? eventMonsterBook.entries : [])
                .map((monster, index) => String(monster.name || monster.id || `魔物 ${{index + 1}}`).trim())
                .filter(Boolean);
              let activeMonsterPickerCard = null;
              let activeMonsterPickerSelection = new Set();
              const eventState = {{
                version: Number(initialEventBook.version || 4),
                categories: eventNormalizeCategories(initialEventBook),
              }};
              let eventOpenKeys = new Set();

              function eventDefaults(index) {{
                return {{
                  id: `event_${{index + 1}}`,
                  name: "",
                  enabled: true,
                  keys: [],
                  location_tags: [],
                  compatible_monsters: [],
                  content: "",
                  event_gimmick: "",
                  success_ending: "",
                  obstacle_ending: "",
                }};
              }}

              function eventSplitList(value) {{
                const raw = Array.isArray(value) ? value.join("\\n") : String(value || "");
                return raw.split(/[\\n,，、]/).map((item) => item.trim()).filter(Boolean);
              }}

              function eventNormalizeEntry(entry, index) {{
                return {{
                  id: String(entry.id || `event_${{index + 1}}`).trim(),
                  name: String(entry.name || entry.title || "").trim(),
                  enabled: entry.enabled !== false,
                  keys: eventSplitList(entry.keys),
                  location_tags: eventSplitList(entry.location_tags),
                  compatible_monsters: eventSplitList(entry.compatible_monsters),
                  content: String(entry.content || ""),
                  event_gimmick: String(entry.event_gimmick || ""),
                  success_ending: String(entry.success_ending || ""),
                  obstacle_ending: String(entry.obstacle_ending || ""),
                }};
              }}

              function eventNormalizeCategories(book) {{
                const byId = new Map();
                eventDefaultCategories.forEach((category) => byId.set(category.id, {{ ...category, events: [] }}));
                const rawCategories = Array.isArray(book.categories) ? book.categories : [];
                rawCategories.forEach((category, categoryIndex) => {{
                  if (!category || typeof category !== "object") return;
                  const id = String(category.id || `category_${{categoryIndex + 1}}`).trim();
                  if (!id) return;
                  const existing = byId.get(id) || {{ id, name: id, events: [] }};
                  existing.name = String(category.name || existing.name || id);
                  existing.events = Array.isArray(category.events) ? category.events.map(eventNormalizeEntry) : [];
                  byId.set(id, existing);
                }});
                return Array.from(byId.values());
              }}

              function eventEscapeHtml(value) {{
                return String(value || "")
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
              }}

              function eventEscapeAttr(value) {{
                return eventEscapeHtml(value).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
              }}

              function eventEntryKey(category, entry, eventIndex) {{
                return `${{category.id}}:${{String(entry.id || entry.name || `event_${{eventIndex + 1}}`).trim()}}`;
              }}

              function eventLoadOpenState() {{
                try {{
                  const raw = localStorage.getItem(eventStorageKey);
                  if (raw) eventOpenKeys = new Set(JSON.parse(raw).map(String));
                }} catch (error) {{
                  console.warn("failed to load event book open state", error);
                }}
              }}

              function eventPersistOpenState() {{
                try {{
                  localStorage.setItem(eventStorageKey, JSON.stringify(Array.from(eventOpenKeys)));
                }} catch (error) {{
                  console.warn("failed to save event book open state", error);
                }}
              }}

              function eventMonsterChipHtml(name) {{
                return `
                  <span class="monster-selected-chip" data-monster-name="${{eventEscapeAttr(name)}}">
                    <span>${{eventEscapeHtml(name)}}</span>
                    <button type="button" data-action="remove-compatible-monster" aria-label="移除 ${{eventEscapeAttr(name)}}">×</button>
                  </span>
                `;
              }}

              function eventSelectedMonsterListHtml(values) {{
                const selected = eventSplitList(values);
                if (!selected.length) {{
                  return '<span class="muted">未选择魔物</span>';
                }}
                return selected.map(eventMonsterChipHtml).join("");
              }}

              function eventSetCardMonsters(card, names) {{
                const clean = Array.from(new Set(names.map((name) => String(name || "").trim()).filter(Boolean)));
                card.dataset.compatibleMonsters = JSON.stringify(clean);
                const list = card.querySelector("[data-role='compatible-monster-list']");
                if (list) {{
                  list.innerHTML = eventSelectedMonsterListHtml(clean);
                }}
              }}

              function eventGetCardMonsters(card) {{
                try {{
                  const parsed = JSON.parse(card.dataset.compatibleMonsters || "[]");
                  return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : [];
                }} catch (error) {{
                  return [];
                }}
              }}

              function eventOpenMonsterPicker(card) {{
                activeMonsterPickerCard = card;
                activeMonsterPickerSelection = new Set(eventGetCardMonsters(card));
                eventRenderMonsterPickerModal();
                document.getElementById("event-monster-picker-modal").hidden = false;
              }}

              function eventCloseMonsterPicker() {{
                document.getElementById("event-monster-picker-modal").hidden = true;
                activeMonsterPickerCard = null;
                activeMonsterPickerSelection = new Set();
              }}

              function eventRenderMonsterPickerModal() {{
                const options = document.getElementById("event-monster-picker-options");
                if (!options) return;
                options.innerHTML = eventMonsterOptions.map((name) => `
                  <button class="monster-choice-card${{activeMonsterPickerSelection.has(name) ? " selected" : ""}}" type="button" data-monster-name="${{eventEscapeAttr(name)}}">
                    <span class="monster-choice-check">${{activeMonsterPickerSelection.has(name) ? "✓" : ""}}</span>
                    <strong>${{eventEscapeHtml(name)}}</strong>
                  </button>
                `).join("") || '<p class="muted empty-state">当前魔物书没有可选择的魔物。</p>';
              }}

              function eventSyncFromDom() {{
                eventState.categories = Array.from(eventEntriesEl.querySelectorAll(".event-category")).map((block) => {{
                  const categoryId = block.dataset.categoryId;
                  const existing = eventState.categories.find((category) => category.id === categoryId) || {{ id: categoryId, name: categoryId, events: [] }};
                  return {{
                    id: categoryId,
                    name: existing.name,
                    events: Array.from(block.querySelectorAll(".event-entry")).map((card, index) => eventNormalizeEntry({{
                      id: card.querySelector("[data-field='id']").value,
                      name: card.querySelector("[data-field='name']").value,
                      enabled: card.querySelector("[data-field='enabled']").checked,
                      keys: card.querySelector("[data-field='keys']").value,
                      location_tags: card.querySelector("[data-field='location_tags']").value,
                      compatible_monsters: eventGetCardMonsters(card),
                      content: card.querySelector("[data-field='content']").value,
                      event_gimmick: card.querySelector("[data-field='event_gimmick']").value,
                      success_ending: card.querySelector("[data-field='success_ending']").value,
                      obstacle_ending: card.querySelector("[data-field='obstacle_ending']").value,
                    }}, index)),
                  }};
                }});
              }}

              function eventTextarea(name, value, label, extraClass = "entry-content-editor") {{
                return `
                  <div class="block-field">
                    <label>${{eventEscapeHtml(label)}}</label>
                    <textarea data-field="${{eventEscapeAttr(name)}}" class="${{eventEscapeAttr(extraClass)}}" spellcheck="false">${{eventEscapeHtml(Array.isArray(value) ? value.join("\\n") : value)}}</textarea>
                  </div>
                `;
              }}

              function eventRender() {{
                eventEntriesEl.innerHTML = "";
                eventState.categories.forEach((category) => {{
                  const categoryBlock = document.createElement("section");
                  categoryBlock.className = "region-block event-category";
                  categoryBlock.dataset.categoryId = category.id;
                  categoryBlock.innerHTML = `
                    <details open>
                      <summary class="world-entry-head region-head">
                        <span class="entry-title">${{eventEscapeHtml(category.name || category.id)}}</span>
                        <span class="tag">${{eventEscapeHtml(category.id)}}</span>
                      </summary>
                      <div class="region-body">
                        <div class="region-entries"></div>
                      </div>
                    </details>
                  `;
                  const list = categoryBlock.querySelector(".region-entries");
                  category.events = (Array.isArray(category.events) ? category.events : []).map(eventNormalizeEntry);
                  category.events.forEach((entry, index) => {{
                    const key = eventEntryKey(category, entry, index);
                    const isMonsterCategory = category.id === "monster_enemy";
                    const card = document.createElement("article");
                    card.className = "world-entry event-entry";
                    card.dataset.entryKey = key;
                    card.innerHTML = `
                      <details ${{eventOpenKeys.has(key) ? "open" : ""}}>
                        <summary class="world-entry-head">
                          <span class="entry-title">${{eventEscapeHtml(entry.name || entry.id || `事件 ${{index + 1}}`)}}</span>
                          <span class="tag">${{eventEscapeHtml(entry.id)}}</span>
                          <label class="summary-check"><input data-field="enabled" type="checkbox"${{entry.enabled ? " checked" : ""}}> 启用</label>
                          <button class="danger compact-button" type="button" data-action="delete">删除</button>
                        </summary>
                        <div class="world-entry-body">
                          <div class="world-entry-grid">
                            <label class="title-field">ID<input data-field="id" type="text" value="${{eventEscapeAttr(entry.id)}}" spellcheck="false"></label>
                            <label class="title-field">名称<input data-field="name" type="text" value="${{eventEscapeAttr(entry.name)}}" spellcheck="false"></label>
                          </div>
                          ${{eventTextarea("keys", entry.keys, "关键词", "keys-editor")}}
                          ${{eventTextarea("location_tags", entry.location_tags, "地点标签", "keys-editor")}}
                          ${{isMonsterCategory ? `
                            <div class="monster-picker">
                              <div class="monster-picker-head">
                                <strong>兼容魔物</strong>
                                <button class="compact-button secondary monster-add-button" type="button" data-action="open-compatible-monster-picker">+</button>
                              </div>
                              <div class="monster-selected-list" data-role="compatible-monster-list">${{eventSelectedMonsterListHtml(entry.compatible_monsters)}}</div>
                            </div>
                          ` : ""}}
                          ${{eventTextarea("content", entry.content, "正文")}}
                          ${{eventTextarea("event_gimmick", entry.event_gimmick, "事件机制")}}
                          <div class="monster-ending-grid">
                            ${{eventTextarea("success_ending", entry.success_ending, "顺利进行")}}
                            ${{eventTextarea("obstacle_ending", entry.obstacle_ending, "受到阻碍")}}
                          </div>
                        </div>
                      </details>
                    `;
                    eventSetCardMonsters(card, entry.compatible_monsters);
                    card.querySelector("details").addEventListener("toggle", (event) => {{
                      if (event.currentTarget.open) eventOpenKeys.add(key);
                      else eventOpenKeys.delete(key);
                      eventPersistOpenState();
                    }});
                    card.querySelector("[data-action='delete']").addEventListener("click", () => {{
                      eventSyncFromDom();
                      category.events.splice(index, 1);
                      eventOpenKeys.delete(key);
                      eventRender();
                    }});
                    const openMonsterPickerButton = card.querySelector("[data-action='open-compatible-monster-picker']");
                    if (openMonsterPickerButton) {{
                      openMonsterPickerButton.addEventListener("click", () => eventOpenMonsterPicker(card));
                    }}
                    card.addEventListener("click", (event) => {{
                      const removeButton = event.target.closest("[data-action='remove-compatible-monster']");
                      if (!removeButton) return;
                      const chip = removeButton.closest("[data-monster-name]");
                      const name = chip ? chip.dataset.monsterName : "";
                      eventSetCardMonsters(card, eventGetCardMonsters(card).filter((item) => item !== name));
                      eventSyncFromDom();
                    }});
                    card.addEventListener("input", eventSyncFromDom);
                    card.addEventListener("change", eventSyncFromDom);
                    list.appendChild(card);
                  }});
                  eventEntriesEl.appendChild(categoryBlock);
                }});
              }}

              addEventButton.addEventListener("click", () => {{
                eventSyncFromDom();
                const category = eventState.categories[0] || eventDefaultCategories[0];
                const entry = eventDefaults(category.events.length);
                category.events.push(entry);
                eventOpenKeys.add(eventEntryKey(category, entry, category.events.length - 1));
                eventRender();
              }});

              document.getElementById("event-monster-picker-options").addEventListener("click", (event) => {{
                const option = event.target.closest("[data-monster-name]");
                if (!option) return;
                const name = option.dataset.monsterName;
                if (activeMonsterPickerSelection.has(name)) activeMonsterPickerSelection.delete(name);
                else activeMonsterPickerSelection.add(name);
                eventRenderMonsterPickerModal();
              }});

              document.querySelectorAll("[data-action='close-event-monster-picker']").forEach((button) => {{
                button.addEventListener("click", eventCloseMonsterPicker);
              }});

              document.querySelector("[data-action='confirm-event-monster-picker']").addEventListener("click", () => {{
                if (activeMonsterPickerCard) {{
                  eventSetCardMonsters(activeMonsterPickerCard, Array.from(activeMonsterPickerSelection));
                  eventSyncFromDom();
                }}
                eventCloseMonsterPicker();
              }});

              eventForm.addEventListener("submit", () => {{
                eventSyncFromDom();
                const output = {{
                  version: eventState.version,
                  categories: eventState.categories.map((category) => ({{
                    id: category.id,
                    name: category.name,
                    events: category.events.map((entry) => {{
                      const clean = eventNormalizeEntry(entry, 0);
                      if (!clean.compatible_monsters.length) delete clean.compatible_monsters;
                      return clean;
                    }}),
                  }})),
                }};
                eventContentInput.value = JSON.stringify(output, null, 2);
              }});

              eventLoadOpenState();
              eventRender();
            </script>
            """,
        )

    @classmethod
    def _normalize_fetish_book(cls, raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            return {"version": 1, "base_path": "/主角/快感状态/性癖/", "entries": []}
        raw_entries = raw.get("entries", [])
        if not isinstance(raw_entries, list):
            raw_entries = []
        ranges = ("0-20", "21-40", "41-60", "61-80", "81-100")
        entries: list[dict[str, object]] = []
        for idx, entry in enumerate(raw_entries):
            if not isinstance(entry, dict):
                continue
            descriptions = entry.get("percentage_descriptions")
            if not isinstance(descriptions, dict):
                descriptions = {}
            entries.append(
                {
                    "id": str(entry.get("id") or f"entry_{idx + 1}").strip(),
                    "title": str(entry.get("title") or "").strip(),
                    "enabled": entry.get("enabled", True) is not False,
                    "recursive": entry.get("recursive", False) is not False,
                    "strategy": "always"
                    if str(entry.get("strategy") or "").strip().lower() == "always"
                    else "keyword",
                    "keys": cls._normalize_editor_text_list(entry.get("keys")),
                    "content": str(entry.get("content") or "").strip(),
                    "percentage_descriptions": {
                        range_name: str(descriptions.get(range_name) or "").strip()
                        for range_name in ranges
                    },
                }
            )
        return {
            "version": int(raw.get("version") or 1),
            "base_path": str(raw.get("base_path") or "/主角/快感状态/性癖/").strip(),
            "entries": entries,
        }

    @classmethod
    def _normalize_monster_book(cls, raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            return {"version": 1, "base_path": "/魔物图鉴/", "entries": []}
        entries: list[dict[str, object]] = []
        raw_entries = raw.get("entries", [])
        if not isinstance(raw_entries, list):
            raw_entries = []
        for idx, entry in enumerate(raw_entries):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or entry.get("title") or "").strip()
            content = str(entry.get("content") or entry.get("detail") or "").strip()
            if not name and not content:
                continue
            entries.append(
                {
                    "id": str(entry.get("id") or f"monster_{idx + 1}").strip(),
                    "name": name,
                    "keys": cls._normalize_editor_text_list(entry.get("keys")),
                    "content": content,
                    "battle_gimmick": str(entry.get("battle_gimmick") or "").strip(),
                    "victory_ending": str(entry.get("victory_ending") or "").strip(),
                    "defeat_ending": str(entry.get("defeat_ending") or "").strip(),
                }
            )
        return {
            "version": int(raw.get("version") or 1),
            "base_path": str(raw.get("base_path") or "/魔物图鉴/").strip(),
            "entries": entries,
        }

    @classmethod
    def _normalize_event_book(cls, raw: object) -> dict[str, object]:
        default_categories = [
            {"id": "monster_enemy", "name": "目标是魔物", "events": []},
            {"id": "character_enemy", "name": "目标是魔法少女", "events": []},
        ]
        if not isinstance(raw, dict):
            return {"version": 4, "categories": default_categories}

        categories_by_id: dict[str, dict[str, object]] = {
            str(category["id"]): dict(category) for category in default_categories
        }
        raw_categories = raw.get("categories", [])
        if not isinstance(raw_categories, list):
            raw_categories = []
        for category_idx, raw_category in enumerate(raw_categories):
            if not isinstance(raw_category, dict):
                continue
            category_id = str(raw_category.get("id") or f"category_{category_idx + 1}").strip()
            if not category_id:
                continue
            category = categories_by_id.get(
                category_id,
                {"id": category_id, "name": category_id, "events": []},
            )
            category["name"] = str(raw_category.get("name") or category.get("name") or category_id).strip()
            raw_events = raw_category.get("events", [])
            if not isinstance(raw_events, list):
                raw_events = []
            category["events"] = [
                cls._normalize_event_book_entry(entry, idx)
                for idx, entry in enumerate(raw_events)
                if isinstance(entry, dict)
            ]
            categories_by_id[category_id] = category

        return {
            "version": int(raw.get("version") or 4),
            "categories": list(categories_by_id.values()),
        }

    @classmethod
    def _normalize_event_book_entry(cls, entry: dict[str, object], idx: int) -> dict[str, object]:
        normalized: dict[str, object] = {
            "id": str(entry.get("id") or f"event_{idx + 1}").strip(),
            "name": str(entry.get("name") or entry.get("title") or "").strip(),
            "enabled": entry.get("enabled", True) is not False,
            "keys": cls._normalize_editor_text_list(entry.get("keys")),
            "location_tags": cls._normalize_editor_text_list(entry.get("location_tags")),
            "content": str(entry.get("content") or "").strip(),
            "event_gimmick": str(entry.get("event_gimmick") or "").strip(),
            "success_ending": str(entry.get("success_ending") or "").strip(),
            "obstacle_ending": str(entry.get("obstacle_ending") or "").strip(),
        }
        compatible_monsters = cls._normalize_editor_text_list(entry.get("compatible_monsters"))
        if compatible_monsters:
            normalized["compatible_monsters"] = compatible_monsters
        return normalized

    @staticmethod
    def _normalize_editor_text_list(value: object) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = re.split(r"[\n,，、]+", value)
        else:
            raw_items = []
        items: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
        return items

    @staticmethod
    def _loads_json_text(content: str) -> object:
        return json.loads(str(content or "").lstrip("\ufeff"))

    @classmethod
    def _format_book_key_info(cls, raw: object, *, file_id: str = "") -> str:
        return json.dumps(raw, ensure_ascii=False, indent=2)

    @staticmethod
    def _single_line_text(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _default_book_display_name(file_id: str) -> str:
        if file_id == "skill_book/default.json":
            return "技能进度"
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
            key_info = self._format_book_key_info(
                self._loads_json_text(content), file_id=file_id
            )
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
                self._loads_json_text(content)
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
            "这里展示完整行动正文、短事件记忆与长期记忆摘要。删除单条记录只会移除 daily_memory.jsonl 中对应一行，不会回滚当前状态。"
            if is_admin
            else "这里展示该存档的完整行动正文、短事件记忆与长期记忆摘要。"
        )
        cameo_note = (
            "这里展示其他玩家与该角色实际互动后生成的客串交互记忆。删除单条记录只会移除 cameo_memory.jsonl 中对应一行。"
            if is_admin
            else "这里展示其他玩家与该角色实际互动后生成的客串交互记忆。"
        )
        log_clear_button = (
            self._player_clear_form(
                group_id,
                user_id,
                "/player/log/clear",
                "删除全部行动记录",
                "确定删除该玩家的全部行动记录？当前状态不会自动回滚。",
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
                  <h2>行动记录</h2>
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

    async def _player_relationships(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if not session or session["role"] != SESSION_USER_ROLE:
            return self._forbidden()

        group_id = request.query.get("group_id", "")
        user_id = request.query.get("user_id", "")
        if not self._can_access_player(session, user_id):
            return self._forbidden()

        detail = self.repository.read_save_detail(group_id, user_id)
        if detail is None:
            raise web.HTTPNotFound(text="save not found")

        graph_data = self.repository.read_relationship_graph(group_id, user_id)
        player_data = detail.get("player_data", {})
        protagonist = player_data.get("主角", {}) if isinstance(player_data, dict) else {}
        title_name = self._get_nested(
            protagonist,
            ["个人信息", "姓名"],
            player_data.get("nickname", ""),
        ) or user_id
        graph_json = json.dumps(graph_data, ensure_ascii=False).replace("<", "\\u003c")
        back_url = self._url(
            f"/player?group_id={quote(group_id, safe='')}&user_id={quote(user_id, safe='')}"
        )

        return self._html_response(
            f"Relationship Graph - {title_name}",
            f"""
            <section class="relationship-shell" aria-label="Relationship Graph">
              <div class="player-stars" aria-hidden="true">
                <span></span><span></span><span></span><span></span><span></span>
              </div>
              <header class="relationship-head">
                <a class="player-back-link" href="{back_url}">返回个人档案</a>
                <p class="player-kicker">Relationship Graph</p>
                <h1>城市关系网</h1>
                <p>{self._e(title_name)}的城市关系网络。点击圈圈，右侧会显示你对 TA 的当前印象。</p>
              </header>

              <section class="relationship-layout">
                <div class="relationship-graph-panel">
                  <svg id="relationshipGraph" class="relationship-graph" role="img" aria-label="玩家关系图"></svg>
                  <p id="relationshipEmpty" class="relationship-empty" hidden>还没有关系记录。</p>
                </div>
                <aside class="relationship-card" aria-live="polite">
                  <div class="profile-card-head">
                    <span>Selected</span>
                    <h2 id="relationshipCardName">未选择</h2>
                  </div>
                  <p id="relationshipCardPlayer" class="relationship-card-subtitle"></p>
                  <div class="relationship-perspective" role="group" aria-label="印象视角">
                    <button id="relationshipPerspectiveOut" class="active" type="button">我眼里的TA</button>
                    <button id="relationshipPerspectiveIn" type="button">TA眼里的我</button>
                  </div>
                  <div class="relationship-card-section">
                    <span>Relationship</span>
                    <p id="relationshipCardRelationship">-</p>
                  </div>
                  <div class="relationship-card-section">
                    <span>Impression</span>
                    <p id="relationshipCardImpression">点击圈圈查看关系。</p>
                  </div>
                  <div class="relationship-card-section">
                    <span>Evidence</span>
                    <p id="relationshipCardEvidence">-</p>
                  </div>
                  <div class="relationship-card-section">
                    <span>Summary</span>
                    <p id="relationshipCardSummary">-</p>
                  </div>
                  <div id="relationshipCardTags" class="relationship-tag-row"></div>
                </aside>
              </section>
              <script id="relationshipGraphData" type="application/json">{graph_json}</script>
              <script>{self._relationship_graph_script()}</script>
            </section>
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
        page_prefix = "魔法少女"
        page_name = f"{page_prefix} {display_name}"
        shell_class = "faction-magical"
        profile_kicker = "Mahou Shoujo Profile"
        emblem = "✦"
        hero_copy = f"{city_name}记录中的魔法少女档案。这里汇总了你的身份、外观、装备、成长进度与最近的冒险痕迹。"
        aria_label = f"{page_prefix}个人档案"

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
        extra_profile_html = self._player_site_extra_profile_cards(protagonist)
        relationship_url = self._url(
            f"/player/relationships?group_id={quote(group_id, safe='')}&user_id={quote(user_id, safe='')}"
        )

        return self._html_response(
            page_name,
            f"""
            <section class="player-detail-shell {shell_class}" aria-label="{self._e(aria_label)}">
              <div class="player-stars" aria-hidden="true">
                <span></span><span></span><span></span><span></span><span></span>
              </div>
              <nav class="player-floating-actions" aria-label="档案操作">
                <a class="player-floating-action" href="{relationship_url}" title="关系图">关系图</a>
              </nav>
              <header class="player-detail-hero">
                <a class="player-back-link" href="{self._url('/')}">返回个人档案</a>
                <div class="player-detail-emblem" aria-hidden="true">{emblem}</div>
                <p class="player-kicker">{self._e(profile_kicker)}</p>
                <h1>{self._e(page_name)}</h1>
                <p>{self._e(hero_copy)}</p>
                <div class="player-hero-tags">
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
                    {extra_profile_html}
                  </div>
                </section>

                <article class="player-site-section player-memory-row">
                  <div class="profile-card-head">
                    <span>Diary</span>
                    <h2>最近冒险记录</h2>
                  </div>
                  <p class="muted">完整行动正文、短事件记忆与长期记忆摘要。</p>
                  <div class="log-list">{logs_html}</div>
                </article>
                <article class="player-site-section player-memory-row">
                  <div class="profile-card-head">
                    <span>Connections</span>
                    <h2>城市中的交互</h2>
                  </div>
                  <p class="muted">其他玩家与你实际互动后生成的客串交互记忆。</p>
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

    def _player_site_extra_profile_cards(self, protagonist: dict[str, Any]) -> str:
        if not isinstance(protagonist, dict):
            return '<p class="player-site-empty">暂无补充档案。</p>'
        skipped = {"个人信息", "相貌特征", "身材细节", "技能", "快感状态"}
        cards: list[str] = []
        for section_name, section_value in protagonist.items():
            label = str(section_name or "").strip()
            if not label or label in skipped:
                continue
            items = self._player_site_flatten_extra_items(section_value)
            if not items:
                continue
            cards.append(self._player_site_profile_card("Supplement", label, items))
        return "\n".join(cards) or '<p class="player-site-empty">暂无补充档案。</p>'

    def _player_site_flatten_extra_items(
        self,
        value: Any,
        prefix: str = "",
    ) -> list[tuple[str, object]]:
        if isinstance(value, dict):
            items: list[tuple[str, object]] = []
            for key, child in value.items():
                label = str(key or "").strip()
                if not label:
                    continue
                next_prefix = f"{prefix} / {label}" if prefix else label
                items.extend(self._player_site_flatten_extra_items(child, next_prefix))
            return items
        if isinstance(value, list):
            items = []
            for index, child in enumerate(value, start=1):
                next_prefix = f"{prefix} #{index}" if prefix else f"#{index}"
                items.extend(self._player_site_flatten_extra_items(child, next_prefix))
            return items
        text = str(value or "").strip()
        if not text:
            return []
        return [(prefix or "内容", text)]

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
                  <span>{self._e(class_name)}</span>
                </div>
              </div>
            </section>
            <section class="detail-grid">
              {profile_panel}
              <article class="detail-panel">
                <h2>当前状态</h2>
                <div class="meta-list">
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
            return "<p class=\"muted empty-state\">还没有行动记录。</p>"

        cards: list[str] = []
        for display_index, log in enumerate(logs, start=1):
            log_index = int(log.get("_log_index", -1))
            raw_log_type = str(log.get("type", "log"))
            log_type = self._e(raw_log_type)
            title = self._e(log.get("title") or log.get("message") or "行动记录")
            action = self._e(log.get("action") or "")
            diary = self._e(log.get("story_text") or log.get("summary") or "")
            memory_text = self._e(log.get("memory_text") or "")
            result = self._e(log.get("message") or "")
            changes = log.get("changes")
            if not isinstance(changes, list):
                changes = log.get("rewards") if isinstance(log.get("rewards"), list) else []
            change_html = "".join(f"<span class=\"tag\">{self._e(item)}</span>" for item in changes)
            created_at = self._format_time(log.get("created_at"))
            world_date = self._world_date_display(log)
            world_date_html = f"<span>{self._e(world_date)}</span>" if world_date else ""
            conversation_no = self._positive_int(log.get("conversation_no"))
            conversation_html = (
                f"<span>事件序号 #{conversation_no}</span>"
                if conversation_no > 0
                else ""
            )
            action_html = f"<p class=\"log-action\">{action}</p>" if action else ""
            diary_html = f"<p class=\"log-result\">{diary}</p>" if diary else ""
            memory_html = (
                f"<p class=\"log-result\"><strong>短事件记忆：</strong>{memory_text}</p>"
                if raw_log_type == "action_turn" and memory_text
                else ""
            )
            changes_block = (
                f"<div class=\"tag-row\">{change_html}</div>"
                if change_html
                else ""
            )
            delete_button = ""
            if allow_delete and log_index >= 0 and raw_log_type in {"action_turn", "memory_summary"}:
                delete_button = f"""
                  <form class="inline-form" method="post" action="{self._url('/player/log/delete')}" onsubmit="return confirm('确定删除这条行动记录？当前 state 不会自动回滚。');">
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
                      {conversation_html}
                      <span>{log_type}</span>
                    </div>
                  </summary>
                  <div class="log-card-body">
                    <div class="log-meta">
                      <span>{self._e(created_at)}</span>
                      {world_date_html}
                      {conversation_html}
                      <span>{log_type}</span>
                    </div>
                    {action_html}
                    {diary_html}
                    {memory_html}
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
            source_name = self._e(self._cameo_source_label(memory) or "未知角色")
            summary = self._e(memory.get("memory_text") or "")
            created_at = self._format_time(memory.get("created_at"))
            world_date = self._world_date_display(memory)
            world_date_html = f"<span>{self._e(world_date)}</span>" if world_date else ""
            conversation_no = self._positive_int(memory.get("conversation_no"))
            conversation_html = (
                f"<span>事件序号 #{conversation_no}</span>"
                if conversation_no > 0
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
                      {conversation_html}
                      <span>来源：{source_name}</span>
                    </div>
                  </summary>
                  <div class="log-card-body">
                    <div class="log-meta">
                      <span>{self._e(created_at)}</span>
                      {world_date_html}
                      {conversation_html}
                      <span>来源：{source_name}</span>
                    </div>
                    <p class="log-result">{summary}</p>
                  </div>
                </details>
                """
            )
        return "\n".join(cards)

    @staticmethod
    def _cameo_source_label(memory: dict[str, Any]) -> str:
        source_name = str(
            memory.get("source_name") or memory.get("source_target_name") or ""
        ).strip()
        magical_name = str(memory.get("source_magical_name") or "").strip()
        age = str(memory.get("source_age") or "").strip()
        identity = str(memory.get("source_identity") or "").strip()

        details = [value for value in (magical_name, age, identity) if value]
        if source_name and details:
            return f"{source_name}（{'，'.join(details)}）"
        if source_name:
            return source_name
        if details:
            return "（" + "，".join(details) + "）"
        return ""

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
            "技能进度",
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
              <p class="muted empty-state">暂无可展示的成长进度。</p>
            </section>
            """
        rows = "".join(
            f"""
            <div class="progress-row">
              <div class="progress-head">
                <div class="progress-name">
                  <span>{self._e(item.label)}</span>
                </div>
                <div class="progress-xp">{self._e(item.value)} <small>/ 100</small></div>
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
                  <p class="muted">包含当前 state 中除成长进度外的状态项；原始 JSON 可在上方展开查看。</p>
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

    def _relationship_graph_script(self) -> str:
        return r"""
(() => {
  const dataEl = document.getElementById("relationshipGraphData");
  const svg = document.getElementById("relationshipGraph");
  const empty = document.getElementById("relationshipEmpty");
  if (!dataEl || !svg) return;

  const graph = JSON.parse(dataEl.textContent || "{}");
  const nodes = (graph.nodes || []).map((node, index) => ({ ...node, index, x: 0, y: 0, vx: 0, vy: 0 }));
  const edges = graph.edges || [];
  const viewerId = String(graph.viewer_user_id || "");
  const byId = new Map(nodes.map((node) => [String(node.user_id), node]));
  const viewerEdges = new Map(
    edges.filter((edge) => String(edge.from_user_id) === viewerId).map((edge) => [String(edge.to_user_id), edge])
  );
  const targetEdges = new Map(
    edges.filter((edge) => String(edge.to_user_id) === viewerId).map((edge) => [String(edge.from_user_id), edge])
  );
  let selectedId = viewerEdges.keys().next().value || viewerId || (nodes[0] && String(nodes[0].user_id)) || "";
  let selectedPerspective = "out";

  const card = {
    name: document.getElementById("relationshipCardName"),
    player: document.getElementById("relationshipCardPlayer"),
    relationship: document.getElementById("relationshipCardRelationship"),
    impression: document.getElementById("relationshipCardImpression"),
    evidence: document.getElementById("relationshipCardEvidence"),
    summary: document.getElementById("relationshipCardSummary"),
    tags: document.getElementById("relationshipCardTags"),
    outButton: document.getElementById("relationshipPerspectiveOut"),
    inButton: document.getElementById("relationshipPerspectiveIn"),
  };
  const text = (value, fallback = "") => String(value || fallback || "").trim();
  const displayName = (node) => text(node && (node.magical_name || node.target_name || node.user_id), "Unknown");
  const directEdge = (node, perspective = selectedPerspective) => {
    const nodeId = String(node && node.user_id);
    return perspective === "in" ? targetEdges.get(nodeId) : viewerEdges.get(nodeId);
  };
  const setButtonState = (button, active, disabled, label) => {
    if (!button) return;
    button.textContent = label;
    button.classList.toggle("active", active);
    button.disabled = disabled;
    button.setAttribute("aria-pressed", active ? "true" : "false");
  };

  function updateCard(node) {
    if (!node) return;
    const outbound = directEdge(node, "out");
    const inbound = directEdge(node, "in");
    const targetName = displayName(node);
    const edge = directEdge(node);
    card.name.textContent = displayName(node);
    card.player.textContent = text(node.target_name) ? `玩家名：${node.target_name}` : "";
    card.relationship.textContent = edge ? text(edge.relationship, "-") : "-";
    card.impression.textContent = edge ? text(edge.impression, "暂无直接印象记录。") : "暂无直接印象记录。";
    card.evidence.textContent = edge ? text(edge.evidence, "-") : "-";
    card.summary.textContent = edge ? text(edge.summary, "-") : "-";
    setButtonState(card.outButton, selectedPerspective === "out", !outbound, `我眼里的${targetName}`);
    setButtonState(card.inButton, selectedPerspective === "in", !inbound, `${targetName}眼里的我`);
    card.tags.innerHTML = "";
    const tags = edge && Array.isArray(edge.tags) ? edge.tags : [];
    tags.forEach((tag) => {
      const span = document.createElement("span");
      span.textContent = text(tag);
      if (span.textContent) card.tags.appendChild(span);
    });
  }

  function layout(width, height) {
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.max(80, Math.min(width, height) * 0.34);
    nodes.forEach((node, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(1, nodes.length);
      node.x = cx + Math.cos(angle) * radius;
      node.y = cy + Math.sin(angle) * radius;
      node.vx = 0;
      node.vy = 0;
    });
    for (let step = 0; step < 260; step += 1) {
      for (let i = 0; i < nodes.length; i += 1) {
        for (let j = i + 1; j < nodes.length; j += 1) {
          const a = nodes[i];
          const b = nodes[j];
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          let dist2 = Math.max(1, dx * dx + dy * dy);
          const dist = Math.sqrt(dist2);
          const force = 5600 / dist2;
          dx /= dist;
          dy /= dist;
          a.vx -= dx * force;
          a.vy -= dy * force;
          b.vx += dx * force;
          b.vy += dy * force;
        }
      }
      edges.forEach((edge) => {
        const a = byId.get(String(edge.from_user_id));
        const b = byId.get(String(edge.to_user_id));
        if (!a || !b) return;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
        const force = (dist - 190) * 0.018;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      });
      nodes.forEach((node) => {
        node.vx += (cx - node.x) * 0.006;
        node.vy += (cy - node.y) * 0.006;
        node.vx *= 0.82;
        node.vy *= 0.82;
        node.x = Math.min(width - 76, Math.max(76, node.x + node.vx));
        node.y = Math.min(height - 58, Math.max(58, node.y + node.vy));
      });
    }
  }

  function render() {
    const rect = svg.getBoundingClientRect();
    const width = Math.max(320, rect.width || 720);
    const height = Math.max(360, rect.height || 560);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.innerHTML = "";
    empty.hidden = nodes.length > 0 && edges.length > 0;
    if (!nodes.length) return;
    layout(width, height);

    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    defs.innerHTML = '<marker id="relationshipArrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#b5539c"></path></marker>';
    svg.appendChild(defs);

    edges.forEach((edge) => {
      const a = byId.get(String(edge.from_user_id));
      const b = byId.get(String(edge.to_user_id));
      if (!a || !b) return;
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      group.setAttribute("class", "relationship-edge");
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", a.x);
      line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x);
      line.setAttribute("y2", b.y);
      line.setAttribute("marker-end", "url(#relationshipArrow)");
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", (a.x + b.x) / 2);
      label.setAttribute("y", (a.y + b.y) / 2 - 8);
      label.textContent = String(edge.from_user_id) === viewerId ? text(edge.relationship).slice(0, 12) : "";
      group.append(line);
      if (label.textContent) group.appendChild(label);
      svg.appendChild(group);
    });

    nodes.forEach((node) => {
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      group.setAttribute("class", `relationship-node${String(node.user_id) === String(selectedId) ? " selected" : ""}`);
      group.setAttribute("tabindex", "0");
      group.setAttribute("role", "button");
      group.setAttribute("aria-label", displayName(node));
      group.setAttribute("transform", `translate(${node.x}, ${node.y})`);
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("r", String(String(node.user_id) === viewerId ? 46 : 39));
      const name = document.createElementNS("http://www.w3.org/2000/svg", "text");
      name.setAttribute("class", "node-name");
      name.setAttribute("y", "-2");
      name.textContent = displayName(node).slice(0, 8);
      group.append(circle, name);
      group.addEventListener("click", () => {
        selectedId = String(node.user_id);
        selectedPerspective = "out";
        updateCard(node);
        render();
      });
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          group.dispatchEvent(new Event("click"));
        }
      });
      svg.appendChild(group);
    });
    updateCard(byId.get(String(selectedId)) || nodes[0]);
  }

  if (card.outButton) {
    card.outButton.addEventListener("click", () => {
      selectedPerspective = "out";
      updateCard(byId.get(String(selectedId)));
    });
  }
  if (card.inButton) {
    card.inButton.addEventListener("click", () => {
      selectedPerspective = "in";
      updateCard(byId.get(String(selectedId)));
    });
  }

  render();
  window.addEventListener("resize", () => window.requestAnimationFrame(render));
})();
"""

    def _html_response(
        self,
        title: str,
        content: str,
        status: int = 200,
        show_logout: bool = True,
    ) -> web.Response:
        logout_html = (
            f"""
            <form class="logout-form" method="post" action="{self._url('/logout')}" onsubmit="return confirm('确定要退出登录吗？');">
              <button class="secondary compact-button" type="submit">退出登录</button>
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
    .player-floating-actions {{ position: fixed; right: 22px; top: 50%; z-index: 10; display: grid; gap: 10px; transform: translateY(-50%); }}
    .player-floating-action {{ min-width: 66px; min-height: 42px; display: inline-flex; align-items: center; justify-content: center; padding: 0 12px; border: 1px solid rgba(255,255,255,.78); border-radius: 8px; background: linear-gradient(135deg, #ff5fae, #b56bff 52%, #45c9ee); color: #fff; font-size: 13px; font-weight: 900; box-shadow: 0 16px 34px rgba(180, 70, 176, .28), inset 0 1px 0 rgba(255,255,255,.42); text-shadow: 0 1px 8px rgba(89,31,116,.35); }}
    .player-floating-action:hover {{ text-decoration: none; filter: brightness(1.04); transform: translateY(-1px); }}
    main:has(.relationship-shell) .player-floating-actions {{ display: none; }}
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
    main:has(.relationship-shell) {{ width: 100%; max-width: none; min-height: 100vh; box-sizing: border-box; padding: 0; overflow: hidden; }}
    main:has(.relationship-shell) .topbar {{ position: absolute; top: 18px; right: 22px; z-index: 5; margin: 0; min-height: 0; }}
    main:has(.relationship-shell) .topbar button {{ border: 1px solid rgba(255,255,255,.68); background: rgba(112, 72, 156, .72); box-shadow: 0 12px 28px rgba(108, 53, 133, .18); backdrop-filter: blur(10px); }}
    .relationship-shell {{ position: relative; min-height: 100vh; padding: 70px clamp(16px, 4vw, 54px) 42px; box-sizing: border-box; overflow: hidden; background: radial-gradient(circle at 13% 16%, rgba(255,241,151,.82) 0 7%, transparent 21%), radial-gradient(circle at 82% 18%, rgba(139,229,255,.72) 0 8%, transparent 22%), radial-gradient(circle at 80% 82%, rgba(255,139,200,.52) 0 11%, transparent 25%), linear-gradient(135deg, #fff5fb 0%, #f7ddff 30%, #dff7ff 66%, #fff6c7 100%); color: #42233f; isolation: isolate; }}
    .relationship-shell::before {{ content: ""; position: absolute; inset: -20%; z-index: -2; background-image: linear-gradient(rgba(255,255,255,.48) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.42) 1px, transparent 1px); background-size: 42px 42px; transform: rotate(-7deg); }}
    .relationship-shell::after {{ content: ""; position: absolute; inset: 0; z-index: -1; background: radial-gradient(circle at 48% 38%, transparent 0 28%, rgba(255,255,255,.28) 29%, transparent 30%); }}
    .relationship-head {{ position: relative; max-width: 1180px; margin: 0 auto 18px; padding: 26px 128px 24px; border: 1px solid rgba(255,255,255,.72); border-radius: 8px; background: rgba(255,255,255,.5); box-shadow: 0 22px 64px rgba(141,76,146,.15), inset 0 0 0 1px rgba(255,255,255,.42); backdrop-filter: blur(14px); text-align: center; }}
    .relationship-head h1 {{ margin: 0 0 10px; color: #64204f; font-size: clamp(30px, 4vw, 52px); line-height: 1.05; text-shadow: 0 2px 0 #fff, 0 18px 40px rgba(204,70,157,.18); overflow-wrap: anywhere; }}
    .relationship-head p:last-child {{ max-width: 54em; margin: 0 auto; color: #67425e; line-height: 1.7; }}
    .relationship-layout {{ position: relative; max-width: 1360px; min-height: min(680px, calc(100vh - 250px)); margin: 0 auto; display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 340px); gap: 14px; }}
    .relationship-graph-panel, .relationship-card {{ border: 1px solid rgba(221,91,169,.28); border-radius: 8px; background: linear-gradient(180deg, rgba(255,255,255,.88), rgba(255,247,252,.76)); box-shadow: 0 18px 46px rgba(175,74,151,.14); backdrop-filter: blur(12px); }}
    .relationship-graph-panel {{ position: relative; min-height: 560px; overflow: hidden; }}
    .relationship-graph {{ width: 100%; height: 100%; min-height: 560px; display: block; }}
    .relationship-empty {{ position: absolute; left: 50%; top: 50%; margin: 0; padding: 18px 22px; border: 1px dashed rgba(207,84,161,.42); border-radius: 8px; background: rgba(255,255,255,.72); color: #76506c; transform: translate(-50%, -50%); }}
    .relationship-edge line {{ stroke: rgba(181,83,156,.58); stroke-width: 2; }}
    .relationship-edge text {{ paint-order: stroke; stroke: rgba(255,255,255,.92); stroke-width: 5px; fill: #7f3b73; font-size: 13px; font-weight: 900; text-anchor: middle; dominant-baseline: middle; pointer-events: none; }}
    .relationship-node {{ cursor: pointer; outline: none; }}
    .relationship-node circle {{ fill: url(#unused); stroke: rgba(255,255,255,.92); stroke-width: 3; filter: drop-shadow(0 12px 18px rgba(147,73,145,.22)); }}
    .relationship-node circle {{ fill: #ff8bc6; }}
    .relationship-node:nth-of-type(3n) circle {{ fill: #8fe8ff; }}
    .relationship-node:nth-of-type(3n+1) circle {{ fill: #ffd66b; }}
    .relationship-node.selected circle {{ stroke: #b56bff; stroke-width: 5; }}
    .relationship-node text {{ text-anchor: middle; dominant-baseline: middle; pointer-events: none; }}
    .relationship-node .node-name {{ fill: #4b2447; font-size: 13px; font-weight: 900; }}
    .relationship-card {{ align-self: stretch; padding: 20px; overflow: auto; }}
    .relationship-card-subtitle {{ margin: -6px 0 18px; color: #76506c; font-size: 13px; font-weight: 800; overflow-wrap: anywhere; }}
    .relationship-perspective {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }}
    .relationship-perspective button {{ flex: 1 1 132px; min-height: 34px; margin: 0; padding: 7px 10px; border: 1px solid rgba(211,91,165,.3); border-radius: 6px; background: rgba(255,255,255,.74); color: #744160; font-size: 12px; font-weight: 900; line-height: 1.2; overflow-wrap: anywhere; }}
    .relationship-perspective button.active {{ border-color: rgba(181,83,156,.7); background: #b5539c; color: #fff; box-shadow: 0 8px 18px rgba(181,83,156,.18); }}
    .relationship-perspective button:disabled {{ cursor: not-allowed; opacity: .45; box-shadow: none; }}
    .relationship-card-section {{ padding: 13px 0; border-top: 1px solid rgba(211,91,165,.22); }}
    .relationship-card-section span {{ display: block; margin-bottom: 6px; color: #c54793; font-size: 12px; font-weight: 900; text-transform: uppercase; letter-spacing: 0; }}
    .relationship-card-section p {{ margin: 0; color: #4b2447; line-height: 1.65; overflow-wrap: anywhere; }}
    .relationship-tag-row {{ display: flex; flex-wrap: wrap; gap: 8px; padding-top: 14px; border-top: 1px solid rgba(211,91,165,.22); }}
    .relationship-tag-row span {{ min-height: 26px; display: inline-flex; align-items: center; padding: 3px 9px; border: 1px solid rgba(211,91,165,.26); border-radius: 999px; background: rgba(255,255,255,.72); color: #744160; font-size: 12px; font-weight: 900; }}
    .player-profile-side {{ gap: 14px; align-content: start; }}
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
    #fetish-book-entries {{ display: grid; gap: 6px; }}
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
    .compact-field {{ display: flex; align-items: center; gap: 6px; margin: 0; }}
    .compact-field span {{ flex: 0 0 auto; color: #3a4350; }}
    .world-entry input[type="text"], .world-entry input[type="number"], .world-entry select {{ width: 100%; min-width: 0; box-sizing: border-box; padding: 6px 8px; border: 1px solid #c8d0dc; border-radius: 7px; font: inherit; background: #fbfdff; }}
    .block-field {{ margin-top: 12px; }}
    .monster-ending-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }}
    .monster-ending-grid .block-field {{ margin-top: 12px; }}
    .fetish-range-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; align-items: start; }}
    .fetish-range-grid .block-field:last-child {{ grid-column: 1 / -1; }}
    textarea.keys-editor {{ min-height: 48px; font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }}
    textarea.entry-content-editor {{ min-height: 78px; }}
    .monster-picker {{ margin-top: 12px; padding: 10px 12px; border: 1px solid #d9e1eb; border-radius: 8px; background: #f8fafc; }}
    .monster-picker-head {{ display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; margin-bottom: 8px; }}
    .monster-selected-list {{ display: flex; flex-wrap: wrap; gap: 8px; min-height: 30px; align-items: center; }}
    .monster-selected-chip {{ display: inline-flex; align-items: center; gap: 6px; padding: 4px 6px 4px 10px; border: 1px solid #c8d6e5; border-radius: 999px; background: #fff; color: #263241; font-size: 13px; font-weight: 800; }}
    .monster-selected-chip button {{ width: 22px; height: 22px; min-height: 0; display: inline-grid; place-items: center; margin: 0; padding: 0; border-radius: 50%; border: 1px solid #d9e1eb; background: #eef4fa; color: #536172; line-height: 1; }}
    .monster-selected-chip button:hover {{ background: #ffe7e3; color: #b42318; border-color: #f0b8b0; }}
    .monster-add-button {{ width: 30px; min-height: 30px; display: inline-grid; place-items: center; margin: 0; padding: 0; border-radius: 999px; font-size: 18px; line-height: 1; }}
    .monster-unmatched-tags {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 8px; padding: 7px 9px; border: 1px dashed #d3b969; border-radius: 8px; background: #fff9df; color: #6f5410; font-size: 13px; }}
    .monster-unmatched-tags code {{ padding: 2px 5px; border-radius: 5px; background: rgba(111,84,16,.1); }}
    .monster-picker-add, .monster-advanced-tags {{ margin-top: 10px; }}
    .monster-picker-add > summary, .monster-advanced-tags > summary {{ cursor: pointer; color: #1f5f99; font-weight: 900; }}
    .monster-picker-add input[type="search"] {{ width: 100%; box-sizing: border-box; margin: 8px 0; padding: 8px 10px; border: 1px solid #c8d0dc; border-radius: 7px; font: inherit; background: #fff; }}
    .monster-picker-options {{ max-height: 280px; display: grid; gap: 6px; overflow: auto; padding-right: 4px; }}
    .monster-picker-option {{ width: 100%; display: grid; gap: 4px; margin: 0; padding: 9px 10px; text-align: left; border: 1px solid #d9e1eb; border-radius: 8px; background: #fff; color: #263241; }}
    .monster-picker-option:hover:not(:disabled) {{ border-color: #8ab5e6; background: #f3f8ff; }}
    .monster-picker-option:disabled {{ opacity: .54; cursor: not-allowed; }}
    .monster-picker-main {{ display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }}
    .monster-picker-main strong {{ color: #172033; }}
    .monster-picker-main em {{ color: #68707d; font-style: normal; font-size: 12px; }}
    .monster-picker-preview {{ color: #3a4350; font-size: 13px; line-height: 1.45; }}
    .monster-picker-meta {{ color: #68707d; font-size: 12px; }}
    .monster-advanced-tags textarea.keys-editor {{ width: 100%; box-sizing: border-box; margin-top: 6px; background: #fff; }}
    .monster-modal[hidden] {{ display: none; }}
    .monster-modal {{ position: fixed; inset: 0; z-index: 40; display: grid; place-items: center; padding: 24px; }}
    .monster-modal-backdrop {{ position: absolute; inset: 0; background: rgba(15, 23, 42, .54); backdrop-filter: blur(4px); }}
    .monster-modal-panel {{ position: relative; width: min(980px, 100%); max-height: min(760px, calc(100vh - 48px)); display: grid; grid-template-rows: auto minmax(0, 1fr) auto; overflow: hidden; border: 1px solid #d9e1eb; border-radius: 8px; background: #fff; box-shadow: 0 24px 70px rgba(15, 23, 42, .32); }}
    .monster-modal-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; padding: 14px 16px; border-bottom: 1px solid #e5ebf2; background: #f8fafc; }}
    .monster-modal-head span {{ display: block; color: #68707d; font-size: 12px; font-weight: 900; text-transform: uppercase; }}
    .monster-modal-head h2 {{ margin: 2px 0 0; color: #172033; }}
    .monster-modal-actions {{ display: flex; justify-content: flex-end; gap: 10px; padding: 12px 16px; border-top: 1px solid #e5ebf2; background: #f8fafc; }}
    .monster-choice-grid {{ max-height: min(560px, calc(100vh - 190px)); display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; overflow: auto; padding: 16px; align-content: start; }}
    .monster-choice-card {{ min-height: 74px; display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 10px; align-items: center; margin: 0; padding: 12px; border: 1px solid #d8e0eb; border-radius: 8px; background: #fff; color: #263241; text-align: left; box-shadow: 0 5px 14px rgba(31, 41, 55, .05); }}
    .monster-choice-card:hover {{ border-color: #8ab5e6; background: #f6faff; transform: translateY(-1px); }}
    .monster-choice-card.selected {{ border-color: #1f6feb; background: #eef6ff; box-shadow: 0 0 0 3px rgba(31, 111, 235, .12); }}
    .monster-choice-check {{ width: 22px; height: 22px; display: grid; place-items: center; border: 1px solid #c8d6e5; border-radius: 6px; background: #f8fafc; color: #1f6feb; font-weight: 900; }}
    .monster-choice-card.selected .monster-choice-check {{ background: #1f6feb; border-color: #1f6feb; color: #fff; }}
    .delete-modal[hidden] {{ display: none; }}
    .delete-modal {{ position: fixed; inset: 0; z-index: 45; display: grid; place-items: center; padding: 24px; }}
    .delete-modal-backdrop {{ position: absolute; inset: 0; background: rgba(15, 23, 42, .54); backdrop-filter: blur(4px); }}
    .delete-modal-panel {{ position: relative; width: min(420px, 100%); padding: 18px; border: 1px solid #d9e1eb; border-radius: 8px; background: #fff; box-shadow: 0 24px 70px rgba(15, 23, 42, .32); }}
    .delete-modal-panel h2 {{ margin: 0 36px 8px 0; color: #172033; }}
    .delete-modal-close {{ position: absolute; top: 10px; right: 10px; width: 30px; height: 30px; min-height: 0; display: grid; place-items: center; padding: 0; border-radius: 50%; border-color: #d9e1eb; background: #f8fafc; color: #384252; font-size: 20px; line-height: 1; }}
    .delete-monster-option {{ display: flex; align-items: center; gap: 8px; margin: 16px 0; padding: 10px 12px; border: 1px solid #dde2ea; border-radius: 8px; background: #f8fafc; color: #263241; font-weight: 800; }}
    .delete-monster-option input {{ width: 16px; height: 16px; }}
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
    @media (max-width: 900px) {{ .world-entry-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 900px) {{ .state-overview-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 900px) {{ .detail-grid, .raw-grid {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 960px) {{ .player-detail-layout, .player-profile-triad, .player-split-grid, .player-memory-grid {{ grid-template-columns: 1fr; }} .player-profile-main {{ order: -1; }} .player-side-stack {{ grid-template-rows: auto; }} .player-profile-main .player-info-grid {{ grid-template-columns: 1fr; }} .primary-profile-card {{ grid-row: auto; }} .player-state-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 960px) {{ .relationship-layout {{ grid-template-columns: 1fr; min-height: 0; }} .relationship-card {{ max-height: none; }} }}
    @media (max-width: 720px) {{ .player-shell {{ padding: 76px 16px 34px; }} .player-hero {{ padding: 6px 74px 0 0; text-align: left; margin-bottom: 22px; }} .player-hero::before {{ width: 60px; height: 60px; font-size: 27px; }} .player-city-section {{ padding: 16px; }} .player-section-head {{ display: block; }} .player-city-card {{ grid-template-columns: 52px 1fr; padding: 15px; }} .city-card-orb {{ width: 46px; height: 46px; }} }}
    @media (max-width: 720px) {{ .player-detail-shell {{ --player-detail-width: 100%; padding: 62px 10px 24px; overflow: auto; }} .player-detail-shell .topbar {{ top: 10px; right: 10px; }} .player-detail-shell .topbar button {{ min-height: 30px; padding: 5px 8px; font-size: 12px; }} .player-detail-hero {{ margin-bottom: 12px; padding: 42px 54px 14px 12px; text-align: left; }} .player-detail-hero h1 {{ margin-bottom: 8px; font-size: clamp(24px, 9vw, 34px); }} .player-detail-hero p {{ font-size: 13px; line-height: 1.55; }} .player-detail-emblem {{ top: 12px; right: 12px; width: 42px; height: 42px; font-size: 21px; box-shadow: 0 10px 22px rgba(188, 80, 166, .2), inset 0 0 0 5px rgba(255,255,255,.48); }} .player-back-link {{ left: 10px; top: 10px; min-height: 28px; padding: 0 9px; font-size: 12px; }} .player-kicker, .player-section-head span, .city-card-label, .profile-card-head span {{ margin-bottom: 4px; font-size: 10px; }} .player-hero-tags {{ justify-content: flex-start; gap: 6px; margin-top: 10px; }} .player-hero-tags span {{ min-height: 22px; padding: 2px 7px; font-size: 11px; }} .player-detail-hero .player-top-grid {{ grid-template-columns: 1fr; gap: 7px; margin-top: 12px; }} .player-detail-hero .player-top-item, .player-top-item {{ min-height: 46px; padding: 8px 10px; }} .player-top-item span {{ font-size: 10px; }} .player-top-item strong {{ margin-top: 3px; font-size: 16px; }} .player-detail-flow, .player-profile-triad, .player-side-stack, .player-info-grid, .player-state-grid, .log-list {{ gap: 8px; }} .player-info-grid, .player-state-grid {{ grid-template-columns: 1fr; }} .player-profile-card, .player-site-section {{ padding: 10px; }} .player-profile-triad .player-profile-card {{ padding: 10px; }} .profile-card-head {{ margin-bottom: 8px; }} .profile-card-head h2, .player-profile-triad .profile-card-head h2 {{ font-size: 17px; }} .player-info-item, .player-state-item, .player-profile-triad .player-info-item {{ min-height: 40px; padding: 7px 8px; }} .player-info-item span, .player-state-item span, .player-profile-triad .player-info-item span {{ font-size: 10px; }} .player-info-item strong, .player-state-item strong, .player-profile-triad .player-info-item strong {{ margin-top: 2px; font-size: 12px; line-height: 1.35; }} .player-site-empty {{ padding: 12px; font-size: 12px; }} .player-memory-row .log-card-summary, .log-card-summary {{ padding: 9px 10px; }} .player-memory-row .log-card-body, .log-card-body {{ padding: 0 10px 10px; }} .log-card h3 {{ font-size: 14px; }} .log-meta, .log-result, .log-action {{ font-size: 12px; line-height: 1.55; }} .progress-list {{ gap: 10px; }} .progress-name {{ font-size: 14px; }} .progress-head {{ gap: 8px; margin-bottom: 5px; }} .progress-name::before {{ width: 12px; height: 12px; border-width: 2px; }} .progress-xp {{ font-size: 12px; }} .player-floating-actions {{ right: 10px; top: 50%; bottom: auto; transform: translateY(-50%); }} .player-floating-action {{ min-width: 48px; min-height: 34px; padding: 0 8px; font-size: 11px; border-radius: 7px; }} }}
    @media (max-width: 720px) {{ .relationship-shell {{ padding: 76px 14px 28px; overflow: auto; }} .relationship-head {{ padding: 58px 18px 20px; }} .relationship-head .player-kicker {{ display: none; }} .relationship-graph-panel {{ min-height: 430px; }} .relationship-graph {{ min-height: 430px; }} .relationship-card {{ padding: 16px; }} }}
    @media (max-width: 560px) {{ .state-overview-grid {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 560px) {{ .progress-list {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 560px) {{ .profile-edit-grid {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 720px) {{ .world-entry-grid, .book-config-grid, .monster-ending-grid {{ grid-template-columns: 1fr; }} .world-book-toolbar {{ flex-direction: column; }} .world-entry-head {{ flex-wrap: wrap; }} .hero-card {{ align-items: flex-start; }} .log-card-head {{ flex-direction: column; }} }}
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

    def _llm_record_pre(self, value: object) -> str:
        return self._e(value).replace("\\n", "\n")

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
    def _positive_int(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _world_date_display(item: dict[str, Any]) -> str:
        world_time = str(item.get("world_time") or "").strip()
        if world_time:
            return world_time
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
