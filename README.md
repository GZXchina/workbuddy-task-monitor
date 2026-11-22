# WorkBuddy 任务状态主动推送（Task Monitor）

监控 WorkBuddy 桌面端任务列表中的所有任务，在任务完成、异常终止、等待用户确认、疑似卡住时，主动推送通知到微信，微信额度用尽或推送失败时自动降级为邮件通知。

## 项目简介

WorkBuddy 桌面端会在本地 SQLite 数据库中记录每个会话任务的状态（运行中 / 已完成 / 终止等）。本项目是一个零第三方依赖的常驻 Python 小程序，通过**只读**方式轮询该数据库，识别任务状态变化，并通过 Server 酱 Turbo 推送到微信；当日微信额度用尽或推送失败时，自动改用 SMTP 邮件发送，确保通知不中断。

- **零侵入**：只读数据库，不修改 WorkBuddy 任何数据与配置
- **零依赖**：仅用 Python 标准库（sqlite3 / urllib / smtplib）
- **双通道**：微信为主、邮件兜底
- **可视化面板**：双击即开，实时看到运行状态，一键开关监控
- **防轰炸**：状态指纹去重 + 冷却时间 + 每日限额

## 主要功能

| 功能 | 说明 |
|---|---|
| 任务完成通知 | 任务由运行中变为 completed 时推送 |
| 异常终止通知 | 任务变为 terminated / failed / cancelled 时推送 |
| 待确认提醒 | 检测到新的等待用户确认/授权记录时推送，并附上待确认内容 |
| 卡住提醒 | 任务仍在运行但超过设定时长（默认 10 分钟）无活动时提醒，带冷却时间 |
| 双通道推送 | Server 酱 Turbo → 微信；额度用尽或失败时自动降级 SMTP 邮件 |
| 去重与限额 | 仅在状态真正变化时推送，每日微信发送上限可配 |
| 单实例锁 | 本地端口加锁，避免重复启动多个监控进程 |
| WorkBuddy 进程感知 | 自动检测 WorkBuddy 是否运行：未启动时静默待机（不读数据库、不刷错误日志），一启动立即恢复巡检 |
| 可视化面板 | 本地网页控制台：打开即检测监控是否在运行，实时展示状态，一键开启/关闭、立即巡检、测试推送、开关开机自启 |
| 日志追溯 | 全程写入 monitor.log，便于排查 |

## 安装与运行步骤

### 环境要求

- Windows 10/11（其他系统亦可运行，开机自启部分需自行适配）
- Python 3.8+（无需安装任何第三方包）
- WorkBuddy 桌面端（提供状态数据库）

### 步骤

**方式一：一键部署（推荐，Windows）**

1. 克隆或下载本项目到本地任意目录。
2. 在项目目录打开 PowerShell，执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```

3. 首次运行会自动生成 `task_monitor_config.json` 并用记事本打开，按提示填写 `sendkey` 后保存退出，**再次运行同一命令**即可完成部署（自动注册计划任务并启动）。
4. 想验证是否成功，可执行：

   ```powershell
   python task_monitor.py --self-test
   ```

**方式二：手动部署**

1. 下载或克隆本项目到本地任意目录。
2. 复制示例配置并重命名：

   ```bash
   copy task_monitor_config.example.json task_monitor_config.json
   ```

3. 编辑 `task_monitor_config.json`，至少填写：
   - `sendkey`：Server 酱 Turbo 的 SendKey
   - `email.smtp_user` / `email.smtp_pass` / `email.mail_to`（如需邮件兜底）

4. 自检（读取任务状态并发送测试通知，不改动状态文件）：

   ```bash
   python task_monitor.py --self-test
   ```

5. 仅测试邮件通道：

   ```bash
   python task_monitor.py --test-email
   ```

6. 常驻运行：

   ```bash
   python task_monitor.py
   ```

   脚本启动后会按 `poll_seconds` 循环巡检。

### 在另一台电脑上部署

计划任务是绑定到具体电脑的系统配置，不会随代码复制过去，所以新电脑需要部署一次。流程已尽量简化：

1. 安装 Python 3.8+（官网安装包，勾选 **Add Python to PATH**），不需要装任何第三方包。
2. 把整个项目文件夹复制过去（或用 `git clone`）。
3. 双击 `启动面板.bat`，面板里点「开启监控」；如果还没配 SendKey，面板会直接帮你打开配置文件。
4. 在面板底部把「开机自启」打开，就完成部署了。

前提：新电脑装有 WorkBuddy 桌面端，且至少启动过一次（生成状态数据库）。如果 WorkBuddy 数据库不在默认位置（`用户目录\.workbuddy\workbuddy.db`），在配置里填 `db_path` 指向实际路径。

> 也可以用命令行方式：`powershell -ExecutionPolicy Bypass -File .\setup.ps1`。

注意：`sendkey` 是个人凭据，不要提交到任何仓库；每台电脑各自保存一份本地配置即可（可用同一个 SendKey，也可在 Server 酱后台建多个）。

### 配置开机自启（Windows）

用「任务计划程序」创建一个触发器为「登录时」的任务，操作指向：

- 程序：`pythonw.exe` 的完整路径
- 参数：`task_monitor.py` 的完整路径

这样登录后静默启动、不弹黑窗。程序内置单实例锁，重复启动不会产生多个实例。

**建议再补一条守护触发**：在同一个任务里增加「每 5 分钟重复一次」的触发器。程序启动时会先抢单实例锁，已在运行则立即退出，因此不会重复；进程若意外退出，最多 5 分钟内会被自动拉起。

**自动跟随 WorkBuddy**：程序每一轮都会检测 `WorkBuddy.exe` 是否在运行，未启动时进入低耗待机（每 30 秒轻量检测，不读数据库、不写错误日志），检测到启动后立即恢复巡检。所以不需要在 WorkBuddy 里做任何配置——打开 WorkBuddy 开始干活，监控自然就进入工作状态。

## 可视化面板

双击 **`启动面板.bat`**，会自动在浏览器打开 `http://127.0.0.1:47832` 的本地控制台（也可以直接运行 `python monitor_panel.py`）。

面板一打开就会自动检测后台监控程序是否在运行，并每 2 秒刷新一次：

| 区域 | 内容 |
|---|---|
| 顶部状态 | 运行中 / 待机中 / 已暂停 / 未运行，附进程 PID |
| 状态卡片 | WorkBuddy 是否在跑、已监控任务数、今日推送量、最后巡检时间、运行时长、邮件兜底是否就绪 |
| 主按钮 | **开启监控** / **关闭监控** / 立即巡检 / 发送测试通知 |
| 参数标签 | 巡检间隔、卡住阈值、每日上限、SendKey 与邮件配置状态 |
| 最近动态 | 实时日志，成功/失败/暂停按颜色区分 |
| 底部 | 打开配置文件、查看日志、打开目录、开机自启开关、关闭面板 |

几个行为说明：

- **关闭监控** = 让后台程序暂停巡检（不读任务、不发通知），进程仍在后台待命，点「开启监控」约 2 秒内恢复。这样既不用杀进程，也不会被开机自启的守护触发器重新拉起。
- **开启监控** 会自动检查配置：SendKey 没填时直接帮你打开配置文件。
- **开机自启** 开关会直接增删 Windows 计划任务 `WorkBuddyTaskMonitor`，不需要再手动跑脚本。
- 面板只监听 `127.0.0.1`，仅本机可访问；关闭浏览器标签页后，面板服务会在 2 分钟内自动退出，不留后台进程。

面板的实现全部使用 Python 标准库（`http.server`），不需要安装 Flask 等任何框架，也不需要浏览器插件。


## 配置说明

配置文件：`task_monitor_config.json`（由 `task_monitor_config.example.json` 复制而来，已被 .gitignore 排除，不会提交）。

| 字段 | 类型 | 说明 | 默认 |
|---|---|---|---|
| `sendkey` | string | Server 酱 Turbo 的 SendKey，形如 `SCT` 开头 | 空 |
| `db_path` | string | WorkBuddy 数据库路径；留空则用 `~/.workbuddy/workbuddy.db` | 空 |
| `poll_seconds` | int | 巡检间隔（秒） | 60 |
| `stuck_minutes` | int | 超过多少分钟无活动判定为疑似卡住 | 10 |
| `stuck_cooldown_minutes` | int | 卡住提醒的冷却时间（分钟） | 30 |
| `daily_limit` | int | 每日微信推送条数上限 | 4 |
| `exclude_titles` | array | 按标题关键词排除的任务（如监控程序自身所在对话） | [] |
| `exclude_cwd` | array | 按工作目录关键词排除的任务 | [] |
| `email.enabled` | bool | 是否启用邮件兜底 | true |
| `email.smtp_host` | string | SMTP 服务器地址 | smtp.qq.com |
| `email.smtp_port` | int | SMTP 端口 | 465 |
| `email.use_ssl` | bool | 是否使用 SSL（465 端口为 true，587 端口为 false） | true |
| `email.smtp_user` | string | 发件邮箱地址 | 空 |
| `email.smtp_pass` | string | SMTP 授权码（不是登录密码） | 空 |
| `email.mail_to` | string | 收件邮箱地址 | 空 |
| `email.sender_name` | string | 发件人显示名 | 任务监控助手 |

### 获取 Server 酱 SendKey

1. 打开 `https://sct.ftqq.com`，用接收通知的微信扫码登录。
2. 关注「方糖」公众号并绑定。
3. 在「SendKey」页面复制以 `SCT` 开头的密钥。

### 获取 QQ 邮箱 SMTP 授权码

1. 登录 QQ 邮箱网页版 → 设置 → 账户。
2. 开启「IMAP/SMTP 服务」，按提示完成短信验证。
3. 获得 16 位授权码，填入 `email.smtp_pass`。

> 163、Gmail 等邮箱同理：开启 SMTP 服务后生成授权码。

## 使用方法

日常用**面板**就够了：双击 `启动面板.bat`，在里面开关和看状态。命令行方式适合排查问题：

```bash
# 打开可视化面板（本地网页控制台）
python monitor_panel.py

# 常驻监控（默认）
python task_monitor.py

# 只跑一轮巡检
python task_monitor.py --once

# 自检：打印当前所有任务状态，并发送微信与邮件测试通知（不改动状态文件）
python task_monitor.py --self-test

# 仅测试邮件通道
python task_monitor.py --test-email
```

运行后无需干预。通知示例：

- `✅ 完成｜某任务` — 任务已完成
- `⚠️ 异常｜某任务` — 任务异常终止
- `🙋 待拍板｜某任务` — 任务在等待你确认
- `⏳ 卡住｜某任务` — 任务超过阈值无进展

### 日常运维

- 看状态 / 开关监控：双击 `启动面板.bat`
- 查看日志：`monitor.log`（面板里也能直接打开）
- 调整参数：编辑 `task_monitor_config.json`，下一轮巡检生效
- 状态文件：`task-monitor-state.json`（删除后重建基线，历史任务不会被误报）
- 重启监控：面板里「关闭监控」再「开启监控」，或在任务计划程序里重启对应任务

## 目录结构

```
.
├── task_monitor.py                    # 主程序（零第三方依赖）
├── monitor_panel.py                   # 可视化面板（本地网页控制台，标准库实现）
├── 启动面板.bat                        # 双击打开面板
├── setup.ps1                          # Windows 一键部署脚本（检查环境/生成配置/注册自启）
├── task_monitor_config.example.json   # 配置示例（复制为 task_monitor_config.json 后填写）
├── .gitignore                         # 排除敏感配置与运行时产物
├── README.md                          # 本文档
├── LICENSE                            # MIT
└── （运行时生成，不提交）
    ├── task_monitor_config.json       # 实际配置（含密钥，绝不提交）
    ├── task-monitor-state.json        # 任务状态快照
    ├── monitor_status.json            # 心跳与实时状态（面板读取）
    ├── monitor_control.json           # 面板下发的暂停/立即巡检指令
    └── monitor.log                    # 运行日志
```

## 注意事项

- **关闭监控的含义**：面板上的「关闭监控」是让后台程序暂停巡检（写入 `monitor_control.json` 的 `paused`），不是杀进程。这样不会被开机自启的守护触发器重新拉起，恢复也只需要一次点击。
- **只读数据库**：程序以 `mode=ro` 只读方式打开数据库，不会修改 WorkBuddy 任何数据。
- **自动跟随 WorkBuddy**：WorkBuddy 未运行时程序自动待机，不产生错误日志；启动后自动恢复巡检。无需手动干预。
- **卡住判定是启发式**：基于「长时间无活动」，长时间计算的正常任务也可能被提醒，阈值可调。
- **待确认识别的粒度**：依赖 WorkBuddy 是否将等待确认记录写入 `session_turn_interruptions` 表；若不写入则无法提醒。
- **凭据安全**：SendKey、SMTP 授权码仅保存在本机 `task_monitor_config.json`，该文件已被 .gitignore 排除。请勿将其提交到任何版本库。
- **免费额度**：Server 酱免费版每日 5 条，程序默认上限 4 条留余量；超额自动转邮件。
- **邮件可能进垃圾箱**：首次收到通知请标记为「非垃圾邮件」。
- **休眠/关机不运行**：电脑休眠或关机期间程序不工作，恢复后按当前状态判定。
- **数据库结构依赖**：读取 `sessions` 与 `session_turn_interruptions` 两张表；若 WorkBuddy 版本升级导致表结构变化，可能需要适配。

## 许可

MIT License，详见 LICENSE 文件。
