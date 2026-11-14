"""日志初始化。"""

import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def setup_logging(level: str = "INFO", debug: bool = False) -> None:
    """初始化全局日志。

    Windows 控制台默认使用 GBK，这里统一改为 UTF-8，避免中文输出报错。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else level.upper())
