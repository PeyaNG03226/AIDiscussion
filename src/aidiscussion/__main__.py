"""命令行入口：自检与快速试聊，后续章节会扩展为完整聊天客户端。"""

import argparse
import asyncio
import sys

from aidiscussion import __version__
from aidiscussion.config import Settings, get_settings
from aidiscussion.llm.base import LLMMessage
from aidiscussion.llm.errors import LLMError
from aidiscussion.llm.factory import get_llm
from aidiscussion.logging import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aidiscussion",
        description="和一群 AI 成员一起群聊、私聊的桌面聊天工作台",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--check", action="store_true", help="打印当前配置并检查完整性")
    parser.add_argument("--ask", metavar="问题", help="快速试聊，验证模型接入是否正常")
    parser.add_argument("--chat", action="store_true", help="进入交互式群聊")
    parser.add_argument("--serve", action="store_true", help="启动 Web 服务（REST + WebSocket）")
    parser.add_argument(
        "--desktop", action="store_true", help="桌面悬浮球模式（需要 --extra desktop）"
    )
    return parser


def render_check(settings: Settings) -> str:
    lines = [
        f"应用名称    : {settings.app_name}",
        f"版本        : {__version__}",
        f"调试模式    : {settings.debug}",
        f"日志级别    : {settings.log_level.upper()}",
        f"数据目录    : {settings.data_dir.resolve()}",
        f"数据库      : {settings.resolved_database_url}",
        f"模型接口    : {settings.llm_base_url}",
        f"默认模型    : {settings.llm_model}",
        f"密钥        : {settings.masked_llm_key()}",
        f"密钥状态    : {'已配置' if settings.has_llm_key else '未配置'}",
    ]
    lines.append("厂商状态    :")
    for spec in settings.provider_specs():
        status = f"已配置（{spec.model}）" if spec.has_key else "未配置，成员会回退到离线 Mock"
        lines.append(f"  {spec.label:<8}: {status}")
    return "\n".join(lines)


async def ask(question: str) -> int:
    llm = get_llm()
    messages = [
        LLMMessage(role="system", content="你是一个简洁直接的中文助手。"),
        LLMMessage(role="user", content=question),
    ]
    try:
        async for piece in llm.stream_chat(messages):
            print(piece, end="", flush=True)
    except LLMError as exc:
        print(f"\n[模型调用失败] {exc}", file=sys.stderr)
        return 1
    finally:
        await llm.aclose()
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level, settings.debug)
    settings.ensure_data_dir()

    if args.check:
        print(render_check(settings))
        return 0

    if args.ask:
        return asyncio.run(ask(args.ask))

    if args.chat:
        from aidiscussion.cli.app import start

        return start()

    if args.serve:
        import uvicorn

        from aidiscussion.api.app import create_app

        uvicorn.run(create_app(), host=settings.api_host, port=settings.api_port)
        return 0

    if args.desktop:
        from aidiscussion.desktop.app import start_desktop

        return start_desktop()

    print(f"{settings.app_name} v{__version__} 骨架就绪，使用 --check 查看配置。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
