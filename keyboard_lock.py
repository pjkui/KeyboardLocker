# -*- coding: utf-8 -*-
"""
键盘鼠标锁定工具 (Windows)
----------------------------------------
功能：
  - 锁住所有键盘和鼠标输入，屏幕仍正常显示
  - 按 Ctrl+Alt+Win+K 解锁
  - 60 秒后自动解锁
  - 纯 ctypes 实现，无需第三方库

使用：
  以管理员身份运行：  python keyboard_lock.py
  或：                python keyboard_lock.py --timeout 60

注意：
  1. 必须以管理员权限运行才能拦截所有窗口的输入。
  2. Ctrl+Alt+Del 由 Windows 安全桌面处理，任何程序都无法拦截，
     可作为兜底脱身方案。
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time
import threading
import argparse

# 尝试使用统一日志，若失败则回退到 print（保持模块可独立使用）
try:
    import app_logger as _L
    def _log_info(msg, *a):    _L.info(msg, *a)
    def _log_warn(msg, *a):    _L.warning(msg, *a)
    def _log_error(msg, *a):   _L.error(msg, *a)
except Exception:
    def _log_info(msg, *a):    print(("[INFO] "  + msg) % a if a else "[INFO] "  + msg)
    def _log_warn(msg, *a):    print(("[WARN] "  + msg) % a if a else "[WARN] "  + msg)
    def _log_error(msg, *a):   print(("[ERROR] " + msg) % a if a else "[ERROR] " + msg)

# --------- Win32 常量 ---------
WH_KEYBOARD_LL = 13
WH_MOUSE_LL    = 14

WM_KEYDOWN     = 0x0100
WM_KEYUP       = 0x0101
WM_SYSKEYDOWN  = 0x0104
WM_SYSKEYUP    = 0x0105

# 虚拟键码
VK_CONTROL = 0x11
VK_MENU    = 0x12  # Alt
VK_SHIFT   = 0x10
VK_LCTRL   = 0xA2
VK_RCTRL   = 0xA3
VK_LMENU   = 0xA4
VK_RMENU   = 0xA5
VK_LSHIFT  = 0xA0
VK_RSHIFT  = 0xA1
VK_LWIN    = 0x5B
VK_RWIN    = 0x5C
VK_K       = 0x4B

# 字母 A-Z 的 VK 就是 ASCII 码 0x41..0x5A
# 数字 0-9 的 VK 就是 ASCII 码 0x30..0x39
def vk_from_name(name):
    """把按键名字（单个字符，如 'K' / '1'）转成 VK 码"""
    n = name.strip().upper()
    if len(n) == 1 and ('A' <= n <= 'Z' or '0' <= n <= '9'):
        return ord(n)
    # 支持几个常用功能键
    table = {
        'F1': 0x70, 'F2': 0x71, 'F3': 0x72, 'F4': 0x73,
        'F5': 0x74, 'F6': 0x75, 'F7': 0x76, 'F8': 0x77,
        'F9': 0x78, 'F10': 0x79, 'F11': 0x7A, 'F12': 0x7B,
        'SPACE': 0x20, 'ESC': 0x1B, 'ENTER': 0x0D, 'TAB': 0x09,
    }
    return table.get(n, 0)

# GetAsyncKeyState 的最高位表示当前是否按下
KEY_PRESSED_MASK = 0x8000

WM_QUIT = 0x0012

# --------- 加载 DLL ---------
user32   = ctypes.WinDLL('user32',   use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# --------- 结构体 ---------
ULONG_PTR = ctypes.c_size_t

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('vkCode',      wt.DWORD),
        ('scanCode',    wt.DWORD),
        ('flags',       wt.DWORD),
        ('time',        wt.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    ]

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('pt',          wt.POINT),
        ('mouseData',   wt.DWORD),
        ('flags',       wt.DWORD),
        ('time',        wt.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    ]

# 回调函数签名:  LRESULT CALLBACK HookProc(int nCode, WPARAM wParam, LPARAM lParam)
LowLevelProc = ctypes.WINFUNCTYPE(
    ctypes.c_long,           # 返回 LRESULT
    ctypes.c_int,            # nCode
    wt.WPARAM,               # wParam
    wt.LPARAM,               # lParam
)

# --------- 函数原型 ---------
user32.SetWindowsHookExW.restype  = wt.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelProc, wt.HINSTANCE, wt.DWORD]

user32.CallNextHookEx.restype  = ctypes.c_long
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]

user32.UnhookWindowsHookEx.restype  = wt.BOOL
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]

user32.GetMessageW.restype  = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]

user32.PostThreadMessageW.restype  = wt.BOOL
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]

user32.GetAsyncKeyState.restype  = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

kernel32.GetModuleHandleW.restype  = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

kernel32.GetCurrentThreadId.restype = wt.DWORD

# --------- 全局状态 ---------
_kbd_hook   = None
_mouse_hook = None
_hook_thread_id = 0
_should_unlock = threading.Event()

# 自己跟踪修饰键状态（因为我们 return 1 拦截了事件，
# 系统的 GetAsyncKeyState 可能拿不到真实状态）
_ctrl_down  = False
_alt_down   = False
_shift_down = False
_win_down   = False

# 当前使用的解锁组合键配置（由 lock_input 设置）
_cfg_need_ctrl  = True
_cfg_need_alt   = True
_cfg_need_shift = False
_cfg_need_win   = False
_cfg_main_vk    = VK_K

# 钩子临时放行：当某个 PID 的窗口处于前台时，不拦截输入
# 用于密码验证时让用户能输入
_allowed_pids = set()
_allow_lock   = threading.Lock()

# 快捷键回调（不是直接退出，交给上层决定要不要真解锁）
_on_hotkey_cb = None


def set_allowed_pids(pids):
    """设置允许输入的 PID 集合。空集合=拦截所有。"""
    global _allowed_pids
    with _allow_lock:
        _allowed_pids = set(pids) if pids else set()


def request_unlock():
    """外部（如密码验证通过后）主动触发解锁。"""
    _should_unlock.set()
    if _hook_thread_id:
        user32.PostThreadMessageW(_hook_thread_id, WM_QUIT, 0, 0)


# 额外的 Win32 原型 ---------
user32.GetForegroundWindow.restype  = wt.HWND
user32.GetForegroundWindow.argtypes = []
user32.GetWindowThreadProcessId.restype  = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]


def _foreground_pid():
    """返回前台窗口所在进程的 PID，失败返回 0"""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def _is_input_allowed_now():
    """当前前台窗口是否属于放行的 PID"""
    with _allow_lock:
        if not _allowed_pids:
            return False
        pid = _foreground_pid()
        return pid in _allowed_pids


# --------- 钩子回调 ---------
@LowLevelProc
def _keyboard_proc(nCode, wParam, lParam):
    global _ctrl_down, _alt_down, _shift_down, _win_down

    if nCode == 0:  # HC_ACTION
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT))[0]
        vk = kb.vkCode
        is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_up   = wParam in (WM_KEYUP,   WM_SYSKEYUP)

        # 维护修饰键的真实按下状态（始终要做，方便快捷键判断）
        if vk in (VK_CONTROL, VK_LCTRL, VK_RCTRL):
            if is_down: _ctrl_down = True
            elif is_up: _ctrl_down = False
        elif vk in (VK_MENU, VK_LMENU, VK_RMENU):
            if is_down: _alt_down = True
            elif is_up: _alt_down = False
        elif vk in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT):
            if is_down: _shift_down = True
            elif is_up: _shift_down = False
        elif vk in (VK_LWIN, VK_RWIN):
            if is_down: _win_down = True
            elif is_up: _win_down = False

        # 检查解锁组合键：所有需要的修饰键都按下 + 按下主键
        if is_down and vk == _cfg_main_vk:
            ok = True
            if _cfg_need_ctrl  and not _ctrl_down:  ok = False
            if _cfg_need_alt   and not _alt_down:   ok = False
            if _cfg_need_shift and not _shift_down: ok = False
            if _cfg_need_win   and not _win_down:   ok = False
            if ok:
                if _on_hotkey_cb is not None:
                    # 交给上层处理（比如弹密码框），钩子不直接退出
                    try:
                        _on_hotkey_cb()
                    except Exception as e:
                        _log_error("on_hotkey 回调异常: %s", e)
                else:
                    # 没有回调 → 直接解锁
                    request_unlock()
                return 1  # 吞掉主键

        # 放行列表：如果前台窗口属于"允许输入"的进程，不拦截
        if _is_input_allowed_now():
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        # 其他所有按键一律拦截
        return 1
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


@LowLevelProc
def _mouse_proc(nCode, wParam, lParam):
    if nCode == 0:
        if _is_input_allowed_now():
            return user32.CallNextHookEx(None, nCode, wParam, lParam)
        # 所有鼠标事件（移动/点击/滚轮）全部拦截
        return 1
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


# --------- 主逻辑 ---------
def _auto_unlock_timer(timeout_sec):
    """倒计时线程：到时间就触发解锁"""
    start = time.time()
    while time.time() - start < timeout_sec:
        if _should_unlock.is_set():
            return
        time.sleep(0.2)
    _should_unlock.set()
    if _hook_thread_id:
        user32.PostThreadMessageW(_hook_thread_id, WM_QUIT, 0, 0)


def lock_input(timeout_sec=60, hotkey=None, on_hotkey=None):
    """
    锁定键盘鼠标。阻塞调用，直到解锁或超时才返回。

    参数:
        timeout_sec: 自动解锁秒数
        hotkey: dict，例如 {'ctrl': True, 'alt': True, 'shift': False,
                          'win': False, 'key': 'K'}
                为 None 时使用默认 Ctrl+Alt+Win+K
        on_hotkey: callable 或 None。
                   - None: 按下快捷键立即解锁（默认行为）
                   - callable: 按下快捷键时调用它（钩子线程里回调，
                     必须快速返回。典型用法是在里面异步启动密码验证，
                     验证通过后再调用 request_unlock()）
    """
    global _kbd_hook, _mouse_hook, _hook_thread_id
    global _cfg_need_ctrl, _cfg_need_alt, _cfg_need_shift, _cfg_need_win, _cfg_main_vk
    global _ctrl_down, _alt_down, _shift_down, _win_down
    global _on_hotkey_cb

    # 应用快捷键配置
    if hotkey is None:
        hotkey = {'ctrl': True, 'alt': True, 'shift': False, 'win': True, 'key': 'K'}
    _cfg_need_ctrl  = bool(hotkey.get('ctrl',  False))
    _cfg_need_alt   = bool(hotkey.get('alt',   False))
    _cfg_need_shift = bool(hotkey.get('shift', False))
    _cfg_need_win   = bool(hotkey.get('win',   False))
    main_vk = vk_from_name(hotkey.get('key', 'K'))
    if main_vk == 0:
        raise ValueError(f"不支持的主键: {hotkey.get('key')}")
    _cfg_main_vk = main_vk
    _on_hotkey_cb = on_hotkey

    # 重置状态
    _ctrl_down = _alt_down = _shift_down = _win_down = False
    _should_unlock.clear()
    set_allowed_pids(None)  # 默认拦截所有

    _hook_thread_id = kernel32.GetCurrentThreadId()
    hmod = kernel32.GetModuleHandleW(None)

    _kbd_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _keyboard_proc, hmod, 0)
    if not _kbd_hook:
        raise ctypes.WinError(ctypes.get_last_error())

    _mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, _mouse_proc, hmod, 0)
    if not _mouse_hook:
        user32.UnhookWindowsHookEx(_kbd_hook)
        raise ctypes.WinError(ctypes.get_last_error())

    # 启动自动解锁倒计时
    timer = threading.Thread(target=_auto_unlock_timer, args=(timeout_sec,), daemon=True)
    timer.start()

    _log_info("键盘鼠标已锁定，按 %s 解锁，或 %d 秒后自动解锁", format_hotkey(hotkey), timeout_sec)

    # 进入消息循环，钩子必须在有消息泵的线程里才能工作
    try:
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if _should_unlock.is_set():
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        if _kbd_hook:
            user32.UnhookWindowsHookEx(_kbd_hook)
        if _mouse_hook:
            user32.UnhookWindowsHookEx(_mouse_hook)
        _kbd_hook = None
        _mouse_hook = None
        _hook_thread_id = 0
        _on_hotkey_cb = None
        set_allowed_pids(None)
        _log_info("已解锁")


def format_hotkey(hotkey):
    """把 hotkey dict 格式化成可读字符串，如 'Ctrl+Alt+K'"""
    parts = []
    if hotkey.get('ctrl'):  parts.append('Ctrl')
    if hotkey.get('alt'):   parts.append('Alt')
    if hotkey.get('shift'): parts.append('Shift')
    if hotkey.get('win'):   parts.append('Win')
    parts.append(str(hotkey.get('key', 'K')).upper())
    return '+'.join(parts)


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="锁住键盘和鼠标输入（屏幕仍可显示）")
    parser.add_argument('--timeout', type=int, default=20,
                        help='自动解锁的秒数（默认 60）')
    parser.add_argument('--delay', type=int, default=3,
                        help='启动前等待秒数，给用户松开按键的时间（默认 3）')
    args = parser.parse_args()

    if sys.platform != 'win32':
        print("此脚本仅支持 Windows。")
        sys.exit(1)

    if not _is_admin():
        print("[警告] 当前不是管理员权限，部分高权限窗口的输入可能无法拦截。")
        print("       建议右键以管理员身份打开 cmd/PowerShell 后再运行本脚本。")

    for i in range(args.delay, 0, -1):
        print(f"{i} 秒后开始锁定...")
        time.sleep(1)

    lock_input(timeout_sec=args.timeout)


if __name__ == '__main__':
    main()
