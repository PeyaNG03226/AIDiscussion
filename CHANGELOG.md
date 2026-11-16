# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [0.1.0] - 2026-11-16

首个可运行版本：从工程骨架到桌面悬浮球，完整覆盖「和一群 AI 聊天」的核心链路。

### 新增

- 工程骨架：uv + src 布局、pydantic-settings 配置、结构化日志、pytest 与 ruff
- 领域模型：成员、会话、会话成员、消息四张核心表，群聊与私聊共用一套消息链路
- LLM 适配层：httpx 直连 OpenAI 兼容接口、SSE 流式输出、错误映射与指数退避重试，
  未配置密钥时自动降级为离线 Mock
- 会话编排：@ 点名、自由讨论、主持人调度三种群聊模式，私聊与 `max_replies` 轮次上限
- 终端客户端：`--check` / `--ask` / `--chat` 三种入口与完整会话管理命令
- Web 服务：FastAPI REST + WebSocket，覆盖成员、会话、历史消息与实时流式回复
- Web 界面：原生 HTML/CSS/JS，零构建，流式气泡、模式切换、成员管理与新建群聊
- 上下文压缩：滑动窗口 + 增量摘要，摘要记录覆盖点，失败时硬截断兜底
- 长期记忆：从用户消息提取事实，跨会话共享，按关键词重合度召回
- 桌面悬浮球：pywebview 外壳，点击展开聊天、关闭缩回，内置服务运行在后台线程
- 容器化与 CI：Dockerfile、docker-compose、Ubuntu/Windows 测试矩阵与 Docker 构建校验
