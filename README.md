# QQ 魔法少女转生人物卡

这是一个 AstrBot 插件，用于在群聊中生成魔法少女转生人物卡并通过行动回合推进故事。插件会把角色档案、状态、行动记录和其他玩家互动保存到本地存档中。

## 玩家命令

```text
/魔法少女帮助
/魔法少女转生
/魔法少女转生 想成为会治疗魔法的小法师
/魔法少女行动
/魔法少女行动 和某个参与对象一起去买甜点
/魔法少女存档删除
/魔法少女存档删除 确认
```

- `/魔法少女帮助`：显示玩家可用命令、新手用法和角色档案面板地址。
- `/魔法少女转生`：创建角色档案。命令后可填写补充偏好，影响角色设定。
- `/魔法少女行动`：读取玩家存档、行动记忆、其他人与主角的交互和设定书，按当前剧情阶段生成一次完整行动回合。
- 公共魔物书是普通魔物目标图鉴。每个魔物使用正文、战斗机制、战斗胜利结尾和战斗失败结尾四个文本字段；关键词仅供 LLM 理解候选语义。
- 事件书按目标类型组织场景事件。每个事件使用正文、事件机制、顺利进行和受到阻碍四个文本字段；关键词、地点标签和兼容魔物仅供 LLM 理解和选择候选，代码不会据此匹配或补选。
- 每个群拥有独立的公元时间和行动对话序号。初始时间为 `公元2020年4月1日 8:00`；每次成功保存 `/魔法少女行动` 都会记录群内连续的 `conversation_no`。LLM 每轮通过 `/世界/时间` 输出正文增加的 `H:MM`，代码累加分钟并自动处理跨日；其他保存流程不会自动增加时间。
- `/魔法少女存档删除`：删除当前群自己的魔法少女存档。为避免误删，需要再次发送 `/魔法少女存档删除 确认`；删除时会同步清理同群其他玩家记忆中由你产生的客串记录。

角色档案面板：

```text
https://www.youxiajiang.com/Games/AIBot/
```

创建完角色后，玩家可在面板中查看角色档案、状态、行动记录和其他人与主角的交互。

## 管理员命令

```text
/开启魔法少女网页
/关闭魔法少女网页
```

魔法少女网页会随插件自动启动，默认监听 `8501`。管理员可使用开启/关闭命令手动控制网页服务。打开网页后输入 QQ 号登录；管理员登录码由代码中的 `ADMIN_LOGIN_CODE` 控制。

如果通过子路径反代，例如 `/Games/AIBot/`，请把 `web_viewer.public_path_prefix` 设置为 `/Games/AIBot`。如果需要让命令回复公网地址，请设置 `web_viewer.public_base_url`。

## 存档结构

转生成功后会在 AstrBot 插件数据目录下创建：

```text
data/plugin_data/astrbot_plugin_qq_MahouShoujo/saves/groups/{group_id}/users/{user_id}/
```

主要文件：

- `index.json`：轻量索引，保存群号和角色名。列表和点名匹配优先读取它。
- `profile.json`：转生人物卡、昵称等固定档案。
- `daily_memory.jsonl`：行动记录与长期记忆摘要。每一行是一条独立 JSON 记录。
- `cameo_memory.jsonl`：交互记忆。每次行动结束后，子任务 AI 会为正文中实际互动的其他玩家追加一条约 100 字客观摘要。
- `world_clock.json`：群级世界时钟与绝对行动序号，保存 `next_day_offset`、`next_minute_of_day` 和 `next_conversation_no`，位于群目录中，不属于单个玩家存档。

同一个群的行动请求会通过群锁串行处理，确保世界日期和绝对行动序号不会重复；同一玩家的其他请求也会按玩家锁排队。

## 玩家互动

玩家行动中点名同群角色时，该角色会作为可客串 NPC 注入行动 Prompt。例如 `/魔法少女行动 去拯救洛洛`。

点名匹配第一版采用简单规则：`target_name in action_text`。为了降低成本，插件会先扫描同群玩家的 `index.json`；只有命中名字后，才读取该玩家完整的 `profile.json`、`state.json`、最近战斗和客串记忆。

每次 `/魔法少女行动` 结束后，插件会根据完整故事正文判断哪些候选玩家实际参与互动，并给这些玩家追加一条 `cameo_memory.jsonl` 客观摘要。

## 行动记录

`/魔法少女行动` 要求 LLM 依次返回故事正文、`<行动选项>` 和 `<UpdateVariable>`。程序会把完整故事正文写入 `daily_memory.jsonl`，行动选项用于回复玩家，变量补丁用于更新当前状态。


## 世界书、状态书、技能书和性癖书

区域书暂时按世界书方式匹配：扫描文本命中关键词后注入条目的详细介绍。区域分组仅用于整理条目，不参与玩家位置判断；简略介绍暂时保留，但永远不会触发。

管理员网页中可编辑这些世界背景资源：

- `world_book/default.json`：公共世界设定。
- `status_book/default.json`：状态相关补充设定。结构、触发方式和注入逻辑与世界书一致。
- `skill_book/default.json`：技能成长提示和默认 change 路径。

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
- 技能、性癖和其他长期成长统一保存为 `{"进度": 0..100}`；达到 100 后封顶。
- 查看行动记录。
- 查看"其他人与主角的交互"。
- 管理员删除玩家存档或删除单条行动记录。
- 管理员编辑、导出、导入玩家存档源码。
- 管理员查看最近文本补全消息记录，并调整保留数量。默认保存最近 12 次发送给 AI 的完整消息和 AI 原始回复。

玩家存档源码支持以下文件：

- `index.json`
- `profile.json`
- `state.json`
- `battle_log.jsonl`
- `cameo_memory.jsonl`

`.json` 文件保存前必须是 JSON 对象；`.jsonl` 文件保存前要求每一行都是 JSON 对象。导入或源码保存时会先备份旧文件。

## 配置项

- `llm.llm_provider_id`：人物卡和战斗日记生成 Provider。
- `analysis_features.keep_original_persona`：是否保留原会话人格影响。
- `analysis_features.use_plugin_specific_persona`：是否强制使用插件指定人格。
- `analysis_features.plugin_specific_persona_id`：插件指定人格 ID。
- `battle.interaction_memory_target_chars`：每轮当前玩家事件记忆及客串交互记忆的目标字数，默认 100。
- `battle.memory_compaction_threshold_chars`：两类有效记忆正文合计达到该字数后触发长期记忆压缩，默认 20000，填 0 关闭。
- `battle.memory_compaction_target_chars`：长期故事摘要目标字数，默认 2000。
- `battle.teammate_recent_record_count`：参与对象确定后，正文主 LLM 接收该玩家最近多少条短事件记忆，默认 1，范围 1–5；选择子 LLM 不接收这些记忆。
- `battle.use_mock_data`：静态假数据模式。
- `t2i_rendering`：HTML 转图片策略。
- `performance.max_concurrent_t2i`：最大并发渲染数。
- `web_viewer.host`：网页监听地址，默认 `0.0.0.0`。
- `web_viewer.port`：网页端口，默认 `8501`。
- `web_viewer.public_base_url`：回复给管理员的公网访问地址。
- `web_viewer.public_path_prefix`：反代子路径前缀，例如 `/Games/AIBot`。

## 测试建议

1. 开启 `battle.use_mock_data`。
2. 在 QQ 群发送 `/魔法少女帮助`，确认能看到命令说明和档案面板地址。
3. 发送 `/魔法少女转生`，确认能生成角色卡并创建存档。
4. 发送 `/魔法少女行动 去森林边缘采集草药`，确认能生成行动记录并更新玩家状态和 `daily_memory.jsonl`。
5. 创建两个同群玩家，确认行动 Prompt 能注入同群 NPC。
6. 使用 `/魔法少女行动 去拯救某个角色名`，确认点名玩家能被注入。
7. 行动正文中让另一名玩家实际参与互动，确认该玩家的 `cameo_memory.jsonl` 有新增客观摘要。
8. 打开网页面板，确认能查看档案、行动记录、其他人与主角的交互，并测试管理员导入/导出功能。
