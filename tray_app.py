# -*- coding: utf-8 -*-
"""
键盘鼠标锁定工具 - 系统托盘版
-------------------------------------
托盘菜单：
  - 立即锁定（使用当前配置）
  - 设置超时时间
  - 设置解锁快捷键
  - 关于
  - 退出

配置保存在同目录下的 config.json。

依赖：
  pip install pystray Pillow
"""

import os
import sys
import json
import time
import hashlib
import secrets
import threading
import ctypes
import tkinter as tk
from tkinter import ttk, messagebox


try:
    import pystray
    from pystray import MenuItem as Item, Menu
    from PIL import Image, ImageDraw
except ImportError:
    print("缺少依赖，请先执行： pip install pystray Pillow")
    sys.exit(1)

import keyboard_lock as kl
import autostart
import strict_mode
import app_logger as L
import activity_monitor as am
import updater
from version import __version__


APP_NAME = "KeyboardLocker"
SINGLE_INSTANCE_MUTEX = "Global\\KeyboardLocker_Tray_Singleton"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# 后台静默检查更新的最小间隔（秒），防止频繁请求 GitHub API
UPDATE_CHECK_INTERVAL_SEC = 24 * 3600


DEFAULT_CONFIG = {
    "timeout": 60,
    "hotkey": {
        "ctrl":  True,
        "alt":   True,
        "shift": False,
        "win":   True,
        "key":   "K",
    },
    "countdown_before_lock": 3,  # 锁定前的倒计时（秒）
    "lock_workstation_after_unlock": False,  # 解锁后是否触发 Windows 锁屏
    "password": None,  # {"salt": <hex>, "hash": <hex>}；None 表示未设密码
    "strict_mode": False,  # 严格模式：锁定期间限制 Ctrl+Alt+Del
    "auto_lock_enabled": False,  # 空闲自动上锁
    "auto_lock_idle_seconds": 300,  # 空闲多少秒后自动上锁
    "auto_lock_video_exempt": True,  # 看视频时不自动上锁
    "auto_lock_video_fullscreen_only": True,  # 仅前台全屏视频进程触发豁免
    "auto_lock_video_processes": [
        "potplayermini64.exe",
        "vlc.exe",
        "mpc-hc64.exe",
        "mpc-be64.exe",
        "qqvideo.exe",
        "bilibili.exe",
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
    ],
    "bluetooth_auto_lock_enabled": False,  # 预留：蓝牙离开范围自动上锁（暂未接入）

    # ---------------- 更新检查 ----------------
    "auto_check_update": True,        # 启动时静默检查 GitHub 最新 Release
    "last_update_check_ts": 0,         # 上次检查时间戳（秒），用于节流
    "skip_update_version": "",         # 用户选择"忽略此版本"，不再提醒
}



# ---------- 配置读写 ----------
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # 合并默认值，防止缺字段
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            merged["hotkey"] = {**DEFAULT_CONFIG["hotkey"], **cfg.get("hotkey", {})}

            # 列表字段做一次安全兜底
            vps = merged.get("auto_lock_video_processes")
            if not isinstance(vps, list) or not vps:
                merged["auto_lock_video_processes"] = list(DEFAULT_CONFIG["auto_lock_video_processes"])

            # 数值字段做边界保护
            try:
                merged["auto_lock_idle_seconds"] = int(merged.get("auto_lock_idle_seconds", 300))
            except Exception:
                merged["auto_lock_idle_seconds"] = 300
            merged["auto_lock_idle_seconds"] = max(5, min(7200, merged["auto_lock_idle_seconds"]))
            return merged
        except Exception as e:
            print(f"[WARN] 配置读取失败，使用默认值: {e}")
    return dict(DEFAULT_CONFIG)



def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] 配置保存失败: {e}")


# ---------- 托盘图标绘制 ----------
def make_icon_image(locked=False):
    """动态画一个图标。locked=True 时画红色，未锁定时画蓝色。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    color = (220, 50, 50) if locked else (60, 130, 220)
    # 画一个锁形：底部方块 + 上部拱形
    # 底部矩形
    d.rounded_rectangle([12, 28, 52, 58], radius=6, fill=color)
    # 上部拱形（用两个椭圆叠出"U"形）
    d.rectangle([20, 18, 44, 34], fill=(0, 0, 0, 0))
    d.arc([18, 8, 46, 36], start=180, end=360, fill=color, width=6)
    # 钥匙孔
    d.ellipse([28, 36, 36, 44], fill=(255, 255, 255))
    d.rectangle([31, 40, 33, 50], fill=(255, 255, 255))
    return img


# ---------- Windows 锁屏 ----------
def lock_workstation():
    """调用 Win32 API 锁屏（等价于按 Win+L）。"""
    import ctypes
    try:
        ok = ctypes.windll.user32.LockWorkStation()
        if not ok:
            err = ctypes.get_last_error()
            print(f"[WARN] LockWorkStation 失败，错误码={err}")
    except Exception as e:
        print(f"[WARN] 锁屏调用异常: {e}")


# ---------- 密码 ----------
def _hash_password(password, salt=None):
    """PBKDF2-HMAC-SHA256，返回 (salt_hex, hash_hex)。"""
    if salt is None:
        salt = secrets.token_bytes(16)
    elif isinstance(salt, str):
        salt = bytes.fromhex(salt)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return salt.hex(), h.hex()


def make_password_record(password):
    """生成可保存到 config.json 的密码记录。password 为空串则返回 None。"""
    if not password:
        return None
    salt_hex, hash_hex = _hash_password(password)
    return {"salt": salt_hex, "hash": hash_hex}


def verify_password(record, password):
    """record: dict 或 None；password: str。record 为 None 直接 True（未设密码）。"""
    if not record:
        return True
    try:
        _, h = _hash_password(password, record["salt"])
        return secrets.compare_digest(h, record["hash"])
    except Exception:
        return False


# ---------- 主应用 ----------
class TrayApp:
    def __init__(self):
        self.config = load_config()
        self.icon = None
        self.lock_thread = None
        self.is_locked = False

        # Tk 根窗口，用来弹对话框（隐藏主窗口）
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()

        # 后台空闲自动上锁监控
        self.auto_lock_monitor = am.InactivityMonitor(
            enabled_getter=lambda: bool(self.config.get("auto_lock_enabled", False)) and (not self.is_locked),
            idle_seconds_getter=lambda: int(self.config.get("auto_lock_idle_seconds", 300)),
            video_exempt_getter=lambda: bool(self.config.get("auto_lock_video_exempt", True)),
            video_active_checker=self._is_video_exempt_active,
            on_idle_timeout=self._on_idle_timeout,
            log_info=L.info,
            log_warn=L.warning,
            poll_interval=1.0,
        )
        self.auto_lock_monitor.start()

        # 启动时静默检查更新（节流 + 配置开关 + 后台线程）
        self._maybe_start_silent_update_check()


    # ---- 更新检查 ----
    def _maybe_start_silent_update_check(self):
        if not bool(self.config.get("auto_check_update", True)):
            return
        last = float(self.config.get("last_update_check_ts", 0) or 0)
        if (time.time() - last) < UPDATE_CHECK_INTERVAL_SEC:
            L.info("距离上次检查更新不足 %ds，跳过", UPDATE_CHECK_INTERVAL_SEC)
            return
        threading.Thread(target=self._silent_check_update, daemon=True).start()

    def _silent_check_update(self):
        """后台静默检查；有新版本才弹气球提示。"""
        try:
            L.info("开始静默检查更新（当前版本 %s）", __version__)
            result = updater.check_update(__version__)
            self.config["last_update_check_ts"] = int(time.time())
            save_config(self.config)
            if result.get("error"):
                L.warning("检查更新失败: %s", result["error"])
                return
            if not result["has_update"]:
                L.info("已是最新版本（latest=%s）", result["latest_tag"])
                return
            latest_tag = result["latest_tag"]
            if latest_tag and latest_tag == self.config.get("skip_update_version"):
                L.info("用户已忽略此版本: %s", latest_tag)
                return
            L.info("发现新版本: %s → %s", __version__, latest_tag)
            # 在 Tk 主线程弹更新对话框
            self.tk_root.after(0, lambda: self._show_update_dialog(result, silent_mode=True))
        except Exception:
            L.exception("静默检查更新异常")

    def manual_check_update(self, icon=None, item=None):
        """菜单 → 立即检查更新（无论结果都给反馈）。"""
        def _do():
            L.info("手动检查更新（当前版本 %s）", __version__)
            result = updater.check_update(__version__)
            self.config["last_update_check_ts"] = int(time.time())
            save_config(self.config)
            if result.get("error"):
                self.tk_root.after(0, lambda: messagebox.showwarning(
                    "检查更新", f"检查更新失败：\n{result['error']}\n\n请稍后重试，或访问：\n{updater.RELEASES_URL}"
                ))
                return
            if result["has_update"]:
                self.tk_root.after(0, lambda: self._show_update_dialog(result, silent_mode=False))
            else:
                self.tk_root.after(0, lambda: messagebox.showinfo(
                    "检查更新",
                    f"当前已是最新版本。\n\n本地版本: {__version__}\n最新版本: {result['latest_tag']}",
                ))
        threading.Thread(target=_do, daemon=True).start()

    def _show_update_dialog(self, result, silent_mode):
        """显示新版本提醒对话框。silent_mode=True 时多提供"忽略此版本"按钮。"""
        rel = result.get("release") or {}
        latest_tag = result["latest_tag"]
        url = rel.get("url") or updater.RELEASES_URL
        body = rel.get("body") or "（无更新说明）"

        dlg = tk.Toplevel(self.tk_root)
        dlg.title(f"发现新版本 {latest_tag}")
        dlg.geometry("520x420")
        dlg.attributes("-topmost", True)

        tk.Label(
            dlg,
            text=f"发现新版本：{latest_tag}\n当前版本：{__version__}",
            font=("Microsoft YaHei", 11, "bold"),
            justify="left",
        ).pack(anchor="w", padx=16, pady=(14, 6))

        tk.Label(dlg, text="更新说明：", font=("Microsoft YaHei", 10)).pack(anchor="w", padx=16)

        text_frame = tk.Frame(dlg)
        text_frame.pack(fill="both", expand=True, padx=16, pady=(4, 10))
        text = tk.Text(text_frame, wrap="word", font=("Microsoft YaHei", 9), height=12)
        scroll = tk.Scrollbar(text_frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", body)
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def open_release():
            try:
                import webbrowser
                webbrowser.open(url, new=2)
            except Exception as e:
                L.warning("打开浏览器失败: %s", e)

        def skip_this_version():
            self.config["skip_update_version"] = latest_tag
            save_config(self.config)
            L.info("用户选择忽略版本 %s", latest_tag)
            dlg.destroy()

        btns = tk.Frame(dlg)
        btns.pack(pady=10)
        tk.Button(btns, text="前往下载", width=12, command=open_release).pack(side=tk.LEFT, padx=6)
        tk.Button(btns, text="稍后提醒", width=12, command=dlg.destroy).pack(side=tk.LEFT, padx=6)
        if silent_mode:
            tk.Button(btns, text="忽略此版本", width=12, command=skip_this_version).pack(side=tk.LEFT, padx=6)

    def toggle_auto_check_update(self, icon=None, item=None):
        cur = bool(self.config.get("auto_check_update", True))
        self.config["auto_check_update"] = not cur
        # 切到开启时清掉 skip_version 与 节流，让用户能看到结果
        if not cur:
            self.config["skip_update_version"] = ""
            self.config["last_update_check_ts"] = 0
        save_config(self.config)
        L.info("自动检查更新: %s", not cur)
        if self.icon:
            self.icon.menu = self._build_menu()
            self.icon.update_menu()


    # ---- 锁定 ----
    def _is_video_exempt_active(self):
        """前台视频豁免检测：命中进程白名单 +（可选）全屏。"""
        try:
            return am.is_video_exempt_active(
                self.config.get("auto_lock_video_processes", []),
                require_fullscreen=bool(self.config.get("auto_lock_video_fullscreen_only", True)),
            )
        except Exception:
            return False

    def _on_idle_timeout(self, idle_seconds):
        """后台空闲达到阈值时触发。"""
        if self.is_locked:
            return
        L.info("达到空闲阈值，触发自动上锁 | idle=%.1fs | 阈值=%ss", idle_seconds, self.config.get("auto_lock_idle_seconds", 300))
        self.start_lock(reason="auto_idle")

    def _do_lock(self, lock_reason="manual"):
        """在独立线程里执行锁定（钩子必须在有消息循环的线程里）"""

        if self.is_locked:
            return
        self.is_locked = True
        self._update_icon()

        # 密码框状态：避免连续触发多个对话框
        self._password_dlg_open = False

        def _on_hotkey():
            """快捷键被按下时的回调（在钩子线程里执行，要快速返回）"""
            L.info("检测到解锁快捷键按下")
            if self.config.get("password"):
                # 有密码 → 丢回 Tk 主线程弹出密码框
                if not self._password_dlg_open:
                    self._password_dlg_open = True
                    self.tk_root.after(0, self._show_password_dialog)
            else:
                # 无密码 → 直接解锁
                L.info("无密码，快捷键直接解锁")
                kl.request_unlock()

        def _on_sas_return():
            """用户按过 Ctrl+Alt+Del 并返回桌面时触发（SasWatcher 线程里调用）"""
            # 只有在设置了密码、且锁定仍在进行时才弹密码框
            if not self.is_locked:
                return
            L.warning("检测到 Ctrl+Alt+Del 返回桌面")
            if not self.config.get("password"):
                # 没设密码，按需求也没别的操作 —— 保持锁定即可
                return
            if not self._password_dlg_open:
                self._password_dlg_open = True
                self.tk_root.after(0, self._show_password_dialog)

        # 严格模式：监控 Ctrl+Alt+Del 返回事件
        strict_on = bool(self.config.get("strict_mode", False))
        watcher = None

        hk_str = kl.format_hotkey(self.config["hotkey"])
        to_s = int(self.config["timeout"])
        L.info(
            "开始锁定 | 触发=%s | 快捷键=%s | 超时=%ds | 严格模式=%s | 密码=%s | 解锁后锁屏=%s",
            lock_reason, hk_str, to_s, strict_on,
            "有" if self.config.get("password") else "无",
            self.config.get("lock_workstation_after_unlock", False),
        )

        try:
            # 手动锁定时可保留倒计时；自动锁定达到阈值后应立即触发
            delay = max(0, int(self.config.get("countdown_before_lock", 3))) if lock_reason == "manual" else 0
            if delay > 0:
                time.sleep(delay)


            if strict_on:
                watcher = strict_mode.SasWatcher(_on_sas_return)
                watcher.start()
                L.info("SAS 监控已启动")

            kl.lock_input(
                timeout_sec=to_s,
                hotkey=self.config["hotkey"],
                on_hotkey=_on_hotkey,
            )
            L.info("锁定已结束（解锁或超时）")
        except Exception:
            L.exception("锁定过程发生异常")
        finally:
            if watcher:
                watcher.stop()
                L.info("SAS 监控已停止")
            self.is_locked = False
            self._password_dlg_open = False
            self._update_icon()

            # 解锁后如果配置了，触发 Windows 锁屏
            if self.config.get("lock_workstation_after_unlock", False):
                # 稍等一下让钩子完全卸载，避免锁屏动画卡顿
                time.sleep(0.2)
                L.info("触发 Windows 锁屏")
                lock_workstation()

    # ---- 密码验证对话框（锁定期间弹出） ----
    def _show_password_dialog(self):
        dlg = tk.Toplevel(self.tk_root)
        dlg.title("请输入解锁密码")
        dlg.geometry("320x160")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        # 避免用户把它点到后台导致键鼠被拦截
        dlg.protocol("WM_DELETE_WINDOW", lambda: _on_cancel())

        tk.Label(dlg, text="请输入密码以解锁：", font=("Microsoft YaHei", 10)).pack(pady=(16, 6))
        var = tk.StringVar()
        entry = tk.Entry(dlg, textvariable=var, show="*", width=22, justify="center")
        entry.pack()

        tip = tk.Label(dlg, text="", fg="#c33", font=("Microsoft YaHei", 9))
        tip.pack(pady=(4, 0))

        # 关键：把当前进程 PID 加入钩子放行列表，这样用户在这个对话框里能输入
        kl.set_allowed_pids([os.getpid()])

        # 强制把焦点抢到输入框
        def _focus():
            try:
                dlg.lift()
                dlg.focus_force()
                entry.focus_set()
            except Exception:
                pass
        dlg.after(50,  _focus)
        dlg.after(200, _focus)

        def _cleanup():
            # 关掉对话框前恢复完全拦截
            kl.set_allowed_pids(None)
            self._password_dlg_open = False
            try:
                dlg.destroy()
            except Exception:
                pass

        def _on_ok(event=None):
            pw = var.get()
            if verify_password(self.config.get("password"), pw):
                L.info("密码验证通过，解锁")
                _cleanup()
                kl.request_unlock()
            else:
                L.warning("密码验证失败")
                tip.config(text="密码错误，请重试。")
                var.set("")
                entry.focus_set()

        def _on_cancel(event=None):
            # 取消 = 保持锁定
            L.info("用户取消密码验证，保持锁定")
            _cleanup()

        btns = tk.Frame(dlg)
        btns.pack(pady=10)
        tk.Button(btns, text="解锁",    width=8, command=_on_ok    ).pack(side=tk.LEFT, padx=6)
        tk.Button(btns, text="保持锁定", width=8, command=_on_cancel).pack(side=tk.LEFT, padx=6)
        dlg.bind("<Return>", _on_ok)
        dlg.bind("<Escape>", _on_cancel)

    def start_lock(self, icon=None, item=None, reason="manual"):
        if self.is_locked:
            return
        self.lock_thread = threading.Thread(target=self._do_lock, args=(reason,), daemon=True)
        self.lock_thread.start()


    # ---- 设置超时 ----
    def open_timeout_dialog(self, icon=None, item=None):
        self.tk_root.after(0, self._show_timeout_dialog)

    def _show_timeout_dialog(self):
        dlg = tk.Toplevel(self.tk_root)
        dlg.title("设置自动解锁时间")
        dlg.geometry("300x140")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)

        tk.Label(dlg, text="自动解锁时间（秒）:", font=("Microsoft YaHei", 10)).pack(pady=(16, 6))
        var = tk.StringVar(value=str(self.config.get("timeout", 60)))
        entry = tk.Entry(dlg, textvariable=var, width=15, justify="center")
        entry.pack()
        entry.focus_set()
        entry.select_range(0, tk.END)

        def on_ok():
            try:
                v = int(var.get())
                if v < 3 or v > 3600:
                    raise ValueError
            except ValueError:
                messagebox.showerror("错误", "请输入 3 ~ 3600 之间的整数秒数。", parent=dlg)
                return
            old = self.config.get("timeout")
            self.config["timeout"] = v
            save_config(self.config)
            L.info("超时时间已更新: %s → %s 秒", old, v)
            dlg.destroy()

        btns = tk.Frame(dlg)
        btns.pack(pady=12)
        tk.Button(btns, text="确定", width=8, command=on_ok).pack(side=tk.LEFT, padx=6)
        tk.Button(btns, text="取消", width=8, command=dlg.destroy).pack(side=tk.LEFT, padx=6)
        dlg.bind("<Return>", lambda e: on_ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ---- 自动上锁设置 ----
    def toggle_auto_lock(self, icon=None, item=None):
        cur = bool(self.config.get("auto_lock_enabled", False))
        self.config["auto_lock_enabled"] = not cur
        save_config(self.config)
        L.info("空闲自动上锁: %s", not cur)
        if self.icon:
            self.icon.menu = self._build_menu()
            self.icon.update_menu()

    def toggle_auto_lock_video_exempt(self, icon=None, item=None):
        cur = bool(self.config.get("auto_lock_video_exempt", True))
        self.config["auto_lock_video_exempt"] = not cur
        save_config(self.config)
        L.info("视频播放豁免自动上锁: %s", not cur)
        if self.icon:
            self.icon.menu = self._build_menu()
            self.icon.update_menu()

    def open_auto_lock_dialog(self, icon=None, item=None):
        self.tk_root.after(0, self._show_auto_lock_dialog)

    def _show_auto_lock_dialog(self):
        dlg = tk.Toplevel(self.tk_root)
        dlg.title("设置自动上锁")
        dlg.geometry("360x220")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)

        tk.Label(dlg, text="空闲自动上锁时间（秒）", font=("Microsoft YaHei", 10)).pack(pady=(16, 6))

        v_idle = tk.StringVar(value=str(self.config.get("auto_lock_idle_seconds", 300)))
        entry = tk.Entry(dlg, textvariable=v_idle, width=16, justify="center")
        entry.pack()
        entry.focus_set()
        entry.select_range(0, tk.END)

        v_video_exempt = tk.BooleanVar(value=bool(self.config.get("auto_lock_video_exempt", True)))
        tk.Checkbutton(
            dlg,
            text="前台视频播放时不自动上锁（白名单 + 全屏）",
            variable=v_video_exempt,
        ).pack(pady=(12, 4))

        tk.Label(
            dlg,
            text="建议：300~1800 秒。最低 5 秒，最高 7200 秒。",
            fg="#666",
            font=("Microsoft YaHei", 9),
        ).pack()

        def on_ok():
            try:
                idle_s = int(v_idle.get())
                if idle_s < 5 or idle_s > 7200:
                    raise ValueError
            except ValueError:
                messagebox.showerror("错误", "请输入 5 ~ 7200 之间的整数秒数。", parent=dlg)
                return

            self.config["auto_lock_idle_seconds"] = idle_s
            self.config["auto_lock_video_exempt"] = bool(v_video_exempt.get())
            save_config(self.config)
            L.info(
                "自动上锁配置已更新: idle=%ss, video_exempt=%s",
                idle_s,
                self.config["auto_lock_video_exempt"],
            )
            if self.icon:
                self.icon.menu = self._build_menu()
                self.icon.update_menu()
            dlg.destroy()

        btns = tk.Frame(dlg)
        btns.pack(pady=12)
        tk.Button(btns, text="确定", width=8, command=on_ok).pack(side=tk.LEFT, padx=6)
        tk.Button(btns, text="取消", width=8, command=dlg.destroy).pack(side=tk.LEFT, padx=6)
        dlg.bind("<Return>", lambda e: on_ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ---- 设置快捷键 ----
    def open_hotkey_dialog(self, icon=None, item=None):
        self.tk_root.after(0, self._show_hotkey_dialog)


    def _show_hotkey_dialog(self):
        dlg = tk.Toplevel(self.tk_root)
        dlg.title("设置解锁快捷键")
        dlg.geometry("320x240")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)

        hk = self.config["hotkey"]
        v_ctrl  = tk.BooleanVar(value=hk.get("ctrl",  True))
        v_alt   = tk.BooleanVar(value=hk.get("alt",   True))
        v_shift = tk.BooleanVar(value=hk.get("shift", False))
        v_win   = tk.BooleanVar(value=hk.get("win",   False))
        v_key   = tk.StringVar(value=str(hk.get("key", "K")).upper())

        frm = tk.Frame(dlg)
        frm.pack(pady=12, padx=16, anchor="w")

        tk.Label(frm, text="修饰键：", font=("Microsoft YaHei", 10)).grid(row=0, column=0, sticky="w")
        tk.Checkbutton(frm, text="Ctrl",  variable=v_ctrl ).grid(row=0, column=1, sticky="w")
        tk.Checkbutton(frm, text="Alt",   variable=v_alt  ).grid(row=0, column=2, sticky="w")
        tk.Checkbutton(frm, text="Shift", variable=v_shift).grid(row=1, column=1, sticky="w")
        tk.Checkbutton(frm, text="Win",   variable=v_win  ).grid(row=1, column=2, sticky="w")

        tk.Label(frm, text="主键：", font=("Microsoft YaHei", 10)).grid(row=2, column=0, sticky="w", pady=(10, 0))
        keys = [chr(c) for c in range(ord("A"), ord("Z") + 1)] + \
               [str(i) for i in range(10)] + \
               [f"F{i}" for i in range(1, 13)]
        combo = ttk.Combobox(frm, textvariable=v_key, values=keys, width=8, state="readonly")
        combo.grid(row=2, column=1, columnspan=2, sticky="w", pady=(10, 0))

        hint = tk.Label(dlg, text="提示：至少需要一个修饰键。", fg="#888", font=("Microsoft YaHei", 9))
        hint.pack()

        def on_ok():
            if not (v_ctrl.get() or v_alt.get() or v_shift.get() or v_win.get()):
                messagebox.showerror("错误", "请至少选择一个修饰键（Ctrl/Alt/Shift/Win）。", parent=dlg)
                return
            key_name = v_key.get().strip().upper()
            if kl.vk_from_name(key_name) == 0:
                messagebox.showerror("错误", f"不支持的主键：{key_name}", parent=dlg)
                return
            self.config["hotkey"] = {
                "ctrl":  v_ctrl.get(),
                "alt":   v_alt.get(),
                "shift": v_shift.get(),
                "win":   v_win.get(),
                "key":   key_name,
            }
            save_config(self.config)
            L.info("解锁快捷键已更新为: %s", kl.format_hotkey(self.config["hotkey"]))
            # 刷新托盘菜单（显示新快捷键）
            if self.icon:
                self.icon.menu = self._build_menu()
                self.icon.update_menu()
            dlg.destroy()

        btns = tk.Frame(dlg)
        btns.pack(pady=10)
        tk.Button(btns, text="确定", width=8, command=on_ok).pack(side=tk.LEFT, padx=6)
        tk.Button(btns, text="取消", width=8, command=dlg.destroy).pack(side=tk.LEFT, padx=6)
        dlg.bind("<Return>", lambda e: on_ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ---- 关于 ----
    def show_about(self, icon=None, item=None):
        def _show():
            lock_ws = "是" if self.config.get("lock_workstation_after_unlock") else "否"
            strict = "开启" if self.config.get("strict_mode") else "关闭"
            auto = autostart.describe()
            pw = "已设置" if self.config.get("password") else "未设置"
            auto_lock = "开启" if self.config.get("auto_lock_enabled") else "关闭"
            video_exempt = "开启" if self.config.get("auto_lock_video_exempt") else "关闭"
            bt_auto = "预留（未接入）" if self.config.get("bluetooth_auto_lock_enabled") else "关闭"
            messagebox.showinfo(
                "关于 " + APP_NAME,
                f"{APP_NAME}\n\n"
                f"锁住键盘和鼠标输入，屏幕仍可正常显示。\n\n"
                f"当前解锁快捷键: {kl.format_hotkey(self.config['hotkey'])}\n"
                f"自动解锁时间: {self.config['timeout']} 秒\n"
                f"空闲自动上锁: {auto_lock}（{self.config.get('auto_lock_idle_seconds', 300)} 秒）\n"
                f"视频播放豁免: {video_exempt}\n"
                f"蓝牙离开自动上锁: {bt_auto}\n"
                f"解锁密码: {pw}\n"
                f"解锁后锁屏: {lock_ws}\n"
                f"Ctrl+Alt+Del 返回需密码: {strict}\n"
                f"开机启动: {auto}\n\n"
                f"提示: 超时自动解锁不需要密码。"
            )

        self.tk_root.after(0, _show)

    # ---- 退出 ----
    def quit_app(self, icon=None, item=None):
        L.info("托盘程序退出")
        try:
            self.auto_lock_monitor.stop()
        except Exception:
            pass
        if self.icon:
            self.icon.stop()

        try:
            self.tk_root.quit()
            self.tk_root.destroy()
        except Exception:
            pass
        os._exit(0)

    # ---- 打开日志文件 ----
    def open_log_file(self, icon=None, item=None):
        try:
            path = L.get_log_path()
            if os.path.exists(path):
                os.startfile(path)  # Windows 上用默认程序打开
            else:
                # 文件还没生成（可能刚启动），打开日志目录
                os.startfile(os.path.dirname(path))
        except Exception as e:
            L.error("打开日志文件失败: %s", e)

    # ---- 切换"解锁后锁屏"开关 ----
    def toggle_lock_workstation(self, icon=None, item=None):
        cur = bool(self.config.get("lock_workstation_after_unlock", False))
        self.config["lock_workstation_after_unlock"] = not cur
        save_config(self.config)
        L.info("解锁后触发 Windows 锁屏: %s", not cur)
        if self.icon:
            self.icon.menu = self._build_menu()
            self.icon.update_menu()

    # ---- 切换"严格模式"开关 ----
    def toggle_strict_mode(self, icon=None, item=None):
        def _do():
            cur = bool(self.config.get("strict_mode", False))
            if not cur:
                # 没设密码时开启没有意义，先提示
                if not self.config.get("password"):
                    messagebox.showwarning(
                        "严格模式",
                        "严格模式依赖解锁密码工作。\n"
                        "请先在菜单中设置解锁密码，然后再开启严格模式。"
                    )
                    return
                ok = messagebox.askokcancel(
                    "严格模式",
                    "开启后：锁定期间若用户按过 Ctrl+Alt+Del，\n"
                    "返回桌面时会弹出密码框，必须输入本程序密码才能解锁。\n\n"
                    "不会修改系统设置，解锁后自动停止监控。\n\n"
                    "确定开启？",
                )
                if not ok:
                    return
            self.config["strict_mode"] = not cur
            save_config(self.config)
            L.info("严格模式: %s", not cur)
            if self.icon:
                self.icon.menu = self._build_menu()
                self.icon.update_menu()
        self.tk_root.after(0, _do)

    # ---- 切换"开机启动"开关 ----
    def toggle_autostart(self, icon=None, item=None):
        def _do():
            if autostart.is_enabled():
                ok, err = autostart.disable()
                if ok:
                    L.info("已取消开机启动")
                    messagebox.showinfo("开机启动", "已取消开机启动。")
                else:
                    L.error("取消开机启动失败: %s", err)
                    messagebox.showerror("开机启动", f"取消失败：\n{err}")
            else:
                ok, method, err = autostart.enable()
                if ok:
                    L.info("已启用开机启动，方式=%s", method)
                    if method == "task":
                        messagebox.showinfo(
                            "开机启动",
                            "已启用开机启动（任务计划方式）。\n"
                            "下次开机登录后将以管理员权限静默启动，不会弹 UAC。"
                        )
                    else:
                        messagebox.showwarning(
                            "开机启动",
                            "已启用开机启动（注册表方式）。\n"
                            f"{err or ''}\n\n"
                            "提示：由于当前未以管理员运行，\n"
                            "开机后启动的托盘会以普通权限运行，\n"
                            "可能无法拦截部分高权限窗口的输入。\n"
                            "建议以管理员身份重新启动后再开启此项。"
                        )
                else:
                    L.error("启用开机启动失败: %s", err)
                    messagebox.showerror("开机启动", f"启用失败：\n{err}")
            # 刷新菜单
            if self.icon:
                self.icon.menu = self._build_menu()
                self.icon.update_menu()
        self.tk_root.after(0, _do)

    # ---- 设置/清除解锁密码 ----
    def open_password_dialog(self, icon=None, item=None):
        self.tk_root.after(0, self._show_set_password_dialog)

    def _show_set_password_dialog(self):
        has_pw = bool(self.config.get("password"))
        dlg = tk.Toplevel(self.tk_root)
        dlg.title("设置解锁密码")
        dlg.geometry("340x230")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)

        tk.Label(
            dlg,
            text=("当前状态：已设置密码" if has_pw else "当前状态：未设置密码"),
            font=("Microsoft YaHei", 10),
        ).pack(pady=(12, 8))

        frm = tk.Frame(dlg)
        frm.pack(padx=16, pady=4, fill="x")

        v_old = tk.StringVar()
        v_new = tk.StringVar()
        v_new2 = tk.StringVar()

        row = 0
        if has_pw:
            tk.Label(frm, text="原密码：", anchor="e", width=8).grid(row=row, column=0, sticky="e", pady=3)
            tk.Entry(frm, textvariable=v_old, show="*", width=22).grid(row=row, column=1, pady=3)
            row += 1

        tk.Label(frm, text="新密码：", anchor="e", width=8).grid(row=row, column=0, sticky="e", pady=3)
        tk.Entry(frm, textvariable=v_new, show="*", width=22).grid(row=row, column=1, pady=3)
        row += 1
        tk.Label(frm, text="确认：", anchor="e", width=8).grid(row=row, column=0, sticky="e", pady=3)
        tk.Entry(frm, textvariable=v_new2, show="*", width=22).grid(row=row, column=1, pady=3)

        tk.Label(
            dlg, text="留空新密码并点\"保存\"可清除密码。",
            fg="#888", font=("Microsoft YaHei", 9),
        ).pack(pady=(6, 0))

        def on_save():
            if has_pw and not verify_password(self.config.get("password"), v_old.get()):
                L.warning("修改密码时原密码验证失败")
                messagebox.showerror("错误", "原密码不正确。", parent=dlg)
                return
            pw1 = v_new.get()
            pw2 = v_new2.get()
            if pw1 != pw2:
                messagebox.showerror("错误", "两次输入的新密码不一致。", parent=dlg)
                return
            if pw1 == "":
                # 清除密码
                self.config["password"] = None
                save_config(self.config)
                L.info("解锁密码已清除")
                messagebox.showinfo("成功", "已清除解锁密码，解锁将无需密码。", parent=dlg)
            else:
                self.config["password"] = make_password_record(pw1)
                save_config(self.config)
                L.info("解锁密码已%s", "更新" if has_pw else "设置")
                messagebox.showinfo("成功", "密码已保存。", parent=dlg)
            if self.icon:
                self.icon.menu = self._build_menu()
                self.icon.update_menu()
            dlg.destroy()

        btns = tk.Frame(dlg)
        btns.pack(pady=10)
        tk.Button(btns, text="保存", width=8, command=on_save).pack(side=tk.LEFT, padx=6)
        tk.Button(btns, text="取消", width=8, command=dlg.destroy).pack(side=tk.LEFT, padx=6)
        dlg.bind("<Return>", lambda e: on_save())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ---- 菜单构建 ----
    def _build_menu(self):
        hk_str = kl.format_hotkey(self.config["hotkey"])
        to_s   = self.config["timeout"]
        return Menu(
            Item(
                f"立即锁定（{hk_str} 解锁 / {to_s}s 超时）",
                self.start_lock,
                default=True,
                enabled=lambda item: not self.is_locked,
            ),
            Menu.SEPARATOR,
            Item("设置超时时间...", self.open_timeout_dialog),
            Item(
                "空闲自动上锁",
                self.toggle_auto_lock,
                checked=lambda item: bool(self.config.get("auto_lock_enabled", False)),
            ),
            Item("设置自动上锁...", self.open_auto_lock_dialog),
            Item(
                "看视频时不自动上锁",
                self.toggle_auto_lock_video_exempt,
                checked=lambda item: bool(self.config.get("auto_lock_video_exempt", True)),
            ),
            Item(
                "蓝牙离开范围自动上锁（预留，暂未接入）",
                lambda icon, item: None,
                enabled=False,
            ),
            Item("设置解锁快捷键...", self.open_hotkey_dialog),

            Item(
                "设置解锁密码..." if not self.config.get("password") else "修改/清除解锁密码...",
                self.open_password_dialog,
            ),

            Item(
                "解锁后触发 Windows 锁屏",
                self.toggle_lock_workstation,
                checked=lambda item: bool(self.config.get("lock_workstation_after_unlock", False)),
            ),
            Item(
                "Ctrl+Alt+Del 返回需密码（严格模式）",
                self.toggle_strict_mode,
                checked=lambda item: bool(self.config.get("strict_mode", False)),
            ),
            Item(
                "开机启动",
                self.toggle_autostart,
                checked=lambda item: autostart.is_enabled(),
            ),
            Menu.SEPARATOR,
            Item("查看日志", self.open_log_file),
            Item("检查更新...", self.manual_check_update),
            Item(
                "自动检查更新",
                self.toggle_auto_check_update,
                checked=lambda item: bool(self.config.get("auto_check_update", True)),
            ),
            Item("关于", self.show_about),
            Item("退出", self.quit_app),
        )

    def _update_icon(self):
        if self.icon:
            try:
                self.icon.icon = make_icon_image(locked=self.is_locked)
                self.icon.title = f"{APP_NAME} v{__version__} - {'已锁定' if self.is_locked else '就绪'}"
            except Exception:
                pass

    # ---- 启动 ----
    def run(self):
        self.icon = pystray.Icon(
            APP_NAME,
            icon=make_icon_image(locked=False),
            title=f"{APP_NAME} v{__version__} - 就绪",
            menu=self._build_menu(),
        )

        # pystray 在 Windows 上必须跑在主线程；tk 的 mainloop 也要跑。
        # 方案：把 pystray 放在后台线程，主线程跑 tk mainloop。
        threading.Thread(target=self.icon.run, daemon=True).start()
        self.tk_root.mainloop()


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _acquire_single_instance_mutex():
    """返回 (mutex_handle, acquired)。acquired=False 表示已有实例在运行。"""
    # CreateMutexW: 若同名 mutex 已存在，返回句柄且 GetLastError=ERROR_ALREADY_EXISTS(183)
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    if not handle:
        return None, True  # 互斥创建失败时不阻止启动，避免误伤

    already_exists = (ctypes.windll.kernel32.GetLastError() == 183)
    if already_exists:
        ctypes.windll.kernel32.CloseHandle(handle)
        return None, False
    return handle, True


def _release_single_instance_mutex(handle):
    if handle:
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


def main():

    if sys.platform != "win32":
        print("此工具仅支持 Windows。")
        sys.exit(1)

    mutex_handle, acquired = _acquire_single_instance_mutex()
    if not acquired:
        L.warning("检测到已有实例运行，本次启动已忽略")
        return

    admin = _is_admin()
    L.info("=" * 50)
    L.info("托盘程序启动 | 版本=%s | PID=%d | 管理员=%s | 日志=%s", __version__, os.getpid(), admin, L.get_log_path())
    if not admin:
        L.warning("当前不是管理员权限，部分高权限窗口的输入可能无法拦截")
    # 如果上次异常退出，可能留下未恢复的"严格模式"注册表状态 → 启动时清理
    try:
        strict_mode.recover_if_needed()
    except Exception:
        L.exception("严格模式恢复检查失败")

    # 捕获未处理异常
    def _on_unhandled(exctype, value, tb):
        L.error("未处理的异常", exc_info=(exctype, value, tb))
    sys.excepthook = _on_unhandled

    try:
        TrayApp().run()
    except Exception:
        L.exception("托盘主循环异常退出")
        raise
    finally:
        _release_single_instance_mutex(mutex_handle)



if __name__ == "__main__":
    main()
