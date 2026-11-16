"""在后台线程里运行 Web 服务，供桌面外壳复用。"""

from __future__ import annotations

import logging
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI

from aidiscussion.api.app import create_app

logger = logging.getLogger(__name__)


def find_free_port() -> int:
    """让操作系统分配一个空闲端口，避免和用户已有的 8000 端口冲突。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ServerThread:
    """把 uvicorn 放进后台线程，主线程留给桌面窗口。"""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        app: FastAPI | None = None,
    ) -> None:
        self.host = host
        self.port = port or find_free_port()
        self._app = app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout: float = 15.0) -> str:
        config = uvicorn.Config(
            self._app or create_app(),
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="aidiscussion-desktop-server",
            daemon=True,
        )
        self._thread.start()

        deadline = time.monotonic() + timeout
        while not self._server.started:
            if self._thread is not None and not self._thread.is_alive():
                raise RuntimeError("内置服务启动失败，请查看日志")
            if time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("内置服务启动超时")
            time.sleep(0.05)

        logger.info("桌面内置服务已启动：%s", self.url)
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._server = None
