from __future__ import annotations

import html
import hmac
import json
import re
import secrets
from typing import Any
from urllib.parse import quote

from aiohttp import web

from ...utils.logger import logger
from ..storage.recent_llm_message_repository import RecentLLMMessageRepository
from ..storage.state_progress import (
    build_progress_sections,
    build_state_display_items,
    level_display,
    level_exp_percent,
)


ADMIN_LOGIN_CODE = "优夏酱世界第一可爱"
SESSION_COOKIE_NAME = "qq_adventurer_session"
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
            <section class="login-panel">
              <h1>魔法少女网页登录</h1>
              <p class="muted">请输入 QQ 号登录。</p>
              {error_html}
              <form method="post" action="{self._url('/login')}">
                <label for="qq-id">QQ号</label>
                <input id="qq-id" name="qq_id" type="text" autocomplete="username" autofocus>
                <div class="actions">
                  <button type="submit">登录</button>
                </div>
              </form>
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

        saves = self.repository.list_saves()
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
                f"<td>{row['group_id']}</td>"
                f"<td>{row['user_id']}</td>"
                f"<td><a href=\"{row['href']}\">{row['nickname']}</a></td>"
                f"<td>{row['class_name']}</td>"
                f"<td>{row['level']}</td>"
                f"<td>{row['updated_at']}</td>"
                f"<td>{delete_form}</td>"
                "</tr>"
            )

        body = "\n".join(rows) or "<tr><td colspan=\"7\">还没有任何玩家存档。</td></tr>"
        return self._html_response(
            "魔法少女存档",
            f"""
            <h1>魔法少女存档</h1>
            <p class="muted">管理员页面。关闭网页命令会立即使当前网页登录态失效。</p>
            <p class="nav-actions">
              <a class="button-link" href="{self._url('/editable?category=world_background')}">编辑世界背景</a>
              <a class="button-link" href="{self._url('/editable?category=text_completion')}">编辑文本补全</a>
              <a class="button-link secondary-link" href="{self._url('/llm-messages')}">最近消息记录</a>
            </p>
            <table>
              <thead>
                <tr>
                  <th>群</th><th>用户</th><th>角色</th>
                  <th>职阶</th><th>等级</th><th>更新时间</th><th>操作</th>
                </tr>
              </thead>
              <tbody>{body}</tbody>
            </table>
            """,
        )

    def _user_index(self, user_id: str) -> web.Response:
        saves = self.repository.list_saves_by_user(user_id)
        rows = []
        for item in saves:
            row = self._save_table_row(item)
            rows.append(
                "<tr>"
                f"<td>{row['group_id']}</td>"
                f"<td><a href=\"{row['href']}\">{row['nickname']}</a></td>"
                f"<td>{row['class_name']}</td>"
                f"<td>{row['level']}</td>"
                f"<td>{row['updated_at']}</td>"
                "</tr>"
            )

        body = "\n".join(rows) or "<tr><td colspan=\"5\">还没有找到你的魔法少女存档。</td></tr>"
        return self._html_response(
            "我的魔法少女存档",
            f"""
            <h1>我的魔法少女存档</h1>
            <p class="muted">当前登录 QQ：{self._e(user_id)}</p>
            <table>
              <thead>
                <tr>
                  <th>群</th><th>角色</th><th>职阶</th><th>等级</th><th>更新时间</th>
                </tr>
              </thead>
              <tbody>{body}</tbody>
            </table>
            """,
        )

    def _save_table_row(self, item: dict[str, Any]) -> dict[str, str]:
        group_id = self._e(item.get("group_id", ""))
        user_id = self._e(item.get("user_id", ""))
        href = self._url(f"/player?group_id={group_id}&user_id={user_id}")
        return {
            "group_id": group_id,
            "user_id": user_id,
            "nickname": self._e(item.get("nickname") or item.get("target_name") or "未命名"),
            "class_name": self._e(item.get("class_name", "")),
            "level": self._e(f"{item.get('level', 1)}级"),
            "updated_at": self._format_time(item.get("updated_at")),
            "href": href,
        }

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
            <p class="muted">保存时会自动备份旧文件。世界书、技能书和状态书 default.json 会先做 JSON 校验。</p>
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
            if file_id == "region_book/default.json":
                return self._region_book_file_response(
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
        is_patch_book = file_id in {"skill_book/default.json", "status_book/default.json"}
        base_path_block = (
            f"""
              <div class="book-config-grid">
                <div>
                  <label for="book-display-name">展示名称</label>
                  <input id="book-display-name" type="text" value="{self._e(book.get('display_name') or self._default_book_display_name(file_id))}" spellcheck="false">
                </div>
                <div>
                  <label for="book-base-path">默认 patch 基础路径</label>
                  <input id="book-base-path" type="text" value="{self._e(book.get('base_path') or '')}" spellcheck="false">
                </div>
              </div>
              <p class="muted">这个路径会发给 AI 作为 update.patches 的路径提示，不代表 JSON 文件实际存放路径。</p>
            """
            if is_patch_book
            else ""
        )
        book_title = (
            "技能书条目"
            if file_id == "skill_book/default.json"
            else "状态书条目"
            if file_id == "status_book/default.json"
            else "世界书条目"
        )
        book_hint = (
            "每个条目会在命中后作为技能说明注入 Prompt。min_level 为等级门槛，max_level 为等级上限，不到门槛或超过上限的玩家无法看到该条目。"
            if file_id == "skill_book/default.json"
            else "条目标题代表可觉醒状态；content 是简单介绍，Lv.1 到 Lv.Max 分别填写当前等级效果。已拥有状态只注入简介和当前等级效果；“总是注入”的已拥有状态每次都会注入，未拥有时会在待觉醒列表附带简单介绍。状态最高 Lv.5；min_level 与 max_level 仍是玩家等级门槛。"
            if file_id == "status_book/default.json"
            else "每个条目会在命中后作为世界背景补充注入 Prompt。min_level 为等级门槛，max_level 为等级上限，不到门槛或超过上限的玩家无法看到该条目。"
        )
        storage_key = "qq_adventurer:book:open_entries:" + file_id.replace("/", ":")
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
              const isStatusBook = {str(file_id == "status_book/default.json").lower()};
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
                  min_level: 1,
                  max_level: 100,
                  content: "",
                  level_descriptions: {{}},
                }};
              }}

              function normalizeEntry(entry, index) {{
                const keys = Array.isArray(entry.keys)
                  ? entry.keys
                  : (typeof entry.keys === "string" ? [entry.keys] : []);
                const minLevel = Number.parseInt(entry.min_level, 10);
                const maxLevel = Number.parseInt(entry.max_level, 10);
                const safeMin = Number.isFinite(minLevel) && minLevel >= 1 ? minLevel : 1;
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
                  min_level: safeMin,
                  max_level: Number.isFinite(maxLevel) && maxLevel >= safeMin ? maxLevel : 100,
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
                  min_level: card.querySelector("[data-field='min_level']").value,
                  max_level: card.querySelector("[data-field='max_level']").value,
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
                        <span class="muted" style="margin-left:4px">Lv.${{normalized.min_level}}${{normalized.max_level < 100 ? "~" + normalized.max_level : ""}}</span>
                        <label class="summary-check"><input data-field="enabled" type="checkbox"${{normalized.enabled ? " checked" : ""}}> 启用</label>
                        <label class="summary-check"><input data-field="recursive" type="checkbox"${{normalized.recursive ? " checked" : ""}}> 允许递归</label>
                        <button class="danger" type="button" data-action="delete">删除</button>
                      </summary>
                      <div class="world-entry-body">
                        <div class="world-entry-grid">
                          <label class="compact-field"><span>ID</span><input data-field="id" type="text" value="${{escapeAttr(normalized.id)}}"></label>
                          <label class="compact-field"><span>标题</span><input data-field="title" type="text" value="${{escapeAttr(normalized.title)}}"></label>
                          <label class="compact-field"><span>等级门槛</span><input data-field="min_level" type="number" step="1" min="1" value="${{normalized.min_level}}"></label>
                          <label class="compact-field"><span>等级上限</span><input data-field="max_level" type="number" step="1" min="1" value="${{normalized.max_level}}"></label>
                          <label class="compact-field"><span>触发方式</span>
                            <select data-field="strategy">
                              <option value="keyword"${{normalized.strategy === "keyword" ? " selected" : ""}}>关键词命中</option>
                              <option value="always"${{normalized.strategy === "always" ? " selected" : ""}}>总是注入</option>
                            </select>
                          </label>
                        </div>
                        <label class="block-field">关键词（支持中文逗号、英文逗号或换行分隔；触发方式为“总是注入”时可留空）
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

    def _region_book_file_response(
        self,
        title: str,
        file_id: str,
        back_category: str,
        note: str,
        content: str,
    ) -> web.Response:
        try:
            book = self._normalize_region_book(json.loads(content))
        except Exception as exc:
            return self._plain_editable_file_response(
                title,
                file_id,
                back_category,
                note,
                content,
                warning=f"区域书 JSON 解析失败，请先修复原始 JSON：{exc}",
            )

        book_json = self._json_script_data(book)
        storage_key = "qq_adventurer:region_book:open_state"
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
            <p class="muted">区域书暂时按世界书方式匹配：命中关键词后注入详细介绍。简略介绍暂时保留，但永远不会触发。min_level 为等级门槛，max_level 为等级上限。排序由网页上下位置决定。</p>
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
            <form id="region-book-form" method="post" action="{self._url('/editable/save')}">
              <input type="hidden" name="id" value="{self._e(file_id)}">
              <input type="hidden" name="category" value="{self._e(back_category)}">
              <input id="region-book-content" type="hidden" name="content" value="">
              <label for="note">资源说明 / 注释</label>
              <textarea id="note" class="note-editor" name="note" spellcheck="false">{self._e(note)}</textarea>

              <div class="world-book-toolbar">
                <div>
                  <h2>区域列表</h2>
                </div>
                <button id="add-region" type="button">+ 添加区域</button>
              </div>
              <div id="region-book-regions"></div>
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
              const initialRegionBook = {book_json};
              const regionsEl = document.getElementById("region-book-regions");
              const addRegionButton = document.getElementById("add-region");
              const rbForm = document.getElementById("region-book-form");
              const rbContentInput = document.getElementById("region-book-content");
              const rbStorageKey = "{self._e(storage_key)}";

              const rbState = {{
                ...initialRegionBook,
                regions: Array.isArray(initialRegionBook.regions) ? initialRegionBook.regions : [],
              }};

              let rbDraggingRegionIdx = null;
              let rbDraggingEntryIdx = null;
              let rbDraggingRegionForEntry = null;
              let rbOpenState = {{ regions: new Set(), entries: new Set() }};
              let rbHasCapturedOpenState = false;

              function rbRegionDefaults(index) {{
                return {{
                  id: `region_${{index + 1}}`,
                  name: "",
                  entries: [],
                }};
              }}

              function rbEntryDefaults(index) {{
                return {{
                  id: `entry_${{index + 1}}`,
                  title: "",
                  enabled: true,
                  recursive: true,
                  strategy: "keyword",
                  keys: [],
                  min_level: 1,
                  max_level: 100,
                  brief: "",
                  content: "",
                }};
              }}

              function rbNormalizeRegion(region, index) {{
                return {{
                  id: String(region.id || `region_${{index + 1}}`).trim(),
                  name: String(region.name || ""),
                  entries: Array.isArray(region.entries) ? region.entries.map((e, ei) => rbNormalizeEntry(e, ei)) : [],
                }};
              }}

              function rbNormalizeEntry(entry, index) {{
                const keys = Array.isArray(entry.keys)
                  ? entry.keys
                  : (typeof entry.keys === "string" ? [entry.keys] : []);
                const minLevel = Number.parseInt(entry.min_level, 10);
                const maxLevel = Number.parseInt(entry.max_level, 10);
                const safeMin = Number.isFinite(minLevel) && minLevel >= 1 ? minLevel : 1;
                return {{
                  id: String(entry.id || `entry_${{index + 1}}`).trim(),
                  title: String(entry.title || ""),
                  enabled: entry.enabled !== false,
                  recursive: entry.recursive !== false,
                  strategy: entry.strategy === "always" ? "always" : "keyword",
                  keys: keys.map((key) => String(key).trim()).filter(Boolean),
                  min_level: safeMin,
                  max_level: Number.isFinite(maxLevel) && maxLevel >= safeMin ? maxLevel : 100,
                  brief: String(entry.brief || ""),
                  content: String(entry.content || ""),
                }};
              }}

              function rbSplitKeys(value) {{
                return String(value || "")
                  .split(/[\\n,，]/)
                  .map((key) => key.trim())
                  .filter(Boolean);
              }}

              function rbEscapeHtml(value) {{
                return String(value)
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
              }}

              function rbEscapeAttr(value) {{
                return rbEscapeHtml(value)
                  .replace(/"/g, "&quot;")
                  .replace(/'/g, "&#39;");
              }}

              function rbRegionKey(region, index) {{
                return String(region.id || region.name || `region_${{index + 1}}`).trim();
              }}

              function rbEntryKey(entry, rIdx, eIdx) {{
                return `${{rIdx}}_${{String(entry.id || entry.title || `entry_${{eIdx + 1}}`).trim()}}`;
              }}

              function rbCaptureOpenState() {{
                rbHasCapturedOpenState = true;
                rbOpenState.regions = new Set();
                rbOpenState.entries = new Set();
                regionsEl.querySelectorAll(".region-block").forEach((block, rIdx) => {{
                  const rKey = block.dataset.regionKey;
                  const rDetails = block.querySelector(":scope > details");
                  if (rKey && rDetails && rDetails.open) rbOpenState.regions.add(rKey);
                  block.querySelectorAll(".rb-entry").forEach((card, eIdx) => {{
                    const eKey = card.dataset.entryKey;
                    const eDetails = card.querySelector("details");
                    if (eKey && eDetails && eDetails.open) rbOpenState.entries.add(eKey);
                  }});
                }});
                rbPersistOpenState();
              }}

              function rbLoadOpenState() {{
                try {{
                  const raw = localStorage.getItem(rbStorageKey);
                  if (!raw) return;
                  const data = JSON.parse(raw);
                  if (!data) return;
                  if (Array.isArray(data.openRegions)) rbOpenState.regions = new Set(data.openRegions.map(String));
                  if (Array.isArray(data.openEntries)) rbOpenState.entries = new Set(data.openEntries.map(String));
                  rbHasCapturedOpenState = true;
                }} catch (error) {{
                  console.warn("failed to load region book open state", error);
                }}
              }}

              function rbPersistOpenState() {{
                try {{
                  localStorage.setItem(rbStorageKey, JSON.stringify({{
                    openRegions: Array.from(rbOpenState.regions),
                    openEntries: Array.from(rbOpenState.entries),
                  }}));
                }} catch (error) {{
                  console.warn("failed to save region book open state", error);
                }}
              }}

              function rbSyncFromDom() {{
                rbState.regions = Array.from(regionsEl.querySelectorAll(".region-block")).map((block, rIdx) => {{
                  const region = rbNormalizeRegion(rbState.regions[rIdx] || {{}}, rIdx);
                  region.id = block.querySelector("[data-field='region-id']").value;
                  region.name = block.querySelector("[data-field='region-name']").value;
                  region.entries = Array.from(block.querySelectorAll(".rb-entry")).map((card, eIdx) => rbNormalizeEntry({{
                    id: card.querySelector("[data-field='id']").value,
                    title: card.querySelector("[data-field='title']").value,
                    enabled: card.querySelector("[data-field='enabled']").checked,
                    recursive: card.querySelector("[data-field='recursive']").checked,
                    strategy: card.querySelector("[data-field='strategy']").value,
                    keys: rbSplitKeys(card.querySelector("[data-field='keys']").value),
                    min_level: card.querySelector("[data-field='min_level']").value,
                    max_level: card.querySelector("[data-field='max_level']").value,
                    brief: card.querySelector("[data-field='brief']").value,
                    content: card.querySelector("[data-field='content']").value,
                  }}, eIdx));
                  return region;
                }});
              }}

              function rbRenderRegions() {{
                regionsEl.innerHTML = "";
                rbState.regions.forEach((region, rIdx) => {{
                  const norm = rbNormalizeRegion(region, rIdx);
                  const rKey = rbRegionKey(norm, rIdx);
                  const rIsOpen = rbHasCapturedOpenState && rbOpenState.regions.has(rKey);
                  const regionEl = document.createElement("section");
                  regionEl.className = "region-block";
                  regionEl.dataset.regionKey = rKey;
                  regionEl.dataset.regionIndex = rIdx;

                  const entryCount = norm.entries.length;
                  const summaryText = norm.name || norm.id || `区域 ${{rIdx + 1}}`;

                  let entriesHtml = "";
                  norm.entries.forEach((entry, eIdx) => {{
                    const eNorm = rbNormalizeEntry(entry, eIdx);
                    const eKey = rbEntryKey(eNorm, rIdx, eIdx);
                    const eIsOpen = rbHasCapturedOpenState && rbOpenState.entries.has(eKey);
                    const entrySummary = eNorm.title || eNorm.id || `条目 ${{eIdx + 1}}`;
                    entriesHtml += `
                      <div class="rb-entry" data-entry-key="${{rbEscapeAttr(eKey)}}" data-entry-index="${{eIdx}}">
                        <details${{eIsOpen ? " open" : ""}}>
                          <summary class="world-entry-head">
                            <button class="drag-handle" type="button" data-action="drag-entry" draggable="true" title="拖动排序" aria-label="拖动排序">☰</button>
                            <span class="entry-title">${{rbEscapeHtml(entrySummary)}}</span>
                            <label class="summary-check"><input data-field="enabled" type="checkbox"${{eNorm.enabled ? " checked" : ""}}> 启用</label>
                            <label class="summary-check"><input data-field="recursive" type="checkbox"${{eNorm.recursive ? " checked" : ""}}> 允许递归</label>
                            <span class="muted" style="margin-left:4px">Lv.${{eNorm.min_level}}${{eNorm.max_level < 100 ? "~" + eNorm.max_level : ""}}</span>
                            <button class="danger" type="button" data-action="delete-entry">删除</button>
                          </summary>
                          <div class="world-entry-body">
                            <div class="world-entry-grid">
                              <label class="compact-field"><span>ID</span><input data-field="id" type="text" value="${{rbEscapeAttr(eNorm.id)}}"></label>
                              <label class="compact-field"><span>标题</span><input data-field="title" type="text" value="${{rbEscapeAttr(eNorm.title)}}"></label>
                              <label class="compact-field"><span>等级门槛</span><input data-field="min_level" type="number" step="1" min="1" value="${{eNorm.min_level}}"></label>
                              <label class="compact-field"><span>等级上限</span><input data-field="max_level" type="number" step="1" min="1" value="${{eNorm.max_level}}"></label>
                              <label class="compact-field"><span>触发方式</span>
                                <select data-field="strategy">
                                  <option value="keyword"${{eNorm.strategy === "keyword" ? " selected" : ""}}>关键词命中</option>
                                  <option value="always"${{eNorm.strategy === "always" ? " selected" : ""}}>总是注入</option>
                                </select>
                              </label>
                            </div>
                            <label class="block-field">关键词（支持中文逗号、英文逗号或换行分隔；触发方式为"总是注入"时可留空）
                              <textarea data-field="keys" class="keys-editor" spellcheck="false">${{rbEscapeHtml(eNorm.keys.join("\\n"))}}</textarea>
                            </label>
                            <label class="block-field">简略介绍（暂不触发，仅保留备用）
                              <textarea data-field="brief" class="entry-content-editor" spellcheck="false" style="height:60px">${{rbEscapeHtml(eNorm.brief)}}</textarea>
                            </label>
                            <label class="block-field">详细介绍（关键词命中时注入）
                              <textarea data-field="content" class="entry-content-editor" spellcheck="false">${{rbEscapeHtml(eNorm.content)}}</textarea>
                            </label>
                          </div>
                        </details>
                      </div>
                    `;
                  }});

                  regionEl.innerHTML = `
                    <details${{rIsOpen ? " open" : ""}}>
                      <summary class="world-entry-head region-head">
                        <button class="drag-handle" type="button" data-action="drag-region" draggable="true" title="拖动排序" aria-label="拖动排序">☰</button>
                        <span class="entry-title">${{rbEscapeHtml(summaryText)}}</span>
                        <span class="muted" style="margin-left:4px">(${{entryCount}} 个条目)</span>
                        <button class="danger" type="button" data-action="delete-region">删除区域</button>
                      </summary>
                      <div class="region-body">
                        <div class="world-entry-grid">
                          <label class="compact-field"><span>区域 ID</span><input data-field="region-id" type="text" value="${{rbEscapeAttr(norm.id)}}"></label>
                          <label class="compact-field"><span>区域名称</span><input data-field="region-name" type="text" value="${{rbEscapeAttr(norm.name)}}" placeholder="如：艾尔森林、碧叶镇、德尔地下城"></label>
                        </div>
                        <div class="world-book-toolbar" style="margin-top:8px">
                          <button class="secondary" type="button" data-action="add-entry">+ 添加条目</button>
                        </div>
                        <div class="region-entries">${{entriesHtml}}</div>
                      </div>
                    </details>
                  `;

                  // Region details toggle
                  const rDetails = regionEl.querySelector(":scope > details");
                  rDetails.addEventListener("toggle", () => {{
                    if (rDetails.open) rbOpenState.regions.add(rKey);
                    else rbOpenState.regions.delete(rKey);
                    rbHasCapturedOpenState = true;
                    rbPersistOpenState();
                  }});

                  // Region name/id sync to summary
                  const nameInput = regionEl.querySelector("[data-field='region-name']");
                  const idInput = regionEl.querySelector("[data-field='region-id']");
                  const titleEl = regionEl.querySelector(".region-head .entry-title");
                  const refreshRegionTitle = () => {{
                    titleEl.textContent = nameInput.value.trim() || idInput.value.trim() || `区域 ${{rIdx + 1}}`;
                  }};
                  nameInput.addEventListener("input", refreshRegionTitle);
                  idInput.addEventListener("input", refreshRegionTitle);

                  // Delete region
                  regionEl.querySelector("[data-action='delete-region']").addEventListener("click", (event) => {{
                    event.preventDefault();
                    event.stopPropagation();
                    if (!confirm("确定删除整个区域及其所有条目？")) return;
                    rbCaptureOpenState();
                    rbSyncFromDom();
                    rbState.regions.splice(rIdx, 1);
                    rbOpenState.regions.delete(rKey);
                    rbPersistOpenState();
                    rbRenderRegions();
                  }});

                  // Add entry
                  regionEl.querySelector("[data-action='add-entry']").addEventListener("click", (event) => {{
                    event.preventDefault();
                    event.stopPropagation();
                    rbCaptureOpenState();
                    rbSyncFromDom();
                    const newEntry = rbEntryDefaults(rbState.regions[rIdx].entries.length);
                    rbState.regions[rIdx].entries.push(newEntry);
                    // Auto-open the new entry
                    const eKey = rbEntryKey(newEntry, rIdx, rbState.regions[rIdx].entries.length - 1);
                    rbOpenState.entries.add(eKey);
                    // Also open the region
                    rbOpenState.regions.add(rKey);
                    rbPersistOpenState();
                    rbRenderRegions();
                  }});

                  // Region drag
                  const regionDragHandle = regionEl.querySelector("[data-action='drag-region']");
                  regionDragHandle.addEventListener("click", (event) => {{
                    event.preventDefault();
                    event.stopPropagation();
                  }});
                  regionDragHandle.addEventListener("dragstart", (event) => {{
                    rbSyncFromDom();
                    rbDraggingRegionIdx = rIdx;
                    regionEl.classList.add("dragging");
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", `region:${{rIdx}}`);
                  }});
                  regionDragHandle.addEventListener("dragend", () => {{
                    rbDraggingRegionIdx = null;
                    regionEl.classList.remove("dragging");
                    regionsEl.querySelectorAll(".drag-over").forEach((item) => item.classList.remove("drag-over"));
                  }});
                  regionEl.addEventListener("dragover", (event) => {{
                    if (rbDraggingRegionIdx === null || rbDraggingRegionIdx === rIdx) return;
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                    regionEl.classList.add("drag-over");
                  }});
                  regionEl.addEventListener("dragleave", () => {{
                    regionEl.classList.remove("drag-over");
                  }});
                  regionEl.addEventListener("drop", (event) => {{
                    event.preventDefault();
                    regionEl.classList.remove("drag-over");
                    if (rbDraggingRegionIdx === null || rbDraggingRegionIdx === rIdx) return;
                    rbCaptureOpenState();
                    rbSyncFromDom();
                    const moved = rbState.regions.splice(rbDraggingRegionIdx, 1)[0];
                    rbState.regions.splice(rIdx, 0, moved);
                    rbDraggingRegionIdx = null;
                    rbRenderRegions();
                  }});

                  // Entry events
                  regionEl.querySelectorAll(".rb-entry").forEach((entryCard, eIdx) => {{
                    const eDetails = entryCard.querySelector("details");
                    const eKey = entryCard.dataset.entryKey;

                    eDetails.addEventListener("toggle", () => {{
                      if (eDetails.open) rbOpenState.entries.add(eKey);
                      else rbOpenState.entries.delete(eKey);
                      rbHasCapturedOpenState = true;
                      rbPersistOpenState();
                    }});

                    entryCard.querySelector(".summary-check").addEventListener("click", (event) => {{
                      event.stopPropagation();
                    }});

                    // Delete entry
                    entryCard.querySelector("[data-action='delete-entry']").addEventListener("click", (event) => {{
                      event.preventDefault();
                      event.stopPropagation();
                      if (!confirm("确定删除这个条目？")) return;
                      rbCaptureOpenState();
                      rbSyncFromDom();
                      rbState.regions[rIdx].entries.splice(eIdx, 1);
                      rbOpenState.entries.delete(eKey);
                      rbPersistOpenState();
                      rbRenderRegions();
                    }});

                    // Entry title sync
                    const titleInput = entryCard.querySelector("[data-field='title']");
                    const idInput = entryCard.querySelector("[data-field='id']");
                    const entryTitleEl = entryCard.querySelector(".entry-title");
                    const refreshEntryTitle = () => {{
                      entryTitleEl.textContent = titleInput.value.trim() || idInput.value.trim() || `条目 ${{eIdx + 1}}`;
                    }};
                    titleInput.addEventListener("input", refreshEntryTitle);
                    idInput.addEventListener("input", refreshEntryTitle);

                    // Entry drag
                    const entryDragHandle = entryCard.querySelector("[data-action='drag-entry']");
                    entryDragHandle.addEventListener("click", (event) => {{
                      event.preventDefault();
                      event.stopPropagation();
                    }});
                    entryDragHandle.addEventListener("dragstart", (event) => {{
                      rbSyncFromDom();
                      rbDraggingEntryIdx = eIdx;
                      rbDraggingRegionForEntry = rIdx;
                      entryCard.classList.add("dragging");
                      event.dataTransfer.effectAllowed = "move";
                      event.dataTransfer.setData("text/plain", `entry:${{rIdx}}:${{eIdx}}`);
                    }});
                    entryDragHandle.addEventListener("dragend", () => {{
                      rbDraggingEntryIdx = null;
                      rbDraggingRegionForEntry = null;
                      entryCard.classList.remove("dragging");
                      regionsEl.querySelectorAll(".drag-over").forEach((item) => item.classList.remove("drag-over"));
                    }});
                    entryCard.addEventListener("dragover", (event) => {{
                      // Only allow drop within same region
                      if (rbDraggingRegionForEntry !== rIdx || rbDraggingEntryIdx === null || rbDraggingEntryIdx === eIdx) return;
                      event.preventDefault();
                      event.dataTransfer.dropEffect = "move";
                      entryCard.classList.add("drag-over");
                    }});
                    entryCard.addEventListener("dragleave", () => {{
                      entryCard.classList.remove("drag-over");
                    }});
                    entryCard.addEventListener("drop", (event) => {{
                      event.preventDefault();
                      entryCard.classList.remove("drag-over");
                      if (rbDraggingRegionForEntry !== rIdx || rbDraggingEntryIdx === null || rbDraggingEntryIdx === eIdx) return;
                      rbCaptureOpenState();
                      rbSyncFromDom();
                      const entries = rbState.regions[rIdx].entries;
                      const movedEntry = entries.splice(rbDraggingEntryIdx, 1)[0];
                      const targetIdx = rbDraggingEntryIdx < eIdx ? eIdx - 1 : eIdx;
                      entries.splice(targetIdx, 0, movedEntry);
                      rbDraggingEntryIdx = null;
                      rbDraggingRegionForEntry = null;
                      rbRenderRegions();
                    }});
                  }});

                  regionsEl.appendChild(regionEl);
                }});
              }}

              addRegionButton.addEventListener("click", () => {{
                rbCaptureOpenState();
                rbSyncFromDom();
                const newRegion = rbRegionDefaults(rbState.regions.length);
                rbState.regions.push(newRegion);
                const rKey = rbRegionKey(newRegion, rbState.regions.length - 1);
                rbOpenState.regions.add(rKey);
                rbPersistOpenState();
                rbRenderRegions();
              }});

              rbForm.addEventListener("submit", () => {{
                rbSyncFromDom();
                rbContentInput.value = JSON.stringify(rbState, null, 2);
              }});

              rbState.regions = rbState.regions.map((r, i) => rbNormalizeRegion(r, i));
              rbLoadOpenState();
              rbRenderRegions();
            </script>
            """,
        )

    @staticmethod
    def _normalize_region_book(raw: object) -> dict:
        book = dict(raw) if isinstance(raw, dict) else {}
        raw_regions = book.get("regions", [])
        if not isinstance(raw_regions, list):
            raw_regions = []

        normalized_regions = []
        for r_idx, raw_region in enumerate(raw_regions):
            if not isinstance(raw_region, dict):
                continue
            region_id = str(raw_region.get("id") or f"region_{r_idx + 1}").strip()
            region_name = str(raw_region.get("name") or "").strip()

            raw_entries = raw_region.get("entries", [])
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
                try:
                    min_level = int(raw_entry.get("min_level", 1))
                except (TypeError, ValueError):
                    min_level = 1
                try:
                    max_level = int(raw_entry.get("max_level", 100))
                except (TypeError, ValueError):
                    max_level = 100
                if max_level < min_level:
                    max_level = min_level
                normalized_entries.append({
                    "id": str(raw_entry.get("id") or f"entry_{e_idx + 1}").strip(),
                    "title": str(raw_entry.get("title") or ""),
                    "enabled": raw_entry.get("enabled", True) is not False,
                    "recursive": raw_entry.get("recursive", True) is not False,
                    "strategy": (
                        "always"
                        if str(raw_entry.get("strategy") or "keyword").strip().lower() == "always"
                        else "keyword"
                    ),
                    "keys": [str(key).strip() for key in keys if str(key).strip()],
                    "min_level": max(1, min_level),
                    "max_level": max(max(1, min_level), max_level),
                    "brief": str(raw_entry.get("brief") or ""),
                    "content": str(raw_entry.get("content") or ""),
                })

            normalized_regions.append({
                "id": region_id,
                "name": region_name,
                "entries": normalized_entries,
            })

        book["version"] = book.get("version", 1)
        book["regions"] = normalized_regions
        return book

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
            elif file_id in {"skill_book/default.json", "status_book/default.json"}:
                self.editable_manager.write_json_book(file_id, content)
            elif file_id == "region_book/default.json":
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

    @staticmethod
    def _normalize_world_book(raw: object) -> dict:
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
            try:
                min_level = int(entry.get("min_level", 1))
            except (TypeError, ValueError):
                min_level = 1
            try:
                max_level = int(entry.get("max_level", 100))
            except (TypeError, ValueError):
                max_level = 100
            if max_level < min_level:
                max_level = min_level
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
                    "min_level": max(1, min_level),
                    "max_level": max(max(1, min_level), max_level),
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
        # Check if this is a region book format
        if isinstance(raw, dict) and "regions" in raw:
            return cls._format_region_book_key_info(raw)

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
            key=lambda item: (int(item[1].get("min_level", 1)), item[0]),
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
            if file_id == "status_book/default.json":
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
    def _format_region_book_key_info(cls, raw: object) -> str:
        book = cls._normalize_region_book(raw)
        regions = book.get("regions", [])
        if not isinstance(regions, list):
            return ""

        region_blocks: list[str] = []
        for region in regions:
            if not isinstance(region, dict):
                continue
            region_name = str(region.get("name") or region.get("id") or "未知区域")
            entries = region.get("entries", [])
            if not isinstance(entries, list):
                continue

            entry_lines: list[str] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                title = cls._single_line_text(
                    entry.get("title") or entry.get("id") or "条目"
                )
                min_level = int(entry.get("min_level", 1))
                brief = str(entry.get("brief") or "").strip()
                content = str(entry.get("content") or "").strip()
                entry_lines.append(f"  [{title}] (Lv.{min_level})")
                if brief:
                    entry_lines.append(f"    简略: {brief}")
                if content:
                    entry_lines.append(f"    详细: {content}")

            if entry_lines:
                region_blocks.append(f"【{region_name}】\n" + "\n".join(entry_lines))

        return "\n\n-----------------------------------------------\n\n".join(region_blocks) + ("\n" if region_blocks else "")

    @staticmethod
    def _single_line_text(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _default_book_display_name(file_id: str) -> str:
        if file_id == "skill_book/default.json":
            return "技能&熟练度"
        if file_id == "status_book/default.json":
            return "特殊状态"
        return ""

    @staticmethod
    def _json_script_data(value: object) -> str:
        return (
            json.dumps(value, ensure_ascii=False)
            .replace("</", "<\\/")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
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

        profile = detail.get("profile", {})
        state = detail.get("state", {})
        logs = detail.get("logs", [])
        cameo_memories = detail.get("cameo_memories", [])
        card = profile.get("card", {}) if isinstance(profile, dict) else {}
        title_name = card.get("target_name") or profile.get("nickname") or user_id
        summary = self._player_summary_html(
            group_id,
            user_id,
            profile,
            state,
            card,
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
        progress_overview = self._progress_overview_html(state)
        state_overview = self._state_overview_html(state)
        log_note = (
            "删除单条记录只会移除 adventure_log.jsonl 中对应一行，不会回滚当前 state。"
            if is_admin
            else "这里展示该存档最近的冒险记录。"
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
                "删除全部冒险记录",
                "确定删除该玩家的全部冒险记录？当前 state 不会自动回滚。",
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
                "确定刷新 state.json？等级、物品、技能和状态进度都会恢复为初始值。",
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
              <span>删除该玩家的 profile、state 和 adventure_log。</span>
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
                  <h2>冒险记录</h2>
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
                <summary>查看 profile.json</summary>
                <pre>{self._e_json(profile)}</pre>
              </details>
              <div class="raw-panel raw-panel-wrapper">
                <div class="raw-panel-head">
                  <strong>state.json</strong>
                  {state_reset_button}
                </div>
                <details>
                  <summary>查看 state.json</summary>
                  <pre>{self._e_json(state)}</pre>
                </details>
              </div>
            </section>
            {state_overview}
            {source_file_panel}
            {danger_zone}
            """,
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
            "title",
            "subtitle",
            "target_name",
            "class_name",
            "appearance",
            "personality",
            "talent",
            "birth_description",
            "quote",
            "footer",
        ]
        updates: dict[str, Any] = {
            key: str(data.get(key, ""))
            for key in fields
        }
        likes_text = str(data.get("likes", ""))
        updates["likes"] = [
            item.strip()
            for item in re.split(r"[\n,，、]+", likes_text)
            if item.strip()
        ]

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
        profile: dict[str, Any],
        state: dict[str, Any],
        card: dict[str, Any],
        can_edit: bool = False,
    ) -> str:
        avatar_html = "<span>転</span>"
        likes = card.get("likes") if isinstance(card.get("likes"), list) else []
        like_items = "".join(
            f"<span class=\"tag\">{self._e(item)}</span>"
            for item in likes[:8]
        )
        likes_text = "\n".join(str(item) for item in likes)
        created_at = self._format_time(profile.get("created_at"))
        updated_at = self._format_time(state.get("updated_at") or profile.get("updated_at"))
        profile_panel = (
            f"""
              <form class="detail-panel profile-edit-panel" method="post" action="{self._url('/player/profile/save')}">
                <input type="hidden" name="group_id" value="{self._e(group_id)}">
                <input type="hidden" name="user_id" value="{self._e(user_id)}">
                <div class="section-head profile-edit-head">
                  <h2>角色档案</h2>
                  <button class="compact-button" type="submit">保存</button>
                </div>
                <div class="profile-edit-grid">
                  {self._profile_edit_field("标题", "title", card.get("title"))}
                  {self._profile_edit_field("副标题", "subtitle", card.get("subtitle"))}
                  {self._profile_edit_field("姓名", "target_name", card.get("target_name"))}
                  {self._profile_edit_field("职业", "class_name", card.get("class_name"))}
                </div>
                {self._profile_edit_field("外貌", "appearance", card.get("appearance"), multiline=True)}
                {self._profile_edit_field("性格", "personality", card.get("personality"), multiline=True)}
                {self._profile_edit_field("天赋", "talent", card.get("talent"), multiline=True)}
                {self._profile_edit_field("初醒之地", "birth_description", card.get("birth_description"), multiline=True)}
                {self._profile_edit_field("喜好", "likes", likes_text, multiline=True)}
                {self._profile_edit_field("台词", "quote", card.get("quote"), multiline=True)}
                {self._profile_edit_field("页脚", "footer", card.get("footer"))}
              </form>
            """
            if can_edit
            else f"""
              <article class="detail-panel">
                <h2>角色档案</h2>
                {self._profile_field("外貌", card.get("appearance"))}
                {self._profile_field("性格", card.get("personality"))}
                {self._profile_field("天赋", card.get("talent"))}
                {self._profile_field("初醒之地", card.get("birth_description"))}
                {self._profile_field("台词", card.get("quote"))}
              </article>
            """
        )
        return f"""
            <section class="hero-card">
              <div class="avatar-large">{avatar_html}</div>
              <div class="hero-main">
                <div class="kicker">群 {self._e(group_id)} / 用户 {self._e(user_id)}</div>
                <h2>{self._e(card.get("title") or "魔法少女转生人物卡")}</h2>
                <p class="subtitle">{self._e(card.get("subtitle") or "")}</p>
                <div class="identity-line">
                  <span>{self._e(level_display(state))}</span>
                  <span>等级经验 {self._e(level_exp_percent(state))}%</span>
                  <span>{self._e(card.get("class_name") or "未知职阶")}</span>
                </div>
              </div>
            </section>
            <section class="detail-grid">
              {profile_panel}
              <article class="detail-panel">
                <h2>当前状态</h2>
                <div class="tag-row">{like_items}</div>
                <div class="meta-list">
                  <div><span>HP</span><strong>{self._e(state.get("hp", 100))}</strong></div>
                  <div><span>MP</span><strong>{self._e(state.get("mp", 100))}</strong></div>
                  <div><span>等级经验</span><strong>{self._e(level_exp_percent(state))}%</strong></div>
                  <div><span>创建</span><strong>{self._e(created_at)}</strong></div>
                  <div><span>更新</span><strong>{self._e(updated_at)}</strong></div>
                </div>
              </article>
            </section>
        """

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
            return "<p class=\"muted empty-state\">还没有冒险记录。</p>"

        cards: list[str] = []
        for display_index, log in enumerate(logs, start=1):
            log_index = int(log.get("_log_index", -1))
            raw_log_type = str(log.get("type", "log"))
            log_type = self._e(raw_log_type)
            title = self._e(log.get("title") or log.get("message") or "冒险记录")
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
            if allow_delete and log_index >= 0 and raw_log_type == "adventure_diary":
                delete_button = f"""
                  <form class="inline-form" method="post" action="{self._url('/player/log/delete')}" onsubmit="return confirm('确定删除这条冒险记录？当前 state 不会自动回滚。');">
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
                "status_book/default.json",
                "/主角/快感状态/性癖/",
            ),
            limit=16,
        )
        skill_title = self.editable_manager.read_book_display_name(
            "skill_book/default.json",
            "技能&熟练度",
        )
        status_title = self.editable_manager.read_book_display_name(
            "status_book/default.json",
            "特殊状态",
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

    def _state_overview_html(self, state: dict[str, Any]) -> str:
        items = build_state_display_items(state, limit=40)
        if not items:
            return ""
        item_html = "".join(
            f"""
            <div class="state-item">
              <span>{self._e(label)}</span>
              <strong>{self._e(value)}</strong>
            </div>
            """
            for label, value in items
        )
        return f"""
            <section class="detail-panel state-overview-panel">
              <h2>完整状态</h2>
              <p class="muted">包含当前 state 中除经验进度外的状态项；原始 JSON 可在上方展开查看。</p>
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

        self.repository.delete_adventure_log(group_id, user_id, log_index)
        raise web.HTTPFound(
            self._url(f"/player?group_id={self._e(group_id)}&user_id={self._e(user_id)}")
        )

    async def _player_log_clear(self, request: web.Request) -> web.Response:
        group_id, user_id = await self._admin_player_action_ids(request)
        self.repository.clear_adventure_logs(group_id, user_id)
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
    .login-panel {{ max-width: 420px; margin: 12vh auto 0; padding: 24px; border: 1px solid #dde2ea; border-radius: 8px; background: #fff; box-shadow: 0 10px 24px rgba(31, 41, 55, 0.06); }}
    .danger-zone {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 16px 0 22px; padding: 14px 16px; border: 1px solid #f0b8b0; border-radius: 8px; background: #fff5f3; color: #6f1d15; }}
    .danger-zone span {{ color: #7a3b34; }}
    .world-book-toolbar {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin: 16px 0 8px; padding: 10px 12px; border: 1px solid #d9e1eb; border-radius: 8px; background: rgba(255,255,255,0.86); box-shadow: 0 6px 14px rgba(31, 41, 55, 0.05); }}
    .world-book-toolbar h2 {{ margin: 0 0 3px; }}
    .world-book-toolbar p {{ margin: 0; }}
    .book-config-grid {{ display: grid; grid-template-columns: minmax(180px, .72fr) minmax(260px, 1.28fr); gap: 12px; align-items: start; }}
    #world-book-entries {{ display: grid; gap: 6px; }}
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
    .compact-field {{ display: flex; align-items: center; gap: 6px; margin: 0; }}
    .compact-field span {{ flex: 0 0 auto; color: #3a4350; }}
    .world-entry input[type="text"], .world-entry input[type="number"], .world-entry select {{ width: 100%; min-width: 0; box-sizing: border-box; padding: 6px 8px; border: 1px solid #c8d0dc; border-radius: 7px; font: inherit; background: #fbfdff; }}
    .block-field {{ margin-top: 12px; }}
    textarea.keys-editor {{ min-height: 48px; font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }}
    textarea.entry-content-editor {{ min-height: 78px; }}
    .status-level-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; }}
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
    @media (max-width: 900px) {{ .world-entry-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 900px) {{ .state-overview-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 900px) {{ .detail-grid, .raw-grid {{ grid-template-columns: 1fr; }} }}
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
            "skill_book/default.json",
            "status_book/default.json",
            "region_book/default.json",
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
