# 贡献指南

感谢你对 AIDiscussion 感兴趣。这个项目按章节逐步演进，欢迎提交 issue、补充测试或改进文档。

## 开发环境

```bash
git clone <repo-url>
cd AIDiscussion
uv sync                        # 安装运行时与开发依赖
cp .env.example .env           # 可选：填入 AIDISCUSSION_LLM_API_KEY
uv sync --extra desktop        # 可选：桌面悬浮球
```

没有配置密钥时，项目会自动使用离线 Mock 模型，所有测试和界面调试都不依赖网络。

## 提交前检查

```bash
uv run ruff check .    # 静态检查
uv run pytest          # 全部测试
```

两件事都必须通过。新增功能请同时补充测试，尤其是：

- 仓储层：事务边界、排序稳定性、边界条件
- 服务层：纯函数优先，LLM 用 Fake 替代，不产生真实网络请求
- API 层：REST 用 `httpx.AsyncClient`，WebSocket 用 FastAPI 的同步 `TestClient`

## 代码约定

- Python 3.12，类型注解尽量完整；公开函数写简短中文 docstring
- 仓储函数只 `flush` 不 `commit`，事务边界统一放在 `Database.session()`
- 时间统一用 UTC（`datetime.now(UTC)`）
- 前端不引入构建工具，保持原生 HTML / CSS / JS
- 注释只解释「为什么」，不复述代码在做什么

## 提交信息

采用 `<type>: <中文描述>` 的形式，例如：

```
feat: 增加桌面悬浮球、Docker 镜像与 CI
fix: 修复成员加入时间相同时的排序问题
docs: 补充架构说明
```

一次提交只做一件事，保持提交历史可读。

## 分支与 PR

1. 从 `main` 切出功能分支：`feat/xxx` 或 `fix/xxx`
2. 提交前跑通 ruff 与 pytest
3. PR 描述写清楚：改了什么、为什么改、如何验证

## 新增模型供应商

实现 `aidiscussion.llm.base.LLMClient` 协议（`stream_chat` + `aclose`），
在 `aidiscussion.llm.factory.get_llm()` 中按配置选择即可。
任何 OpenAI 兼容接口（DeepSeek、Moonshot、通义等）当前都已支持，只需改 `.env`。
