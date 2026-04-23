# -*- coding: utf-8 -*-
"""
开机自启管理
--------------------------------------------------
支持两种方式：
  1. TaskScheduler（推荐）—— 以管理员权限静默启动，不弹 UAC
  2. Registry       —— 写 HKCU\\...\\Run，权限等同普通程序（会弹 UAC）

首次开启时会优先尝试创建计划任务；若失败（例如没管理员权限）则退回到注册表方式。
"""

import os
import sys
import ctypes
import subprocess
import winreg

TASK_NAME = "KeyboardLockerTrayAutoStart"
REG_APP_NAME = "KeyboardLockerTray"
# 旧版名称（用于升级时自动清理遗留项，避免双开机启动）
_LEGACY_TASK_NAME = "KeyboardLockTrayAutoStart"
_LEGACY_REG_APP_NAME = "KeyboardLockTray"
REG_RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _is_frozen():
    """是否运行在 PyInstaller 打包产物里。"""
    return getattr(sys, "frozen", False)


def _get_pythonw_exe():
    """返回 pythonw.exe 的绝对路径，找不到就用 python.exe"""
    exe_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(exe_dir, "pythonw.exe")
    return pythonw if os.path.isfile(pythonw) else sys.executable


def _get_target_script():
    """返回 tray_app.py 的绝对路径"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "tray_app.py"))


def _build_command():
    """返回 (exe, arg) 两部分，用于任务计划/注册表。
    - 打包成 exe 后：直接启动自身（sys.executable），无需参数
    - 源码运行：pythonw + tray_app.py
    """
    if _is_frozen():
        return sys.executable, ""
    exe = _get_pythonw_exe()
    script = _get_target_script()
    return exe, f'"{script}"'


# ---------------- 注册表方式 ----------------
def _reg_enable():
    exe, arg = _build_command()
    cmd = f'"{exe}" {arg}'.rstrip()
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_PATH, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, REG_APP_NAME, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        return True, None
    except Exception as e:
        return False, str(e)


def _reg_disable():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_PATH, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, REG_APP_NAME)
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
        return True, None
    except Exception as e:
        return False, str(e)


def _reg_is_enabled():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_PATH, 0, winreg.KEY_READ)
        try:
            val, _ = winreg.QueryValueEx(key, REG_APP_NAME)
            return bool(val)
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


# ---------------- 任务计划程序方式 ----------------
def _run_schtasks(args):
    """静默执行 schtasks 命令，返回 (returncode, stdout, stderr)"""
    try:
        # 隐藏窗口
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        proc = subprocess.run(
            ["schtasks"] + args,
            capture_output=True, text=True,
            startupinfo=si, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return -1, "", str(e)


def _task_exists():
    rc, _, _ = _run_schtasks(["/Query", "/TN", TASK_NAME])
    return rc == 0


def _task_enable():
    """创建以最高权限、登录时触发的计划任务"""
    if not _is_admin():
        return False, "创建计划任务需要管理员权限"

    exe, arg = _build_command()
    # schtasks 创建任务:
    #   /SC ONLOGON : 登录时触发
    #   /RL HIGHEST : 以最高权限运行
    #   /F          : 覆盖已有同名任务
    #   /TR         : 要执行的命令
    tr = f'"{exe}" {arg}'.rstrip()
    rc, out, err = _run_schtasks([
        "/Create", "/TN", TASK_NAME,
        "/TR", tr,
        "/SC", "ONLOGON",
        "/RL", "HIGHEST",
        "/F",
    ])
    if rc == 0:
        return True, None
    return False, (err or out or f"schtasks exit code {rc}").strip()


def _task_disable():
    if not _task_exists():
        return True, None
    if not _is_admin():
        return False, "删除计划任务需要管理员权限"
    rc, out, err = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    if rc == 0:
        return True, None
    return False, (err or out or f"schtasks exit code {rc}").strip()


# ---------------- 对外统一接口 ----------------
def is_enabled():
    """当前是否已启用开机自启（任一方式即算启用）"""
    return _task_exists() or _reg_is_enabled()


def enable():
    """
    启用开机自启。优先使用任务计划（以管理员身份静默启动），
    失败则回退到注册表。
    返回 (ok, method, err_msg)
      method: 'task' | 'registry' | None
    """
    if _is_admin():
        ok, err = _task_enable()
        if ok:
            return True, "task", None
        # 如果任务创建失败，降级到注册表
        ok2, err2 = _reg_enable()
        if ok2:
            return True, "registry", f"计划任务创建失败({err})，已退回注册表方式"
        return False, None, f"两种方式都失败: task={err}; reg={err2}"
    else:
        ok, err = _reg_enable()
        if ok:
            return True, "registry", None
        return False, None, err


def disable():
    """取消开机自启（两种方式都清理一遍，同时清理旧版遗留项）"""
    errs = []
    # 注册表方式不需要管理员权限
    ok, err = _reg_disable()
    if not ok:
        errs.append(f"registry: {err}")

    if _task_exists():
        ok, err = _task_disable()
        if not ok:
            errs.append(f"task: {err}")

    # 顺带清理旧版（KeyboardLock）遗留项，避免改名后重复开机启动
    _cleanup_legacy()

    if errs:
        return False, "; ".join(errs)
    return True, None


def _cleanup_legacy():
    """清理旧版 KeyboardLock 时期的注册表项与计划任务（静默，失败忽略）。"""
    # 旧注册表 Run 项
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_PATH, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, _LEGACY_REG_APP_NAME)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass

    # 旧计划任务
    try:
        rc, _, _ = _run_schtasks(["/Query", "/TN", _LEGACY_TASK_NAME])
        if rc == 0 and _is_admin():
            _run_schtasks(["/Delete", "/TN", _LEGACY_TASK_NAME, "/F"])
    except Exception:
        pass


def describe():
    """返回当前启用方式的描述字符串，用于 UI 展示"""
    if _task_exists():
        return "已启用（任务计划，管理员权限）"
    if _reg_is_enabled():
        return "已启用（注册表，普通权限）"
    return "未启用"
