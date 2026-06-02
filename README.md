# QQ 异世界转生人物卡

这是一个 AstrBot 插件，用于在群聊中生成“异世界转生人物卡”和后续“冒险日记”。玩家可以先建档，再通过命令推进自己的异世界生活；插件会把角色档案、状态、冒险记录和其他玩家互动保存到本地存档中。

## 玩家命令

```text
/异世界帮助
/异世界转生
/异世界转生 想成为会治疗魔法的小法师
/异世界冒险
/异世界冒险 去森林边缘采集草药
/异世界存档删除
/异世界存档删除 确认
```

- `/异世界帮助`：显示玩家可用命令、新手用法和角色档案面板地址。
- `/异世界转生`：创建角色档案。不写补充偏好时，会参考玩家最近群聊发言和 QQ 头像；写了补充偏好时，会按偏好建档并跳过群发言读取。
- `/异世界冒险`：读取玩家自己的存档、状态、最近冒险日志、其他人与主角的交互、世界书/技能书/状态书，然后生成一次冒险日记。命令后可写本次行动；不写行动时由 LLM 根据当前状态自由生成。
- 每个群拥有独立的创世纪时间。该群第一次冒险为 `创世纪1000年1月1日`，之后每次成功保存冒险自动推进一天。
- `/异世界存档删除`：删除当前群自己的异世界存档。为避免误删，需要再次发送 `/异世界存档删除 确认`；删除时会同步清理同群其他玩家记忆中由你产生的客串记录。

角色档案面板：

```text
https://www.youxiajiang.com/Games/AIBot/
```

创建完角色后，玩家可在面板中查看角色档案、状态、冒险记录和其他人与主角的交互。

## 管理员命令

```text
/开启异世界网页
/关闭异世界网页
```

异世界网页会随插件自动启动，默认监听 `8501`。管理员可使用开启/关闭命令手动控制网页服务。打开网页后输入 QQ 号登录；管理员登录码由代码中的 `ADMIN_LOGIN_CODE` 控制。

如果通过子路径反代，例如 `/Games/AIBot/`，请把 `web_viewer.public_path_prefix` 设置为 `/Games/AIBot`。如果需要让命令回复公网地址，请设置 `web_viewer.public_base_url`。

## 存档结构

转生成功后会在 AstrBot 插件数据目录下创建：

```text
data/plugin_data/astrbot_plugin_qq_adventurer/saves/groups/{group_id}/users/{user_id}/
```

主要文件：

- `index.json`：轻量索引，保存群号、角色名、当前区域和地点。列表和点名匹配优先读取它。
- `profile.json`：转生人物卡、头像、昵称等固定档案。
- `state.json`：当前区域、地点、等级、经验、HP、MP、金币、背包、技能、任务、flags 等冒险状态。
- `adventure_log.jsonl`：冒险日志。每一行是一条独立 JSON 记录。
- `cameo_memory.jsonl`：客串记忆。别人日记中明确提到该角色时，会在这里追加一条互动记忆。
- `world_clock.json`：群级世界时钟，位于群目录中，不属于单个玩家存档。

同一个群同一个 QQ 号的请求会串行排队处理，避免并发写坏同一份存档；不同玩家可以并行处理。

## 玩家互动

插件支持两类“其他玩家作为 NPC”注入：

- 同出生地区玩家：如果其他玩家的 `profile.card.birth_region` 与当前玩家相同，会作为同地区可客串 NPC 注入冒险日记 Prompt。
- 行动文本点名玩家：如果玩家行动中提到同群某个角色名，例如 `/异世界冒险 去拯救洛洛`，即使双方不在同地区，也会把洛洛的信息注入 Prompt。

点名匹配第一版采用简单规则：`target_name in action_text`。为了降低成本，插件会先扫描同群玩家的 `index.json`；只有命中名字后，才读取该玩家完整的 `profile.json`、`state.json`、最近冒险和客串记忆。

如果最终生成的 `encounter` 或 `result` 中出现某个 NPC 的名字，插件会给该 NPC 追加一条 `cameo_memory.jsonl`。这样即使该玩家没有主动冒险，也会因为其他人的日记逐渐积累互动记忆。

## 冒险日记

冒险日记要求 LLM 返回纯 JSON，并渲染成图片卡片。核心字段包括：

```json
{
  "title": "异世界冒险日记",
  "subtitle": "本次冒险副标题",
  "target_name": "玩家角色名",
  "action": "玩家本次行动",
  "date_label": "第 N 次冒险",
  "region": "本次冒险区域",
  "location": "本次冒险地点",
  "diary": "完整冒险日记正文",
  "encounter": "本次主要遭遇",
  "result": "本次事件结算",
  "stats": {"魔力": "A", "力量": "F", "敏捷": "C", "体质": "E"},
  "changes": ["获得物品", "技能熟练度提升"],
  "footer": "底部说明",
  "update": {
    "analysis": "状态更新依据",
    "patches": [
      {"op": "delta", "path": "/主角/等级/经验", "value": 20}
    ]
  }
}
```

当前代码会处理等级、等级经验、区域、地点，并追加日志。`update.patches` 还会用于技能、状态等嵌套进度；经验满 100 后自动升级。

## 世界书、技能书和状态书

管理员网页中可编辑三类世界背景资源：

- `world_book/default.json`：公共世界设定。
- `skill_book/default.json`：技能成长提示和默认 patch 路径。
- `status_book/default.json`：可觉醒状态、简单介绍、Lv.1 至 Lv.Max 的分级效果和默认 patch 路径。状态最高 Lv.5；已拥有状态只会向 AI 注入简介和当前等级效果。“总是注入”的状态已拥有时每次都会注入，未拥有时会在待觉醒列表附带简单介绍。

这些资源支持：

- 可视化编辑条目。
- 编辑源码。
- 导出 JSON。
- 导入 JSON。

保存和导入时会进行 JSON 校验。

## 存档网页

网页面板支持：

- 查看玩家列表。
- 查看角色档案、状态概览、成长进度。
- 查看冒险记录。
- 查看“其他人与主角的交互”。
- 管理员删除玩家存档或删除单条冒险日志。
- 管理员编辑、导出、导入玩家存档源码。
- 管理员查看最近文本补全消息记录，并调整保留数量。默认保存最近 3 次发送给 AI 的完整消息和 AI 原始回复。

玩家存档源码支持以下文件：

- `index.json`
- `profile.json`
- `state.json`
- `adventure_log.jsonl`
- `cameo_memory.jsonl`

`.json` 文件保存前必须是 JSON 对象；`.jsonl` 文件保存前要求每一行都是 JSON 对象。导入或源码保存时会先备份旧文件。

## 头像与聊天记录

- `/异世界转生` 不写补充偏好时，会尝试读取触发者最近群聊发言。
- 写了补充偏好时，不读取群发言，直接按偏好建档。
- QQ 头像 URL 由发送者 QQ 号生成。
- 头像视觉转述默认关闭；开启 `vision.enable_avatar_caption` 后，需要选择支持图片输入的视觉 Provider。
- 头像转述失败不会中断流程，只会跳过头像外貌参考。

## 配置项

- `llm.llm_provider_id`：人物卡和冒险日记生成 Provider。
- `vision.enable_avatar_caption`：是否启用头像视觉转述。
- `vision.vision_provider_id`：头像视觉转述 Provider。
- `vision.avatar_caption_prompt`：头像转述提示词。
- `analysis_features.keep_original_persona`：是否保留原会话人格影响。
- `analysis_features.use_plugin_specific_persona`：是否强制使用插件指定人格。
- `analysis_features.plugin_specific_persona_id`：插件指定人格 ID。
- `adventure.max_history_messages`：读取群历史消息上限。
- `adventure.diary_compress_interval` / `adventure.diary_compress_count`：冒险记录压缩配置。默认每 10 条未压缩冒险，把最早 6 条交给 AI 压缩为一条“第 1-6 次冒险”范围摘要。
- `adventure.cameo_compress_interval` / `adventure.cameo_compress_count`：其他人与主角的交互压缩配置。默认每 10 条交互记录，把最早 6 条交给 AI 压缩为一条范围摘要。
- `adventure.use_mock_data`：静态假数据模式。
- `t2i_rendering`：HTML 转图片策略。
- `performance.max_concurrent_t2i`：最大并发渲染数。
- `web_viewer.host`：网页监听地址，默认 `0.0.0.0`。
- `web_viewer.port`：网页端口，默认 `8501`。
- `web_viewer.public_base_url`：回复给管理员的公网访问地址。
- `web_viewer.public_path_prefix`：反代子路径前缀，例如 `/Games/AIBot`。

## 测试建议

1. 开启 `adventure.use_mock_data`。
2. 在 QQ 群发送 `/异世界帮助`，确认能看到命令说明和档案面板地址。
3. 发送 `/异世界转生`，确认能生成角色卡并创建存档。
4. 发送 `/异世界冒险 去森林边缘采集草药`，确认能生成日记卡并更新 `state.json`、`index.json` 和 `adventure_log.jsonl`。
5. 创建两个同出生地区玩家，确认日记 Prompt 能注入同地区 NPC。
6. 使用 `/异世界冒险 去拯救某个角色名`，确认跨地区点名玩家也能被注入。
7. 如果日记遭遇或结算里提到 NPC 名字，确认 NPC 的 `cameo_memory.jsonl` 有新增记录。
8. 打开网页面板，确认能查看档案、冒险记录、其他人与主角的交互，并测试管理员导入/导出功能。
