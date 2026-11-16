"""桌面外壳测试：后台服务、悬浮球桥接、命令行开关。"""

from pathlib import Path
from types import SimpleNamespace

import httpx

from aidiscussion.__main__ import build_parser
from aidiscussion.api.app import create_app
from aidiscussion.db.database import Database
from aidiscussion.desktop.app import DesktopBridge
from aidiscussion.desktop.server import ServerThread, find_free_port
from aidiscussion.llm.mock import MockLLM


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self) -> None:
        self.events = SimpleNamespace(closing=FakeEvent())
        self.visible = False
        self.restored = False
        self.destroyed = False

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def restore(self) -> None:
        self.restored = True

    def destroy(self) -> None:
        self.destroyed = True


def test_find_free_port_returns_usable_port() -> None:
    assert 1024 < find_free_port() < 65536


def test_server_thread_serves_health(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'desktop.db'}")
    app = create_app(db=database, llm=MockLLM())
    server = ServerThread(app=app)

    url = server.start(timeout=20)
    try:
        response = httpx.get(f"{url}/api/health", timeout=5)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert httpx.get(f"{url}/ball.html", timeout=5).status_code == 200
        assert httpx.get(f"{url}/ball.png", timeout=5).status_code == 200
    finally:
        server.stop()


def test_bridge_opens_and_retracts_chat() -> None:
    bridge = DesktopBridge()
    ball, chat = FakeWindow(), FakeWindow()
    bridge._attach(ball, chat)

    bridge.open_chat()

    assert chat.visible is True
    assert chat.restored is True

    handler = chat.events.closing.handlers[0]
    assert handler() is False
    assert chat.visible is False

    bridge.quit_app()
    assert ball.destroyed is True
    assert chat.destroyed is True


def test_ball_page_wires_up_bridge() -> None:
    web_dir = Path(__file__).resolve().parents[1] / "src" / "aidiscussion" / "web"

    ball_html = (web_dir / "ball.html").read_text(encoding="utf-8")

    assert "pywebview.api.open_chat()" in ball_html
    assert "pywebview.api.quit_app()" in ball_html
    assert "ball.png" in ball_html
    assert (web_dir / "ball.png").stat().st_size > 0


def test_desktop_flag_is_parsed() -> None:
    args = build_parser().parse_args(["--desktop"])

    assert args.desktop is True
