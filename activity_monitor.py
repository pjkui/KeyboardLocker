# -*- coding: utf-8 -*-
"""
系统活动检测与自动上锁监控
---------------------------------
- 通过 GetLastInputInfo 检测键盘/鼠标最近输入时间
- 通过 XInput 轮询检测手柄输入活动（Xbox/兼容 XInput 设备）
- 提供前台进程 + 全屏判断，用于“看视频时不自动上锁”豁免
"""

import os
import time
import ctypes
import ctypes.wintypes as wt
import threading


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("dwTime", wt.DWORD),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wt.LONG),
        ("top", wt.LONG),
        ("right", wt.LONG),
        ("bottom", wt.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wt.DWORD),
    ]


user32.GetLastInputInfo.restype = wt.BOOL
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]

user32.GetForegroundWindow.restype = wt.HWND
user32.GetForegroundWindow.argtypes = []

user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]

user32.MonitorFromWindow.restype = wt.HMONITOR
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]

user32.GetMonitorInfoW.restype = wt.BOOL
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]

kernel32.GetTickCount.restype = wt.DWORD
kernel32.GetTickCount.argtypes = []

kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]

kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2


# ---- XInput ----
class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wt.WORD),
        ("bLeftTrigger", wt.BYTE),
        ("bRightTrigger", wt.BYTE),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", wt.DWORD),
        ("Gamepad", XINPUT_GAMEPAD),
    ]


_xinput = None
for _dll in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
    try:
        _xinput = ctypes.WinDLL(_dll)
        break
    except OSError:
        continue

if _xinput is not None:
    _xinput.XInputGetState.restype = wt.DWORD
    _xinput.XInputGetState.argtypes = [wt.DWORD, ctypes.POINTER(XINPUT_STATE)]


def _tick_diff_seconds(now_tick, old_tick):
    # GetTickCount 为 32-bit，约 49 天回绕；此处用无符号差值规避回绕问题
    return ((int(now_tick) - int(old_tick)) & 0xFFFFFFFF) / 1000.0


class ActivityTracker:
    """追踪最近输入活动时间（键盘/鼠标/手柄）。"""

    def __init__(self):
        self._last_gamepad_tick = kernel32.GetTickCount()
        self._gamepad_packet_no = {}

    def _last_keyboard_mouse_tick(self):
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(li)):
            return kernel32.GetTickCount()
        return li.dwTime

    def _poll_gamepad(self, now_tick):
        if _xinput is None:
            return

        for idx in range(4):
            state = XINPUT_STATE()
            ret = _xinput.XInputGetState(idx, ctypes.byref(state))
            if ret == 0:
                prev = self._gamepad_packet_no.get(idx)
                if prev is None or prev != state.dwPacketNumber:
                    self._gamepad_packet_no[idx] = state.dwPacketNumber
                    self._last_gamepad_tick = now_tick
            else:
                self._gamepad_packet_no.pop(idx, None)

    def get_idle_seconds(self):
        now_tick = kernel32.GetTickCount()
        self._poll_gamepad(now_tick)
        last_tick = max(int(self._last_keyboard_mouse_tick()), int(self._last_gamepad_tick))
        return _tick_diff_seconds(now_tick, last_tick)


def get_foreground_process_name():
    """返回前台窗口进程名（小写，含 .exe）；失败返回空串。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""

    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == 0:
        return ""

    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not hproc:
        return ""

    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size))
        if not ok:
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(hproc)


def is_foreground_fullscreen(min_cover_ratio=0.95):
    """判断前台窗口是否近似全屏。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False

    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False

    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not hmon:
        return False

    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return False

    win_w = max(0, rect.right - rect.left)
    win_h = max(0, rect.bottom - rect.top)
    mon_w = max(1, mi.rcMonitor.right - mi.rcMonitor.left)
    mon_h = max(1, mi.rcMonitor.bottom - mi.rcMonitor.top)

    cover_w = min(win_w / float(mon_w), 1.0)
    cover_h = min(win_h / float(mon_h), 1.0)
    return (cover_w >= min_cover_ratio) and (cover_h >= min_cover_ratio)


def is_video_exempt_active(video_processes, require_fullscreen=True):
    """
    判断是否处于“视频豁免”状态：
    - 前台进程命中白名单
    - 且（可选）处于全屏
    """
    if not video_processes:
        return False

    p = get_foreground_process_name()
    if not p:
        return False

    white = {str(x).strip().lower() for x in video_processes if str(x).strip()}
    if p not in white:
        return False

    if require_fullscreen and not is_foreground_fullscreen():
        return False

    return True


class InactivityMonitor:
    """后台空闲监控。达到阈值后触发回调（每次输入周期只触发一次）。"""

    def __init__(
        self,
        enabled_getter,
        idle_seconds_getter,
        video_exempt_getter,
        video_active_checker,
        on_idle_timeout,
        log_info=None,
        log_warn=None,
        poll_interval=1.0,
    ):
        self._enabled_getter = enabled_getter
        self._idle_seconds_getter = idle_seconds_getter
        self._video_exempt_getter = video_exempt_getter
        self._video_active_checker = video_active_checker
        self._on_idle_timeout = on_idle_timeout
        self._log_info = log_info or (lambda *a, **k: None)
        self._log_warn = log_warn or (lambda *a, **k: None)
        self._poll_interval = max(0.2, float(poll_interval))

        self._tracker = ActivityTracker()
        self._stop = threading.Event()
        self._thread = None
        self._fired_in_current_idle = False
        self._video_skip_logged = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._log_info("空闲自动上锁监控已启动")

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                enabled = bool(self._enabled_getter())
                if not enabled:
                    self._fired_in_current_idle = False
                    self._video_skip_logged = False
                    time.sleep(self._poll_interval)
                    continue

                threshold = int(self._idle_seconds_getter())
                threshold = max(5, threshold)
                idle_s = self._tracker.get_idle_seconds()

                if idle_s < threshold:
                    self._fired_in_current_idle = False
                    self._video_skip_logged = False
                    time.sleep(self._poll_interval)
                    continue

                # 已达到空闲阈值
                if bool(self._video_exempt_getter()) and bool(self._video_active_checker()):
                    if not self._video_skip_logged:
                        self._video_skip_logged = True
                        self._log_info("检测到视频播放场景，跳过本次自动上锁")
                    time.sleep(self._poll_interval)
                    continue

                if not self._fired_in_current_idle:
                    self._fired_in_current_idle = True
                    self._video_skip_logged = False
                    self._on_idle_timeout(idle_s)

            except Exception as e:
                self._log_warn("空闲监控异常: %s", e)

            time.sleep(self._poll_interval)
