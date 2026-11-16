"""无控制台启动入口。

打包工具会为 `[project.gui-scripts]` 生成一个不弹黑框的可执行文件，
Windows 快捷方式直接指向它，双击即可打开悬浮球。
"""

from aidiscussion.desktop.app import start_desktop


def main() -> int:
    return start_desktop()


if __name__ == "__main__":
    raise SystemExit(main())
