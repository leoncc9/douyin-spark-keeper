# 抖音续火花（Douyin Spark Keeper）

一个 Windows 桌面小工具：自动给抖音好友 / 群聊发消息，帮你把**火花**续上。

界面用 tkinter 手写（不依赖任何 GUI 框架），浏览器自动化用 Playwright 驱动**你本机已有的 Edge / Chrome**——程序不会去下载浏览器内核，也不会上传任何东西。

## 直接下载就能用（推荐）

👉 **[点这里下载完整版 v2.25](https://github.com/leoncc9/douyin-spark-keeper/releases/latest)**

下载 zip → 解压到任意目录 → 双击 `DouyinSparkKeeper.exe`，**不需要装 Python**（包里内置了运行环境，解压后约 160MB）。

首次使用：

1. 点「扫码登录」——用手机抖音扫一次码（登录态只存在本机 `browser_data/`，不会外传）
2. 点「同步账户表情」——把你账号里的表情拉下来备用
3. 点「读取抖音列表」——右边面板列出你的好友 / 群聊
4. **双击右边某个好友** → 加进左边的目标列表
5. **双击左边那一行** → 设置「发什么」和「几点发」
6. 点「一键续火花」立刻试一轮；点「启动自动模式」交给它每天自己跑

## 它能干什么

- **读取会话列表**：一键拉取你抖音里的好友 / 群聊，显示头像、昵称、聊天天数、今日火花状态
- **目标管理**：每个目标可以单独设置好友 / 群聊、发送时间（支持多个时间点，如 `09:30, 21:00`）、发送内容（文字 / 超级表情 / 本地图片）
- **一键续火花**：立刻给所有启用的目标发一轮
- **自动模式**：每天按设定时间自动发送。逐字输入、目标之间随机间隔、计划时间随机抖动，尽量不像机器人
- **开机自启 + 系统托盘**：开机后直接待在托盘里按计划跑；关窗口是收进托盘，右键托盘图标可打开或退出
- **不会重复补发**：开机 / 启动自动模式时先**只读**看一眼抖音里今天续没续（只看火苗图标，不点进会话），只有真没续的目标才补一轮；已经发过的目标在发送前会被再确认一次并跳过
- **演练模式**：只输入、只定位、绝不回车，换电脑或抖音改版后可以先自测
- **单实例保护、深浅色主题、自绘通知、日志面板**

## 从源码跑（开发者）

要求：Windows 10 / 11（64 位），系统里已经装了 **Microsoft Edge 或 Google Chrome**。

仓库里**只有源码**，不含 `runtime/`（内置 Python 运行时约 160MB，放在 Release 的完整包里）。

```bat
pip install playwright
python app\main.py            :: 直接跑
python app\main.py --selftest :: 自检，不打开浏览器
```

```
DouyinSparkKeeper.exe     双击启动（需要同目录有 runtime/，即完整版）
调试启动.bat                带控制台启动，排查问题用
```

更详细的逐步说明看 **[使用说明.md](使用说明.md)**（程序右上角「操作说明」按钮里也是同一份）。

## 源码结构

| 路径 | 作用 |
|---|---|
| `app/main.py` | 入口（支持 `--selftest` 自检、`--tray` 直接后台启动） |
| `app/gui.py` | 全部界面：主窗口、弹窗、托盘菜单、通知、设置、计划调度 |
| `app/douyin.py` | 浏览器自动化：登录、读会话、发送、只读查火花状态 |
| `app/config.py` | 配置读写、默认值与归一化 |
| `app/tray.py` | Win32 系统托盘（ctypes 直调 Shell_NotifyIcon，无第三方依赖） |
| `app/autostart.py` | 开机自启（写注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`） |
| `app/single_instance.py` | 单实例互斥体（重复双击不会开出第二个） |
| `app/logger.py` | 按月写 `logs/keeper_YYYYMM.log` |
| `打包分发.ps1` | 生成一份剔除了个人数据、可直接发给朋友的完整副本 |
| `使用说明.md` | 面向使用者的说明 |

## 打包给朋友

```powershell
powershell -ExecutionPolicy Bypass -File 打包分发.ps1
```

会生成 `抖音续火花_分发版/`：复制 `app/` + `runtime/`，自动剔除 `browser_data/`、`config.json`（连同 `config.json.bak-*` 备份）、`conversations.json`、`stickers.json`、`logs/`、`avatars/`、`stickers/` 等个人数据，再写一份默认配置（占位好友 A/B/C/D）。收到的人解压双击就能用。

## 关于隐私

仓库里**不含**任何账号数据或本机产物，它们都在 `.gitignore` 里：

`browser_data/`（登录态 cookie）、`config.json` 及 `config.json.bak-*`、`conversations.json`、`stickers.json`、`logs/`、`avatars/`、`stickers/`、`runtime/`。

首次运行时会自动生成一份默认 `config.json`。Release 里的完整包同样已剔除上述数据，你登录的是你自己的账号。

## 注意

- 本工具会驱动你自己的浏览器、用你自己的登录态给好友发消息，**使用风险请自行判断**。
- 建议不要把目标间隔调得太小（默认 3~12 秒），发送频率过高可能触发平台风控。
- 仅供个人学习与自用，请勿用于骚扰他人或任何商业用途。
