from __future__ import annotations

import ctypes
import ctypes.wintypes
import base64
import hashlib
import json
import bisect
import math
import os
import queue
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
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
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
                if vk in self._pressed:
                    return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)
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
        win32con.VK_SPACE: "Space",
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
    status_changed = Signal(bool, str, bool, float)
    INPUT_VOLUME_METERS = 1 << 16

    def __init__(self, settings: "AppSettings") -> None:
        super().__init__()
        self._settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._request_id = 0
        self._mic_input_name = ""
        self._mic_level = 0.0
        self._live = False
        self._scene = "-"

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
                self._read_obs_status()
            except Exception:
                self._mic_input_name = ""
                self._mic_level = 0.0
                self.status_changed.emit(False, "-", False, 0.0)
            self._stop.wait(1.0)

    def _read_obs_status(self) -> None:
        ws = websocket.create_connection("ws://127.0.0.1:4455", timeout=1.5)
        try:
            hello = json.loads(ws.recv())
            hello_data = hello.get("d", {})
            identify = {"rpcVersion": 1, "eventSubscriptions": self.INPUT_VOLUME_METERS}
            auth = hello_data.get("authentication")
            password = self._settings.obs_password or os.environ.get("OBS_WEBSOCKET_PASSWORD", "")
            if auth and password:
                identify["authentication"] = obs_auth(password, auth["salt"], auth["challenge"])
            ws.send(json.dumps({"op": 1, "d": identify}))
            response = json.loads(ws.recv())
            if response.get("op") != 2:
                raise RuntimeError("OBS websocket identification failed")

            next_status = 0.0
            ws.settimeout(0.5)
            while not self._stop.is_set():
                now = time.time()
                if now >= next_status:
                    live, scene = self._read_stream_scene(ws)
                    self._live = live
                    self._scene = scene
                    self._choose_mic_input(ws)
                    self.status_changed.emit(live, scene, True, self._mic_level)
                    next_status = now + 3.0
                try:
                    message = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                self._handle_obs_event(message)
        finally:
            ws.close()

    def _read_stream_scene(self, ws) -> tuple[bool, str]:
        stream = self._request(ws, "GetStreamStatus")
        scene = self._request(ws, "GetCurrentProgramScene")
        return bool(stream.get("outputActive")), str(scene.get("currentProgramSceneName", "-"))

    def _choose_mic_input(self, ws) -> None:
        configured = self._settings.obs_mic_input.strip()
        if configured:
            self._mic_input_name = configured
            return
        inputs = self._request(ws, "GetInputList").get("inputs", [])
        self._mic_input_name = self._guess_mic_input(inputs)

    def _guess_mic_input(self, inputs: list[dict]) -> str:
        preferred_words = ("mic", "microphone", "麥", "麦")
        capture_kinds = ("wasapi_input_capture", "pulse_input_capture", "coreaudio_input_capture")
        for item in inputs:
            name = str(item.get("inputName", ""))
            if any(word in name.lower() for word in preferred_words):
                return name
        for item in inputs:
            kind = str(item.get("inputKind", "")).lower()
            if any(capture_kind in kind for capture_kind in capture_kinds):
                return str(item.get("inputName", ""))
        return ""

    def _handle_obs_event(self, message: dict) -> None:
        if message.get("op") != 5:
            return
        data = message.get("d", {})
        if data.get("eventType") != "InputVolumeMeters":
            return
        event_data = data.get("eventData", {})
        inputs = event_data.get("inputs", [])
        if not self._mic_input_name:
            self._mic_input_name = self._guess_mic_input(inputs)
        item = self._select_meter_input(inputs)
        if item:
            self._mic_input_name = str(item.get("inputName", self._mic_input_name))
            self._mic_level = self._level_from_meter(item.get("inputLevelsMul", []))
            self.status_changed.emit(self._live, self._scene, True, self._mic_level)

    def _select_meter_input(self, inputs: list[dict]) -> dict | None:
        configured = self._settings.obs_mic_input.strip()
        for item in inputs:
            if configured and item.get("inputName") == configured:
                return item
        for item in inputs:
            if self._mic_input_name and item.get("inputName") == self._mic_input_name:
                return item
        guessed = self._guess_mic_input(inputs)
        for item in inputs:
            if guessed and item.get("inputName") == guessed:
                return item
        if inputs:
            return max(inputs, key=lambda item: self._raw_level_from_meter(item.get("inputLevelsMul", [])))
        return None

    def _level_from_meter(self, levels) -> float:
        raw = self._raw_level_from_meter(levels)
        return max(0.0, min(math.pow(raw, 0.45), 1.0))

    def _raw_level_from_meter(self, levels) -> float:
        values: list[float] = []

        def collect(value) -> None:
            if isinstance(value, (int, float)):
                values.append(float(value))
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(levels)
        if not values:
            return 0.0
        return max(0.0, min(max(values), 1.0))

    def _request(self, ws, request_type: str) -> dict:
        self._request_id += 1
        request_id = str(self._request_id)
        ws.send(json.dumps({"op": 6, "d": {"requestType": request_type, "requestId": request_id}}))
        deadline = time.time() + 2.5
        while True:
            if time.time() > deadline:
                raise RuntimeError(f"OBS request timed out: {request_type}")
            try:
                message = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if message.get("op") == 5:
                self._handle_obs_event(message)
                continue
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
        self._mic_level = 0.0
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

    def set_obs_status(self, live: bool, scene: str, connected: bool, mic_level: float = 0.0) -> None:
        self._live = live
        self._scene = scene or "-"
        self._obs_connected = connected
        self._mic_level = max(0.0, min(float(mic_level), 1.0))
        self.update()

    def add_key(self, text: str, ctrl: bool, shift: bool, alt: bool) -> None:
        now = time.time()
        if text in {"Ctrl", "Shift", "Alt"}:
            self._tokens.append((text, True))
            self._token_times.append(now)
        elif text == "Backspace":
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
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        metrics = painter.fontMetrics()
        available_width = self.width() - 22
        bar_width = 58 if self._obs_connected else 0
        if available_width < 150:
            bar_width = 36 if self._obs_connected and available_width >= 110 else 0
        bar_height = 8
        status_width = metrics.horizontalAdvance(status)
        scene_width = metrics.horizontalAdvance(scene)
        gap = 9
        width = status_width + scene_width + 18
        if bar_width:
            width += bar_width + gap * 2
        width = min(width, self.width() - 22)
        rect = QRect(self.width() - width - 10, self.height() - 27, width, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(220, 38, 38, 132) if self._live else QColor(18, 22, 30, 88))
        painter.drawRoundedRect(rect, 6, 6)

        x = rect.x() + 8
        center_y = rect.y() + rect.height() // 2
        painter.setPen(QColor(255, 255, 255, 228))
        painter.drawText(QRect(x, rect.y(), status_width, rect.height()), Qt.AlignmentFlag.AlignVCenter, status)
        x += status_width + gap

        if bar_width:
            bar_rect = QRect(x, center_y - bar_height // 2, bar_width, bar_height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 56))
            painter.drawRoundedRect(bar_rect, 4, 4)
            fill_width = max(2, int(bar_width * self._mic_level)) if self._mic_level > 0 else 0
            if fill_width:
                fill_color = QColor(34, 197, 94, 218)
                if self._mic_level > 0.82:
                    fill_color = QColor(250, 204, 21, 230)
                if self._mic_level > 0.95:
                    fill_color = QColor(239, 68, 68, 235)
                painter.setBrush(fill_color)
                painter.drawRoundedRect(QRect(bar_rect.x(), bar_rect.y(), fill_width, bar_rect.height()), 4, 4)
            x += bar_width + gap

        scene_width_left = rect.right() - x - 7
        if scene_width_left > 0:
            scene_rect = QRect(x, rect.y(), scene_width_left, rect.height())
            painter.setPen(QColor(255, 255, 255, 218))
            painter.drawText(scene_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, scene)

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
        self._hud_height = 118
        self._hud_bg_alpha = 82
        self._hud_font_family = "Cascadia Mono"
        self._hud_font_size = 21
        self._hud_text_color = QColor(252, 254, 255)
        self._hud_text_alpha = 250
        self._text_disappear_secs = 0
        self._obs_password = ""
        self._obs_mic_input = ""
        self._record_bg = True
        self._record_bg_interval = 0.5
        self._zoom_step = 0.25
        self._zoom_idle_timeout = 0
        self._crosshair_style = "十字線 (Crosshair)"
        self._crosshair_size = 14
        self._crosshair_color = QColor(255, 255, 255)
        self._crosshair_alpha = 150
        self._magnify_style = "全螢幕 (Fullscreen)"
        self._magnify_start_zoom = 1.0
        self._lens_radius = 150
        self._draw_mod_line = "Shift"
        self._draw_mod_circle = "Alt"
        self._draw_mod_rect = "Ctrl"
        self._pulse_style = "雙圓圈 (Double)"
        self._pulse_size = 20
        self._pulse_speed = 1.0
        self._pulse_scale = 0.6
        self._pulse_color = QColor(30, 120, 255)
        self._pulse_color2 = QColor(255, 255, 255)
        self._trail_icon = "飛機 (Plane)"
        self._trail_length = 200
        self._trail_width = 3
        self._trail_color = QColor(255, 80, 40)
        self._trail_icon_size = 16
        self._waypoint_pause = 0.5
        self._waypoint_dot_size = 8
        self._waypoint_dot_color = QColor(255, 220, 0)
        self._waypoint_dot_alpha = 80
        self._waypoint_label_color = QColor(0, 0, 0)
        self._waypoint_border_width = 1
        self._waypoint_border_color = QColor(0, 0, 0)
        self._suppress_emit = False
        self._hotkeys = {
            "escape": {"vk": 27, "ctrl": False, "shift": False, "alt": False},
            "recording": {"vk": 112, "ctrl": True, "shift": False, "alt": False},
            "waypoint": {"vk": 113, "ctrl": True, "shift": False, "alt": False},
            "magnify": {"vk": 114, "ctrl": True, "shift": False, "alt": False},
            "replay": {"vk": 116, "ctrl": True, "shift": False, "alt": False},
            "undo": {"vk": 90, "ctrl": True, "shift": False, "alt": False},
            "redo": {"vk": 90, "ctrl": True, "shift": True, "alt": False},
        }
        self.load()

    def _emit(self) -> None:
        if self._suppress_emit:
            return
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
    def obs_password(self) -> str:
        return self._obs_password

    @obs_password.setter
    def obs_password(self, v: str) -> None:
        if self._obs_password != v:
            self._obs_password = v
            self._emit()

    @property
    def obs_mic_input(self) -> str:
        return self._obs_mic_input

    @obs_mic_input.setter
    def obs_mic_input(self, v: str) -> None:
        if self._obs_mic_input != v:
            self._obs_mic_input = v
            self._emit()

    @property
    def record_bg(self) -> bool:
        return self._record_bg

    @record_bg.setter
    def record_bg(self, v: bool) -> None:
        if self._record_bg != v:
            self._record_bg = v
            self._emit()

    @property
    def record_bg_interval(self) -> float:
        return self._record_bg_interval

    @record_bg_interval.setter
    def record_bg_interval(self, v: float) -> None:
        if self._record_bg_interval != v:
            self._record_bg_interval = v
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
    def draw_mod_line(self) -> str:
        return self._draw_mod_line

    @draw_mod_line.setter
    def draw_mod_line(self, v: str) -> None:
        if self._draw_mod_line != v:
            self._draw_mod_line = v
            self._emit()

    @property
    def draw_mod_circle(self) -> str:
        return self._draw_mod_circle

    @draw_mod_circle.setter
    def draw_mod_circle(self, v: str) -> None:
        if self._draw_mod_circle != v:
            self._draw_mod_circle = v
            self._emit()

    @property
    def draw_mod_rect(self) -> str:
        return self._draw_mod_rect

    @draw_mod_rect.setter
    def draw_mod_rect(self, v: str) -> None:
        if self._draw_mod_rect != v:
            self._draw_mod_rect = v
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
    def pulse_scale(self) -> float:
        return self._pulse_scale

    @pulse_scale.setter
    def pulse_scale(self, v: float) -> None:
        if self._pulse_scale != v:
            self._pulse_scale = v
            self._emit()

    @property
    def pulse_color(self) -> QColor:
        return self._pulse_color

    @pulse_color.setter
    def pulse_color(self, v: QColor) -> None:
        if self._pulse_color != v:
            self._pulse_color = v
            self._emit()

    @property
    def pulse_color2(self) -> QColor:
        return self._pulse_color2

    @pulse_color2.setter
    def pulse_color2(self, v: QColor) -> None:
        if self._pulse_color2 != v:
            self._pulse_color2 = v
            self._emit()

    @property
    def trail_icon(self) -> str:
        return self._trail_icon

    @trail_icon.setter
    def trail_icon(self, v: str) -> None:
        if self._trail_icon != v:
            self._trail_icon = v
            self._emit()

    @property
    def trail_length(self) -> int:
        return self._trail_length

    @trail_length.setter
    def trail_length(self, v: int) -> None:
        if self._trail_length != v:
            self._trail_length = v
            self._emit()

    @property
    def trail_width(self) -> int:
        return self._trail_width

    @trail_width.setter
    def trail_width(self, v: int) -> None:
        if self._trail_width != v:
            self._trail_width = v
            self._emit()

    @property
    def trail_color(self) -> QColor:
        return self._trail_color

    @trail_color.setter
    def trail_color(self, v: QColor) -> None:
        if self._trail_color != v:
            self._trail_color = v
            self._emit()

    @property
    def trail_icon_size(self) -> int:
        return self._trail_icon_size

    @trail_icon_size.setter
    def trail_icon_size(self, v: int) -> None:
        if self._trail_icon_size != v:
            self._trail_icon_size = v
            self._emit()

    @property
    def waypoint_pause(self) -> float:
        return self._waypoint_pause

    @waypoint_pause.setter
    def waypoint_pause(self, v: float) -> None:
        if self._waypoint_pause != v:
            self._waypoint_pause = v
            self._emit()

    @property
    def waypoint_dot_size(self) -> int:
        return self._waypoint_dot_size

    @waypoint_dot_size.setter
    def waypoint_dot_size(self, v: int) -> None:
        if self._waypoint_dot_size != v:
            self._waypoint_dot_size = v
            self._emit()

    @property
    def waypoint_dot_color(self) -> QColor:
        return self._waypoint_dot_color

    @waypoint_dot_color.setter
    def waypoint_dot_color(self, v: QColor) -> None:
        if self._waypoint_dot_color != v:
            self._waypoint_dot_color = v
            self._emit()

    @property
    def waypoint_dot_alpha(self) -> int:
        return self._waypoint_dot_alpha

    @waypoint_dot_alpha.setter
    def waypoint_dot_alpha(self, v: int) -> None:
        if self._waypoint_dot_alpha != v:
            self._waypoint_dot_alpha = v
            self._emit()

    @property
    def waypoint_label_color(self) -> QColor:
        return self._waypoint_label_color

    @waypoint_label_color.setter
    def waypoint_label_color(self, v: QColor) -> None:
        if self._waypoint_label_color != v:
            self._waypoint_label_color = v
            self._emit()

    @property
    def waypoint_border_width(self) -> int:
        return self._waypoint_border_width

    @waypoint_border_width.setter
    def waypoint_border_width(self, v: int) -> None:
        if self._waypoint_border_width != v:
            self._waypoint_border_width = v
            self._emit()

    @property
    def waypoint_border_color(self) -> QColor:
        return self._waypoint_border_color

    @waypoint_border_color.setter
    def waypoint_border_color(self, v: QColor) -> None:
        if self._waypoint_border_color != v:
            self._waypoint_border_color = v
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
        s.setValue("obs_password", self._obs_password)
        s.setValue("obs_mic_input", self._obs_mic_input)
        s.setValue("record_bg", self._record_bg)
        s.setValue("record_bg_interval", self._record_bg_interval)
        s.setValue("zoom_step", self._zoom_step)
        s.setValue("zoom_idle_timeout", self._zoom_idle_timeout)
        s.setValue("crosshair_style", self._crosshair_style)
        s.setValue("crosshair_size", self._crosshair_size)
        s.setValue("crosshair_color", self._crosshair_color.rgba())
        s.setValue("crosshair_alpha", self._crosshair_alpha)
        s.setValue("magnify_style", self._magnify_style)
        s.setValue("magnify_start_zoom", self._magnify_start_zoom)
        s.setValue("lens_radius", self._lens_radius)
        s.setValue("draw_mod_line", self._draw_mod_line)
        s.setValue("draw_mod_circle", self._draw_mod_circle)
        s.setValue("draw_mod_rect", self._draw_mod_rect)
        s.setValue("pulse_style", self._pulse_style)
        s.setValue("pulse_size", self._pulse_size)
        s.setValue("pulse_speed", self._pulse_speed)
        s.setValue("pulse_scale", self._pulse_scale)
        s.setValue("pulse_color", self._pulse_color.rgba())
        s.setValue("pulse_color2", self._pulse_color2.rgba())
        s.setValue("trail_icon", self._trail_icon)
        s.setValue("trail_length", self._trail_length)
        s.setValue("trail_width", self._trail_width)
        s.setValue("trail_color", self._trail_color.rgba())
        s.setValue("trail_icon_size", self._trail_icon_size)
        s.setValue("waypoint_pause", self._waypoint_pause)
        s.setValue("waypoint_dot_size", self._waypoint_dot_size)
        s.setValue("waypoint_dot_color", self._waypoint_dot_color.rgba())
        s.setValue("waypoint_dot_alpha", self._waypoint_dot_alpha)
        s.setValue("waypoint_label_color", self._waypoint_label_color.rgba())
        s.setValue("waypoint_border_width", self._waypoint_border_width)
        s.setValue("waypoint_border_color", self._waypoint_border_color.rgba())
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
        self._obs_password = str(s.value("obs_password", self._obs_password))
        self._obs_mic_input = str(s.value("obs_mic_input", self._obs_mic_input))
        self._record_bg = s.value("record_bg", self._record_bg, type=bool)
        self._record_bg_interval = float(s.value("record_bg_interval", self._record_bg_interval))
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
        self._draw_mod_line = str(s.value("draw_mod_line", self._draw_mod_line))
        self._draw_mod_circle = str(s.value("draw_mod_circle", self._draw_mod_circle))
        self._draw_mod_rect = str(s.value("draw_mod_rect", self._draw_mod_rect))
        self._pulse_style = str(s.value("pulse_style", self._pulse_style))
        self._pulse_size = int(s.value("pulse_size", self._pulse_size))
        self._pulse_speed = float(s.value("pulse_speed", self._pulse_speed))
        self._pulse_scale = float(s.value("pulse_scale", self._pulse_scale))
        rgba_pulse = s.value("pulse_color")
        if rgba_pulse is not None:
            self._pulse_color = QColor.fromRgba(int(rgba_pulse))
        rgba_pulse2 = s.value("pulse_color2")
        if rgba_pulse2 is not None:
            self._pulse_color2 = QColor.fromRgba(int(rgba_pulse2))
        self._trail_icon = str(s.value("trail_icon", self._trail_icon))
        self._trail_length = int(s.value("trail_length", self._trail_length))
        self._trail_width = int(s.value("trail_width", self._trail_width))
        rgba_trail = s.value("trail_color")
        if rgba_trail is not None:
            self._trail_color = QColor.fromRgba(int(rgba_trail))
        self._trail_icon_size = int(s.value("trail_icon_size", self._trail_icon_size))
        self._waypoint_pause = float(s.value("waypoint_pause", self._waypoint_pause))
        self._waypoint_dot_size = int(s.value("waypoint_dot_size", self._waypoint_dot_size))
        rgba_wp = s.value("waypoint_dot_color")
        if rgba_wp is not None:
            self._waypoint_dot_color = QColor.fromRgba(int(rgba_wp))
        self._waypoint_dot_alpha = int(s.value("waypoint_dot_alpha", self._waypoint_dot_alpha))
        rgba_wl = s.value("waypoint_label_color")
        if rgba_wl is not None:
            self._waypoint_label_color = QColor.fromRgba(int(rgba_wl))
        self._waypoint_border_width = int(s.value("waypoint_border_width", self._waypoint_border_width))
        rgba_wb = s.value("waypoint_border_color")
        if rgba_wb is not None:
            self._waypoint_border_color = QColor.fromRgba(int(rgba_wb))
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

TRAIL_ICONS = [
    "飛機 (Plane)",
    "箭頭 (Arrow)",
    "火箭 (Rocket)",
    "無圖示 (None)",
]


class SettingsDialog(QDialog):
    hotkey_listening = Signal(str)

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("設定")
        self.setMinimumWidth(480)
        self._listening_action: str | None = None
        self._listening_button: QPushButton | None = None

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        # ── Tab 1: 一般 ──────────────────────────────────────────────
        tab_general = QWidget()
        tg_layout = QVBoxLayout(tab_general)

        hud_group = QGroupBox("文字區域 (HUD)")
        hud_form = QFormLayout(hud_group)

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
        hud_form.addRow("區域:", size_row)

        self.hud_bg_slider = QSlider(Qt.Orientation.Horizontal)
        self.hud_bg_slider.setRange(0, 100)
        self.hud_bg_slider.setValue(settings.hud_bg_alpha * 100 // 255)
        self.hud_bg_slider.valueChanged.connect(lambda v: setattr(settings, "hud_bg_alpha", v * 255 // 100))
        hud_form.addRow("背景透明度:", self.hud_bg_slider)

        self.font_combo = QComboBox()
        self.font_combo.setEditable(True)
        import platform
        self.font_combo.addItems(
            ["Cascadia Mono", "Consolas", "Courier New", "Microsoft JhengHei", "Segoe UI", "Arial"]
            if platform.system() == "Windows" else ["monospace", "sans-serif", "serif"]
        )
        idx = self.font_combo.findText(settings.hud_font_family)
        if idx >= 0:
            self.font_combo.setCurrentIndex(idx)
        else:
            self.font_combo.setCurrentText(settings.hud_font_family)
        self.font_combo.currentTextChanged.connect(lambda v: setattr(settings, "hud_font_family", v))
        hud_form.addRow("字型:", self.font_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 120)
        self.font_size_spin.setValue(settings.hud_font_size)
        self.font_size_spin.valueChanged.connect(lambda v: setattr(settings, "hud_font_size", v))
        hud_form.addRow("字體大小:", self.font_size_spin)

        text_color_row = QHBoxLayout()
        self.text_color_btn = QPushButton()
        self._update_color_button(self.text_color_btn, settings.hud_text_color)
        self.text_color_btn.clicked.connect(self._pick_text_color)
        text_color_row.addWidget(self.text_color_btn)
        self.text_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.text_alpha_slider.setRange(0, 100)
        self.text_alpha_slider.setValue(settings.hud_text_alpha * 100 // 255)
        self.text_alpha_slider.valueChanged.connect(lambda v: setattr(settings, "hud_text_alpha", v * 255 // 100))
        text_color_row.addWidget(self.text_alpha_slider)
        hud_form.addRow("文字顏色/透明度:", text_color_row)

        self.text_disappear_spin = QSpinBox()
        self.text_disappear_spin.setRange(0, 999)
        self.text_disappear_spin.setSuffix(" 秒")
        self.text_disappear_spin.setSpecialValueText("永不")
        self.text_disappear_spin.setValue(settings.text_disappear_secs)
        self.text_disappear_spin.valueChanged.connect(lambda v: setattr(settings, "text_disappear_secs", v))
        hud_form.addRow("文字自動消失:", self.text_disappear_spin)

        tg_layout.addWidget(hud_group)

        obs_group = QGroupBox("OBS WebSocket")
        obs_form = QFormLayout(obs_group)

        obs_hint = QLabel("OBS → 工具 → WebSocket 伺服器設定\n若沒有設密碼，留空即可")
        obs_hint.setStyleSheet("color: #aaa; font-size: 11px;")
        obs_form.addRow(obs_hint)

        self.obs_password_edit = QLineEdit()
        self.obs_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_password_edit.setPlaceholderText("留空 = 無密碼")
        self.obs_password_edit.setText(settings.obs_password)
        self.obs_password_edit.textChanged.connect(lambda v: setattr(settings, "obs_password", v))
        obs_form.addRow("WebSocket 密碼:", self.obs_password_edit)

        self.obs_mic_input_edit = QLineEdit()
        self.obs_mic_input_edit.setPlaceholderText("留空 = 自動尋找 Mic/Aux 或麥克風")
        self.obs_mic_input_edit.setText(settings.obs_mic_input)
        self.obs_mic_input_edit.textChanged.connect(lambda v: setattr(settings, "obs_mic_input", v))
        obs_form.addRow("麥克風來源:", self.obs_mic_input_edit)

        tg_layout.addWidget(obs_group)
        tg_layout.addStretch()
        tabs.addTab(tab_general, "一般")

        # ── Tab 2: 放大鏡 ────────────────────────────────────────────
        tab_mag = QWidget()
        tm_layout = QVBoxLayout(tab_mag)

        mag_group = QGroupBox("放大鏡")
        mag_form = QFormLayout(mag_group)

        self.magnify_style_combo = QComboBox()
        self.magnify_style_combo.addItems(MAGNIFY_STYLES)
        idx_ms = self.magnify_style_combo.findText(settings.magnify_style)
        if idx_ms >= 0:
            self.magnify_style_combo.setCurrentIndex(idx_ms)
        self.magnify_style_combo.currentTextChanged.connect(lambda v: setattr(settings, "magnify_style", v))
        mag_form.addRow("樣式:", self.magnify_style_combo)

        self.magnify_start_zoom_spin = QDoubleSpinBox()
        self.magnify_start_zoom_spin.setRange(1.0, 6.0)
        self.magnify_start_zoom_spin.setSingleStep(0.25)
        self.magnify_start_zoom_spin.setValue(settings.magnify_start_zoom)
        self.magnify_start_zoom_spin.valueChanged.connect(lambda v: setattr(settings, "magnify_start_zoom", v))
        mag_form.addRow("進入初始縮放:", self.magnify_start_zoom_spin)

        self.lens_radius_spin = QSpinBox()
        self.lens_radius_spin.setRange(50, 600)
        self.lens_radius_spin.setSuffix(" px")
        self.lens_radius_spin.setValue(settings.lens_radius)
        self.lens_radius_spin.valueChanged.connect(lambda v: setattr(settings, "lens_radius", v))
        mag_form.addRow("鏡頭半徑:", self.lens_radius_spin)

        self.zoom_step_spin = QDoubleSpinBox()
        self.zoom_step_spin.setRange(0.05, 2.0)
        self.zoom_step_spin.setSingleStep(0.05)
        self.zoom_step_spin.setValue(settings.zoom_step)
        self.zoom_step_spin.valueChanged.connect(lambda v: setattr(settings, "zoom_step", v))
        mag_form.addRow("縮放步進:", self.zoom_step_spin)

        self.zoom_idle_spin = QSpinBox()
        self.zoom_idle_spin.setRange(0, 300)
        self.zoom_idle_spin.setSuffix(" 秒")
        self.zoom_idle_spin.setSpecialValueText("永不")
        self.zoom_idle_spin.setValue(settings.zoom_idle_timeout)
        self.zoom_idle_spin.valueChanged.connect(lambda v: setattr(settings, "zoom_idle_timeout", v))
        mag_form.addRow("閒置自動退出:", self.zoom_idle_spin)

        sep = QLabel("繪製快捷鍵")
        sep.setStyleSheet("color: #aaa; font-size: 11px; padding-top: 6px")
        mag_form.addRow(sep)

        MOD_KEYS = ["Shift", "Ctrl", "Alt"]
        self.draw_mod_line_combo = QComboBox()
        self.draw_mod_line_combo.addItems(MOD_KEYS)
        self.draw_mod_line_combo.setCurrentText(settings.draw_mod_line)
        self.draw_mod_line_combo.currentTextChanged.connect(lambda v: setattr(settings, "draw_mod_line", v))
        mag_form.addRow("直線:", self.draw_mod_line_combo)

        self.draw_mod_circle_combo = QComboBox()
        self.draw_mod_circle_combo.addItems(MOD_KEYS)
        self.draw_mod_circle_combo.setCurrentText(settings.draw_mod_circle)
        self.draw_mod_circle_combo.currentTextChanged.connect(lambda v: setattr(settings, "draw_mod_circle", v))
        mag_form.addRow("圓形:", self.draw_mod_circle_combo)

        self.draw_mod_rect_combo = QComboBox()
        self.draw_mod_rect_combo.addItems(MOD_KEYS)
        self.draw_mod_rect_combo.setCurrentText(settings.draw_mod_rect)
        self.draw_mod_rect_combo.currentTextChanged.connect(lambda v: setattr(settings, "draw_mod_rect", v))
        mag_form.addRow("矩形:", self.draw_mod_rect_combo)

        tm_layout.addWidget(mag_group)

        xhair_group = QGroupBox("準心 (Crosshair)")
        xhair_form = QFormLayout(xhair_group)

        self.crosshair_style_combo = QComboBox()
        self.crosshair_style_combo.addItems(CROSSHAIR_STYLES)
        idx2 = self.crosshair_style_combo.findText(settings.crosshair_style)
        if idx2 >= 0:
            self.crosshair_style_combo.setCurrentIndex(idx2)
        self.crosshair_style_combo.currentTextChanged.connect(lambda v: setattr(settings, "crosshair_style", v))
        xhair_form.addRow("樣式:", self.crosshair_style_combo)

        self.crosshair_size_spin = QSpinBox()
        self.crosshair_size_spin.setRange(2, 100)
        self.crosshair_size_spin.setValue(settings.crosshair_size)
        self.crosshair_size_spin.valueChanged.connect(lambda v: setattr(settings, "crosshair_size", v))
        xhair_form.addRow("大小:", self.crosshair_size_spin)

        xhair_color_row = QHBoxLayout()
        self.crosshair_color_btn = QPushButton()
        self._update_color_button(self.crosshair_color_btn, settings.crosshair_color)
        self.crosshair_color_btn.clicked.connect(self._pick_crosshair_color)
        xhair_color_row.addWidget(self.crosshair_color_btn)
        self.crosshair_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.crosshair_alpha_slider.setRange(0, 100)
        self.crosshair_alpha_slider.setValue(settings.crosshair_alpha * 100 // 255)
        self.crosshair_alpha_slider.valueChanged.connect(lambda v: setattr(settings, "crosshair_alpha", v * 255 // 100))
        xhair_color_row.addWidget(self.crosshair_alpha_slider)
        xhair_form.addRow("顏色/透明度:", xhair_color_row)

        tm_layout.addWidget(xhair_group)
        tm_layout.addStretch()
        tabs.addTab(tab_mag, "放大鏡")

        # ── Tab 3: 遊標效果 ──────────────────────────────────────────
        tab_cursor = QWidget()
        tc_layout = QVBoxLayout(tab_cursor)

        pulse_group = QGroupBox("呼吸效果")
        pulse_form = QFormLayout(pulse_group)

        self.pulse_style_combo = QComboBox()
        self.pulse_style_combo.addItems(PULSE_STYLES)
        idx_ps = self.pulse_style_combo.findText(settings.pulse_style)
        if idx_ps >= 0:
            self.pulse_style_combo.setCurrentIndex(idx_ps)
        self.pulse_style_combo.currentTextChanged.connect(lambda v: setattr(settings, "pulse_style", v))
        pulse_form.addRow("樣式:", self.pulse_style_combo)

        self.pulse_size_spin = QSpinBox()
        self.pulse_size_spin.setRange(4, 120)
        self.pulse_size_spin.setValue(settings.pulse_size)
        self.pulse_size_spin.valueChanged.connect(lambda v: setattr(settings, "pulse_size", v))
        pulse_form.addRow("大小 (基礎半徑):", self.pulse_size_spin)

        self.pulse_speed_spin = QDoubleSpinBox()
        self.pulse_speed_spin.setRange(0.1, 5.0)
        self.pulse_speed_spin.setSingleStep(0.1)
        self.pulse_speed_spin.setValue(settings.pulse_speed)
        self.pulse_speed_spin.valueChanged.connect(lambda v: setattr(settings, "pulse_speed", v))
        pulse_form.addRow("速度:", self.pulse_speed_spin)

        self.pulse_scale_spin = QDoubleSpinBox()
        self.pulse_scale_spin.setRange(0.0, 3.0)
        self.pulse_scale_spin.setSingleStep(0.05)
        self.pulse_scale_spin.setDecimals(2)
        self.pulse_scale_spin.setValue(settings.pulse_scale)
        self.pulse_scale_spin.valueChanged.connect(lambda v: setattr(settings, "pulse_scale", v))
        pulse_form.addRow("外圈放大比例:", self.pulse_scale_spin)

        self.pulse_color_btn = QPushButton()
        self._update_color_button(self.pulse_color_btn, settings.pulse_color)
        self.pulse_color_btn.clicked.connect(self._pick_pulse_color)
        pulse_form.addRow("外框顏色:", self.pulse_color_btn)

        self.pulse_color2_btn = QPushButton()
        self._update_color_button(self.pulse_color2_btn, settings.pulse_color2)
        self.pulse_color2_btn.clicked.connect(self._pick_pulse_color2)
        pulse_form.addRow("中心顏色:", self.pulse_color2_btn)

        tc_layout.addWidget(pulse_group)
        tc_layout.addStretch()
        tabs.addTab(tab_cursor, "遊標效果")

        # ── Tab 4: 路徑軌跡 ──────────────────────────────────────────
        tab_trail = QWidget()
        tt_layout = QVBoxLayout(tab_trail)

        trail_group = QGroupBox("軌跡外觀")
        trail_form = QFormLayout(trail_group)

        self.trail_icon_combo = QComboBox()
        self.trail_icon_combo.addItems(TRAIL_ICONS)
        idx_ti = self.trail_icon_combo.findText(settings.trail_icon)
        if idx_ti >= 0:
            self.trail_icon_combo.setCurrentIndex(idx_ti)
        self.trail_icon_combo.currentTextChanged.connect(lambda v: setattr(settings, "trail_icon", v))
        trail_form.addRow("頭部圖示:", self.trail_icon_combo)

        self.trail_icon_size_spin = QSpinBox()
        self.trail_icon_size_spin.setRange(8, 60)
        self.trail_icon_size_spin.setSuffix(" px")
        self.trail_icon_size_spin.setValue(settings.trail_icon_size)
        self.trail_icon_size_spin.valueChanged.connect(lambda v: setattr(settings, "trail_icon_size", v))
        trail_form.addRow("圖示大小:", self.trail_icon_size_spin)

        self.trail_length_spin = QSpinBox()
        self.trail_length_spin.setRange(20, 2000)
        self.trail_length_spin.setSuffix(" px")
        self.trail_length_spin.setValue(settings.trail_length)
        self.trail_length_spin.valueChanged.connect(lambda v: setattr(settings, "trail_length", v))
        trail_form.addRow("軌跡長度:", self.trail_length_spin)

        self.trail_width_spin = QSpinBox()
        self.trail_width_spin.setRange(1, 20)
        self.trail_width_spin.setValue(settings.trail_width)
        self.trail_width_spin.valueChanged.connect(lambda v: setattr(settings, "trail_width", v))
        trail_form.addRow("線條粗細:", self.trail_width_spin)

        self.trail_color_btn = QPushButton()
        self._update_color_button(self.trail_color_btn, settings.trail_color)
        self.trail_color_btn.clicked.connect(self._pick_trail_color)
        trail_form.addRow("軌跡顏色:", self.trail_color_btn)

        tt_layout.addWidget(trail_group)

        wp_group = QGroupBox("中斷點 (Waypoint)")
        wp_form = QFormLayout(wp_group)

        wp_hint = QLabel("錄製中按中斷點快速鍵 = 插入中斷點\n播放時依序停在每個中斷點")
        wp_hint.setStyleSheet("color: #aaa; font-size: 11px;")
        wp_form.addRow(wp_hint)

        self.waypoint_pause_spin = QDoubleSpinBox()
        self.waypoint_pause_spin.setRange(0.0, 5.0)
        self.waypoint_pause_spin.setSingleStep(0.1)
        self.waypoint_pause_spin.setSuffix(" 秒")
        self.waypoint_pause_spin.setValue(settings.waypoint_pause)
        self.waypoint_pause_spin.valueChanged.connect(lambda v: setattr(settings, "waypoint_pause", v))
        wp_form.addRow("停頓時間:", self.waypoint_pause_spin)

        self.waypoint_dot_size_spin = QSpinBox()
        self.waypoint_dot_size_spin.setRange(0, 30)
        self.waypoint_dot_size_spin.setSuffix(" px")
        self.waypoint_dot_size_spin.setSpecialValueText("隱藏")
        self.waypoint_dot_size_spin.setValue(settings.waypoint_dot_size)
        self.waypoint_dot_size_spin.valueChanged.connect(lambda v: setattr(settings, "waypoint_dot_size", v))
        wp_form.addRow("中斷點大小:", self.waypoint_dot_size_spin)

        self.waypoint_dot_color_btn = QPushButton()
        self._update_color_button(self.waypoint_dot_color_btn, settings.waypoint_dot_color)
        self.waypoint_dot_color_btn.clicked.connect(self._pick_waypoint_dot_color)
        wp_form.addRow("背景顏色:", self.waypoint_dot_color_btn)

        self.waypoint_dot_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.waypoint_dot_alpha_slider.setRange(0, 100)
        self.waypoint_dot_alpha_slider.setValue(settings.waypoint_dot_alpha)
        self.waypoint_dot_alpha_slider.valueChanged.connect(lambda v: setattr(settings, "waypoint_dot_alpha", v))
        wp_form.addRow("背景透明度 (%):", self.waypoint_dot_alpha_slider)

        self.waypoint_label_color_btn = QPushButton()
        self._update_color_button(self.waypoint_label_color_btn, settings.waypoint_label_color)
        self.waypoint_label_color_btn.clicked.connect(self._pick_waypoint_label_color)
        wp_form.addRow("數字顏色:", self.waypoint_label_color_btn)

        self.waypoint_border_width_spin = QSpinBox()
        self.waypoint_border_width_spin.setRange(0, 10)
        self.waypoint_border_width_spin.setSuffix(" px")
        self.waypoint_border_width_spin.setSpecialValueText("無")
        self.waypoint_border_width_spin.setValue(settings.waypoint_border_width)
        self.waypoint_border_width_spin.valueChanged.connect(lambda v: setattr(settings, "waypoint_border_width", v))
        wp_form.addRow("外框粗細:", self.waypoint_border_width_spin)

        self.waypoint_border_color_btn = QPushButton()
        self._update_color_button(self.waypoint_border_color_btn, settings.waypoint_border_color)
        self.waypoint_border_color_btn.clicked.connect(self._pick_waypoint_border_color)
        wp_form.addRow("外框顏色:", self.waypoint_border_color_btn)

        tt_layout.addWidget(wp_group)

        bg_group = QGroupBox("播放背景截圖")
        bg_form = QFormLayout(bg_group)

        self.record_bg_chk = QCheckBox("錄製時同步截取螢幕畫面")
        self.record_bg_chk.setChecked(settings.record_bg)
        self.record_bg_chk.toggled.connect(lambda v: setattr(settings, "record_bg", v))
        bg_form.addRow(self.record_bg_chk)

        self.record_bg_interval_spin = QDoubleSpinBox()
        self.record_bg_interval_spin.setRange(0.2, 5.0)
        self.record_bg_interval_spin.setSingleStep(0.1)
        self.record_bg_interval_spin.setDecimals(1)
        self.record_bg_interval_spin.setSuffix(" 秒")
        self.record_bg_interval_spin.setValue(settings.record_bg_interval)
        self.record_bg_interval_spin.valueChanged.connect(lambda v: setattr(settings, "record_bg_interval", v))
        bg_form.addRow("截圖間隔:", self.record_bg_interval_spin)

        bg_hint = QLabel("啟用後播放路徑動畫時，會以錄製當時的\n螢幕截圖作為背景，呈現完整示範情境。")
        bg_hint.setWordWrap(True)
        bg_hint.setStyleSheet("color: gray;")
        bg_form.addRow(bg_hint)

        tt_layout.addWidget(bg_group)
        tt_layout.addStretch()
        tabs.addTab(tab_trail, "路徑軌跡")

        # ── Tab 5: 快速鍵 ────────────────────────────────────────────
        tab_hk = QWidget()
        thk_layout = QVBoxLayout(tab_hk)

        hotkeys_group = QGroupBox("快速鍵")
        hotkeys_layout = QFormLayout(hotkeys_group)

        self._hotkey_widgets: dict[str, QPushButton] = {}
        for action, label in [
            ("recording", "錄製切換"),
            ("waypoint", "插入中斷點"),
            ("replay", "重播動畫"),
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

        thk_layout.addWidget(hotkeys_group)
        thk_layout.addStretch()
        tabs.addTab(tab_hk, "快速鍵")

        # ── 底部按鈕 ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("重設預設")
        reset_btn.clicked.connect(self._reset_defaults)
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

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
        color = QColorDialog.getColor(self.settings.pulse_color, self, "選擇外框顏色")
        if color.isValid():
            self.settings.pulse_color = color
            self._update_color_button(self.pulse_color_btn, color)

    def _pick_pulse_color2(self) -> None:
        color = QColorDialog.getColor(self.settings.pulse_color2, self, "選擇中心顏色")
        if color.isValid():
            self.settings.pulse_color2 = color
            self._update_color_button(self.pulse_color2_btn, color)

    def _pick_trail_color(self) -> None:
        color = QColorDialog.getColor(self.settings.trail_color, self, "選擇軌跡顏色")
        if color.isValid():
            self.settings.trail_color = color
            self._update_color_button(self.trail_color_btn, color)

    def _pick_waypoint_dot_color(self) -> None:
        color = QColorDialog.getColor(self.settings.waypoint_dot_color, self, "選擇中斷點背景顏色")
        if color.isValid():
            self.settings.waypoint_dot_color = color
            self._update_color_button(self.waypoint_dot_color_btn, color)

    def _pick_waypoint_label_color(self) -> None:
        color = QColorDialog.getColor(self.settings.waypoint_label_color, self, "選擇數字顏色")
        if color.isValid():
            self.settings.waypoint_label_color = color
            self._update_color_button(self.waypoint_label_color_btn, color)

    def _pick_waypoint_border_color(self) -> None:
        color = QColorDialog.getColor(self.settings.waypoint_border_color, self, "選擇外框顏色")
        if color.isValid():
            self.settings.waypoint_border_color = color
            self._update_color_button(self.waypoint_border_color_btn, color)

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
        s._suppress_emit = True
        try:
            s.obs_password = ""
            s.obs_mic_input = ""
            s.hud_width = 430
            s.hud_height = 118
            s.hud_bg_alpha = 82
            s.hud_font_family = "Cascadia Mono"
            s.hud_font_size = 21
            s.hud_text_color = QColor(252, 254, 255)
            s.hud_text_alpha = 250
            s.text_disappear_secs = 0
            s.magnify_style = "全螢幕 (Fullscreen)"
            s.magnify_start_zoom = 1.0
            s.lens_radius = 150
            s.draw_mod_line = "Shift"
            s.draw_mod_circle = "Alt"
            s.draw_mod_rect = "Ctrl"
            s.pulse_style = "雙圓圈 (Double)"
            s.pulse_size = 20
            s.pulse_speed = 1.0
            s.pulse_scale = 0.6
            s.pulse_color = QColor(30, 120, 255)
            s.pulse_color2 = QColor(255, 255, 255)
            s.trail_icon = "飛機 (Plane)"
            s.trail_length = 200
            s.trail_width = 3
            s.trail_color = QColor(255, 80, 40)
            s.trail_icon_size = 16
            s.waypoint_pause = 0.5
            s.waypoint_dot_size = 8
            s.waypoint_dot_color = QColor(255, 220, 0)
            s.waypoint_dot_alpha = 80
            s.waypoint_label_color = QColor(0, 0, 0)
            s.waypoint_border_width = 1
            s.waypoint_border_color = QColor(0, 0, 0)
            s.record_bg = True
            s.record_bg_interval = 0.5
            s.zoom_step = 0.25
            s.zoom_idle_timeout = 0
            s.crosshair_style = "十字線 (Crosshair)"
            s.crosshair_size = 14
            s.crosshair_color = QColor(255, 255, 255)
            s.crosshair_alpha = 150
            s.set_hotkey("escape", 27, False, False, False)
            s.set_hotkey("recording", 112, True, False, False)
            s.set_hotkey("waypoint", 113, True, False, False)
            s.set_hotkey("magnify", 114, True, False, False)
            s.set_hotkey("replay", 116, True, False, False)
            s.set_hotkey("undo", 90, True, False, False)
            s.set_hotkey("redo", 90, True, True, False)
        finally:
            s._suppress_emit = False
        s.save()
        s.changed.emit()
        self._sync_ui()

    def _sync_ui(self) -> None:
        s = self.settings
        self.obs_password_edit.blockSignals(True)
        self.obs_password_edit.setText(s.obs_password)
        self.obs_password_edit.blockSignals(False)

        self.obs_mic_input_edit.blockSignals(True)
        self.obs_mic_input_edit.setText(s.obs_mic_input)
        self.obs_mic_input_edit.blockSignals(False)

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

        self.draw_mod_line_combo.blockSignals(True)
        self.draw_mod_line_combo.setCurrentText(s.draw_mod_line)
        self.draw_mod_line_combo.blockSignals(False)

        self.draw_mod_circle_combo.blockSignals(True)
        self.draw_mod_circle_combo.setCurrentText(s.draw_mod_circle)
        self.draw_mod_circle_combo.blockSignals(False)

        self.draw_mod_rect_combo.blockSignals(True)
        self.draw_mod_rect_combo.setCurrentText(s.draw_mod_rect)
        self.draw_mod_rect_combo.blockSignals(False)

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

        self.pulse_scale_spin.blockSignals(True)
        self.pulse_scale_spin.setValue(s.pulse_scale)
        self.pulse_scale_spin.blockSignals(False)

        self.trail_icon_combo.blockSignals(True)
        idx_ti = self.trail_icon_combo.findText(s.trail_icon)
        if idx_ti >= 0:
            self.trail_icon_combo.setCurrentIndex(idx_ti)
        self.trail_icon_combo.blockSignals(False)

        self.trail_icon_size_spin.blockSignals(True)
        self.trail_icon_size_spin.setValue(s.trail_icon_size)
        self.trail_icon_size_spin.blockSignals(False)

        self.trail_length_spin.blockSignals(True)
        self.trail_length_spin.setValue(s.trail_length)
        self.trail_length_spin.blockSignals(False)

        self.trail_width_spin.blockSignals(True)
        self.trail_width_spin.setValue(s.trail_width)
        self.trail_width_spin.blockSignals(False)

        self.waypoint_pause_spin.blockSignals(True)
        self.waypoint_pause_spin.setValue(s.waypoint_pause)
        self.waypoint_pause_spin.blockSignals(False)

        self.waypoint_dot_size_spin.blockSignals(True)
        self.waypoint_dot_size_spin.setValue(s.waypoint_dot_size)
        self.waypoint_dot_size_spin.blockSignals(False)

        self.waypoint_dot_alpha_slider.blockSignals(True)
        self.waypoint_dot_alpha_slider.setValue(s.waypoint_dot_alpha)
        self.waypoint_dot_alpha_slider.blockSignals(False)

        self.waypoint_border_width_spin.blockSignals(True)
        self.waypoint_border_width_spin.setValue(s.waypoint_border_width)
        self.waypoint_border_width_spin.blockSignals(False)

        self.record_bg_chk.blockSignals(True)
        self.record_bg_chk.setChecked(s.record_bg)
        self.record_bg_chk.blockSignals(False)

        self.record_bg_interval_spin.blockSignals(True)
        self.record_bg_interval_spin.setValue(s.record_bg_interval)
        self.record_bg_interval_spin.blockSignals(False)

        self._update_color_button(self.text_color_btn, s.hud_text_color)
        self._update_color_button(self.crosshair_color_btn, s.crosshair_color)
        self._update_color_button(self.pulse_color_btn, s.pulse_color)
        self._update_color_button(self.pulse_color2_btn, s.pulse_color2)
        self._update_color_button(self.trail_color_btn, s.trail_color)
        self._update_color_button(self.waypoint_dot_color_btn, s.waypoint_dot_color)
        self._update_color_button(self.waypoint_label_color_btn, s.waypoint_label_color)
        self._update_color_button(self.waypoint_border_color_btn, s.waypoint_border_color)
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
        self._recorded_waypoints: list[int] = []
        self.paths: list[list[QPoint]] = []
        self._path_times: list[list[float]] = []
        self._path_anim_starts: list[float] = []
        self._path_waypoints: list[list[int]] = []
        self._recorded_frames: list[tuple[float, QPixmap]] = []
        self._path_frames: list[list[tuple[float, QPixmap]]] = []
        self._last_frame_capture = 0.0
        self.draw_color = QColor(255, 70, 70)
        self.freeze_pixmap: QPixmap | None = None
        self.zoom = 3.0
        self.mouse_local = QPoint(self.width() // 2, self.height() // 2)
        self._zoom_anchor = QPoint(self.width() // 2, self.height() // 2)
        self._magnify_strokes: list[tuple] = []
        self._magnify_active: list[QPoint] | None = None
        self._magnify_redo: list[tuple] = []
        self._magnify_rect_origin: QPoint | None = None
        self._magnify_rect_current: QPoint | None = None
        self._magnify_draw_type: str = ""
        self._last_interaction_time = 0.0
        self._screenshot_flash = 0
        self._rec_badge_pos = QPoint(self.width() - 50, 30)
        self._dragging_rec = False
        self._drag_rec_offset = QPoint()

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
                if self.settings.record_bg and self._recorded_times:
                    now = time.time()
                    if now - self._last_frame_capture >= self.settings.record_bg_interval:
                        px = self.screen.grabWindow(0)
                        if not px.isNull():
                            t0 = self._recorded_times[0]
                            self._recorded_frames.append((now - t0, px))
                        self._last_frame_capture = now
        if self.mode == Mode.MAGNIFY and self.settings.zoom_idle_timeout > 0:
            if time.time() - self._last_interaction_time > self.settings.zoom_idle_timeout:
                self.return_to_normal()
                return
        if self._screenshot_flash > 0:
            self._screenshot_flash -= 1
        self.update()

    def toggle_recording(self) -> None:
        if self.mode != Mode.NORMAL:
            self.return_to_normal()
        if not self.recording:
            self.recording = True
            self.recorded_points = []
            self._recorded_times = []
            self._recorded_waypoints = []
            self._recorded_frames = []
            self._last_frame_capture = 0.0
        else:
            self.recording = False
            if len(self.recorded_points) > 1:
                self.paths.append([QPoint(p) for p in self.recorded_points])
                t0 = self._recorded_times[0]
                self._path_times.append([t - t0 for t in self._recorded_times])
                self._path_anim_starts.append(time.time())
                self._path_waypoints.append(list(self._recorded_waypoints))
                self._path_frames.append(list(self._recorded_frames))
            self.recorded_points = []
            self._recorded_times = []
            self._recorded_waypoints = []
            self._recorded_frames = []
        self.update()

    def insert_waypoint(self) -> None:
        if not self.recording or not self.recorded_points:
            return
        idx = len(self.recorded_points) - 1
        if not self._recorded_waypoints or self._recorded_waypoints[-1] != idx:
            self._recorded_waypoints.append(idx)
            self.update()

    def replay_animations(self) -> None:
        if not self.paths:
            return
        now = time.time()
        for i in range(len(self._path_anim_starts)):
            self._path_anim_starts[i] = now
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
        self._recorded_waypoints = []
        self.paths.clear()
        self._path_times.clear()
        self._path_anim_starts.clear()
        self._path_waypoints.clear()
        self._path_frames.clear()
        self._recorded_frames.clear()
        self._magnify_strokes.clear()
        self._magnify_active = None
        self._magnify_redo.clear()
        self._magnify_rect_origin = None
        self._magnify_rect_current = None
        self._magnify_draw_type = ""
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
            radius = 20
            cx = self.width() - 40
            cy = 40
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 18, 24, 220))
            painter.drawEllipse(QPoint(cx, cy), radius, radius)
            painter.setFont(QFont("Segoe UI", 16, QFont.Weight.DemiBold))
            painter.setPen(QColor(248, 250, 252))
            painter.drawText(
                QRect(cx - radius, cy - radius, radius * 2, radius * 2),
                Qt.AlignmentFlag.AlignCenter, "M",
            )
            if self._screenshot_flash > 0:
                self._paint_mode_badge(painter, "截圖已儲存!")
            return

        self._paint_paths(painter)
        if self.recording:
            self._paint_recording_waypoints(painter)
            self._paint_rec_badge(painter)
        self._paint_pulse(painter)

    def _paint_recording_waypoints(self, painter: QPainter) -> None:
        if not self._recorded_waypoints or not self.recorded_points:
            return
        s = self.settings
        dot_r = s.waypoint_dot_size
        if dot_r <= 0:
            return
        alpha = int(s.waypoint_dot_alpha * 255 / 100)
        dc = s.waypoint_dot_color
        fill = QColor(dc.red(), dc.green(), dc.blue(), alpha)
        bw = s.waypoint_border_width
        bc = s.waypoint_border_color
        lc = s.waypoint_label_color
        font_size = max(7, dot_r - 2)
        font = QFont()
        font.setPixelSize(font_size)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        for n, wp_idx in enumerate(self._recorded_waypoints, start=1):
            if wp_idx < len(self.recorded_points):
                pt = self.recorded_points[wp_idx]
                pen = QPen(bc, bw) if bw > 0 else QPen(Qt.PenStyle.NoPen)
                painter.setPen(pen)
                painter.setBrush(fill)
                painter.drawEllipse(pt, dot_r, dot_r)
                label = str(n)
                tw = fm.horizontalAdvance(label)
                th = fm.ascent()
                painter.setPen(lc)
                painter.drawText(pt.x() - tw // 2, pt.y() + th // 2, label)

    def _trail_limit(self, path_idx: int, elapsed: float) -> int:
        path = self.paths[path_idx]
        times = self._path_times[path_idx]
        wps: list[int] = self._path_waypoints[path_idx] if path_idx < len(self._path_waypoints) else []
        pause = self.settings.waypoint_pause

        seg_boundaries = [0] + wps + [len(path) - 1]
        accumulated = 0.0
        for k in range(len(seg_boundaries) - 1):
            s_start = seg_boundaries[k]
            s_end = seg_boundaries[k + 1]
            seg_dur = times[s_end] - times[s_start]

            if elapsed < accumulated + seg_dur:
                t_in_seg = elapsed - accumulated
                local_times = [t - times[s_start] for t in times[s_start: s_end + 1]]
                lim = bisect.bisect_right(local_times, t_in_seg)
                return max(s_start + 1, min(s_start + lim, s_end + 1))

            accumulated += seg_dur

            if k < len(seg_boundaries) - 2:
                if elapsed < accumulated + pause:
                    return s_end + 1
                accumulated += pause

        return len(path)

    def _paint_paths(self, painter: QPainter) -> None:
        now = time.time()
        limits: list[int] = []
        elapseds: list[float] = []
        for i, path in enumerate(self.paths):
            if i < len(self._path_times) and i < len(self._path_anim_starts):
                elapsed = now - self._path_anim_starts[i]
                limit = self._trail_limit(i, elapsed)
            else:
                elapsed = float("inf")
                limit = len(path)
            limits.append(limit)
            elapseds.append(elapsed)

        # draw background screenshot for the most-progressed path with frames
        best_i = -1
        best_frac = -1.0
        for i in range(len(self.paths)):
            frames = self._path_frames[i] if i < len(self._path_frames) else []
            if not frames:
                continue
            total_t = self._path_times[i][-1] if i < len(self._path_times) and self._path_times[i] else 1.0
            frac = elapseds[i] / total_t if total_t > 0 else 0.0
            if frac > best_frac:
                best_frac = frac
                best_i = i
        if best_i >= 0:
            frames = self._path_frames[best_i]
            e = elapseds[best_i]
            frame_idx = max(0, bisect.bisect_right([f[0] for f in frames], e) - 1)
            painter.drawPixmap(self.rect(), frames[frame_idx][1])

        for i, path in enumerate(self.paths):
            wps = self._path_waypoints[i] if i < len(self._path_waypoints) else []
            self._paint_trail(painter, path, limits[i], wps)

    def _paint_trail(self, painter: QPainter, path: list[QPoint], limit: int, wps: list[int]) -> None:
        if limit < 2:
            return
        active = path[:limit]
        trail_px = self.settings.trail_length
        tw = self.settings.trail_width
        color = self.settings.trail_color

        # build cumulative arc distances for active portion
        dists: list[float] = [0.0]
        for a, b in zip(active, active[1:]):
            dists.append(dists[-1] + math.hypot(b.x() - a.x(), b.y() - a.y()))
        total = dists[-1]

        # find where the visible tail starts
        tail_dist = max(0.0, total - trail_px)
        tail_idx = max(0, bisect.bisect_right(dists, tail_dist) - 1)

        # interpolate exact tail start point
        if tail_idx + 1 < len(active) and dists[tail_idx + 1] > dists[tail_idx]:
            t = (tail_dist - dists[tail_idx]) / (dists[tail_idx + 1] - dists[tail_idx])
            tx = active[tail_idx].x() + t * (active[tail_idx + 1].x() - active[tail_idx].x())
            ty = active[tail_idx].y() + t * (active[tail_idx + 1].y() - active[tail_idx].y())
            visible = [QPoint(int(tx), int(ty))] + list(active[tail_idx + 1:])
        else:
            visible = list(active[tail_idx:])

        n = len(visible) - 1
        if n < 1:
            return

        # draw each segment with fading alpha (tail transparent → head opaque)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for j, (p1, p2) in enumerate(zip(visible, visible[1:])):
            frac = (j + 1) / n
            alpha = int(frac * 230)
            c = QColor(color.red(), color.green(), color.blue(), alpha)
            pen = QPen(c, tw)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(p1, p2)

        # draw directional icon at head
        head = active[-1]
        look_back = min(6, len(active) - 1)
        ref = active[-1 - look_back]
        dx = head.x() - ref.x()
        dy = head.y() - ref.y()
        angle = math.degrees(math.atan2(dy, dx)) if (dx or dy) else 0.0
        self._paint_trail_icon(painter, head, angle, color)

        # draw reached waypoint dots with numbers
        dot_r = self.settings.waypoint_dot_size
        if dot_r > 0:
            s = self.settings
            alpha = int(s.waypoint_dot_alpha * 255 / 100)
            dc = s.waypoint_dot_color
            fill = QColor(dc.red(), dc.green(), dc.blue(), alpha)
            bw = s.waypoint_border_width
            bc = s.waypoint_border_color
            lc = s.waypoint_label_color
            font_size = max(7, dot_r - 2)
            font = QFont()
            font.setPixelSize(font_size)
            font.setBold(True)
            painter.setFont(font)
            fm = painter.fontMetrics()
            for n, wp_idx in enumerate(wps, start=1):
                if wp_idx < limit and wp_idx < len(path):
                    pt = path[wp_idx]
                    pen = QPen(bc, bw) if bw > 0 else QPen(Qt.PenStyle.NoPen)
                    painter.setPen(pen)
                    painter.setBrush(fill)
                    painter.drawEllipse(pt, dot_r, dot_r)
                    label = str(n)
                    tw = fm.horizontalAdvance(label)
                    th = fm.ascent()
                    painter.setPen(lc)
                    painter.drawText(pt.x() - tw // 2, pt.y() + th // 2, label)

    def _paint_trail_icon(self, painter: QPainter, pos: QPoint, angle_deg: float, color: QColor) -> None:
        style = self.settings.trail_icon
        if style == "無圖示 (None)":
            return
        s = float(self.settings.trail_icon_size)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(pos)
        painter.rotate(angle_deg)

        # shift so the icon tip (front point) sits exactly on pos, not the center
        if style == "箭頭 (Arrow)" or style == "火箭 (Rocket)":
            painter.translate(-s, 0)
        elif style == "飛機 (Plane)":
            painter.translate(-s * 0.9, 0)

        icon_path = QPainterPath()
        if style == "箭頭 (Arrow)":
            icon_path.moveTo(s, 0)
            icon_path.lineTo(0, -s * 0.5)
            icon_path.lineTo(0, -s * 0.2)
            icon_path.lineTo(-s * 0.65, -s * 0.2)
            icon_path.lineTo(-s * 0.65, s * 0.2)
            icon_path.lineTo(0, s * 0.2)
            icon_path.lineTo(0, s * 0.5)
            icon_path.closeSubpath()
        elif style == "飛機 (Plane)":
            body = QPainterPath()
            body.moveTo(s * 0.9, 0)
            body.lineTo(s * 0.3, -s * 0.13)
            body.lineTo(-s * 0.55, -s * 0.13)
            body.lineTo(-s * 0.75, 0)
            body.lineTo(-s * 0.55, s * 0.13)
            body.lineTo(s * 0.3, s * 0.13)
            body.closeSubpath()

            wing_t = QPainterPath()
            wing_t.moveTo(s * 0.1, -s * 0.13)
            wing_t.lineTo(-s * 0.15, -s * 0.6)
            wing_t.lineTo(-s * 0.42, -s * 0.6)
            wing_t.lineTo(-s * 0.42, -s * 0.13)
            wing_t.closeSubpath()

            wing_b = QPainterPath()
            wing_b.moveTo(s * 0.1, s * 0.13)
            wing_b.lineTo(-s * 0.15, s * 0.6)
            wing_b.lineTo(-s * 0.42, s * 0.6)
            wing_b.lineTo(-s * 0.42, s * 0.13)
            wing_b.closeSubpath()

            tail_t = QPainterPath()
            tail_t.moveTo(-s * 0.5, -s * 0.13)
            tail_t.lineTo(-s * 0.58, -s * 0.35)
            tail_t.lineTo(-s * 0.75, -s * 0.35)
            tail_t.lineTo(-s * 0.75, -s * 0.13)
            tail_t.closeSubpath()

            tail_b = QPainterPath()
            tail_b.moveTo(-s * 0.5, s * 0.13)
            tail_b.lineTo(-s * 0.58, s * 0.35)
            tail_b.lineTo(-s * 0.75, s * 0.35)
            tail_b.lineTo(-s * 0.75, s * 0.13)
            tail_b.closeSubpath()

            icon_path = body.united(wing_t).united(wing_b).united(tail_t).united(tail_b)
        elif style == "火箭 (Rocket)":
            body = QPainterPath()
            body.moveTo(s, 0)
            body.lineTo(s * 0.35, -s * 0.22)
            body.lineTo(-s * 0.5, -s * 0.22)
            body.lineTo(-s * 0.5, s * 0.22)
            body.lineTo(s * 0.35, s * 0.22)
            body.closeSubpath()

            fin_t = QPainterPath()
            fin_t.moveTo(-s * 0.38, -s * 0.22)
            fin_t.lineTo(-s * 0.38, -s * 0.55)
            fin_t.lineTo(-s * 0.85, -s * 0.65)
            fin_t.lineTo(-s * 0.85, -s * 0.22)
            fin_t.closeSubpath()

            fin_b = QPainterPath()
            fin_b.moveTo(-s * 0.38, s * 0.22)
            fin_b.lineTo(-s * 0.38, s * 0.55)
            fin_b.lineTo(-s * 0.85, s * 0.65)
            fin_b.lineTo(-s * 0.85, s * 0.22)
            fin_b.closeSubpath()

            icon_path = body.united(fin_t).united(fin_b)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(icon_path)
        painter.restore()

    def _paint_pulse(self, painter: QPainter) -> None:
        style = self.settings.pulse_style
        if style == "無 (None)":
            return
        speed = self.settings.pulse_speed
        # time-based so animation speed is frame-rate independent
        pulse = (math.sin(time.time() * speed * 2.5) + 1.0) / 2.0
        size = self.settings.pulse_size
        c = self.settings.pulse_color    # 外框色
        c2 = self.settings.pulse_color2  # 中心色
        mx, my = self.mouse_local.x(), self.mouse_local.y()
        scale = self.settings.pulse_scale

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if style == "雙圓圈 (Double)":
            radius = size + int(size * scale * pulse)
            outer_alpha = int(215 - 75 * pulse)
            outer = QColor(c.red(), c.green(), c.blue(), outer_alpha)
            painter.setPen(QPen(outer, 3))
            painter.setBrush(QColor(c.red(), c.green(), c.blue(), 30))
            painter.drawEllipse(self.mouse_local, radius, radius)
            inner_r = max(4, radius // 4)
            inner = QColor(c2.red(), c2.green(), c2.blue(), int(200 + 55 * pulse))
            painter.setPen(QPen(inner, 2))
            painter.setBrush(QColor(c2.red(), c2.green(), c2.blue(), int(80 + 60 * pulse)))
            painter.drawEllipse(self.mouse_local, inner_r, inner_r)

        elif style == "單圓圈 (Ring)":
            radius = size + int(size * scale * pulse)
            ring = QColor(c.red(), c.green(), c.blue(), int(220 * (1 - pulse * 0.4)))
            painter.setPen(QPen(ring, 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.mouse_local, radius, radius)
            dot = QColor(c2.red(), c2.green(), c2.blue(), 200)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(self.mouse_local, 5, 5)

        elif style == "十字線 (Cross)":
            arm = size + int(size * scale * pulse)
            cross = QColor(c.red(), c.green(), c.blue(), int(220 * (1 - pulse * 0.3)))
            pen = QPen(cross, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(mx - arm, my, mx + arm, my)
            painter.drawLine(mx, my - arm, mx, my + arm)
            dot = QColor(c2.red(), c2.green(), c2.blue(), 230)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot)
            painter.drawEllipse(self.mouse_local, 4, 4)

        elif style == "點+圓 (Dot+Ring)":
            radius = size + int(size * scale * pulse)
            ring = QColor(c.red(), c.green(), c.blue(), int(180 * (1 - pulse)))
            painter.setPen(QPen(ring, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.mouse_local, radius, radius)
            dot = QColor(c2.red(), c2.green(), c2.blue(), 230)
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
        self._paint_crosshair_at(painter, cursor_dx, cursor_dy, self.zoom)

    def _paint_magnify_strokes(self, painter: QPainter, src_x: int, src_y: int, src_w: int, src_h: int) -> None:
        def td(pts: list[QPoint]) -> list[QPoint]:
            return [self._screen_to_display(p, src_x, src_y, src_w, src_h) for p in pts]

        for item in self._magnify_strokes:
            kind = item[0]
            color = item[1]
            if kind == "rect":
                _, _, p1, p2 = item
                dp1 = self._screen_to_display(p1, src_x, src_y, src_w, src_h)
                dp2 = self._screen_to_display(p2, src_x, src_y, src_w, src_h)
                painter.setPen(QPen(color, self.settings.stroke_width, Qt.PenStyle.SolidLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRect(dp1, dp2))
            elif kind in ("circle", "line"):
                _, _, p1, p2 = item
                dp1 = self._screen_to_display(p1, src_x, src_y, src_w, src_h)
                dp2 = self._screen_to_display(p2, src_x, src_y, src_w, src_h)
                painter.setPen(QPen(color, self.settings.stroke_width, Qt.PenStyle.SolidLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if kind == "circle":
                    dx = dp2.x() - dp1.x()
                    dy = dp2.y() - dp1.y()
                    r = int(math.sqrt(dx * dx + dy * dy))
                    painter.drawEllipse(dp1, r, r)
                else:
                    painter.drawLine(dp1, dp2)
            else:
                _, _, points = item
                self._paint_stroke(painter, color, td(points))

        if self._magnify_rect_origin is not None and self._magnify_rect_current is not None:
            dp1 = self._screen_to_display(self._magnify_rect_origin, src_x, src_y, src_w, src_h)
            dp2 = self._screen_to_display(self._magnify_rect_current, src_x, src_y, src_w, src_h)
            painter.setPen(QPen(self.draw_color, self.settings.stroke_width, Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self._magnify_draw_type == "circle":
                dx = dp2.x() - dp1.x()
                dy = dp2.y() - dp1.y()
                r = int(math.sqrt(dx * dx + dy * dy))
                painter.drawEllipse(dp1, r, r)
            elif self._magnify_draw_type == "line":
                painter.drawLine(dp1, dp2)
            else:
                painter.drawRect(QRect(dp1, dp2))
        elif self._magnify_active:
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

        self._paint_crosshair_at(painter, cx, cy, self.zoom)

    def _paint_crosshair(self, painter: QPainter) -> None:
        self._paint_crosshair_at(painter, self.width() // 2, self.height() // 2)

    def _paint_crosshair_at(self, painter: QPainter, cx: int, cy: int, zoom: float = 1.0) -> None:
        style = self.settings.crosshair_style
        base_size = self.settings.crosshair_size
        size = max(1, int(base_size * zoom))
        color = QColor(self.settings.crosshair_color)
        color.setAlpha(self.settings.crosshair_alpha)
        pen = QPen(color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        if style == "點 (Dot)":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QPoint(cx, cy), 3, 3)
            return

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

        painter.setPen(Qt.PenStyle.NoPen)
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

    def _paint_rec_badge(self, painter: QPainter) -> None:
        radius = 18
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(239, 68, 68, 230))
        painter.drawEllipse(self._rec_badge_pos, radius, radius)
        painter.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            QRect(self._rec_badge_pos.x() - radius, self._rec_badge_pos.y() - radius, radius * 2, radius * 2),
            Qt.AlignmentFlag.AlignCenter, "R",
        )

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

    def _mod_matches(self, mods, name: str) -> bool:
        if name == "Ctrl":
            return bool(mods & Qt.KeyboardModifier.ControlModifier)
        elif name == "Shift":
            return bool(mods & Qt.KeyboardModifier.ShiftModifier)
        elif name == "Alt":
            return bool(mods & Qt.KeyboardModifier.AltModifier)
        return False

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self.mode == Mode.MAGNIFY:
                mods = event.modifiers()
                if self._mod_matches(mods, self.settings.draw_mod_line):
                    self._magnify_draw_type = "line"
                    self._magnify_rect_origin = QPoint(self.mouse_local)
                    self._magnify_rect_current = QPoint(self.mouse_local)
                elif self._mod_matches(mods, self.settings.draw_mod_circle):
                    self._magnify_draw_type = "circle"
                    self._magnify_rect_origin = QPoint(self.mouse_local)
                    self._magnify_rect_current = QPoint(self.mouse_local)
                elif self._mod_matches(mods, self.settings.draw_mod_rect):
                    self._magnify_draw_type = "rect"
                    self._magnify_rect_origin = QPoint(self.mouse_local)
                    self._magnify_rect_current = QPoint(self.mouse_local)
                else:
                    self._magnify_active = [QPoint(self.mouse_local)]
                self._magnify_redo.clear()
                self.update()
            elif self.mode == Mode.NORMAL and self.recording:
                pos = event.position().toPoint()
                dx = pos.x() - self._rec_badge_pos.x()
                dy = pos.y() - self._rec_badge_pos.y()
                if dx * dx + dy * dy <= 20 * 20:
                    self._dragging_rec = True
                    self._drag_rec_offset = pos - self._rec_badge_pos

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        self.mouse_local = pos
        if self._dragging_rec:
            self._rec_badge_pos = pos - self._drag_rec_offset
            self.update()
        elif self.mode == Mode.MAGNIFY:
            self._last_interaction_time = time.time()
            if self._magnify_rect_origin is not None:
                self._magnify_rect_current = QPoint(self.mouse_local)
            elif self._magnify_active is not None:
                point = QPoint(self.mouse_local)
                if manhattan(self._magnify_active[-1], point) >= 2:
                    self._magnify_active.append(point)
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging_rec:
            self._dragging_rec = False
            self.update()
        elif self.mode == Mode.MAGNIFY:
            if self._magnify_rect_origin is not None:
                p1 = self._magnify_rect_origin
                p2 = self._magnify_rect_current or QPoint(self.mouse_local)
                if self._magnify_draw_type == "circle":
                    self._magnify_strokes.append(("circle", QColor(self.draw_color), p1, p2))
                else:
                    self._magnify_strokes.append(("rect", QColor(self.draw_color), p1, p2))
                self._magnify_rect_origin = None
                self._magnify_rect_current = None
                self._magnify_draw_type = ""
            elif self._magnify_active is not None:
                if len(self._magnify_active) > 1:
                    self._magnify_strokes.append(("freehand", QColor(self.draw_color), self._magnify_active))
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
        self.obs_poller = ObsStatusPoller(self.settings)
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
        elif action == "waypoint":
            self.overlay.insert_waypoint()
        elif action == "replay":
            self.overlay.replay_animations()
        elif action == "undo":
            self.overlay.undo()
        elif action == "redo":
            self.overlay.redo()
        else:
            self.overlay.set_draw_color_by_key(text)

    def _on_obs_status(self, live: bool, scene: str, connected: bool, mic_level: float) -> None:
        if self.hud:
            self.hud.set_obs_status(live, scene, connected, mic_level)

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
