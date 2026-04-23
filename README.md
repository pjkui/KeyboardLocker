# KeyboardLocker - 键盘鼠标锁定工具

一个轻量级 Windows 工具，锁住键盘和鼠标输入，**屏幕仍然正常显示**（适合演示、视频播放时防止别人误操作）。

> 仓库地址：https://github.com/pjkui/KeyboardLocker

支持两种使用方式：
- **命令行版**：`keyboard_lock.py`（零第三方依赖）
- **系统托盘版**：`tray_app.py`（图形化菜单，可配置）

## 快速开始

```bash
git clone https://github.com/pjkui/KeyboardLocker.git
cd KeyboardLocker
# 首次安装依赖
install_deps.bat
# 启动托盘版
run_tray_as_admin.bat
```

## 下载预编译 EXE

每次推送到 `main` 分支，GitHub Actions 都会自动打包一份 Windows x64 exe，可在仓库的 [Actions](https://github.com/pjkui/KeyboardLocker/actions) 页面下载 artifact（7 天保留）。

正式版本请在 [Releases](https://github.com/pjkui/KeyboardLocker/releases) 页面下载：
- `KeyboardLocker.exe` — 托盘版（自带 UAC 管理员请求，双击即可）
- `keyboard_lock.exe` — 命令行版

> 发布新版本：打一个 `v1.2.3` 格式的 tag 即可自动触发构建并创建 Release。
>
> ```bash
> git tag v1.0.0 && git push origin v1.0.0
> ```

## 功能

- 拦截所有键盘/鼠标输入，屏幕/视频照常刷新
- 默认快捷键 `Ctrl+Alt+Win+K` 解锁，可在托盘中修改
- 默认 60 秒后自动解锁，可在托盘中修改
- 支持**空闲自动上锁**（可设置阈值，默认支持键盘/鼠标/XInput 手柄活动检测）
- 支持“**看视频时不自动上锁**”（前台视频白名单进程 + 全屏场景）
- 预留“蓝牙离开范围自动上锁”开关（后续可接入蓝牙模块）
- 托盘图标：蓝色=就绪，红色=已锁定
- 防重复启动：托盘版仅允许单实例运行，重复启动会自动忽略



---

## 一、系统托盘版（推荐）

### 1. 安装依赖（首次）

双击 `install_deps.bat`，或手动执行：
```bash
pip install -r requirements.txt
```

### 2. 启动

双击 `run_tray_as_admin.bat`，同意 UAC 弹窗。

右键或左键点击托盘图标，菜单包含：

| 菜单项 | 说明 |
| --- | --- |
| 立即锁定 | 使用当前设置开始锁定（默认有 3 秒预留时间） |
| 设置超时时间... | 自动解锁秒数（3 ~ 3600） |
| 空闲自动上锁 | 开/关后台空闲检测，达到阈值自动上锁 |
| 设置自动上锁... | 设置空闲阈值（5 ~ 7200 秒）与“视频播放豁免” |
| 看视频时不自动上锁 | 开/关视频豁免（前台白名单视频进程 + 全屏） |
| 蓝牙离开范围自动上锁（预留） | 当前仅预留入口，待后续接入蓝牙模块 |
| 设置解锁快捷键... | 自由勾选 Ctrl/Alt/Shift/Win + 选择主键 A~Z/0~9/F1~F12 |

| 设置解锁密码... | 设置/修改/清除解锁密码。设密码后，按快捷键会弹出密码框，输入正确才真正解锁。留空新密码即清除。**超时自动解锁不需要密码** |
| 解锁后触发 Windows 锁屏 | 勾选后，无论是快捷键解锁还是超时解锁，都会自动进入 Windows 锁屏（等价于 Win+L） |
| Ctrl+Alt+Del 返回需密码（严格模式） | 需先设置解锁密码。开启后：用户若按过 Ctrl+Alt+Del（哪怕只是打开又点取消），返回桌面时会弹出密码框，必须输入正确密码才能解锁。不会修改任何系统设置 |
| 开机启动 | 勾选后，开机登录时自动启动托盘。以管理员身份启用时会使用**任务计划程序**（免 UAC、静默启动、管理员权限）；普通权限启用时退回注册表 `Run` 键 |
| 查看日志 | 打开日志文件 `logs/keyboard_lock.log`。日志按天滚动、保留 7 天 |
| 关于 | 显示当前配置 |
| 退出 | 退出托盘程序 |

配置会自动保存到 `config.json`，下次启动沿用。

---

## 二、命令行版

在**管理员** cmd 中：

```bash
python keyboard_lock.py                    # 默认 60 秒后解锁
python keyboard_lock.py --timeout 120      # 120 秒后解锁
python keyboard_lock.py --delay 5          # 启动前等 5 秒再锁定
```

或双击 `run_as_admin.bat`。

---

## 解锁方式

| 方式 | 说明 |
| --- | --- |
| 设定的快捷键（默认 `Ctrl+Alt+Win+K`） | 按下后：若设了密码，需在弹出的密码框中输入正确密码；否则立即解锁 |
| 等超时时间 | 自动解锁（**不需要密码**） |
| `Ctrl+Alt+Del` | 系统级兜底，任何程序都拦不住 |

---

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `keyboard_lock.py` | 锁定核心（命令行入口 + 可被导入） |
| `tray_app.py` | 托盘版入口（GUI 配置） |
| `config.json` | 托盘版的配置（自动生成） |
| `install_deps.bat` | 安装 Python 依赖 |
| `run_tray_as_admin.bat` | 以管理员启动托盘版 |
| `run_as_admin.bat` | 以管理员启动命令行版 |
| `requirements.txt` | 依赖清单 |

## 已知限制

1. **Ctrl+Alt+Del 无法拦截**：由 Windows 安全桌面处理，这是系统级设计，也是兜底方案。
2. **UAC 弹窗期间钩子暂时失效**：UAC 对话框在 Secure Desktop 上。
3. **部分使用 DirectInput / Raw Input 的全屏游戏** 可能绕过低级钩子。
4. 必须**以管理员权限**运行，否则高权限窗口的输入无法被拦截。
