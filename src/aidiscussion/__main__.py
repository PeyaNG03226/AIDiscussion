"""命令行入口：当前阶段只提供自检，后续章节会扩展为聊天客户端。"""

import argparse
import sys

from aidiscussion import __version__
from aidiscussion.config import Settings, get_settings
from aidiscussion.logging import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aidiscussion",
        description="和一群 AI 成员一起群聊、私聊的桌面聊天工作台",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--check", action="store_true", help="打印当前配置并检查完整性")
    return parser


def render_check(settings: Settings) -> str:
    lines = [
        f"应用名称    : {settings.app_name}",
        f"版本        : {__version__}",
        f"调试模式    : {settings.debug}",
        f"日志级别    : {settings.log_level.upper()}",
        f"数据目录    : {settings.data_dir.resolve()}",
        f"模型接口    : {settings.llm_base_url}",
        f"默认模型    : {settings.llm_model}",
        f"密钥        : {settings.masked_llm_key()}",
        f"密钥状态    : {'已配置' if settings.has_llm_key else '未配置'}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level, settings.debug)
    settings.ensure_data_dir()

    if args.check:
        print(render_check(settings))
        return 0

    print(f"{settings.app_name} v{__version__} 骨架就绪，使用 --check 查看配置。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
