from __future__ import annotations

import ctypes
import ctypes.wintypes
import base64
import hashlib
import json
import bisect
import math
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum

import win32api
import win32con
import win32gui
import websocket
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal, QObject, QSettings
from PySide6.QtGui import (
    QColor, QCursor, QFont, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012


class Mode(Enum):
    NORMAL = "normal"
    MAGNIFY = "magnify"


@dataclass(frozen=True)
class ScreenInfo:
    index: int
    name: str
    geometry: QRect

    @property
    def label(self) -> str:
        g = self.geometry
        return f"Screen {self.index + 1}: {self.name}  {g.width()}x{g.height()} at {g.x()},{g.y()}"


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, ctypes.c_int, ctypes.c_void_p
)

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    LowLevelKeyboardProc,
    ctypes.wintypes.HINSTANCE,
    ctypes.wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
user32.CallNextHookEx.argtypes = [
    ctypes.wintypes.HHOOK,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.wintypes.LPARAM
user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(ctypes.wintypes.MSG),
    ctypes.wintypes.HWND,
    ctypes.wintypes.UINT,
    ctypes.wintypes.UINT,
]
user32.GetMessageW.restype = ctypes.wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL


class KeyboardHook(QWidget):
    key_pressed = Signal(int, str, bool, bool, bool)

    def __init__(self) -> None:
        super().__init__()
        self._hook = None
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._proc = LowLevelKeyboardProc(self._keyboard_proc)
        self._pressed: set[int] = set()

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread = None

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0
        )
        if not self._hook:
            return
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _keyboard_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == 0:
            data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(data.vkCode)
            is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)
            if is_down:
                self._pressed.add(vk)
                text = key_text(vk, self._pressed)
                ctrl = win32api.GetAsyncKeyState(win32con.VK_CONTROL) < 0
                shift = win32api.GetAsyncKeyState(win32con.VK_SHIFT) < 0
                alt = win32api.GetAsyncKeyState(win32con.VK_MENU) < 0
                self.key_pressed.emit(vk, text, ctrl, shift, alt)
            elif is_up:
                self._pressed.discard(vk)
        return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)


def key_text(vk: int, pressed: set[int]) -> str:
    names = {
        win32con.VK_ESCAPE: "Esc",
        win32con.VK_BACK: "Backspace",
        win32con.VK_TAB: "Tab",
        win32con.VK_RETURN: "Enter",
        win32con.VK_SPACE: " ",
        win32con.VK_PRIOR: "PageUp",
        win32con.VK_NEXT: "PageDown",
        win32con.VK_END: "End",
        win32con.VK_HOME: "Home",
        win32con.VK_LEFT: "Left",
        win32con.VK_UP: "Up",
        win32con.VK_RIGHT: "Right",
        win32con.VK_DOWN: "Down",
        win32con.VK_DELETE: "Delete",
        win32con.VK_CONTROL: "Ctrl",
        win32con.VK_LCONTROL: "Ctrl",
        win32con.VK_RCONTROL: "Ctrl",
        win32con.VK_SHIFT: "Shift",
        win32con.VK_LSHIFT: "Shift",
        win32con.VK_RSHIFT: "Shift",
        win32con.VK_MENU: "Alt",
        win32con.VK_LMENU: "Alt",
        win32con.VK_RMENU: "Alt",
    }
    if win32con.VK_F1 <= vk <= win32con.VK_F24:
        return f"F{vk - win32con.VK_F1 + 1}"
    if vk in names:
        return names[vk]

    if any(k in pressed for k in (win32con.VK_CONTROL, win32con.VK_LCONTROL, win32con.VK_RCONTROL)):
        if 0x30 <= vk <= 0x5A:
            return chr(vk)

    keyboard_state = (ctypes.c_ubyte * 256)()
    user32.GetKeyboardState(ctypes.byref(keyboard_state))
    buff = ctypes.create_unicode_buffer(8)
    layout = user32.GetKeyboardLayout(0)
    scan = user32.MapVirtualKeyExW(vk, 0, layout)
    result = user32.ToUnicodeEx(vk, scan, keyboard_state, buff, len(buff), 0, layout)
    if result > 0:
        return buff.value[:result]

    if 0x30 <= vk <= 0x5A:
        ch = chr(vk)
        caps = bool(user32.GetKeyState(win32con.VK_CAPITAL) & 1)
        shift = any(k in pressed for k in (win32con.VK_SHIFT, win32con.VK_LSHIFT, win32con.VK_RSHIFT))
        return ch if caps ^ shift else ch.lower()
    return f"VK{vk}"


def obs_auth(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode("utf-8")).digest()).decode("utf-8")


def hotkey_to_text(vk: int, ctrl: bool, shift: bool, alt: bool) -> str:
    parts = []
    if ctrl:
        parts.append("Ctrl")
    if shift:
        parts.append("Shift")
    if alt:
        parts.append("Alt")
    if win32con.VK_F1 <= vk <= win32con.VK_F24:
        parts.append(f"F{vk - win32con.VK_F1 + 1}")
    elif vk == win32con.VK_ESCAPE:
        parts.append("Esc")
    elif vk == win32con.VK_BACK:
        parts.append("Backspace")
    elif vk == win32con.VK_RETURN:
        parts.append("Enter")
    elif vk == win32con.VK_TAB:
        parts.append("Tab")
    elif vk == win32con.VK_SPACE:
        parts.append("Space")
    elif vk == win32con.VK_DELETE:
        parts.append("Delete")
    elif 0x30 <= vk <= 0x5A:
        parts.append(chr(vk))
    else:
        parts.append(f"VK{vk}")
    return "+".join(parts)


def take_screenshot_from_screen(screen) -> str | None:
    import datetime
    pixmap = screen.grabWindow(0)
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
    os.makedirs(folder, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(folder, f"StreamMouse_{timestamp}.png")
    if pixmap.save(path):
        return path
    return None


class ObsStatusPoller(QWidget):
    status_changed = Signal(bool, str, bool)

    def __init__(self) -> None:
        super().__init__()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._request_id = 0

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                live, scene = self._read_obs_status()
                self.status_changed.emit(live, scene, True)
            except Exception:
                self.status_changed.emit(False, "-", False)
            self._stop.wait(3.0)

    def _read_obs_status(self) -> tuple[bool, str]:
        ws = websocket.create_connection("ws://127.0.0.1:4455", timeout=1.5)
        try:
            hello = json.loads(ws.recv())
            hello_data = hello.get("d", {})
            identify = {"rpcVersion": 1}
            auth = hello_data.get("authentication")
            password = os.environ.get("OBS_WEBSOCKET_PASSWORD", "")
            if auth and password:
                identify["authentication"] = obs_auth(password, auth["salt"], auth["challenge"])
            ws.send(json.dumps({"op": 1, "d": identify}))
            response = json.loads(ws.recv())
            if response.get("op") != 2:
                raise RuntimeError("OBS websocket identification failed")

            stream = self._request(ws, "GetStreamStatus")
            scene = self._request(ws, "GetCurrentProgramScene")
            return bool(stream.get("outputActive")), str(scene.get("currentProgramSceneName", "-"))
        finally:
            ws.close()

    def _request(self, ws, request_type: str) -> dict:
        self._request_id += 1
        request_id = str(self._request_id)
        ws.send(json.dumps({"op": 6, "d": {"requestType": request_type, "requestId": request_id}}))
        while True:
            message = json.loads(ws.recv())
            data = message.get("d", {})
            if message.get("op") == 7 and data.get("requestId") == request_id:
                status = data.get("requestStatus", {})
                if not status.get("result", False):
                    raise RuntimeError(status.get("comment", request_type))
                return data.get("responseData", {})


class KeyboardHud(QWidget):
    def __init__(self, screen_geometry: QRect, settings: AppSettings) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._settings = settings
        self._screen_geometry = screen_geometry
        self._tokens: deque[tuple[str, bool]] = deque(maxlen=48)
        self._token_times: deque[float] = deque(maxlen=48)
        self._drag_offset: QPoint | None = None
        self._hovered = False
        self._live = False
        self._scene = "-"
        self._obs_connected = False
        self._update_size()

        self._disappear_timer = QTimer(self)
        self._disappear_timer.timeout.connect(self._check_disappear)
        self._disappear_timer.start(1000)

        settings.changed.connect(self._on_settings_changed)

    def _update_size(self) -> None:
        self.setFixedSize(self._settings.hud_width, self._settings.hud_height)
        self.move(
            self._screen_geometry.x() + (self._screen_geometry.width() - self.width()) // 2,
            self._screen_geometry.y() + self._screen_geometry.height() - self.height() - 56,
        )

    def _on_settings_changed(self) -> None:
        self._update_size()
        self.update()

    def set_obs_status(self, live: bool, scene: str, connected: bool) -> None:
        self._live = live
        self._scene = scene or "-"
        self._obs_connected = connected
        self.update()

    def add_key(self, text: str, ctrl: bool, shift: bool, alt: bool) -> None:
        if text in {"Ctrl", "Shift", "Alt"}:
            return
        now = time.time()
        if text == "Backspace":
            if self._tokens:
                self._tokens.pop()
                self._token_times.pop()
        elif text == "Enter":
            self._tokens.append(("Enter", True))
            self._token_times.append(now)
        elif text == "Tab":
            self._tokens.append(("Tab", True))
            self._token_times.append(now)
        elif len(text) == 1:
            if ctrl or alt:
                combo = "+".join(
                    [name for name, on in (("Ctrl", ctrl), ("Alt", alt), ("Shift", shift)) if on]
                    + [text.upper()]
                )
                self._tokens.append((combo, True))
            else:
                self._tokens.append((text, False))
            self._token_times.append(now)
        else:
            prefix = "+".join(
                [name for name, on in (("Ctrl", ctrl), ("Alt", alt), ("Shift", shift)) if on]
            )
            self._tokens.append((f"{prefix + '+' if prefix else ''}{text}", True))
            self._token_times.append(now)

        joined = "".join(token for token, _ in self._tokens)
        while len(joined) > 80 and self._tokens:
            self._tokens.popleft()
            self._token_times.popleft()
            joined = "".join(token for token, _ in self._tokens)
        self.update()

    def _check_disappear(self) -> None:
        secs = self._settings.text_disappear_secs
        if secs <= 0:
            return
        now = time.time()
        changed = False
        while self._token_times and (now - self._token_times[0]) > secs:
            self._token_times.popleft()
            self._tokens.popleft()
            changed = True
        if changed:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        if self._hovered or self._drag_offset is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(18, 22, 30, self._settings.hud_bg_alpha))
            painter.drawRoundedRect(rect, 10, 10)

        self._paint_tokens(painter)
        self._paint_obs_status(painter)

    def _paint_tokens(self, painter: QPainter) -> None:
        text_color = QColor(self._settings.hud_text_color)
        text_color.setAlpha(self._settings.hud_text_alpha)
        painter.setFont(QFont(self._settings.hud_font_family, self._settings.hud_font_size, QFont.Weight.DemiBold))
        metrics = painter.fontMetrics()
        y = 12
        min_x = 12
        x = self.width() - 12
        placements: list[tuple[int, int, str, bool]] = []
        for token, special in reversed(self._tokens):
            if special:
                token_width = metrics.horizontalAdvance(token) + 18
                next_x = x - token_width
                if next_x < min_x:
                    break
                placements.append((next_x, token_width, token, True))
                x = next_x - 7
            else:
                token_width = metrics.horizontalAdvance(token)
                next_x = x - token_width
                if next_x < min_x:
                    break
                placements.append((next_x, token_width, token, False))
                x = next_x - 2

        for x, token_width, token, special in reversed(placements):
            if special:
                key_rect = QRect(x, y + 1, token_width, max(20, self._settings.hud_font_size + 8))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 52))
                painter.drawRoundedRect(key_rect, 6, 6)
                painter.setPen(text_color)
                painter.drawText(key_rect, Qt.AlignmentFlag.AlignCenter, token)
            else:
                painter.setPen(text_color)
                painter.drawText(x, y + self._settings.hud_font_size + 6, token)

    def _paint_obs_status(self, painter: QPainter) -> None:
        status = "LIVE" if self._live else "OFFLINE"
        scene = self._scene if self._obs_connected else "-"
        text = f"{status}  {scene}"
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        metrics = painter.fontMetrics()
        width = min(metrics.horizontalAdvance(text) + 18, self.width() - 22)
        rect = QRect(self.width() - width - 10, self.height() - 27, width, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(220, 38, 38, 132) if self._live else QColor(18, 22, 30, 88))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor(255, 255, 255, 228))
        painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, text)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is None:
            return
        pos = event.globalPosition().toPoint() - self._drag_offset
        max_x = self._screen_geometry.x() + self._screen_geometry.width() - self.width()
        max_y = self._screen_geometry.y() + self._screen_geometry.height() - self.height()
        pos.setX(max(self._screen_geometry.x(), min(pos.x(), max_x)))
        pos.setY(max(self._screen_geometry.y(), min(pos.y(), max_y)))
        self.move(pos)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None


class AppSettings(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._qsettings = QSettings("StreamMouse", "AppSettings")
        self._line_width = 4
        self._stroke_width = 5
        self._hud_width = 430
        self._hud_height = 78
        self._hud_bg_alpha = 82
        self._hud_font_family = "Cascadia Mono"
        self._hud_font_size = 21
        self._hud_text_color = QColor(252, 254, 255)
        self._hud_text_alpha = 250
        self._text_disappear_secs = 0
        self._zoom_step = 0.25
        self._zoom_idle_timeout = 0
        self._crosshair_style = "十字線 (Crosshair)"
        self._crosshair_size = 14
        self._crosshair_color = QColor(255, 255, 255)
        self._crosshair_alpha = 150
        self._magnify_style = "全螢幕 (Fullscreen)"
        self._magnify_start_zoom = 1.0
        self._lens_radius = 150
        self._pulse_style = "雙圓圈 (Double)"
        self._pulse_size = 20
        self._pulse_speed = 1.0
        self._pulse_color = QColor(30, 120, 255)
        self._hotkeys = {
            "escape": {"vk": 27, "ctrl": False, "shift": False, "alt": False},
            "recording": {"vk": 112, "ctrl": True, "shift": False, "alt": False},
            "magnify": {"vk": 114, "ctrl": True, "shift": False, "alt": False},
            "undo": {"vk": 90, "ctrl": True, "shift": False, "alt": False},
            "redo": {"vk": 90, "ctrl": True, "shift": True, "alt": False},
        }
        self.load()

    def _emit(self) -> None:
        self.changed.emit()
        self.save()

    @property
    def line_width(self) -> int:
        return self._line_width

    @line_width.setter
    def line_width(self, v: int) -> None:
        if self._line_width != v:
            self._line_width = v
            self._emit()

    @property
    def stroke_width(self) -> int:
        return self._stroke_width

    @stroke_width.setter
    def stroke_width(self, v: int) -> None:
        if self._stroke_width != v:
            self._stroke_width = v
            self._emit()

    @property
    def hud_width(self) -> int:
        return self._hud_width

    @hud_width.setter
    def hud_width(self, v: int) -> None:
        if self._hud_width != v:
            self._hud_width = v
            self._emit()

    @property
    def hud_height(self) -> int:
        return self._hud_height

    @hud_height.setter
    def hud_height(self, v: int) -> None:
        if self._hud_height != v:
            self._hud_height = v
            self._emit()

    @property
    def hud_bg_alpha(self) -> int:
        return self._hud_bg_alpha

    @hud_bg_alpha.setter
    def hud_bg_alpha(self, v: int) -> None:
        if self._hud_bg_alpha != v:
            self._hud_bg_alpha = v
            self._emit()

    @property
    def hud_font_family(self) -> str:
        return self._hud_font_family

    @hud_font_family.setter
    def hud_font_family(self, v: str) -> None:
        if self._hud_font_family != v:
            self._hud_font_family = v
            self._emit()

    @property
    def hud_font_size(self) -> int:
        return self._hud_font_size

    @hud_font_size.setter
    def hud_font_size(self, v: int) -> None:
        if self._hud_font_size != v:
            self._hud_font_size = v
            self._emit()

    @property
    def hud_text_color(self) -> QColor:
        return self._hud_text_color

    @hud_text_color.setter
    def hud_text_color(self, v: QColor) -> None:
        if self._hud_text_color != v:
            self._hud_text_color = v
            self._emit()

    @property
    def hud_text_alpha(self) -> int:
        return self._hud_text_alpha

    @hud_text_alpha.setter
    def hud_text_alpha(self, v: int) -> None:
        if self._hud_text_alpha != v:
            self._hud_text_alpha = v
            self._emit()

    @property
    def text_disappear_secs(self) -> int:
        return self._text_disappear_secs

    @text_disappear_secs.setter
    def text_disappear_secs(self, v: int) -> None:
        if self._text_disappear_secs != v:
            self._text_disappear_secs = v
            self._emit()

    @property
    def zoom_step(self) -> float:
        return self._zoom_step

    @zoom_step.setter
    def zoom_step(self, v: float) -> None:
        if self._zoom_step != v:
            self._zoom_step = v
            self._emit()

    @property
    def zoom_idle_timeout(self) -> int:
        return self._zoom_idle_timeout

    @zoom_idle_timeout.setter
    def zoom_idle_timeout(self, v: int) -> None:
        if self._zoom_idle_timeout != v:
            self._zoom_idle_timeout = v
            self._emit()

    @property
    def crosshair_style(self) -> str:
        return self._crosshair_style

    @crosshair_style.setter
    def crosshair_style(self, v: str) -> None:
        if self._crosshair_style != v:
            self._crosshair_style = v
            self._emit()

    @property
    def crosshair_size(self) -> int:
        return self._crosshair_size

    @crosshair_size.setter
    def crosshair_size(self, v: int) -> None:
        if self._crosshair_size != v:
            self._crosshair_size = v
            self._emit()

    @property
    def crosshair_color(self) -> QColor:
        return self._crosshair_color

    @crosshair_color.setter
    def crosshair_color(self, v: QColor) -> None:
        if self._crosshair_color != v:
            self._crosshair_color = v
            self._emit()

    @property
    def crosshair_alpha(self) -> int:
        return self._crosshair_alpha

    @crosshair_alpha.setter
    def crosshair_alpha(self, v: int) -> None:
        if self._crosshair_alpha != v:
            self._crosshair_alpha = v
            self._emit()

    @property
    def magnify_style(self) -> str:
        return self._magnify_style

    @magnify_style.setter
    def magnify_style(self, v: str) -> None:
        if self._magnify_style != v:
            self._magnify_style = v
            self._emit()

    @property
    def magnify_start_zoom(self) -> float:
        return self._magnify_start_zoom

    @magnify_start_zoom.setter
    def magnify_start_zoom(self, v: float) -> None:
        if self._magnify_start_zoom != v:
            self._magnify_start_zoom = v
            self._emit()

    @property
    def lens_radius(self) -> int:
        return self._lens_radius

    @lens_radius.setter
    def lens_radius(self, v: int) -> None:
        if self._lens_radius != v:
            self._lens_radius = v
            self._emit()

    @property
    def pulse_style(self) -> str:
        return self._pulse_style

    @pulse_style.setter
    def pulse_style(self, v: str) -> None:
        if self._pulse_style != v:
            self._pulse_style = v
            self._emit()

    @property
    def pulse_size(self) -> int:
        return self._pulse_size

    @pulse_size.setter
    def pulse_size(self, v: int) -> None:
        if self._pulse_size != v:
            self._pulse_size = v
            self._emit()

    @property
    def pulse_speed(self) -> float:
        return self._pulse_speed

    @pulse_speed.setter
    def pulse_speed(self, v: float) -> None:
        if self._pulse_speed != v:
            self._pulse_speed = v
            self._emit()

    @property
    def pulse_color(self) -> QColor:
        return self._pulse_color

    @pulse_color.setter
    def pulse_color(self, v: QColor) -> None:
        if self._pulse_color != v:
            self._pulse_color = v
            self._emit()

    def get_hotkey_text(self, action: str) -> str:
        hk = self._hotkeys.get(action)
        if hk is None:
            return ""
        return hotkey_to_text(hk["vk"], hk["ctrl"], hk["shift"], hk["alt"])

    def set_hotkey(self, action: str, vk: int, ctrl: bool, shift: bool, alt: bool) -> None:
        if action in self._hotkeys:
            self._hotkeys[action] = {"vk": vk, "ctrl": ctrl, "shift": shift, "alt": alt}
            self._emit()

    def match_hotkey(self, vk: int, ctrl: bool, shift: bool, alt: bool) -> str | None:
        for action, hk in self._hotkeys.items():
            if hk["vk"] == vk and hk["ctrl"] == ctrl and hk["shift"] == shift and hk["alt"] == alt:
                return action
        return None

    def save(self) -> None:
        s = self._qsettings
        s.setValue("line_width", self._line_width)
        s.setValue("stroke_width", self._stroke_width)
        s.setValue("hud_width", self._hud_width)
        s.setValue("hud_height", self._hud_height)
        s.setValue("hud_bg_alpha", self._hud_bg_alpha)
        s.setValue("hud_font_family", self._hud_font_family)
        s.setValue("hud_font_size", self._hud_font_size)
        s.setValue("hud_text_color", self._hud_text_color.rgba())
        s.setValue("hud_text_alpha", self._hud_text_alpha)
        s.setValue("text_disappear_secs", self._text_disappear_secs)
        s.setValue("zoom_step", self._zoom_step)
        s.setValue("zoom_idle_timeout", self._zoom_idle_timeout)
        s.setValue("crosshair_style", self._crosshair_style)
        s.setValue("crosshair_size", self._crosshair_size)
        s.setValue("crosshair_color", self._crosshair_color.rgba())
        s.setValue("crosshair_alpha", self._crosshair_alpha)
        s.setValue("magnify_style", self._magnify_style)
        s.setValue("magnify_start_zoom", self._magnify_start_zoom)
        s.setValue("lens_radius", self._lens_radius)
        s.setValue("pulse_style", self._pulse_style)
        s.setValue("pulse_size", self._pulse_size)
        s.setValue("pulse_speed", self._pulse_speed)
        s.setValue("pulse_color", self._pulse_color.rgba())
        for action, hk in self._hotkeys.items():
            s.setValue(f"hotkey_{action}_vk", hk["vk"])
            s.setValue(f"hotkey_{action}_ctrl", hk["ctrl"])
            s.setValue(f"hotkey_{action}_shift", hk["shift"])
            s.setValue(f"hotkey_{action}_alt", hk["alt"])

    def load(self) -> None:
        s = self._qsettings
        self._line_width = int(s.value("line_width", self._line_width))
        self._stroke_width = int(s.value("stroke_width", self._stroke_width))
        self._hud_width = int(s.value("hud_width", self._hud_width))
        self._hud_height = int(s.value("hud_height", self._hud_height))
        self._hud_bg_alpha = int(s.value("hud_bg_alpha", self._hud_bg_alpha))
        self._hud_font_family = str(s.value("hud_font_family", self._hud_font_family))
        self._hud_font_size = int(s.value("hud_font_size", self._hud_font_size))
        rgba = s.value("hud_text_color")
        if rgba is not None:
            self._hud_text_color = QColor.fromRgba(int(rgba))
        self._hud_text_alpha = int(s.value("hud_text_alpha", self._hud_text_alpha))
        self._text_disappear_secs = int(s.value("text_disappear_secs", self._text_disappear_secs))
        self._zoom_step = float(s.value("zoom_step", self._zoom_step))
        self._zoom_idle_timeout = int(s.value("zoom_idle_timeout", self._zoom_idle_timeout))
        self._crosshair_style = str(s.value("crosshair_style", self._crosshair_style))
        self._crosshair_size = int(s.value("crosshair_size", self._crosshair_size))
        rgba2 = s.value("crosshair_color")
        if rgba2 is not None:
            self._crosshair_color = QColor.fromRgba(int(rgba2))
        self._crosshair_alpha = int(s.value("crosshair_alpha", self._crosshair_alpha))
        self._magnify_style = str(s.value("magnify_style", self._magnify_style))
        self._magnify_start_zoom = float(s.value("magnify_start_zoom", self._magnify_start_zoom))
        self._lens_radius = int(s.value("lens_radius", self._lens_radius))
        self._pulse_style = str(s.value("pulse_style", self._pulse_style))
        self._pulse_size = int(s.value("pulse_size", self._pulse_size))
        self._pulse_speed = float(s.value("pulse_speed", self._pulse_speed))
        rgba_pulse = s.value("pulse_color")
        if rgba_pulse is not None:
            self._pulse_color = QColor.fromRgba(int(rgba_pulse))
        for action in self._hotkeys:
            vk = s.value(f"hotkey_{action}_vk")
            if vk is not None:
                self._hotkeys[action] = {
                    "vk": int(vk),
                    "ctrl": s.value(f"hotkey_{action}_ctrl", False, type=bool),
                    "shift": s.value(f"hotkey_{action}_shift", False, type=bool),
                    "alt": s.value(f"hotkey_{action}_alt", False, type=bool),
                }


CROSSHAIR_STYLES = [
    "十字線 (Crosshair)",
    "圓圈 (Circle)",
    "圓圈+十字線",
    "瞄準環 (Reticle)",
    "點 (Dot)",
    "無 (None)",
]

MAGNIFY_STYLES = [
    "全螢幕 (Fullscreen)",
    "跟隨鏡頭 (Lens)",
]

PULSE_STYLES = [
    "雙圓圈 (Double)",
    "單圓圈 (Ring)",
    "十字線 (Cross)",
    "點+圓 (Dot+Ring)",
    "無 (None)",
]


class SettingsDialog(QDialog):
    hotkey_listening = Signal(str)

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("設定")
        self.setMinimumWidth(520)
        self._listening_action: str | None = None
        self._listening_button: QPushButton | None = None

        layout = QVBoxLayout(self)

        general_group = QGroupBox("一般設定")
        general_layout = QFormLayout(general_group)

        self.line_width_spin = QSpinBox()
        self.line_width_spin.setRange(1, 50)
        self.line_width_spin.setValue(settings.line_width)
        self.line_width_spin.valueChanged.connect(lambda v: setattr(settings, "line_width", v))
        general_layout.addRow("路徑線條粗細:", self.line_width_spin)

        self.stroke_width_spin = QSpinBox()
        self.stroke_width_spin.setRange(1, 50)
        self.stroke_width_spin.setValue(settings.stroke_width)
        self.stroke_width_spin.valueChanged.connect(lambda v: setattr(settings, "stroke_width", v))
        general_layout.addRow("放大繪圖筆畫粗細:", self.stroke_width_spin)

        layout.addWidget(general_group)

        text_group = QGroupBox("文字區域 (HUD)")
        text_layout = QFormLayout(text_group)

        size_row = QHBoxLayout()
        self.hud_width_spin = QSpinBox()
        self.hud_width_spin.setRange(100, 2000)
        self.hud_width_spin.setValue(settings.hud_width)
        self.hud_width_spin.valueChanged.connect(lambda v: setattr(settings, "hud_width", v))
        size_row.addWidget(QLabel("寬度:"))
        size_row.addWidget(self.hud_width_spin)
        self.hud_height_spin = QSpinBox()
        self.hud_height_spin.setRange(30, 500)
        self.hud_height_spin.setValue(settings.hud_height)
        self.hud_height_spin.valueChanged.connect(lambda v: setattr(settings, "hud_height", v))
        size_row.addWidget(QLabel("高度:"))
        size_row.addWidget(self.hud_height_spin)
        text_layout.addRow("區域:", size_row)

        self.hud_bg_slider = QSlider(Qt.Orientation.Horizontal)
        self.hud_bg_slider.setRange(0, 100)
        self.hud_bg_slider.setValue(settings.hud_bg_alpha * 100 // 255)
        self.hud_bg_slider.valueChanged.connect(
            lambda v: setattr(settings, "hud_bg_alpha", v * 255 // 100)
        )
        text_layout.addRow("背景透明度:", self.hud_bg_slider)

        self.font_combo = QComboBox()
        self.font_combo.setEditable(True)
        import platform
        if platform.system() == "Windows":
            self.font_combo.addItems([
                "Cascadia Mono", "Consolas", "Courier New", "Microsoft JhengHei",
                "Segoe UI", "Arial", "YaHei Consolas Hybrid",
            ])
        else:
            self.font_combo.addItems(["monospace", "sans-serif", "serif"])
        idx = self.font_combo.findText(settings.hud_font_family)
        if idx >= 0:
            self.font_combo.setCurrentIndex(idx)
        else:
            self.font_combo.setCurrentText(settings.hud_font_family)
        self.font_combo.currentTextChanged.connect(lambda v: setattr(settings, "hud_font_family", v))
        text_layout.addRow("字型:", self.font_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 120)
        self.font_size_spin.setValue(settings.hud_font_size)
        self.font_size_spin.valueChanged.connect(lambda v: setattr(settings, "hud_font_size", v))
        text_layout.addRow("字體大小:", self.font_size_spin)

        color_row = QHBoxLayout()
        self.text_color_btn = QPushButton()
        self._update_color_button(self.text_color_btn, settings.hud_text_color)
        self.text_color_btn.clicked.connect(self._pick_text_color)
        color_row.addWidget(self.text_color_btn)
        self.text_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.text_alpha_slider.setRange(0, 100)
        self.text_alpha_slider.setValue(settings.hud_text_alpha * 100 // 255)
        self.text_alpha_slider.valueChanged.connect(
            lambda v: setattr(settings, "hud_text_alpha", v * 255 // 100)
        )
        color_row.addWidget(self.text_alpha_slider)
        text_layout.addRow("文字顏色/透明度:", color_row)

        self.text_disappear_spin = QSpinBox()
        self.text_disappear_spin.setRange(0, 999)
        self.text_disappear_spin.setSuffix(" 秒")
        self.text_disappear_spin.setSpecialValueText("永不")
        self.text_disappear_spin.setValue(settings.text_disappear_secs)
        self.text_disappear_spin.valueChanged.connect(
            lambda v: setattr(settings, "text_disappear_secs", v)
        )
        text_layout.addRow("文字自動消失:", self.text_disappear_spin)

        layout.addWidget(text_group)

        magnifier_group = QGroupBox("放大鏡")
        magnifier_layout = QFormLayout(magnifier_group)

        self.magnify_style_combo = QComboBox()
        self.magnify_style_combo.addItems(MAGNIFY_STYLES)
        idx_ms = self.magnify_style_combo.findText(settings.magnify_style)
        if idx_ms >= 0:
            self.magnify_style_combo.setCurrentIndex(idx_ms)
        self.magnify_style_combo.currentTextChanged.connect(
            lambda v: setattr(settings, "magnify_style", v)
        )
        magnifier_layout.addRow("放大鏡樣式:", self.magnify_style_combo)

        self.magnify_start_zoom_spin = QDoubleSpinBox()
        self.magnify_start_zoom_spin.setRange(1.0, 6.0)
        self.magnify_start_zoom_spin.setSingleStep(0.25)
        self.magnify_start_zoom_spin.setValue(settings.magnify_start_zoom)
        self.magnify_start_zoom_spin.valueChanged.connect(
            lambda v: setattr(settings, "magnify_start_zoom", v)
        )
        magnifier_layout.addRow("進入初始縮放:", self.magnify_start_zoom_spin)

        self.lens_radius_spin = QSpinBox()
        self.lens_radius_spin.setRange(50, 600)
        self.lens_radius_spin.setSuffix(" px")
        self.lens_radius_spin.setValue(settings.lens_radius)
        self.lens_radius_spin.valueChanged.connect(
            lambda v: setattr(settings, "lens_radius", v)
        )
        magnifier_layout.addRow("鏡頭半徑 (Lens):", self.lens_radius_spin)

        self.zoom_step_spin = QDoubleSpinBox()
        self.zoom_step_spin.setRange(0.05, 2.0)
        self.zoom_step_spin.setSingleStep(0.05)
        self.zoom_step_spin.setValue(settings.zoom_step)
        self.zoom_step_spin.valueChanged.connect(lambda v: setattr(settings, "zoom_step", v))
        magnifier_layout.addRow("縮放步進:", self.zoom_step_spin)

        self.zoom_idle_spin = QSpinBox()
        self.zoom_idle_spin.setRange(0, 300)
        self.zoom_idle_spin.setSuffix(" 秒")
        self.zoom_idle_spin.setSpecialValueText("永不")
        self.zoom_idle_spin.setValue(settings.zoom_idle_timeout)
        self.zoom_idle_spin.valueChanged.connect(
            lambda v: setattr(settings, "zoom_idle_timeout", v)
        )
        magnifier_layout.addRow("閒置自動退出:", self.zoom_idle_spin)

        self.crosshair_style_combo = QComboBox()
        self.crosshair_style_combo.addItems(CROSSHAIR_STYLES)
        idx2 = self.crosshair_style_combo.findText(settings.crosshair_style)
        if idx2 >= 0:
            self.crosshair_style_combo.setCurrentIndex(idx2)
        self.crosshair_style_combo.currentTextChanged.connect(
            lambda v: setattr(settings, "crosshair_style", v)
        )
        magnifier_layout.addRow("準心樣式:", self.crosshair_style_combo)

        self.crosshair_size_spin = QSpinBox()
        self.crosshair_size_spin.setRange(2, 100)
        self.crosshair_size_spin.setValue(settings.crosshair_size)
        self.crosshair_size_spin.valueChanged.connect(
            lambda v: setattr(settings, "crosshair_size", v)
        )
        magnifier_layout.addRow("準心大小:", self.crosshair_size_spin)

        crosshair_color_row = QHBoxLayout()
        self.crosshair_color_btn = QPushButton()
        self._update_color_button(self.crosshair_color_btn, settings.crosshair_color)
        self.crosshair_color_btn.clicked.connect(self._pick_crosshair_color)
        crosshair_color_row.addWidget(self.crosshair_color_btn)
        self.crosshair_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.crosshair_alpha_slider.setRange(0, 100)
        self.crosshair_alpha_slider.setValue(settings.crosshair_alpha * 100 // 255)
        self.crosshair_alpha_slider.valueChanged.connect(
            lambda v: setattr(settings, "crosshair_alpha", v * 255 // 100)
        )
        crosshair_color_row.addWidget(self.crosshair_alpha_slider)
        magnifier_layout.addRow("準心顏色/透明度:", crosshair_color_row)

        layout.addWidget(magnifier_group)

        cursor_group = QGroupBox("遊標 (呼吸效果)")
        cursor_layout = QFormLayout(cursor_group)

        self.pulse_style_combo = QComboBox()
        self.pulse_style_combo.addItems(PULSE_STYLES)
        idx_ps = self.pulse_style_combo.findText(settings.pulse_style)
        if idx_ps >= 0:
            self.pulse_style_combo.setCurrentIndex(idx_ps)
        self.pulse_style_combo.currentTextChanged.connect(
            lambda v: setattr(settings, "pulse_style", v)
        )
        cursor_layout.addRow("樣式:", self.pulse_style_combo)

        self.pulse_size_spin = QSpinBox()
        self.pulse_size_spin.setRange(4, 120)
        self.pulse_size_spin.setValue(settings.pulse_size)
        self.pulse_size_spin.valueChanged.connect(lambda v: setattr(settings, "pulse_size", v))
        cursor_layout.addRow("大小 (基礎半徑):", self.pulse_size_spin)

        self.pulse_speed_spin = QDoubleSpinBox()
        self.pulse_speed_spin.setRange(0.1, 5.0)
        self.pulse_speed_spin.setSingleStep(0.1)
        self.pulse_speed_spin.setValue(settings.pulse_speed)
        self.pulse_speed_spin.valueChanged.connect(lambda v: setattr(settings, "pulse_speed", v))
        cursor_layout.addRow("速度:", self.pulse_speed_spin)

        self.pulse_color_btn = QPushButton()
        self._update_color_button(self.pulse_color_btn, settings.pulse_color)
        self.pulse_color_btn.clicked.connect(self._pick_pulse_color)
        cursor_layout.addRow("顏色:", self.pulse_color_btn)

        layout.addWidget(cursor_group)

        hotkeys_group = QGroupBox("快速鍵")
        hotkeys_layout = QFormLayout(hotkeys_group)

        self._hotkey_widgets: dict[str, QPushButton] = {}
        for action, label in [
            ("recording", "錄製切換"),
            ("magnify", "放大鏡/截圖"),
            ("escape", "返回一般模式"),
            ("undo", "放大繪圖還原 (Undo)"),
            ("redo", "放大繪圖復原 (Redo)"),
        ]:
            row = QHBoxLayout()
            btn = QPushButton(settings.get_hotkey_text(action))
            btn.setFixedWidth(200)
            btn.clicked.connect(lambda checked, a=action, b=btn: self._start_listening(a, b))
            row.addWidget(btn)
            row.addStretch()
            hotkeys_layout.addRow(f"{label}:", row)
            self._hotkey_widgets[action] = btn

        layout.addWidget(hotkeys_group)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("重設預設")
        reset_btn.clicked.connect(self._reset_defaults)
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _update_color_button(self, btn: QPushButton, color: QColor) -> None:
        btn.setStyleSheet(
            f"background-color: {color.name()}; min-width: 48px; min-height: 24px; "
            f"border: 2px solid #888; border-radius: 4px;"
        )
        btn.setText("")
        btn.setToolTip(f"{color.name()}  (點擊選擇顏色)")

    def _pick_text_color(self) -> None:
        color = QColorDialog.getColor(self.settings.hud_text_color, self, "選擇文字顏色")
        if color.isValid():
            self.settings.hud_text_color = color
            self._update_color_button(self.text_color_btn, color)

    def _pick_crosshair_color(self) -> None:
        color = QColorDialog.getColor(self.settings.crosshair_color, self, "選擇準心顏色")
        if color.isValid():
            self.settings.crosshair_color = color
            self._update_color_button(self.crosshair_color_btn, color)

    def _pick_pulse_color(self) -> None:
        color = QColorDialog.getColor(self.settings.pulse_color, self, "選擇遊標顏色")
        if color.isValid():
            self.settings.pulse_color = color
            self._update_color_button(self.pulse_color_btn, color)

    def _start_listening(self, action: str, button: QPushButton) -> None:
        self._listening_action = action
        self._listening_button = button
        button.setText("按下按鍵...")
        self.hotkey_listening.emit(action)

    def assign_hotkey(self, vk: int, ctrl: bool, shift: bool, alt: bool) -> None:
        if self._listening_action and self._listening_button:
            self.settings.set_hotkey(self._listening_action, vk, ctrl, shift, alt)
            self._listening_button.setText(self.settings.get_hotkey_text(self._listening_action))
            self._listening_action = None
            self._listening_button = None

    @property
    def is_listening(self) -> bool:
        return self._listening_action is not None

    def _reset_defaults(self) -> None:
        s = self.settings
        s.line_width = 4
        s.stroke_width = 5
        s.hud_width = 430
        s.hud_height = 78
        s.hud_bg_alpha = 82
        s.hud_font_family = "Cascadia Mono"
        s.hud_font_size = 21
        s.hud_text_color = QColor(252, 254, 255)
        s.hud_text_alpha = 250
        s.text_disappear_secs = 0
        s.magnify_style = "全螢幕 (Fullscreen)"
        s.magnify_start_zoom = 1.0
        s.lens_radius = 150
        s.pulse_style = "雙圓圈 (Double)"
        s.pulse_size = 20
        s.pulse_speed = 1.0
        s.pulse_color = QColor(30, 120, 255)
        s.zoom_step = 0.25
        s.zoom_idle_timeout = 0
        s.crosshair_style = "十字線 (Crosshair)"
        s.crosshair_size = 14
        s.crosshair_color = QColor(255, 255, 255)
        s.crosshair_alpha = 150
        s.set_hotkey("escape", 27, False, False, False)
        s.set_hotkey("recording", 112, True, False, False)
        s.set_hotkey("magnify", 114, True, False, False)
        s.set_hotkey("undo", 90, True, False, False)
        s.set_hotkey("redo", 90, True, True, False)
        self._sync_ui()

    def _sync_ui(self) -> None:
        s = self.settings
        self.line_width_spin.blockSignals(True)
        self.line_width_spin.setValue(s.line_width)
        self.line_width_spin.blockSignals(False)

        self.stroke_width_spin.blockSignals(True)
        self.stroke_width_spin.setValue(s.stroke_width)
        self.stroke_width_spin.blockSignals(False)

        self.hud_width_spin.blockSignals(True)
        self.hud_width_spin.setValue(s.hud_width)
        self.hud_width_spin.blockSignals(False)

        self.hud_height_spin.blockSignals(True)
        self.hud_height_spin.setValue(s.hud_height)
        self.hud_height_spin.blockSignals(False)

        self.hud_bg_slider.blockSignals(True)
        self.hud_bg_slider.setValue(s.hud_bg_alpha * 100 // 255)
        self.hud_bg_slider.blockSignals(False)

        self.font_combo.blockSignals(True)
        idx = self.font_combo.findText(s.hud_font_family)
        if idx >= 0:
            self.font_combo.setCurrentIndex(idx)
        else:
            self.font_combo.setCurrentText(s.hud_font_family)
        self.font_combo.blockSignals(False)

        self.font_size_spin.blockSignals(True)
        self.font_size_spin.setValue(s.hud_font_size)
        self.font_size_spin.blockSignals(False)

        self.text_alpha_slider.blockSignals(True)
        self.text_alpha_slider.setValue(s.hud_text_alpha * 100 // 255)
        self.text_alpha_slider.blockSignals(False)

        self.text_disappear_spin.blockSignals(True)
        self.text_disappear_spin.setValue(s.text_disappear_secs)
        self.text_disappear_spin.blockSignals(False)

        self.magnify_style_combo.blockSignals(True)
        idx_ms = self.magnify_style_combo.findText(s.magnify_style)
        if idx_ms >= 0:
            self.magnify_style_combo.setCurrentIndex(idx_ms)
        self.magnify_style_combo.blockSignals(False)

        self.magnify_start_zoom_spin.blockSignals(True)
        self.magnify_start_zoom_spin.setValue(s.magnify_start_zoom)
        self.magnify_start_zoom_spin.blockSignals(False)

        self.lens_radius_spin.blockSignals(True)
        self.lens_radius_spin.setValue(s.lens_radius)
        self.lens_radius_spin.blockSignals(False)

        self.zoom_step_spin.blockSignals(True)
        self.zoom_step_spin.setValue(s.zoom_step)
        self.zoom_step_spin.blockSignals(False)

        self.zoom_idle_spin.blockSignals(True)
        self.zoom_idle_spin.setValue(s.zoom_idle_timeout)
        self.zoom_idle_spin.blockSignals(False)

        self.crosshair_style_combo.blockSignals(True)
        idx2 = self.crosshair_style_combo.findText(s.crosshair_style)
        if idx2 >= 0:
            self.crosshair_style_combo.setCurrentIndex(idx2)
        self.crosshair_style_combo.blockSignals(False)

        self.crosshair_size_spin.blockSignals(True)
        self.crosshair_size_spin.setValue(s.crosshair_size)
        self.crosshair_size_spin.blockSignals(False)

        self.crosshair_alpha_slider.blockSignals(True)
        self.crosshair_alpha_slider.setValue(s.crosshair_alpha * 100 // 255)
        self.crosshair_alpha_slider.blockSignals(False)

        self.pulse_style_combo.blockSignals(True)
        idx_ps = self.pulse_style_combo.findText(s.pulse_style)
        if idx_ps >= 0:
            self.pulse_style_combo.setCurrentIndex(idx_ps)
        self.pulse_style_combo.blockSignals(False)

        self.pulse_size_spin.blockSignals(True)
        self.pulse_size_spin.setValue(s.pulse_size)
        self.pulse_size_spin.blockSignals(False)

        self.pulse_speed_spin.blockSignals(True)
        self.pulse_speed_spin.setValue(s.pulse_speed)
        self.pulse_speed_spin.blockSignals(False)

        self._update_color_button(self.text_color_btn, s.hud_text_color)
        self._update_color_button(self.crosshair_color_btn, s.crosshair_color)
        self._update_color_button(self.pulse_color_btn, s.pulse_color)
        for action, btn in self._hotkey_widgets.items():
            btn.setText(s.get_hotkey_text(action))


class OverlayWindow(QWidget):
    def __init__(self, screen, screen_info: ScreenInfo, settings: AppSettings) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.screen = screen
        self.screen_info = screen_info
        self.screen_geometry = screen_info.geometry
        self.settings = settings
        self.setGeometry(self.screen_geometry)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.mode = Mode.NORMAL
        self.recording = False
        self.recorded_points: list[QPoint] = []
        self._recorded_times: list[float] = []
        self.paths: list[list[QPoint]] = []
        self._path_times: list[list[float]] = []
        self._path_anim_starts: list[float] = []
        self.draw_color = QColor(255, 70, 70)
        self.freeze_pixmap: QPixmap | None = None
        self.zoom = 3.0
        self.mouse_local = QPoint(self.width() // 2, self.height() // 2)
        self._zoom_anchor = QPoint(self.width() // 2, self.height() // 2)
        self._tick = 0
        self._magnify_strokes: list[tuple[QColor, list[QPoint]]] = []
        self._magnify_active: list[QPoint] | None = None
        self._magnify_redo: list[tuple[QColor, list[QPoint]]] = []
        self._last_interaction_time = 0.0
        self._screenshot_flash = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(16)

        settings.changed.connect(self.update)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.set_click_through(True)

    def set_click_through(self, enabled: bool) -> None:
        hwnd = int(self.winId())
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if enabled:
            ex_style |= win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED | win32con.WS_EX_NOACTIVATE
        else:
            ex_style &= ~win32con.WS_EX_TRANSPARENT
            ex_style |= win32con.WS_EX_LAYERED | win32con.WS_EX_NOACTIVATE
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            self.x(),
            self.y(),
            self.width(),
            self.height(),
            win32con.SWP_NOACTIVATE | win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_FRAMECHANGED,
        )

    def _on_tick(self) -> None:
        global_pos = QCursor.pos()
        if self.screen_geometry.contains(global_pos):
            self.mouse_local = global_pos - self.screen_geometry.topLeft()
            if self.recording and self.mode == Mode.NORMAL:
                if not self.recorded_points or manhattan(self.recorded_points[-1], self.mouse_local) >= 3:
                    self.recorded_points.append(QPoint(self.mouse_local))
                    self._recorded_times.append(time.time())
        if self.mode == Mode.MAGNIFY and self.settings.zoom_idle_timeout > 0:
            if time.time() - self._last_interaction_time > self.settings.zoom_idle_timeout:
                self.return_to_normal()
                return
        if self._screenshot_flash > 0:
            self._screenshot_flash -= 1
        self._tick += 1
        self.update()

    def toggle_recording(self) -> None:
        if self.mode != Mode.NORMAL:
            self.return_to_normal()
        if not self.recording:
            self.recording = True
            self.recorded_points = []
            self._recorded_times = []
        else:
            self.recording = False
            if len(self.recorded_points) > 1:
                self.paths.append([QPoint(p) for p in self.recorded_points])
                t0 = self._recorded_times[0]
                self._path_times.append([t - t0 for t in self._recorded_times])
                self._path_anim_starts.append(time.time())
            self.recorded_points = []
            self._recorded_times = []
        self.update()

    def enter_magnify_mode(self) -> None:
        self.recording = False
        self.mode = Mode.MAGNIFY
        self.freeze_pixmap = self.screen.grabWindow(0)
        global_pos = QCursor.pos()
        if self.screen_geometry.contains(global_pos):
            self.mouse_local = global_pos - self.screen_geometry.topLeft()
        self.zoom = self.settings.magnify_start_zoom
        self._zoom_anchor = QPoint(self.mouse_local)
        self._last_interaction_time = time.time()
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.set_click_through(False)
        self.raise_()
        self.update()

    def return_to_normal(self) -> None:
        self.mode = Mode.NORMAL
        self.recording = False
        self.recorded_points = []
        self._recorded_times = []
        self.paths.clear()
        self._path_times.clear()
        self._path_anim_starts.clear()
        self._magnify_strokes.clear()
        self._magnify_active = None
        self._magnify_redo.clear()
        self.freeze_pixmap = None
        self.unsetCursor()
        self.set_click_through(True)
        self.update()

    def undo(self) -> None:
        if self.mode != Mode.MAGNIFY:
            return
        if self._magnify_active:
            self._magnify_active = None
        elif self._magnify_strokes:
            self._magnify_redo.append(self._magnify_strokes.pop())
        self.update()

    def redo(self) -> None:
        if self.mode != Mode.MAGNIFY:
            return
        if self._magnify_redo:
            self._magnify_strokes.append(self._magnify_redo.pop())
        self.update()

    def take_screenshot(self) -> None:
        take_screenshot_from_screen(self.screen)
        self._screenshot_flash = 60
        self.update()

    def set_draw_color_by_key(self, text: str) -> None:
        if self.mode != Mode.MAGNIFY:
            return
        color_map = {
            "r": QColor(255, 65, 65),
            "y": QColor(255, 220, 40),
            "g": QColor(58, 220, 120),
            "b": QColor(70, 150, 255),
            "k": QColor(0, 0, 0),
            "w": QColor(255, 255, 255),
        }
        key = text.lower()
        if key in color_map:
            self.draw_color = color_map[key]
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.mode == Mode.MAGNIFY:
            self._paint_magnifier(painter)
            if self._screenshot_flash > 0:
                self._paint_mode_badge(painter, "截圖已儲存!")
            else:
                self._paint_mode_badge(painter, f"MAGNIFY  {self.zoom:.1f}x")
            return

        self._paint_paths(painter)
        if self.recording:
            self._paint_mode_badge(painter, "REC")
        self._paint_pulse(painter)

    def _paint_paths(self, painter: QPainter) -> None:
        now = time.time()
        for i, path in enumerate(self.paths):
            if i < len(self._path_times) and i < len(self._path_anim_starts):
                elapsed = now - self._path_anim_starts[i]
                times = self._path_times[i]
                limit = bisect.bisect_right(times, elapsed)
                limit = max(2, min(limit, len(path)))
            else:
                limit = len(path)
            self._paint_path(painter, path[:limit], QColor(255, 30, 30), dashed=True)

    def _paint_path(self, painter: QPainter, path: list[QPoint], color: QColor, dashed: bool) -> None:
        if len(path) < 2:
            return
        pen = QPen(color, self.settings.line_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setDashPattern([7, 8])
        painter.setPen(pen)
        for p1, p2 in zip(path, path[1:]):
            painter.drawLine(p1, p2)

    def _paint_pulse(self, painter: QPainter) -> None:
        style = self.settings.pulse_style
        if style == "無 (None)":
            return
        speed = self.settings.pulse_speed
        pulse = (math.sin(self._tick * speed / 12.0) + 1.0) / 2.0
        size = self.settings.pulse_size
        c = self.settings.pulse_color
        mx, my = self.mouse_local.x(), self.mouse_local.y()

        if style == "雙圓圈 (Double)":
            radius = size + int(size * 0.6 * pulse)
            alpha = 215 - int(75 * pulse)
            painter.setPen(QPen(QColor(255, 255, 255, alpha), 3))
            painter.setBrush(QColor(c.red(), c.green(), c.blue(), 34))
            painter.drawEllipse(self.mouse_local, radius, radius)
            painter.setPen(QPen(c, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.mouse_local, max(4, radius // 4), max(4, radius // 4))
        elif style == "單圓圈 (Ring)":
            radius = size + int(size * 0.6 * pulse)
            ring = QColor(c.red(), c.green(), c.blue(), int(220 * (1 - pulse * 0.4)))
            painter.setPen(QPen(ring, 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.mouse_local, radius, radius)
        elif style == "十字線 (Cross)":
            arm = size + int(size * 0.3 * pulse)
            cross = QColor(c.red(), c.green(), c.blue(), int(220 * (1 - pulse * 0.3)))
            pen = QPen(cross, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(mx - arm, my, mx + arm, my)
            painter.drawLine(mx, my - arm, mx, my + arm)
        elif style == "點+圓 (Dot+Ring)":
            radius = size + int(size * 0.6 * pulse)
            ring = QColor(c.red(), c.green(), c.blue(), int(180 * (1 - pulse)))
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.mouse_local, radius, radius)
            dot = QColor(c.red(), c.green(), c.blue(), 220)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(self.mouse_local, 5, 5)

    def _paint_stroke(self, painter: QPainter, color: QColor, points: list[QPoint]) -> None:
        if len(points) < 2:
            return
        painter.setPen(QPen(color, self.settings.stroke_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for p1, p2 in zip(points, points[1:]):
            painter.drawLine(p1, p2)

    def _paint_magnifier(self, painter: QPainter) -> None:
        if not self.freeze_pixmap:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 230))
            return
        if self.settings.magnify_style == "跟隨鏡頭 (Lens)":
            self._paint_lens_magnifier(painter)
        else:
            self._paint_fullscreen_magnifier(painter)

    def _zoom_region(self) -> tuple[int, int, int, int]:
        src_w = max(1, int(self.width() / self.zoom))
        src_h = max(1, int(self.height() / self.zoom))
        src_x = max(0, min(self._zoom_anchor.x() - src_w // 2, self.width() - src_w))
        src_y = max(0, min(self._zoom_anchor.y() - src_h // 2, self.height() - src_h))
        return src_x, src_y, src_w, src_h

    def _screen_to_display(self, p: QPoint, src_x: int, src_y: int, src_w: int, src_h: int) -> QPoint:
        return QPoint(
            int((p.x() - src_x) * self.width() / src_w),
            int((p.y() - src_y) * self.height() / src_h),
        )

    def _paint_fullscreen_magnifier(self, painter: QPainter) -> None:
        src_x, src_y, src_w, src_h = self._zoom_region()
        cropped = self.freeze_pixmap.copy(QRect(src_x, src_y, src_w, src_h))
        painter.drawPixmap(self.rect(), cropped)
        cursor_d = self._screen_to_display(self.mouse_local, src_x, src_y, src_w, src_h)
        cursor_dx = max(0, min(cursor_d.x(), self.width() - 1))
        cursor_dy = max(0, min(cursor_d.y(), self.height() - 1))
        self._paint_magnify_strokes(painter, src_x, src_y, src_w, src_h)
        self._paint_crosshair_at(painter, cursor_dx, cursor_dy)

    def _paint_magnify_strokes(self, painter: QPainter, src_x: int, src_y: int, src_w: int, src_h: int) -> None:
        def td(pts: list[QPoint]) -> list[QPoint]:
            return [self._screen_to_display(p, src_x, src_y, src_w, src_h) for p in pts]
        for color, points in self._magnify_strokes:
            self._paint_stroke(painter, color, td(points))
        if self._magnify_active:
            self._paint_stroke(painter, self.draw_color, td(self._magnify_active))

    def _paint_lens_magnifier(self, painter: QPainter) -> None:
        painter.drawPixmap(self.rect(), self.freeze_pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 55))

        radius = self.settings.lens_radius
        cx = self.mouse_local.x()
        cy = self.mouse_local.y()

        src_w = max(1, int(radius * 2 / self.zoom))
        src_h = max(1, int(radius * 2 / self.zoom))
        src_x = max(0, min(cx - src_w // 2, self.freeze_pixmap.width() - src_w))
        src_y = max(0, min(cy - src_h // 2, self.freeze_pixmap.height() - src_h))
        cropped = self.freeze_pixmap.copy(QRect(src_x, src_y, src_w, src_h))

        clip = QPainterPath()
        clip.addEllipse(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.save()
        painter.setClipPath(clip)
        painter.drawPixmap(QRect(cx - radius, cy - radius, radius * 2, radius * 2), cropped)
        painter.restore()

        border_color = QColor(self.settings.crosshair_color)
        border_color.setAlpha(220)
        painter.setPen(QPen(border_color, 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPoint(cx, cy), radius, radius)

        self._paint_crosshair_at(painter, cx, cy)

    def _paint_crosshair(self, painter: QPainter) -> None:
        self._paint_crosshair_at(painter, self.width() // 2, self.height() // 2)

    def _paint_crosshair_at(self, painter: QPainter, cx: int, cy: int) -> None:
        style = self.settings.crosshair_style
        size = self.settings.crosshair_size
        color = QColor(self.settings.crosshair_color)
        color.setAlpha(self.settings.crosshair_alpha)
        pen = QPen(color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        if style == "十字線 (Crosshair)":
            painter.drawLine(cx - size, cy, cx + size, cy)
            painter.drawLine(cx, cy - size, cx, cy + size)
        elif style == "圓圈 (Circle)":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPoint(cx, cy), size, size)
        elif style == "圓圈+十字線":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPoint(cx, cy), size, size)
            painter.drawLine(cx - size, cy, cx + size, cy)
            painter.drawLine(cx, cy - size, cx, cy + size)
        elif style == "瞄準環 (Reticle)":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPoint(cx, cy), size, size)
            inner = max(4, size // 2)
            painter.drawEllipse(QPoint(cx, cy), inner, inner)
            painter.drawLine(cx - size, cy, cx + size, cy)
            painter.drawLine(cx, cy - size, cx, cy + size)
            tick_len = max(2, size // 5)
            for angle in range(0, 360, 30):
                rad = math.radians(angle)
                r1 = size - tick_len
                painter.drawLine(
                    cx + int(r1 * math.cos(rad)), cy + int(r1 * math.sin(rad)),
                    cx + int(size * math.cos(rad)), cy + int(size * math.sin(rad)),
                )
        elif style == "點 (Dot)":
            painter.setBrush(color)
            painter.drawEllipse(QPoint(cx, cy), 3, 3)

    def _paint_mode_badge(self, painter: QPainter, text: str) -> None:
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        badge = QRect(24, 24, 190, 38)
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
        painter.setBrush(QColor(15, 18, 24, 220))
        painter.drawRoundedRect(badge, 10, 10)
        painter.setPen(QColor(248, 250, 252))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

    def _color_name(self) -> str:
        colors = {
            QColor(255, 65, 65).rgb(): "RED",
            QColor(255, 220, 40).rgb(): "YELLOW",
            QColor(58, 220, 120).rgb(): "GREEN",
            QColor(70, 150, 255).rgb(): "BLUE",
            QColor(0, 0, 0).rgb(): "BLACK",
            QColor(255, 255, 255).rgb(): "WHITE",
        }
        return colors.get(self.draw_color.rgb(), "COLOR")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.mode == Mode.MAGNIFY:
            self._magnify_active = [QPoint(self.mouse_local)]
            self._magnify_redo.clear()
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.mouse_local = event.position().toPoint()
        if self.mode == Mode.MAGNIFY:
            self._last_interaction_time = time.time()
            if self._magnify_active is not None:
                point = QPoint(self.mouse_local)
                if manhattan(self._magnify_active[-1], point) >= 2:
                    self._magnify_active.append(point)
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.mode == Mode.MAGNIFY and self._magnify_active is not None:
            if len(self._magnify_active) > 1:
                self._magnify_strokes.append((QColor(self.draw_color), self._magnify_active))
            self._magnify_active = None
            self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.mode == Mode.MAGNIFY:
            direction = 1 if event.angleDelta().y() > 0 else -1
            self.zoom = max(1.0, min(6.0, self.zoom + direction * self.settings.zoom_step))
            self._zoom_anchor = QPoint(self.mouse_local)
            self._last_interaction_time = time.time()
            self.update()


def manhattan(a: QPoint, b: QPoint) -> int:
    return abs(a.x() - b.x()) + abs(a.y() - b.y())


class ControlWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Stream Mouse Overlay")
        self.setMinimumWidth(520)
        self.settings = AppSettings()
        self.overlay: OverlayWindow | None = None
        self.hud: KeyboardHud | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.keyboard_hook = KeyboardHook()
        self.keyboard_hook.key_pressed.connect(self._on_key_pressed)
        self.keyboard_hook.start()
        self.obs_poller = ObsStatusPoller()
        self.obs_poller.status_changed.connect(self._on_obs_status)
        self.screens = [
            ScreenInfo(i, screen.name(), screen.geometry())
            for i, screen in enumerate(QApplication.screens())
        ]

        self.title = QLabel("Select the monitor for the overlay")
        self.combo = QComboBox()
        for info in self.screens:
            self.combo.addItem(info.label)
        self.start_button = QPushButton("Start overlay")
        self.start_button.clicked.connect(self.start_overlay)
        self.stop_button = QPushButton("Stop overlay")
        self.stop_button.clicked.connect(self.stop_overlay)
        self.stop_button.setEnabled(False)
        self.settings_button = QPushButton("設定")
        self.settings_button.clicked.connect(self.open_settings)
        self.screenshot_folder_button = QPushButton("截圖資料夾")
        self.screenshot_folder_button.clicked.connect(self.open_screenshot_folder)
        self.status = QLabel("Shortcuts: Ctrl+F1 record, Ctrl+F2 draw, Ctrl+F3 magnify/screenshot, Esc reset.")
        self.status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.combo)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.settings_button)
        btn_row.addWidget(self.screenshot_folder_button)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addWidget(self.status)

    def start_overlay(self) -> None:
        index = self.combo.currentIndex()
        if index < 0:
            return
        screen = QApplication.screens()[index]
        info = self.screens[index]
        self.overlay = OverlayWindow(screen, info, self.settings)
        self.hud = KeyboardHud(info.geometry, self.settings)
        self.overlay.show()
        self.hud.show()
        self.obs_poller.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.combo.setEnabled(False)
        self.status.setText(f"Running on {info.label}")

    def stop_overlay(self) -> None:
        self.obs_poller.stop()
        if self.overlay:
            self.overlay.close()
            self.overlay = None
        if self.hud:
            self.hud.close()
            self.hud = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.combo.setEnabled(True)
        self.status.setText("Overlay stopped.")

    def open_screenshot_folder(self) -> None:
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        if os.path.isdir(folder):
            os.startfile(folder)
        else:
            self.status.setText("截圖資料夾不存在，請先擷取一張圖。")

    def open_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.settings, self)
            self.settings_dialog.hotkey_listening.connect(self._on_hotkey_listening)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _on_hotkey_listening(self, action: str) -> None:
        pass

    _MODIFIER_VKS = {
        win32con.VK_CONTROL, win32con.VK_LCONTROL, win32con.VK_RCONTROL,
        win32con.VK_SHIFT, win32con.VK_LSHIFT, win32con.VK_RSHIFT,
        win32con.VK_MENU, win32con.VK_LMENU, win32con.VK_RMENU,
    }

    def _on_key_pressed(self, vk: int, text: str, ctrl: bool, shift: bool, alt: bool) -> None:
        if self.hud:
            self.hud.add_key(text, ctrl, shift, alt)

        if self.settings_dialog and self.settings_dialog.is_listening:
            if vk not in self._MODIFIER_VKS:
                self.settings_dialog.assign_hotkey(vk, ctrl, shift, alt)
            return

        if not self.overlay:
            return

        action = self.settings.match_hotkey(vk, ctrl, shift, alt)
        if action == "escape":
            self.overlay.return_to_normal()
        elif action == "recording":
            self.overlay.toggle_recording()
        elif action == "magnify":
            if self.overlay.mode == Mode.MAGNIFY:
                self.overlay.take_screenshot()
            else:
                self.overlay.enter_magnify_mode()
        elif action == "undo":
            self.overlay.undo()
        elif action == "redo":
            self.overlay.redo()
        else:
            self.overlay.set_draw_color_by_key(text)

    def _on_obs_status(self, live: bool, scene: str, connected: bool) -> None:
        if self.hud:
            self.hud.set_obs_status(live, scene, connected)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.keyboard_hook.stop()
        self.obs_poller.stop()
        if self.overlay:
            self.overlay.close()
            self.overlay = None
        if self.hud:
            self.hud.close()
            self.hud = None
        self.settings.save()
        super().closeEvent(event)


def set_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            user32.SetProcessDPIAware()


def main() -> int:
    set_dpi_awareness()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    window = ControlWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
