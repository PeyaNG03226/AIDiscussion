"""桌面悬浮球：点头像展开聊天窗口，关掉窗口后缩回球。

窗口框架用 pywebview，它只是 Web UI 的外壳：
服务仍然由 FastAPI 提供，因此桌面版和浏览器版看到的是同一个界面、同一份数据。
pywebview 属于可选依赖，未安装时命令行会给出明确提示，不影响 --serve。
"""

from __future__ import annotations

import contextlib
import sys

from aidiscussion.config import get_settings
from aidiscussion.desktop.server import ServerThread


class DesktopBridge:
    """暴露给悬浮球页面的 JS API。"""

    def __init__(self) -> None:
        self._ball = None
        self._chat = None

    def _attach(self, ball, chat) -> None:
        self._ball = ball
        self._chat = chat
        chat.events.closing += self._on_chat_closing

    def open_chat(self) -> None:
        if self._chat is None:
            return
        self._chat.show()
        self._chat.restore()

    def hide_chat(self) -> None:
        if self._chat is not None:
            self._chat.hide()

    def _on_chat_closing(self):
        """关闭聊天窗口时缩回悬浮球，而不是退出整个程序。"""
        self.hide_chat()
        return False

    def quit_app(self) -> None:
        for window in (self._chat, self._ball):
            if window is not None:
                with contextlib.suppress(Exception):
                    window.destroy()


def start_desktop() -> int:
    try:
        import webview
    except ImportError:
        print(
            "桌面模式需要额外依赖：请先执行 `uv sync --extra desktop`",
            file=sys.stderr,
        )
        return 1

    settings = get_settings()
    server = ServerThread(port=0)
    try:
        url = server.start()
        bridge = DesktopBridge()
        ball = webview.create_window(
            "AIDiscussion",
            url=f"{url}/ball.html",
            width=84,
            height=84,
            frameless=True,
            easy_drag=True,
            on_top=True,
            transparent=True,
            resizable=False,
            js_api=bridge,
        )
        chat = webview.create_window(
            "AIDiscussion",
            url=url,
            width=1080,
            height=760,
            min_size=(860, 560),
            hidden=True,
        )
        bridge._attach(ball, chat)
        webview.start(debug=settings.debug)
        return 0
    finally:
        server.stop()
