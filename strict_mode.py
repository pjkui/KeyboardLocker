# -*- coding: utf-8 -*-
"""
严格模式：监控 Secure Desktop 切换
-----------------------------------------------------
Ctrl+Alt+Del 会把桌面切换到 winlogon 安全桌面。用户返回普通桌面时，
本模块触发回调 —— 上层据此要求用户输入本程序的密码才能解锁。

不修改任何注册表、组策略，因此：
  - 不会干扰用户系统的其他功能
  - 退出时不需要恢复任何状态
"""

import time
import ctypes
import ctypes.wintypes as wt
import threading

try:
    import app_logger as _L
    def _warn(msg, *a):  _L.warning(msg, *a)
    def _info(msg, *a):  _L.info(msg, *a)
except Exception:
    def _warn(msg, *a):  print(("[WARN] " + msg) % a if a else "[WARN] " + msg)
    def _info(msg, *a):  print(("[INFO] " + msg) % a if a else "[INFO] " + msg)


user32 = ctypes.WinDLL("user32", use_last_error=True)

UOI_NAME = 2
user32.OpenInputDesktop.restype  = wt.HANDLE
user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

user32.GetUserObjectInformationW.restype  = wt.BOOL
user32.GetUserObjectInformationW.argtypes = [
    wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)
]

user32.CloseDesktop.restype  = wt.BOOL
user32.CloseDesktop.argtypes = [wt.HANDLE]


def _current_input_desktop_name():
    """返回当前输入桌面名（str）；若拿不到返回 None（很可能此刻是 Secure Desktop）"""
    h = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not h:
        return None
    try:
        needed = wt.DWORD(0)
        user32.GetUserObjectInformationW(h, UOI_NAME, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            return None
        buf = ctypes.create_unicode_buffer(needed.value // 2 + 1)
        ok = user32.GetUserObjectInformationW(
            h, UOI_NAME, buf, needed.value, ctypes.byref(needed)
        )
        if not ok:
            return None
        return buf.value
    finally:
        user32.CloseDesktop(h)


class SasWatcher:
    """监控 Secure Desktop 切换。
    用户离开 Default 桌面（进入 winlogon / Secure Desktop），
    回到 Default 时触发 on_return 回调。
    """

    def __init__(self, on_return, poll_interval=0.15):
        self._on_return = on_return
        self._poll = poll_interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        was_away = False  # 上一次轮询时是否处于"非 Default 桌面"
        while not self._stop.is_set():
            name = _current_input_desktop_name()
            # name 可能是 None（权限不够看不到，通常 = Secure Desktop）
            # 或 "Default"（用户桌面）/ "Winlogon"（安全桌面）
            on_default = (name == "Default")
            if on_default:
                if was_away:
                    was_away = False
                    try:
                        self._on_return()
                    except Exception as e:
                        _warn("SAS 回调异常: %s", e)
            else:
                was_away = True
            time.sleep(self._poll)


# 保留一个兼容性空实现，防止旧配置文件残留
def recover_if_needed():
    """旧版本会在注册表里存备份，新版本不再需要。这里保留接口兼容。"""
    import os
    old_backup = None
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        old_backup = os.path.join(here, "_strict_backup.json")
        if os.path.exists(old_backup):
            # 旧版残留的备份（说明旧进程改过注册表），尝试恢复
            import json
            import winreg
            with open(old_backup, "r", encoding="utf-8") as f:
                items = json.load(f)
            for it in items:
                sub, name, original = it["sub"], it["name"], it.get("original")
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0, winreg.KEY_SET_VALUE)
                    try:
                        if original is None:
                            try:
                                winreg.DeleteValue(key, name)
                            except FileNotFoundError:
                                pass
                        else:
                            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(original))
                    finally:
                        winreg.CloseKey(key)
                except Exception:
                    pass
            os.remove(old_backup)
            _info("已清理旧版严格模式的注册表残留")
    except Exception:
        pass
