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

BALL_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<style>
  html, body { margin: 0; height: 100%; background: transparent; overflow: hidden; }
  .ball {
    width: 100%; height: 100%; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: radial-gradient(circle at 30% 28%, #8aa4ff 0%, #4f7cff 45%, #6b4cff 100%);
    box-shadow: 0 10px 26px rgba(79, 124, 255, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.35);
    color: #fff; font: 700 30px/1 "Segoe UI", system-ui, sans-serif;
    cursor: pointer; user-select: none; transition: transform 0.15s ease;
  }
  .ball:hover { transform: scale(1.06); }
  .ball:active { transform: scale(0.96); }
  .ring {
    position: absolute; inset: 2px; border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.35);
    animation: breathe 3.2s ease-in-out infinite;
  }
  @keyframes breathe { 0%, 100% { opacity: 0.25; } 50% { opacity: 0.75; } }
</style>
</head>
<body>
  <div class="ball" onclick="pywebview.api.open_chat()"
       oncontextmenu="pywebview.api.quit_app(); return false;"
       title="左键打开 AIDiscussion，右键退出">
    A<span class="ring"></span>
  </div>
</body>
</html>
"""


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
            html=BALL_HTML,
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
