# -*- coding: utf-8 -*-
"""配置读写：config.json 位于程序根目录。"""
import copy
import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(APP_DIR)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STICKERS_DIR = os.path.join(BASE_DIR, "stickers")
AVATARS_DIR = os.path.join(BASE_DIR, "avatars")
CONVERSATIONS_PATH = os.path.join(BASE_DIR, "conversations.json")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

DEFAULT_TARGETS = [
    {"name": "好友A", "kind": "friend", "enabled": True, "times": ["09:30"], "text": "早安 🔥",
     "sticker_id": None, "conv_id": None, "sec_uid": None},
    {"name": "好友B", "kind": "friend", "enabled": True, "times": ["09:30"], "text": "今天也要开心",
     "sticker_id": None, "conv_id": None, "sec_uid": None},
    {"name": "好友C", "kind": "friend", "enabled": True, "times": ["09:30"], "text": "🔥",
     "sticker_id": None, "conv_id": None, "sec_uid": None},
    {"name": "好友D", "kind": "friend", "enabled": True, "times": ["09:30"], "text": "打卡",
     "sticker_id": None, "conv_id": None, "sec_uid": None},
    {"name": "我的群", "kind": "group", "enabled": True, "times": ["09:30"], "text": "每日打卡",
     "sticker_id": None, "conv_id": None, "sec_uid": None},
]

# 支持的浏览器：都是 Chromium 内核，选择器/登录态格式/沙箱参数完全一致。
# Firefox 不在列：Playwright 驱动它必须用它自己那份打过补丁的构建（连系统装的 Firefox 都不认），
# 需要联网 `playwright install firefox` 下载约 90MB。实测三种写法都报
# "Executable doesn't exist at ...ms-playwrightirefox-<ver>irefox\..."。
BROWSER_CANDIDATES = [
    ("msedge", "Microsoft Edge", [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]),
    ("chrome", "Google Chrome", [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]),
    ("chrome-beta", "Chrome Beta", [
        r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome Beta\Application\chrome.exe",
    ]),
]

BROWSER_LABELS = {c: label for c, label, _ in BROWSER_CANDIDATES}


def detect_browsers():
    """本机装了哪些可用浏览器，返回 [(channel, label, exe路径)]，顺序即优先级（Edge 优先）。"""
    out = []
    for channel, label, paths in BROWSER_CANDIDATES:
        for p in paths:
            if os.path.exists(p):
                out.append((channel, label, p))
                break
    return out


DEFAULT_CONFIG = {
    "browser": {
        # auto = 自动挑一个装了的（优先 Edge，其次 Chrome）
        "channel": "auto",
        "user_data_dir": "browser_data",
        "headless": False,
        # 窗口怎么显示：minimized=最小化到任务栏（默认）/ normal=正常弹出来 / hidden=完全不显示窗口
        "window_mode": "minimized",
        "window_size": None,
        "left_panel_width": None,
        "home_url": "https://www.douyin.com/",
        "chat_url": "https://www.douyin.com/chat",
        "page_load_timeout_ms": 45000,
        "action_timeout_ms": 15000,
        "login_wait_seconds": 300,
    },
    "schedule": {
        "random_jitter_seconds": 90,
        # 错过时间点多少分钟内还补发；超过就跳过（避免"一开机就把今天过点的全发一遍"）
        "miss_grace_minutes": 5,
        "target_interval_seconds": [2, 6],
        "human_like_typing": True,
        "type_char_delay_ms": [50, 140],
        "pause_before_send_ms": [300, 900],
        "retry_times": 1,
        # 演练模式：只输入、只定位，不真的发出任何消息（用于安全自测）
        "dry_run": False,
        # 已经发过的"日期|时间"（同一天同一时间点只发一次，重启也不重复）
        "sent_log": {"date": "", "keys": []},
    },
    "targets": DEFAULT_TARGETS,
    "selectors": {},
    # 界面外观（浅色 / 深色）
    "ui": {"theme": "light",
           # 开机自启（真正的开关在注册表 HKCU\...\Run，这里只是记住上次的状态）
           "autostart": False,
           # 开机自启那一次启动时，要不要顺带把"自动模式"跑起来（不然每次开机还得右键点一下）
           "auto_sched_on_boot": True,
            "boot_catchup": True,
           # 读取抖音列表时：只保留"有火花图标"的会话（认证账号/陌生人没有火花，读进来也没用）
           "only_spark": True,
           # 点窗口右上角 X 时：收起成托盘图标（后台继续跑）还是直接退出
           "close_to_tray": True,
           # 上次验证到的登录状态 {ok: bool, at: "YYYY-mm-dd HH:MM:SS"}
           "login_state": None},
}


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    if not isinstance(override, dict):
        return out
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def normalize(cfg):
    """补齐缺失字段、纠正明显错误的值。"""
    raw = cfg if isinstance(cfg, dict) else {}
    raw_br = raw.get("browser") if isinstance(raw.get("browser"), dict) else {}
    cfg = _deep_merge(DEFAULT_CONFIG, raw)

    sched = cfg["schedule"]
    if not isinstance(sched.get("target_interval_seconds"), (list, tuple)) or len(sched["target_interval_seconds"]) != 2:
        sched["target_interval_seconds"] = [2, 6]
    for key in ("type_char_delay_ms", "pause_before_send_ms"):
        v = sched.get(key)
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            sched[key] = DEFAULT_CONFIG["schedule"][key]
    sched["dry_run"] = bool(sched.get("dry_run", False))
    try:
        # 这个值已经不给用户填了：留 5 分钟只用来吸收轮询延迟，错过的一律交给"启动时核对"
        sched["miss_grace_minutes"] = max(5, int(sched.get("miss_grace_minutes", 5)))
    except Exception:
        sched["miss_grace_minutes"] = 5
    sl = sched.get("sent_log")
    if not isinstance(sl, dict):
        sl = {"date": "", "keys": []}
    sl["date"] = str(sl.get("date") or "")
    sl["keys"] = [str(k) for k in (sl.get("keys") or [])][:200]
    sched["sent_log"] = sl

    br = cfg["browser"]
    ch = str(br.get("channel") or "").strip()
    br["channel"] = ch if ch else "auto"
    # 老配置没写 window_mode：按 headless 推断（headless=true 就是"完全隐藏"）
    wm = str(raw_br.get("window_mode") or "").strip().lower()   # 只看用户写的，缺省按下面推断
    if wm not in ("minimized", "normal", "hidden"):
        wm = "hidden" if br.get("headless") else "minimized"
    br["window_mode"] = wm

    ui = cfg.get("ui")
    if not isinstance(ui, dict):
        ui = {}
    theme = str(ui.get("theme") or "").strip().lower()
    ui["theme"] = theme if theme in ("light", "dark") else "light"
    ui["close_to_tray"] = bool(ui.get("close_to_tray", True))
    ui["autostart"] = bool(ui.get("autostart", False))
    ui["auto_sched_on_boot"] = bool(ui.get("auto_sched_on_boot", True))
    ui["boot_catchup"] = bool(ui.get("boot_catchup", True))
    ui["only_spark"] = bool(ui.get("only_spark", True))
    ls = ui.get("login_state")
    ui["login_state"] = ls if isinstance(ls, dict) else None
    cfg["ui"] = ui

    targets = cfg.get("targets")
    if not isinstance(targets, list):
        targets = copy.deepcopy(DEFAULT_TARGETS)
    cleaned = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        times = t.get("times")
        if not isinstance(times, list):
            times = ["09:30"]
        times = [str(x).strip() for x in times if str(x).strip()]
        cleaned.append({
            "name": name,
            "kind": "group" if str(t.get("kind", "friend")) == "group" else "friend",
            "enabled": bool(t.get("enabled", True)),
            "times": times or ["09:30"],
            "text": str(t.get("text") or ""),
            "sticker_id": t.get("sticker_id") or None,
            "image_path": (str(t.get("image_path")).strip() if t.get("image_path") else None),
            # 稳定身份：从抖音会话列表读到的会话 id（好友/群聊都有）与好友的 sec_uid。
            # 老配置没有这两项也没关系，运行时按名字匹配并能自动回填。
            "conv_id": (str(t.get("conv_id")).strip() or None) if t.get("conv_id") else None,
            "sec_uid": (str(t.get("sec_uid")).strip() or None) if t.get("sec_uid") else None,
        })
    cfg["targets"] = cleaned
    return cfg


def load():
    if not os.path.exists(CONFIG_PATH):
        cfg = normalize({})
        save(cfg)
        return cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        # 配置损坏：备份后重建
        try:
            os.replace(CONFIG_PATH, CONFIG_PATH + ".broken")
        except OSError:
            pass
        cfg = normalize({})
        save(cfg)
        return cfg
    return normalize(raw)


def save(cfg):
    cfg = normalize(cfg)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    return cfg


def user_data_dir(cfg):
    d = cfg["browser"]["user_data_dir"]
    return d if os.path.isabs(d) else os.path.join(BASE_DIR, d)


def stickers_path():
    return os.path.join(BASE_DIR, "stickers.json")


def load_stickers():
    p = stickers_path()
    if not os.path.exists(p):
        return {"updated_at": "", "items": []}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return {"updated_at": "", "items": []}
        return data
    except Exception:
        return {"updated_at": "", "items": []}


def save_stickers(data):
    tmp = stickers_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, stickers_path())


def load_conversations():
    """上次读到的会话列表缓存（关掉程序再打开还在）。"""
    p = CONVERSATIONS_PATH
    if not os.path.exists(p):
        return {"updated_at": "", "rows": []}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
            return {"updated_at": "", "rows": []}
        return data
    except Exception:
        return {"updated_at": "", "rows": []}


def save_conversations(data):
    tmp = CONVERSATIONS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONVERSATIONS_PATH)
