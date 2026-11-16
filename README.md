# AIDiscussion

和一群 AI 成员一起群聊、私聊的桌面聊天工作台。

群里的成员是各家 AI：可以随时添加、踢除；也可以单独私聊某个 AI。
群聊支持 @ 点名、自由讨论和主持人调度，私聊与群聊共用同一套会话与消息模型。

> 项目正在按章节迭代，当前进度：**第 6 章 · Web 服务层**。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 语言 | Python 3.12（uv 管理） |
| 配置 | pydantic-settings |
| 存储 | SQLAlchemy 2.0（异步）+ SQLite |
| 模型 | httpx + OpenAI 兼容协议（流式 SSE） |
| 服务 | FastAPI + WebSocket |
| 后续 | LangGraph · pywebview |

## 快速开始

```bash
uv sync
cp .env.example .env      # 填入 AIDISCUSSION_LLM_API_KEY
uv run aidiscussion --check
uv run aidiscussion --ask "你好"   # 流式试聊，未配置密钥时使用离线 Mock
uv run aidiscussion --chat          # 进入交互式群聊
uv run aidiscussion --serve         # 启动 Web 服务（http://127.0.0.1:8000）
```

## 接口

REST（`/api`）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/me` | 当前用户 |
| GET / POST | `/members` | 成员列表 / 新建 AI 成员 |
| PATCH / DELETE | `/members/{id}` | 修改人设 / 停用成员 |
| GET / POST | `/conversations` | 会话列表 / 新建群聊 |
| POST | `/conversations/direct` | 打开或复用与某个 AI 的私聊 |
| GET | `/conversations/{id}` | 会话详情（含成员与最后一条消息） |
| GET | `/conversations/{id}/messages` | 历史消息（支持 `limit`、`before_id`） |
| POST / DELETE | `/conversations/{id}/members` | 拉人 / 踢人 |

WebSocket `ws://127.0.0.1:8000/ws/conversations/{id}`：

```json
{"type": "send", "content": "@行业顾问 这单怎么谈", "mode": "mention"}
```

服务端依次推送 `user_message`、`reply_start`、多个 `reply_delta`、`reply_done`，失败时推送 `reply_failed`。

## 开发

```bash
uv run pytest        # 测试
uv run ruff check .  # 静态检查
```

## 路线图

- [x] 第 1 章 工程骨架与配置
- [x] 第 2 章 领域模型与持久化
- [x] 第 3 章 LLM 适配层与流式输出
- [x] 第 4 章 会话编排（私聊 / @ / 自由讨论 / 主持人）
- [x] 第 5 章 终端客户端
- [x] 第 6 章 FastAPI + WebSocket 服务层
- [ ] 第 7 章 Web 聊天界面
- [ ] 第 8 章 上下文压缩与长期记忆
- [ ] 第 9 章 桌面悬浮球与容器化
- [ ] 第 10 章 文档与发布
