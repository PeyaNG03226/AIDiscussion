"""Windows 原生分层窗口悬浮球。

WebView 的透明窗口在部分环境会露出窗体底色，所以悬浮球不再用网页渲染，
而是用 Win32 分层窗口按像素合成 PNG：桌面上只留下形象本身，没有圆形或方形底板。
鼠标移到小猪身上时会播一段跳动动画，离开后回到静止姿势。
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

logger = logging.getLogger(__name__)

BALL_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "ball.png"

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - 桌面模式只支持 Windows
    _user32 = _gdi32 = _kernel32 = None

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SW_SHOWNOACTIVATE = 4
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_RGB = 0

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_MOUSELEAVE = 0x02A3
WM_APP_DESTROY = 0x8001
WM_APP_OPEN_CHAT = 0x8002

WINDOW_TITLE = "AIDiscussion"
ERROR_ALREADY_EXISTS = 183
_MUTEX_NAME = "Local\\AIDiscussionDesktop"
_instance_mutex: int | None = None

TME_LEAVE = 0x00000002
HOVER_TIMER_ID = 1
FRAME_INTERVAL_MS = 40
FRAME_COUNT = 22

IDC_HAND = 32649
HWND_TOPMOST = -1
SM_CXSCREEN = 0
SM_CYSCREEN = 1
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HANDLE),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("hwndTrack", wintypes.HWND),
        ("dwHoverTime", wintypes.DWORD),
    ]


def enable_dpi_awareness() -> None:
    """让窗口按物理像素渲染，避免高分屏下悬浮球发虚。"""
    if not _IS_WINDOWS:
        return
    try:
        _user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        )
    except Exception:
        logger.debug("设置 DPI 感知失败，沿用系统默认缩放", exc_info=True)


def _configure_win32() -> None:
    if not _IS_WINDOWS:
        return

    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    _user32.RegisterClassExW.restype = wintypes.WORD
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.DefWindowProcW.restype = LRESULT
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HANDLE,
        wintypes.HINSTANCE,
        ctypes.c_void_p,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND,
        wintypes.HDC,
        ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE),
        wintypes.HDC,
        ctypes.POINTER(wintypes.POINT),
        wintypes.DWORD,
        ctypes.POINTER(BLENDFUNCTION),
        wintypes.DWORD,
    ]
    _user32.UpdateLayeredWindow.restype = wintypes.BOOL
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.GetDC.restype = wintypes.HDC
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.SetCapture.argtypes = [wintypes.HWND]
    _user32.SetCapture.restype = wintypes.HWND
    _user32.ReleaseCapture.argtypes = []
    _user32.ReleaseCapture.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    _user32.LoadCursorW.restype = wintypes.HANDLE
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.TranslateMessage.restype = wintypes.BOOL
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.restype = LRESULT
    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.PostQuitMessage.restype = None
    _user32.SetTimer.argtypes = [
        wintypes.HWND,
        ctypes.c_size_t,
        wintypes.UINT,
        ctypes.c_void_p,
    ]
    _user32.SetTimer.restype = ctypes.c_size_t
    _user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    _user32.KillTimer.restype = wintypes.BOOL
    _user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
    _user32.TrackMouseEvent.restype = wintypes.BOOL
    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    _gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    _gdi32.CreateDIBSection.restype = wintypes.HANDLE
    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    _gdi32.SelectObject.restype = wintypes.HANDLE
    _gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    _gdi32.DeleteObject.restype = wintypes.BOOL
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _gdi32.DeleteDC.restype = wintypes.BOOL

    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND


_configure_win32()


def acquire_single_instance() -> bool:
    """抢占单实例互斥量：返回 False 表示已经有一个桌面端在运行。

    句柄保存在模块级变量里，进程存活期间一直持有；进程结束由系统自动释放。
    """
    global _instance_mutex
    if not _IS_WINDOWS:
        return True
    handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        # 创建失败时宁可放行，也不要让程序打不开
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


def wake_existing_ball() -> bool:
    """把已在运行的悬浮球叫出来：打开它的聊天窗口。"""
    if not _IS_WINDOWS:
        return False
    hwnd = _user32.FindWindowW(FloatingBall._class_name, WINDOW_TITLE)
    if not hwnd:
        return False
    return bool(_user32.PostMessageW(hwnd, WM_APP_OPEN_CHAT, 0, 0))


def _premultiply(source: bytes) -> bytes:
    """把 RGBA 字节转成 UpdateLayeredWindow 需要的预乘 BGRA。"""
    data = bytearray(len(source))
    for index in range(0, len(source), 4):
        red, green, blue, alpha = source[index : index + 4]
        data[index] = blue * alpha // 255
        data[index + 1] = green * alpha // 255
        data[index + 2] = red * alpha // 255
        data[index + 3] = alpha
    return bytes(data)


def _dpi_scale() -> float:
    try:
        dpi = _user32.GetDpiForSystem()
    except Exception:
        return 1.0
    return max(1.0, dpi / 96)


class FloatingBall:
    """无边框、置顶、按像素透明的悬浮球。

    窗口必须待在会处理消息的线程上，否则能画出来却收不到鼠标事件，
    所以创建、绘制、消息循环都放在同一个专用线程里。
    """

    _class_name = "AIDiscussionFloatingBall"
    _class_registered = False

    def __init__(
        self,
        image_path: Path = BALL_IMAGE,
        *,
        size: int = 84,
        on_click: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        if not _IS_WINDOWS:
            raise RuntimeError("悬浮球只支持 Windows")

        self._on_click = on_click or (lambda: None)
        self._on_quit = on_quit or (lambda: None)
        self._dragging = False
        self._moved = False
        self._drag_origin = (0, 0)
        self._window_origin = (0, 0)
        self._hwnd: int | None = None
        self._hdc_mem: int | None = None
        self._hbitmap: int | None = None
        self._old_bitmap: int | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._bits: ctypes.c_void_p | None = None
        self._base_frame = b""
        self._frames: list[bytes] = []
        self._frame_index = 0
        self._timer_id = 0
        self._tracking_leave = False

        scale = _dpi_scale()
        self.size = max(48, round(size * scale))
        # 画布比小猪大一圈，给跳动和旋转留空间；窗口本身仍然逐像素透明
        self._side_padding = round(self.size * 0.20)
        self._bottom_margin = round(self.size * 0.12)
        self._headroom = round(self.size * 0.85)
        self._canvas = (
            self.size + self._side_padding * 2,
            self.size + self._headroom + self._bottom_margin,
        )
        screen_width = _user32.GetSystemMetrics(SM_CXSCREEN)
        screen_height = _user32.GetSystemMetrics(SM_CYSCREEN)
        self._origin = (
            screen_width - self.size - round(48 * scale) - self._side_padding,
            round(screen_height * 0.28) - self._headroom,
        )
        self._image_path = image_path

        self._wndproc = WNDPROC(self._handle_message)
        self._thread = threading.Thread(
            target=self._serve, name="AIDiscussionBall", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("悬浮球窗口创建超时")
        if self._error is not None:
            raise self._error

    # ---------- 创建与绘制 ----------

    def _serve(self) -> None:
        """悬浮球线程：建窗 → 准备帧 → 消息循环。"""
        try:
            self._thread_id = _kernel32.GetCurrentThreadId()
            self._register_class()
            self._create_window()
            self._create_buffer()
            self._build_frames(self._image_path)
            self._show_frame(self._base_frame)
            _user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
        except BaseException as error:  # noqa: BLE001 - 交回主线程抛出
            self._error = error
            if self._hwnd:
                _user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            self._ready.set()
            return

        self._ready.set()

        message = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(message))
            _user32.DispatchMessageW(ctypes.byref(message))

        self._release_resources()

    def _register_class(self) -> None:
        if FloatingBall._class_registered:
            return

        instance = _kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.style = 0
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.hCursor = _user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND))
        window_class.lpszClassName = self._class_name

        if not _user32.RegisterClassExW(ctypes.byref(window_class)):
            error = ctypes.get_last_error()
            if error != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise ctypes.WinError(error)
        FloatingBall._class_registered = True

    def _create_window(self) -> None:
        style = WS_POPUP
        extended = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        width, height = self._canvas
        hwnd = _user32.CreateWindowExW(
            extended,
            self._class_name,
            WINDOW_TITLE,
            style,
            self._origin[0],
            self._origin[1],
            width,
            height,
            None,
            None,
            _kernel32.GetModuleHandleW(None),
            None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = hwnd

    def _create_buffer(self) -> None:
        """一次性创建 32 位 DIB，后续换帧只改写像素。"""
        width, height = self._canvas
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = BI_RGB

        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader = header

        screen_dc = _user32.GetDC(None)
        if not screen_dc:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            bits = ctypes.c_void_p()
            bitmap = _gdi32.CreateDIBSection(
                screen_dc,
                ctypes.byref(bitmap_info),
                DIB_RGB_COLORS,
                ctypes.byref(bits),
                None,
                0,
            )
            if not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())

            memory_dc = _gdi32.CreateCompatibleDC(screen_dc)
            if not memory_dc:
                raise ctypes.WinError(ctypes.get_last_error())
            self._bits = bits
            self._hbitmap = bitmap
            self._hdc_mem = memory_dc
            self._old_bitmap = _gdi32.SelectObject(memory_dc, bitmap)
        finally:
            _user32.ReleaseDC(None, screen_dc)

    def _build_frames(self, image_path: Path) -> None:
        """预渲染静止帧和一组跳动帧（下蹲 → 跳起 → 落地回弹）。"""
        from PIL import Image

        pig = Image.open(image_path).convert("RGBA").resize(
            (self.size, self.size), Image.LANCZOS
        )
        canvas_width, canvas_height = self._canvas

        def render(scale_x: float, scale_y: float, lift: int, angle: float) -> bytes:
            width = max(1, round(self.size * scale_x))
            height = max(1, round(self.size * scale_y))
            sprite = pig.resize((width, height), Image.BICUBIC)
            if angle:
                sprite = sprite.rotate(angle, resample=Image.BICUBIC, expand=True)
            frame = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
            left = (canvas_width - sprite.width) // 2
            top = canvas_height - self._bottom_margin - sprite.height - lift
            frame.alpha_composite(sprite, (max(0, left), max(0, top)))
            return _premultiply(frame.tobytes())

        self._base_frame = render(1.0, 1.0, 0, 0.0)

        frames: list[bytes] = []
        for index in range(FRAME_COUNT):
            phase = index / FRAME_COUNT
            hop = math.sin(math.pi * phase)
            lift = round(self.size * 0.42 * hop)
            squash = 0.09 * (1.0 - hop)
            angle = 4.0 * math.sin(2 * math.pi * phase)
            frames.append(render(1.0 + squash, 1.0 - squash, lift, angle))
        self._frames = frames

    def _show_frame(self, data: bytes) -> None:
        if not self._hwnd or not self._hdc_mem or not data:
            return
        if self._bits is not None:
            ctypes.memmove(self._bits, data, len(data))

        width, height = self._canvas
        size = wintypes.SIZE(width, height)
        source = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

        screen_dc = _user32.GetDC(None)
        if not screen_dc:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # pptDst 传空表示保持窗口当前位置，拖动时不会被拉回原位
            if not _user32.UpdateLayeredWindow(
                self._hwnd,
                screen_dc,
                None,
                ctypes.byref(size),
                self._hdc_mem,
                ctypes.byref(source),
                0,
                ctypes.byref(blend),
                ULW_ALPHA,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _user32.ReleaseDC(None, screen_dc)

    # ---------- 悬停动画 ----------

    def _start_hover(self) -> None:
        if self._dragging or not self._hwnd:
            return
        if not self._tracking_leave:
            track = TRACKMOUSEEVENT()
            track.cbSize = ctypes.sizeof(TRACKMOUSEEVENT)
            track.dwFlags = TME_LEAVE
            track.hwndTrack = self._hwnd
            if _user32.TrackMouseEvent(ctypes.byref(track)):
                self._tracking_leave = True
        if not self._timer_id:
            self._timer_id = _user32.SetTimer(
                self._hwnd, HOVER_TIMER_ID, FRAME_INTERVAL_MS, None
            )
            self._frame_index = 0

    def _stop_hover(self) -> None:
        self._tracking_leave = False
        if self._timer_id:
            _user32.KillTimer(self._hwnd, HOVER_TIMER_ID)
            self._timer_id = 0
            self._frame_index = 0
            self._show_frame(self._base_frame)

    # ---------- 交互 ----------

    def _handle_message(self, hwnd, message, wparam, lparam) -> int:
        try:
            if message == WM_APP_OPEN_CHAT:
                self._run(self._on_click)
                return 0

            if message == WM_APP_DESTROY:
                self._hwnd = None
                _user32.DestroyWindow(hwnd)
                _user32.PostQuitMessage(0)
                return 0

            if message == WM_LBUTTONDOWN:
                self._stop_hover()
                self._dragging = True
                self._moved = False
                self._drag_origin = self._cursor_position()
                self._window_origin = self._window_position(hwnd)
                _user32.SetCapture(hwnd)
                return 0

            if message == WM_MOUSEMOVE:
                if self._dragging:
                    x, y = self._cursor_position()
                    delta_x = x - self._drag_origin[0]
                    delta_y = y - self._drag_origin[1]
                    if abs(delta_x) + abs(delta_y) > 4:
                        self._moved = True
                    if self._moved:
                        _user32.SetWindowPos(
                            hwnd,
                            wintypes.HWND(HWND_TOPMOST),
                            self._window_origin[0] + delta_x,
                            self._window_origin[1] + delta_y,
                            0,
                            0,
                            SWP_NOSIZE | SWP_NOACTIVATE,
                        )
                else:
                    self._start_hover()
                return 0

            if message == WM_MOUSELEAVE:
                self._tracking_leave = False
                self._stop_hover()
                return 0

            if message == WM_TIMER and wparam == HOVER_TIMER_ID:
                if not self._dragging and self._frames:
                    self._frame_index = (self._frame_index + 1) % len(self._frames)
                    self._show_frame(self._frames[self._frame_index])
                return 0

            if message == WM_LBUTTONUP:
                if self._dragging:
                    self._dragging = False
                    _user32.ReleaseCapture()
                    if not self._moved:
                        self._run(self._on_click)
                    self._start_hover()
                return 0

            if message == WM_RBUTTONUP:
                self._run(self._on_quit)
                return 0
        except Exception:
            logger.exception("悬浮球事件处理失败")

        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

    @staticmethod
    def _cursor_position() -> tuple[int, int]:
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    @staticmethod
    def _window_position(hwnd) -> tuple[int, int]:
        rect = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top

    @staticmethod
    def _run(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            logger.exception("悬浮球回调执行失败")

    # ---------- 生命周期 ----------

    def destroy(self) -> None:
        """销毁悬浮球：跨线程时投递消息，让窗口在自己的线程上关闭。"""
        hwnd = self._hwnd
        if not hwnd:
            return
        if not self._thread.is_alive():
            return
        if _kernel32.GetCurrentThreadId() == self._thread_id:
            # 右键退出回调就在悬浮球线程上，关窗后让消息循环自己收尾
            self._hwnd = None
            _user32.DestroyWindow(hwnd)
            _user32.PostQuitMessage(0)
            return
        _user32.PostMessageW(hwnd, WM_APP_DESTROY, 0, 0)
        self._thread.join(timeout=3)

    def _release_resources(self) -> None:
        hwnd, self._hwnd = self._hwnd, None
        if hwnd:
            _user32.DestroyWindow(hwnd)
        if self._hdc_mem and self._old_bitmap:
            _gdi32.SelectObject(self._hdc_mem, self._old_bitmap)
            self._old_bitmap = None
        if self._hbitmap:
            _gdi32.DeleteObject(self._hbitmap)
            self._hbitmap = None
        if self._hdc_mem:
            _gdi32.DeleteDC(self._hdc_mem)
            self._hdc_mem = None
