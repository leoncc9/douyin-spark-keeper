# -*- coding: utf-8 -*-
"""Tkinter 界面 + 后台 asyncio 调度。"""
import asyncio
import binascii
import datetime
import os
import queue
import random
import re
import socket
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

import autostart
import config
import logger
import tray as tray_mod
import version
from douyin import (IMAGE_EXTS, BrowserClosedError, DouyinClient, avatar_stem,
                    friendly_error, is_browser_closed)

FONT = ("Microsoft YaHei", 10)
FONT_B = ("Microsoft YaHei", 10, "bold")
FONT_TITLE = ("Microsoft YaHei", 13, "bold")
FONT_S = ("Microsoft YaHei", 8)

# ---------------------------------------------------------------- 配色（浅色 Web 后台风 / 深色仿抖音）
# 两套主题。真正的"当前颜色"就是下面这批模块级变量（Tk 代码里到处在用），
# 所以"切主题"= 换这批变量 → 重刷 ttk 样式 → 递归重刷控件树（见 SparkKeeperApp._set_theme）。
PALETTES = {
    "light": {
        # 左边导航栏=纯白，右边内容区=浅灰（白卡片浮在灰底上，和参考图一致）
        "BG_MAIN": "#e9ecf4", "BG_SIDE": "#ffffff", "BG_CARD": "#ffffff", "BG_PANEL": "#ffffff",
        "BG_INPUT": "#f4f5f9", "BG_HEAD": "#f7f8fb", "BG_BTN": "#f2f3f5",
        "BG_HOVER": "#e6e9f0", "BG_LINE": "#e4e7ef",
        "BG_ROW_ODD": "#ffffff", "BG_ROW_EVEN": "#f8f9fc", "BG_ROW_ADDED": "#fff5f7",
        "FG": "#1f2329", "FG_DIM": "#8a9099", "BTN_RUN": "#e8eaef",
        "OK_C": "#00b578", "WAIT_C": "#ff4d4f", "SENT_C": "#ff8f1f", "DIM_C": "#a8adb5",
        "SCROLL": "#cfd3de", "SCROLL_HOVER": "#b3b8c6", "SEL_BG": "#ffe9ee", "SEL_FG": "#1f2329",
        "SWITCH_OFF": "#c7ccdb",
        "ACCENT_SOFT": "#ffe9ee", "TITLEBAR_DARK": False,
    },
    "dark": {
        # 深色同理：左边更黑，右边内容区浅一档
        "BG_MAIN": "#1c1e27", "BG_SIDE": "#0b0c12", "BG_CARD": "#262832", "BG_PANEL": "#262832",
        "BG_INPUT": "#2f313b", "BG_HEAD": "#2b2d37", "BG_BTN": "#33353f",
        "BG_HOVER": "#3d404b", "BG_LINE": "#33363f",
        "BG_ROW_ODD": "#24262f", "BG_ROW_EVEN": "#2a2c36", "BG_ROW_ADDED": "#3d2833",
        "FG": "#e9eaef", "FG_DIM": "#8b8f9c", "BTN_RUN": "#2f313b",
        "OK_C": "#3ecf8e", "WAIT_C": "#ff6b6b", "SENT_C": "#ffb04a", "DIM_C": "#7c8089",
        "SCROLL": "#3f4350", "SCROLL_HOVER": "#525664", "SEL_BG": "#3b2a33", "SEL_FG": "#ffffff",
        "SWITCH_OFF": "#454a58",
        "ACCENT_SOFT": "#3b2a33", "TITLEBAR_DARK": True,
    },
}
# 两套主题共用主色（抖音红）
ACCENT = "#ff2d55"
ACCENT_HOVER = "#e6254a"
# 老代码里写死的颜色 -> 当前主题里对应的键（_apply_theme 就是靠它把界面整体刷过去的）
LEGACY_COLOR_MAP = {
    "#888": "FG_DIM", "#666": "FG_DIM", "#999": "FG_DIM",
    "#333": "FG", "#444": "FG",
    "#c0392b": "WAIT_C", "#1a7f37": "OK_C", "#b26a00": "SENT_C",
    "#2b6cb0": "ACCENT", "#7f8c8d": "BTN_RUN", "#eef4ee": "BG_ROW_EVEN",
    "white": "#ffffff",
}
THEME = "light"
# 下面这些由 use_palette() 填（占位，避免 import 期引用未定义名）
BG_MAIN = BG_PANEL = BG_SIDE = BG_CARD = BG_INPUT = BG_HEAD = BG_BTN = ""
BG_HOVER = BG_LINE = BG_ROW_ODD = BG_ROW_EVEN = BG_ROW_ADDED = ""
FG = FG_DIM = BTN_RUN = OK_C = WAIT_C = SENT_C = DIM_C = ""
SCROLL = SCROLL_HOVER = ACCENT_SOFT = SEL_FG = SWITCH_OFF = ""
COLOR_MAP = {}


def use_palette(name):
    """切换当前配色（只改模块级变量；重刷界面由 SparkKeeperApp._set_theme 负责）。"""
    global THEME, COLOR_MAP
    THEME = name if name in PALETTES else "light"
    pal = PALETTES[THEME]
    globals().update({k: v for k, v in pal.items() if k != "TITLEBAR_DARK"})
    other = "dark" if THEME == "light" else "light"
    m = {}
    for k, v in LEGACY_COLOR_MAP.items():
        m[k] = pal[v] if isinstance(v, str) and v in pal else v
    # 另一套主题的颜色 -> 当前主题。两条硬规则（都踩过）：
    # 1) 两套都有的颜色（典型 #ffffff）不进表：否则白色映射成对面"也叫白色"的键（深色 SEL_FG）；
    # 2) **同一套主题里出现多次的颜色也不进表**：浅色的 FG 和 SEL_FG 都是 #1f2329，
    #    反查时后写的 SEL_FG 会赢 → 正文被映射成白色，浅色下"文字看不见"。
    #    这种二义颜色直接不映射，`_c()` 会回落到当前主题的默认色（正文色）。
    cur_vals = {str(v).lower() for v in pal.values() if isinstance(v, str) and v.startswith("#")}
    cnt = {}
    for v in PALETTES[other].values():
        if isinstance(v, str) and v.startswith("#"):
            cnt[v.lower()] = cnt.get(v.lower(), 0) + 1
    for k, v in PALETTES[other].items():
        if not (isinstance(v, str) and v.startswith("#")):
            continue
        c = v.lower()
        if c in cur_vals or cnt.get(c, 0) > 1 or c in m:
            continue
        m[c] = pal[k]
    COLOR_MAP = m
    globals()["_CURRENT_COLORS"] = current_colors()
    return THEME


def current_colors():
    """当前主题里用到的所有颜色（小写），`_c()` 用它判断"这颜色认不认识"。"""
    pal = PALETTES[THEME]
    out = set()
    for v in pal.values():
        if isinstance(v, str) and v.startswith("#"):
            out.add(v.lower())
    out.add(ACCENT.lower())
    out.add(ACCENT_HOVER.lower())
    out.add("#ffffff")
    return out


_CURRENT_COLORS = set()


def titlebar_is_dark():
    return bool(PALETTES[THEME].get("TITLEBAR_DARK"))


use_palette("light")

# 检测结果弹窗里"今日火花"一列的文案
# 右侧会话面板的头像尺寸与留白（Treeview 的单元格没法单独设间距，用透明留白撑开）
# 注意：窗口变窄时不能把头像列一起压 —— 图片会被列边界裁掉、名字贴到头像上。
# 正确做法是"头像本身缩小"：列宽 -> 头像边长/留白/间隙（见 _avatar_metrics）。
AVATAR_SIZE = 34        # 头像最大像素
AVATAR_MIN_SIZE = 20    # 窗口变窄时头像最小缩到多少（缩略图，不是被裁掉）
AVATAR_PAD = 8          # 头像四周的透明留白（**必须左右对称**，否则列里居中会偏）
AVATAR_PAD_Y = 5        # 上下留白
AVATAR_GAP = 12         # 头像列与名字列之间的空隙（靠列宽给，不要塞进图片里）
AVATAR_COL_W = AVATAR_SIZE + AVATAR_PAD * 2 + AVATAR_GAP   # = 62（最宽）
AVATAR_MIN_COL_W = 40   # 头像列最窄到此为止：再窄头像就缩到 AVATAR_MIN_SIZE
# ttk 的树列（#0）**从来不居中图片**，而是把它画在固定内缩处（实测：默认含 indicator 时 x=24，
# 把 indicator 从 Item 布局里去掉后 x=4）。所以"头像居中"只能自己算：
# 列宽 = 2*AVATAR_INSET + 头像边长 + 2*留白，让左右视觉留白相等。
AVATAR_INSET = 4

# 右侧窄面板用的短文案
SPARK_SHORT_CN = {
    "ok": "已点亮",
    "waiting": "待续",
    "sent": "我已发",
    "none": "未建立",
    "missing": "找不到",
    "notlogin": "未登录",
    "unknown": "未知",
}

SPARK_TODAY_CN = {
    "ok": "已点亮",
    "waiting": "待续（轮到我发）",
    "sent": "我今日已发，等对方",
    "none": "未建立火花",
    "missing": "未找到会话",
    "notlogin": "尚未登录",
    "unknown": "状态未知（图标不认识，点刷新再试）",
}


def _rgb(color, default=(255, 255, 255)):
    """'#rrggbb' / '#rgb' -> (r,g,b)；认不出就用 default。"""
    try:
        c = str(color).strip()
        if c.startswith("#"):
            c = c[1:]
            if len(c) == 3:
                c = "".join(ch * 2 for ch in c)
            return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        pass
    return default


_PHOTO_CACHE = {}


def rounded_photo(w, h, radius, fill, bg, supersample=4):
    """带抗锯齿的圆角矩形（PIL 画完缩回来）。

    Tk 的 Canvas 原语**没有抗锯齿**：`create_polygon(smooth=True)` 画出来的圆角是阶梯状的，
    小控件（按钮/开关）上看着就是"割裂、毛边"。这里改成 PIL 画 + LANCZOS 缩放。
    """
    key = ("rr", int(w), int(h), int(radius), str(fill), str(bg))
    ph = _PHOTO_CACHE.get(key)
    if ph is not None:
        return ph
    try:
        from PIL import Image, ImageDraw, ImageTk
    except Exception:
        return None
    s = max(1, int(supersample))
    im = Image.new("RGB", (max(1, int(w) * s), max(1, int(h) * s)), _rgb(bg))
    d = ImageDraw.Draw(im)
    r = max(0, int(radius)) * s
    d.rounded_rectangle([0, 0, int(w) * s - 1, int(h) * s - 1], radius=r, fill=_rgb(fill))
    im = im.resize((max(1, int(w)), max(1, int(h))), Image.LANCZOS)
    ph = ImageTk.PhotoImage(im)
    _PHOTO_CACHE[key] = ph
    return ph


def switch_photo(w, h, pos, track, knob, bg, supersample=4):
    """滑动开关：圆角轨道 + 圆形滑块，一起画好（同样为了抗锯齿）。"""
    key = ("sw", int(w), int(h), round(float(pos), 3), str(track), str(knob), str(bg))
    ph = _PHOTO_CACHE.get(key)
    if ph is not None:
        return ph
    try:
        from PIL import Image, ImageDraw, ImageTk
    except Exception:
        return None
    s = max(1, int(supersample))
    im = Image.new("RGB", (max(1, int(w) * s), max(1, int(h) * s)), _rgb(bg))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, int(w) * s - 1, int(h) * s - 1],
                        radius=(int(h) * s) // 2, fill=_rgb(track))
    r = (h / 2.0 - 3) * s
    kx = (h / 2.0 + (w - h) * float(pos)) * s
    ky = (h / 2.0) * s
    d.ellipse([kx - r, ky - r, kx + r, ky + r], fill=_rgb(knob))
    im = im.resize((max(1, int(w)), max(1, int(h))), Image.LANCZOS)
    ph = ImageTk.PhotoImage(im)
    _PHOTO_CACHE[key] = ph
    return ph


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上画圆角矩形（Tk 没有原生圆角，用平滑多边形近似）。"""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class RoundedButton(tk.Canvas):
    """圆角按钮。

    Tk 原生按钮是系统方块、带凹陷边框，观感很"廉价"；这里用 Canvas 自绘，
    支持 主色/普通/幽灵 三种样式和悬停、按下效果。
    """

    @staticmethod
    def kinds():
        """按钮配色跟着**当前主题**走，所以每次现算（写成类常量的话切主题后不会更新）。"""
        return {
            "accent": (ACCENT, ACCENT_HOVER, "#ffffff"),
            "normal": (BG_BTN, BG_HOVER, FG),
            "ghost": (None, BG_HOVER, FG_DIM),
            "run": (BTN_RUN, BG_HOVER, FG),
        }

    def __init__(self, parent, text="", command=None, kind="normal",
                 font=None, padx=16, height=32, radius=8, bg=None, width=None,
                 icon_left=None, icon_right=None):
        self._font = font if hasattr(font, "measure") else tkfont.Font(font=font or FONT)
        self._bg_parent = bg or parent.cget("bg")
        self._kind = kind if kind in self.kinds() else "normal"
        self._text = text
        self._command = command
        self._radius = radius
        self._padx = padx
        self._icon_left = icon_left
        self._icon_right = icon_right
        self._icon_gap = 6
        self._enabled = True
        w = width or (self._font.measure(text) + padx * 2 + self._icon_width())
        super().__init__(parent, width=w, height=height, bg=self._bg_parent,
                         highlightthickness=0, bd=0, takefocus=0, cursor="hand2")
        self._bw, self._bh = w, height   # 注意：不能叫 _w，那是 Tkinter 内部的 Tcl 控件名
        self._hover = False
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _icon_width(self):
        w = 0
        for im in (self._icon_left, self._icon_right):
            if im is not None:
                w += im.width() + self._icon_gap
        return w

    def set_icons(self, left=None, right=None):
        """给按钮两头挂图标（重算宽度并重画）。一键续火花就是靠它两头放火花。"""
        self._icon_left, self._icon_right = left, right
        need = self._font.measure(self._text) + self._padx * 2 + self._icon_width()
        self._bw = max(self._bw, need)
        try:
            tk.Canvas.configure(self, width=self._bw)
        except Exception:
            pass
        self._draw()

    def apply_theme(self):
        """主题变了：底色跟着父容器、配色重算后重画。"""
        try:
            self._bg_parent = self.master.cget("bg")
        except Exception:
            pass
        try:
            tk.Canvas.configure(self, bg=self._bg_parent)
        except Exception:
            pass
        self._draw()

    def _colors(self):
        base, hover, fg = self.kinds().get(self._kind, self.kinds()["normal"])
        if base is None:
            base = self._bg_parent
        if not self._enabled:
            return base, base, DIM_C
        return base, hover, fg

    def _draw(self):
        self.delete("all")
        base, hover, fg = self._colors()
        fill = hover if self._hover else base
        # 以"控件实际宽度"为准：固定宽度的侧栏按钮可能比请求宽度窄，
        # 按 _bw 画会把两头的内容画到控件外面（火花图标就是这么消失的）
        bw = self._bw
        try:
            real = int(self.winfo_width())
            if real > 1:
                bw = real
        except Exception:
            pass
        ph = rounded_photo(bw, self._bh, self._radius, fill, self._bg_parent)
        if ph is not None:
            self.create_image(0, 0, anchor="nw", image=ph)
        else:
            round_rect(self, 0.5, 0.5, bw - 0.5, self._bh - 0.5, self._radius,
                       fill=fill, outline="")
        il, ir = self._icon_left, self._icon_right
        tw = self._font.measure(self._text)
        total = tw + self._icon_width()
        cy = self._bh / 2.0
        if total > bw:
            # 实在挤不下：图标贴两边、文字居中，宁可挤一点也不画到控件外
            if il is not None:
                self.create_image(1 + il.width() / 2.0, cy, image=il)
            if ir is not None:
                self.create_image(bw - 1 - ir.width() / 2.0, cy, image=ir)
            self.create_text(bw / 2.0, cy, text=self._text, fill=fg, font=self._font)
            return
        x = (bw - total) / 2.0
        if il is not None:
            self.create_image(x + il.width() / 2.0, cy, image=il)
            x += il.width() + self._icon_gap
        self.create_text(x + tw / 2.0, cy, text=self._text, fill=fg, font=self._font)
        x += tw
        if ir is not None:
            self.create_image(x + self._icon_gap + ir.width() / 2.0, cy, image=ir)

    # --- 事件
    def _on_enter(self, _e=None):
        self._hover = True
        self._draw()

    def _on_leave(self, _e=None):
        self._hover = False
        self._draw()

    def _on_press(self, _e=None):
        if self._enabled and self._command:
            try:
                self._command()
            except Exception:
                pass

    def _on_release(self, _e=None):
        self._hover = True
        self._draw()

    # --- 兼容 tk.Button 的 configure(text=...) / cget
    def configure(self, **kw):
        redraw = False
        if "text" in kw:
            self._text = kw.pop("text")
            redraw = True
        if "command" in kw:
            self._command = kw.pop("command")
        if "font" in kw:
            self._font = kw.pop("font")
            redraw = True
        if "bg" in kw:
            c = kw.pop("bg")
            if c in (ACCENT, ACCENT_HOVER):
                self._kind = "accent"
            elif c in (BTN_RUN, "#7f8c8d", "#e8eaef"):
                self._kind = "run"
            elif c:
                self._kind = "normal"
            redraw = True
        if "fg" in kw:
            kw.pop("fg")
        if "kind" in kw:
            k = kw.pop("kind")
            if k in self.kinds():
                self._kind = k
                redraw = True
        if "state" in kw:
            self._enabled = str(kw.pop("state")) != "disabled"
            redraw = True
        if kw:
            try:
                super().configure(**kw)
            except Exception:
                pass
        if redraw:
            self._draw()
        return None

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "bg":
            return self.kinds().get(self._kind, self.kinds()["normal"])[0] or self._bg_parent
        return super().cget(key)


class ToggleSwitch(tk.Canvas):
    """滑动开关（带滑动动效）。

    设置面板里的"深色模式"用它：点一下滑块滑过去/滑回来，底色同时渐变。
    """

    def __init__(self, parent, value=False, command=None, width=48, height=26,
                 bg=None, track_on=None, track_off=None):
        self._bg_parent = bg or parent.cget("bg")
        super().__init__(parent, width=width, height=height, bg=self._bg_parent,
                         highlightthickness=0, bd=0, takefocus=0, cursor="hand2")
        # 注意：不能叫 self._w/_h —— _w 是 Tkinter 内部存 Tcl 控件名的属性（踩过两次）
        self._tw, self._th = width, height
        self._on = bool(value)
        self._command = command
        self._track_on = track_on or ACCENT
        self._track_off = track_off or (SWITCH_OFF or BG_HOVER)
        self._pos = 1.0 if self._on else 0.0          # 0=左 1=右
        self._anim = None
        self._draw()
        self.bind("<Button-1>", self._clicked)
        self.bind("<Enter>", lambda e: self._draw(hover=True))
        self.bind("<Leave>", lambda e: self._draw(hover=False))

    # --- 颜色（跟着主题走）
    def _fg(self):
        return "#ffffff"

    def _draw(self, hover=False):
        self.delete("all")
        r = self._th / 2.0
        # 轨道：用滑块位置插值出颜色，滑动过程中颜色也是渐变的
        on_c, off_c = self._track_on, self._track_off
        t = self._pos
        col = "#%02x%02x%02x" % tuple(
            int(a + (b - a) * t) for a, b in zip(
                tuple(int(off_c[i:i + 2], 16) for i in (1, 3, 5)),
                tuple(int(on_c[i:i + 2], 16) for i in (1, 3, 5))))
        if hover:
            col = "#%02x%02x%02x" % tuple(min(255, int(int(col[i:i + 2], 16) * 1.06)) for i in (1, 3, 5))
        ph = switch_photo(self._tw, self._th, t, col, "#ffffff", self._bg_parent)
        if ph is not None:
            self.create_image(0, 0, anchor="nw", image=ph)
            return
        round_rect(self, 0.5, 0.5, self._tw - 0.5, self._th - 0.5, r, fill=col, outline="")
        kr = r - 3
        kx = r + (self._tw - 2 * r) * t
        self.create_oval(kx - kr, self._th / 2.0 - kr, kx + kr, self._th / 2.0 + kr,
                         fill="#ffffff", outline="")

    def set(self, value, animate=True):
        target = 1.0 if value else 0.0
        if abs(target - self._pos) < 0.01:
            self._on = bool(value)
            return
        self._on = bool(value)
        if not animate:
            self._pos = target
            self._draw()
            return
        self._animate(target)

    def _animate(self, target):
        if self._anim is not None:
            try:
                self.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None
        start = self._pos
        steps = 10
        delay = 16

        def step(i):
            # ease-out：先快后慢，看着更"跟手"
            k = 1 - (1 - i / float(steps)) ** 3
            self._pos = start + (target - start) * k
            self._draw()
            if i < steps:
                self._anim = self.after(delay, lambda: step(i + 1))
            else:
                self._pos = target
                self._anim = None
                self._draw()

        step(1)

    def _clicked(self, _e=None):
        self.set(not self._on)
        if self._command:
            try:
                self._command(self._on)
            except Exception:
                pass

    def get(self):
        return self._on

    def apply_theme(self):
        try:
            self._bg_parent = self.master.cget("bg")
            tk.Canvas.configure(self, bg=self._bg_parent)
        except Exception:
            pass
        self._track_off = SWITCH_OFF or BG_HOVER
        self._draw()


class AsyncWorker:
    """后台线程里跑一个 asyncio 事件循环，供 Playwright 使用。"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="dsh-async")
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


class SparkKeeperApp:
    def __init__(self, start_hidden=False):
        self.start_hidden = bool(start_hidden)
        self.cfg = config.load()
        # 先把主题套上：下面所有控件的颜色都是建的时候读模块级常量的
        self.theme = use_palette((self.cfg.get("ui") or {}).get("theme") or "light")
        self.logs_dir = config.LOGS_DIR
        self.worker = AsyncWorker()
        self.client = None
        self.scheduler_on = False
        self.ui_queue = queue.Queue()
        self.stickers = config.load_stickers()
        self.conv_rows = []
        self.conv_loading = False
        self.avatar_photos = {}
        self.conv_updated_at = ""
        self.target_photos = {}
        self._avatar_cache = {}
        self._target_metrics = (AVATAR_SIZE, AVATAR_PAD, AVATAR_GAP)
        self._conv_metrics = (AVATAR_SIZE, AVATAR_PAD, AVATAR_GAP)
        self._load_saved_conversations()      # 关掉再打开，右侧列表还在

        self.root = tk.Tk()
        self.root.title(version.full_title())          # 标题栏带版本号（唯一来源见 version.py）
        self.root.geometry("1280x760")
        # 最小窗口 1000x640：这个宽度下两张表都还能完整可读（见 _apply_col_widths 的三档分配）。
        # 屏幕（逻辑像素）比 1000 还窄时跟着让一点，否则窗口会比屏幕还宽；那种小屏上
        # 表格会自动改用横向滚动条，不会再把列压成一团。
        try:
            _sw = int(self.root.winfo_screenwidth())
        except Exception:
            _sw = 1280
        self.root.minsize(min(1000, max(760, _sw - 40)), 640)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        self._build_ui()
        self._really_quit = False
        self._sending = False        # 一轮还没发完时，别让第二轮插进来
        self.tray = None
        self._start_tray()
        if self.start_hidden:
            # 开机自启：直接待在托盘里
            if self.tray is not None:
                try:
                    self.root.withdraw()
                except Exception:
                    pass
                logger.info("已最小化到系统托盘（开机自启模式）；右键托盘图标可以打开主界面",
                            self.logs_dir)
                self.root.after(800, lambda: self._toast(
                    "抖音续火花已在后台运行", "开机自启：窗口收在托盘里，定时续火花照常执行。"))
                # 开机自启这次顺带把自动模式跑起来（不然每次开机都得右键点一下）
                self.root.after(1800, self._sched_on_boot)
                # 联网后检查"今天是否已经主动续过"，没续就自动补一轮（等自动模式先跑，别抢）
                self.root.after(6000, self._boot_catchup_go)
            else:
                logger.warn("想要最小化到托盘，但这个环境用不了托盘，先把窗口显示出来", self.logs_dir)
        logger.add_listener(self._queue_log)
        logger.info("程序已启动 %s，配置文件：%s" % (version.version_text(), config.CONFIG_PATH),
                    self.logs_dir)
        self.root.after(120, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ 通知弹窗（自己画）
    def _toast(self, title, text="", ms=7000, spark=True):
        """右下角弹一个自己画的通知。

        为什么不用托盘气泡：Windows 11 会把 Shell_NotifyIcon 的汽泡转成系统通知，
        标题写死成进程名（"Python"）、图标还是个蓝感叹号 —— 用户明确说不要。
        自己画就能用火花图标 + 「抖音续火花」标题，还能显示多行统计。
        """
        try:
            if not hasattr(self, "_toasts"):
                self._toasts = []
            self._toasts = [t for t in self._toasts if t.winfo_exists()]   # 丢掉已经关掉的
            win = tk.Toplevel(self.root)
            win.withdraw()
            win.overrideredirect(True)          # 不要标题栏（系统那套"Python"就是这么来的）
            try:
                win.attributes("-topmost", True)
            except Exception:
                pass
            win.configure(bg=BG_LINE)           # 1px 描边
            card = tk.Frame(win, bg=BG_CARD)
            card.pack(fill="both", expand=True, padx=1, pady=1)
            pad = tk.Frame(card, bg=BG_CARD)
            pad.pack(fill="both", expand=True, padx=14, pady=12)
            top = tk.Frame(pad, bg=BG_CARD)
            top.pack(fill="x")
            icon = self._spark_photo(26) if spark else None
            if icon is not None:
                win._spark_keep = icon          # 留引用，别被回收
                tk.Label(top, image=icon, bg=BG_CARD).pack(side="left", padx=(0, 8))
            tk.Label(top, text=title, font=FONT_B, bg=BG_CARD, fg=FG).pack(side="left")
            close_x = tk.Label(card, text="✕", font=FONT_S, bg=BG_CARD, fg=FG_DIM, cursor="hand2")
            close_x.place(relx=1.0, x=-6, y=4, anchor="ne")
            if text:
                tk.Label(pad, text=text, font=FONT_S, bg=BG_CARD, fg=FG_DIM, justify="left",
                         wraplength=280, anchor="w").pack(fill="x", pady=(6, 0))

            def close(_e=None):
                try:
                    win.destroy()
                except Exception:
                    pass

            def open_main(_e=None):
                close()
                self._show_from_tray()

            for w in (win, card, pad):
                w.bind("<Button-1>", open_main)
                w.bind("<Button-3>", close)
            for w in top.winfo_children() + [top]:
                w.bind("<Button-1>", open_main)
            # × 要吃掉这次的点击（return "break"），不然会继续冒泡到"打开主界面"
            close_x.bind("<Button-1>", lambda e: (close(), "break")[1])
            close_x.bind("<Enter>", lambda e: close_x.configure(fg=FG))
            close_x.bind("<Leave>", lambda e: close_x.configure(fg=FG_DIM))
            win.update_idletasks()
            tw, th = win.winfo_reqwidth(), win.winfo_reqheight()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x = max(0, sw - tw - 18)
            y = max(0, sh - th - 64 - len(self._toasts) * (th + 8))
            win.geometry("+%d+%d" % (x, y))
            win.deiconify()
            try:
                win.lift()
            except Exception:
                pass
            self._toasts.append(win)
            if ms:
                win.after(int(ms), close)
            return win
        except Exception as e:
            logger.warn("通知弹窗失败（不影响使用）：%s" % e, self.logs_dir)
            return None

    def _spark_tally(self, rows):
        """按当前启用的目标，数一下列表里点亮了几个、还差几个。"""
        rows = rows or []
        if not rows:
            return None
        by_id, by_name = {}, {}
        for r in rows:
            cid = (r.get("conv_id") or "").strip()
            if cid:
                by_id[cid] = r
            n = self._norm_name(r.get("name") or "")
            if n and n not in by_name:
                by_name[n] = r
        lit = total = 0
        for t in self.cfg.get("targets") or []:
            if not t.get("enabled"):
                continue
            total += 1
            cid = (t.get("conv_id") or "").strip()
            row = by_id.get(cid) or by_name.get(self._norm_name(t.get("name") or ""))
            if row is not None and self._spark_bucket(row.get("state")) == "ok":
                lit += 1
        return {"total": total, "lit": lit, "unlit": max(0, total - lit)}

    def _notify_spark_done(self, rows, tag=""):
        """发完一轮之后弹"今日火花已续"。"""
        tal = self._spark_tally(rows)
        if not tal or not tal["total"]:
            return
        text = "当前点亮 %d 个 · 未点亮 %d 个（共 %d 个目标）" % (tal["lit"], tal["unlit"], tal["total"])
        self._toast("今日火花已续%s" % ("（%s）" % tag if tag else ""), text)

    # ------------------------------------------------------------ 登录状态
    def _set_login_state(self, ok, save=True):
        """更新侧栏那个登录状态（记住时间，重启后也能看到上次结论）。"""
        old_ok = (getattr(self, "login_state", None) or {}).get("ok")
        self._login_verified = True
        self.login_state = {"ok": bool(ok),
                            "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        if save and old_ok != bool(ok):        # 状态没变就不写盘，别每轮都存一次配置
            try:
                self.cfg.setdefault("ui", {})["login_state"] = self.login_state
                self.cfg = config.save(self.cfg)
            except Exception:
                pass
        self.ui_queue.put(("login", self.login_state))

    def _sync_login_label(self):
        st = getattr(self, "login_state", None) or {}
        ok = st.get("ok")
        at = (st.get("at") or "")[11:16]                 # HH:MM
        verified = getattr(self, "_login_verified", False)
        if ok is True:
            text = "已登录" if verified else ("上次登录 %s" % at if at else "上次已登录")
            color = OK_C
        elif ok is False:
            text = "未登录" if verified else ("上次未登录 %s" % at if at else "未登录")
            color = WAIT_C
        else:
            text = "登录状态未知"
            color = DIM_C
        for w, val in ((getattr(self, "login_dot", None), color),
                       (getattr(self, "login_label", None), color)):
            if w is not None:
                try:
                    w.configure(fg=val)
                except Exception:
                    pass
        if getattr(self, "login_label", None) is not None:
            try:
                self.login_label.configure(text=text)
            except Exception:
                pass

    # ------------------------------------------------------------ 系统托盘
    def _tray_icon_path(self):
        """生成托盘图标（就用那张火花图，抠掉底色转成 .ico）。"""
        ico = os.path.join(self.logs_dir, "tray.ico")
        try:
            if os.path.exists(ico) and os.path.getsize(ico) > 0:
                return ico
            src = self._spark_sticker_path()
            if not src:
                return ""
            from PIL import Image
            png = os.path.join(self.logs_dir, "tray_src.png")
            im = self._knock_out_bg(Image.open(src).convert("RGBA"))
            im.save(png)
            return tray_mod.make_ico(png, ico) or ""
        except Exception:
            return ""

    def _start_tray(self):
        """起托盘图标；用不了就当作没有（行为退回原来的"关窗即退出"）。"""
        try:
            icon = self._tray_icon_path()
            self.tray = tray_mod.TrayIcon(icon, "抖音续火花 %s" % version.version_text(),
                                          on_action=self._on_tray_action)
            if not self.tray.start():
                self.tray = None
                logger.warn("系统托盘用不了（不影响使用）：关窗口会直接退出程序", self.logs_dir)
            else:
                self.tray.set_sched_on(bool(self.scheduler_on))
        except Exception as e:
            self.tray = None
            logger.warn("系统托盘启动失败（不影响使用）：%s" % e, self.logs_dir)

    def _on_tray_action(self, name):
        """**在托盘线程里**被调用：只往队列里丢，别碰 Tk 控件。"""
        try:
            self.ui_queue.put(("tray", name))
        except Exception:
            pass

    def _handle_tray_action(self, name):
        """在主线程里处理托盘菜单/点击。"""
        if name == "show":
            self._show_from_tray()
        elif name == "send":
            # 从托盘点的：**窗口保持收起**（用户要求），后台跑完再弹通知
            logger.info("从托盘触发「一键续火花」（窗口保持收起，跑完会弹通知）", self.logs_dir)
            self._toast("已开始一键续火花", "窗口保持收起，跑完会再弹一条结果通知。", ms=3000)
            self.on_send_all()
        elif name == "sched":
            was_on = bool(self.scheduler_on)
            self.on_toggle_scheduler()
            # scheduler_on 是异步线程里置位的，这里按"刚才是不是开着"判断更准
            if was_on:
                logger.info("从托盘停止自动模式（窗口保持收起）", self.logs_dir)
                self._toast("自动模式已停止", "窗口保持收起。", ms=3000)
            else:
                logger.info("从托盘启动自动模式（窗口保持收起，到点自动发）", self.logs_dir)
                self._toast("自动模式已启动", "窗口保持收起，到点会自动续火花。", ms=3000)
        elif name == "quit":
            self._really_quit = True
            self._on_close()

    def _show_from_tray(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _hide_to_tray(self):
        try:
            self.root.withdraw()
        except Exception:
            pass
        self._toast("抖音续火花还在后台跑",
                    "窗口收起来了，定时续火花照常执行；右键托盘图标可以打开或退出。")

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        # ---------------- 左侧导航栏（抄你给的那套后台：左边一栏、右边内容）
        side = tk.Frame(self.root, bg=BG_SIDE, width=186)
        side._dsk_role = "side"          # 浅色里侧栏和卡片都是白的，颜色分不出来，显式打标
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(side, bg=BG_LINE, width=1).pack(side="right", fill="y")
        brand = tk.Frame(side, bg=BG_SIDE)
        brand.pack(fill="x", padx=20, pady=(20, 16))
        tk.Label(brand, text="抖音续火花", font=("Microsoft YaHei", 19, "bold"),
                 bg=BG_SIDE, fg=FG).pack(anchor="w")
        # 标题下面一张大火花，两行灰字贴在火花的右下方（跟图片底部对齐）
        brow = tk.Frame(brand, bg=BG_SIDE)
        brow.pack(fill="x", pady=(6, 0))
        _brand_spark = self._spark_photo(54)
        if _brand_spark is not None:
            self._brand_spark = _brand_spark          # 留引用，否则图片被回收
            tk.Label(brow, image=_brand_spark, bg=BG_SIDE).pack(side="left")
        bcol = tk.Frame(brow, bg=BG_SIDE)
        bcol.pack(side="left", padx=(10, 0), anchor="s")
        tk.Label(bcol, text="每日自动续火花", font=FONT_S, bg=BG_SIDE,
                 fg=FG_DIM).pack(anchor="w")
        tk.Label(bcol, text=version.version_text(), font=FONT_S, bg=BG_SIDE,
                 fg=FG_DIM).pack(anchor="w", pady=(1, 0))
        tk.Frame(side, bg=BG_LINE, height=1).pack(fill="x", padx=20, pady=(0, 12))

        # 登录状态（放在「扫码登录」上面）
        self._login_verified = False
        self.login_state = (self.cfg.get("ui") or {}).get("login_state") or {}
        lsrow = tk.Frame(side, bg=BG_SIDE)
        lsrow.pack(fill="x", padx=20, pady=(0, 8))
        self.login_dot = tk.Label(lsrow, text="●", font=FONT_S, bg=BG_SIDE, fg=DIM_C)
        self.login_dot.pack(side="left")
        self.login_label = tk.Label(lsrow, text="", font=FONT_S, bg=BG_SIDE, fg=DIM_C)
        self.login_label.pack(side="left", padx=(5, 0))
        self._sync_login_label()

        self.side_btns = {}
        for key, text, cmd in (("login", "扫码登录", self.on_login),
                               ("send", "一键续火花", self.on_send_all),
                               ("sticker", "同步超级表情", self.on_sync_stickers),
                               ("calib", "抖音窗口校准", self.on_calibrate),
                               ("sched", "▶ 启动自动模式", self.on_toggle_scheduler),
                               ("setting", "设置", self.on_settings)):
            b = RoundedButton(side, text, cmd, kind=("accent" if key == "send" else "ghost"),
                              font=FONT, width=146, height=38, radius=8, bg=BG_SIDE,
                              padx=(12 if key == "send" else 16))
            b.pack(padx=20, pady=3)
            self.side_btns[key] = b
        self.sched_btn = self.side_btns["sched"]
        # 「一键续火花」按钮两头挂火花图标（用抖音超级表情里的那个火花）
        _spark = self._spark_photo(16)
        if _spark is not None:
            send_btn = self.side_btns["send"]
            send_btn._icon_gap = 4
            send_btn.set_icons(left=_spark, right=_spark)

        # ---------------- 左下角：界面模式开关（原来在「设置」里，用户要求挪出来）
        # 只留「界面模式」四个字；当前模式那个"×色"加粗、另一个灰字
        th = tk.Frame(side, bg=BG_SIDE)
        th.pack(side="bottom", fill="x", padx=16, pady=(0, 16))
        r1 = tk.Frame(th, bg=BG_SIDE)
        r1.pack(fill="x")
        tk.Label(r1, text="界面模式", font=FONT_S, bg=BG_SIDE, fg=FG_DIM).pack(side="left")
        self.theme_switch = ToggleSwitch(r1, value=(self.theme == "dark"),
                                         command=self._on_theme_toggle, bg=BG_SIDE,
                                         width=40, height=22)
        self.theme_switch.pack(side="left", padx=(8, 0))
        # 深色 / 浅色 放在开关下面（和开关左边缘对齐）
        r2 = tk.Frame(th, bg=BG_SIDE)
        r2.pack(fill="x", pady=(4, 0))
        tk.Frame(r2, bg=BG_SIDE, width=58, height=1).pack(side="left")   # 占位：对齐到开关
        self.theme_labels = {}
        for _key, _text in (("dark", "深色"), ("light", "浅色")):
            _lb = tk.Label(r2, text=_text, font=FONT_S, bg=BG_SIDE, fg=FG_DIM)
            _lb.pack(side="left", padx=(0, 10))
            self.theme_labels[_key] = _lb
        self._sync_theme_labels()

        # ---------------- 右侧内容区
        content = tk.Frame(self.root, bg=BG_MAIN)
        content.pack(side="left", fill="both", expand=True)

        top = tk.Frame(content, bg=BG_MAIN)
        top.pack(fill="x", padx=20, pady=(18, 10))
        _help_btn = RoundedButton(top, "操作说明", self._show_help, kind="ghost", font=FONT_S,
                                  padx=14, height=30, bg=BG_MAIN)
        _help_btn.pack(side="right")
        # 左边放一块和「操作说明」等宽的占位，标题才是真的居中
        tk.Frame(top, bg=BG_MAIN, width=_help_btn._bw, height=1).pack(side="left")
        # 顶部标题：火花！火花！火花！我命令你赶紧给我火花！！！（图片用抖音的「续火花」超级表情）
        # expand=True 让这行在内容区里居中（左右各留了等宽占位）
        trow = tk.Frame(top, bg=BG_MAIN)
        trow.pack(side="left", expand=True)
        self._header_photos = []
        _big = self._spark_photo(34)
        for piece in ("spark", "！", "spark", "！", "spark", "！我命令你赶紧给我", "spark", "！！！"):
            if piece == "spark":
                if _big is None:
                    continue
                self._header_photos.append(_big)
                tk.Label(trow, image=_big, bg=BG_MAIN).pack(side="left", padx=(0, 2))
            else:
                tk.Label(trow, text=piece, font=("Microsoft YaHei", 20, "bold"),
                         bg=BG_MAIN, fg=FG).pack(side="left")

        mid = tk.Frame(content, bg=BG_MAIN)
        mid.pack(fill="both", expand=True, padx=20, pady=(0, 6))
        # 用 grid 分配左右两列：pack 时 ttk.PanedWindow 的请求宽度会被算成 0，右列拿不到空间
        # 两栏纯按 weight 等比缩放（6:5）。
        # 关键：两栏的"请求宽度"必须都很小，否则 grid 会先满足请求、再按 weight 分剩余空间，
        # 6:5 就被带偏了（实测右栏因为工具栏文字、请求宽度约 470，把比例拉成 1:1.4）。
        # 所以在两栏的容器上关掉尺寸传播（pack_propagate(False)），它们的请求宽度≈0，
        # 宽度完全由 weight 决定。之前这里改成按窗口宽度设 minsize 来"顶住"比例，
        # 反而引出了新 bug：minsize 按"当时的 mid 宽度"算，窗口缩小的一瞬间 mid 还没更新，
        # 残留的大 minsize 会把两栏撑出内容区（实测缩到 1000x640 时 minsize 仍是 [574,479]，
        # 合计 1053 > 内容区 774）→ 右卡片被窗口裁掉、表格列被压成 18px。
        mid.columnconfigure(0, weight=6)
        mid.columnconfigure(1, weight=5)
        mid.rowconfigure(0, weight=1)
        self.mid = mid

        # 目标列表（卡片：圆角/细描边改不了，用 1px 描边 + 内边距做出层次）
        left = self._card(mid, "选中的目标", "双击一行编辑 · 右键更多操作")
        left.card_outer.grid(row=0, column=0, sticky="nsew")     # 要 grid 外框（描边那层）
        left.card_outer.pack_propagate(False)   # 卡片内容的请求宽度不要影响两栏比例（见上）

        # 卡片底部留白（"操作说明"已挪到右上角）
        tk.Frame(left, bg=BG_CARD, height=10).pack(side="bottom", fill="x")

        # 表格外面套一层"不传播请求尺寸"的容器：
        # 否则列宽会撑大 Treeview 的请求宽度，从而顶掉 grid 的权重（两栏比例就乱了）
        treewrap = tk.Frame(left, bg=BG_CARD, width=120, height=120)
        treewrap.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=(0, 6))
        treewrap.pack_propagate(False)

        cols = ("enabled", "name", "kind", "times", "content")
        self.tree = ttk.Treeview(treewrap, columns=cols, show="tree headings",
                                 style="Conv.Treeview", height=12)
        self.tree.heading("#0", text="头像", anchor="center")
        self.tree.column("#0", width=AVATAR_COL_W, anchor="center", stretch=False)
        # 对齐规则（用户要求）：只有「昵称」「内容」左对齐，其余一律居中（含头像）
        # 初始列宽刻意给小：真正宽度由 _on_target_tree_resize 按实际宽度算。
        # 若这里给大了，Treeview 的"请求宽度"会顶住 grid 的权重分配，两栏比例就乱了。
        for c, t, w, a in (("enabled", "启用", 40, "center"), ("name", "昵称/群名", 110, "w"),
                           ("kind", "类型", 40, "center"), ("times", "发送时间", 60, "center"),
                           ("content", "内容", 110, "w")):
            self.tree.heading(c, text=t, anchor=a)        # 表头要和内容同一种对齐
            self.tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview, style="Slim.Vertical.TScrollbar")
        sb.pack(side="right", fill="y", padx=(0, 6), pady=(0, 6))
        self.tree.configure(yscrollcommand=sb.set)
        # 横向滚动条平时不显示（放不下时由 _set_hscroll 调出来）
        hsb = ttk.Scrollbar(treewrap, orient="horizontal", command=self.tree.xview,
                            style="Slim.Horizontal.TScrollbar")
        self.tree.configure(xscrollcommand=hsb.set)
        self.tree.dsk_hbar = hsb
        self.tree.dsk_hbar_on = False
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.on_edit())
        self.tree.bind("<Button-3>", self.on_target_right_click)
        self.tree.bind("<Configure>", self._on_target_tree_resize)

        # 右侧：抖音会话（带头像）在上，运行日志在下
        rightwrap = tk.Frame(mid)
        rightwrap.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        rightwrap.pack_propagate(False)         # 同上：右栏的请求宽度不许顶掉 6:5
        pane = ttk.PanedWindow(rightwrap, orient="vertical")
        pane.pack(fill="both", expand=True)
        self.pane = pane

        conv = self._card(pane, "抖音会话", "读到的好友 / 群聊")
        self.conv_card = conv
        pane.add(conv.card_outer, weight=3)

        cbar = tk.Frame(conv, bg=BG_CARD)
        cbar.pack(fill="x", padx=12, pady=(0, 8))
        RoundedButton(cbar, "读取抖音列表", self.on_load_conversations,
                      kind="accent", padx=14, height=30).pack(side="left")
        # 「添加选中」按钮和旧的灰字都撤了，这里只留一行提示（用户要求）
        self.only_spark_v = tk.BooleanVar(value=bool((self.cfg.get("ui") or {}).get("only_spark", True)))
        tk.Checkbutton(cbar, text="只读有火花的", variable=self.only_spark_v, font=FONT_S,
                       command=self._on_only_spark_toggle).pack(side="left", padx=(10, 0))
        self.conv_hint = tk.Label(cbar, text="双击添加目标 · 右键更多操作",
                                  justify="left", font=FONT_S, fg=FG_DIM, bg=BG_CARD)
        self.conv_hint.pack(side="left", padx=8)

        self._style_ttk()
        try:
            _st = ttk.Style()
            _st.configure("Conv.Treeview", rowheight=AVATAR_SIZE + AVATAR_PAD_Y * 2 + 6)
            # 去掉树列的展开箭头：它会给图片额外加 20px 内缩，且树列本来就不理会 anchor
            _st.layout("Conv.Treeview.Item", [('Treeitem.padding', {
                'sticky': 'nswe', 'children': [
                    ('Treeitem.image', {'side': 'left', 'sticky': ''}),
                    ('Treeitem.text', {'side': 'left', 'sticky': ''})]})])
        except Exception:
            pass
        convwrap = tk.Frame(conv, bg=BG_CARD, width=120, height=120)
        convwrap.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=(0, 6))
        convwrap.pack_propagate(False)
        self.conv_tree = ttk.Treeview(convwrap, columns=("name", "days", "today", "time"),
                                      show="tree headings", style="Conv.Treeview", height=8)
        self.conv_tree.heading("#0", text="头像", anchor="center")
        self.conv_tree.column("#0", width=AVATAR_COL_W, anchor="center", stretch=False)
        for c, t, w, a in (("name", "昵称 / 群名", 110, "w"), ("days", "天数", 44, "center"),
                           ("today", "今日火花", 60, "center"), ("time", "最近消息", 52, "center")):
            self.conv_tree.heading(c, text=t, anchor=a)   # 表头要和内容同一种对齐
            self.conv_tree.column(c, width=w, anchor=a, stretch=(c == "name"))
        self.conv_tree.pack(fill="both", expand=True)
        csb = ttk.Scrollbar(conv, orient="vertical", command=self.conv_tree.yview,
                            style="Slim.Vertical.TScrollbar")
        csb.pack(side="right", fill="y", padx=(0, 6), pady=(0, 6))
        self.conv_tree.configure(yscrollcommand=csb.set)
        chsb = ttk.Scrollbar(convwrap, orient="horizontal", command=self.conv_tree.xview,
                             style="Slim.Horizontal.TScrollbar")
        self.conv_tree.configure(xscrollcommand=chsb.set)
        self.conv_tree.dsk_hbar = chsb          # 同左表：放不下时才显示
        self.conv_tree.dsk_hbar_on = False
        self.conv_tree.bind("<Double-1>", lambda e: self.on_conv_add())
        self.conv_tree.bind("<Button-3>", self.on_conv_right_click)
        self.conv_tree.bind("<Configure>", self._on_conv_tree_resize)
        self.conv_tree.tag_configure("ok", foreground=OK_C)
        self.conv_tree.tag_configure("waiting", foreground=WAIT_C)
        self.conv_tree.tag_configure("sent", foreground=SENT_C)
        self.conv_tree.tag_configure("dim", foreground=DIM_C)
        self.conv_tree.tag_configure("added", background=BG_ROW_ADDED)
        self.avatar_photos = {}

        logf = self._card(pane, "运行日志")
        pane.add(logf.card_outer, weight=2)
        self.log_text = tk.Text(logf, width=1, height=10, font=("Consolas", 9), wrap="word",
                                relief="flat", padx=8, pady=6)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=(0, 8))
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log_text.yview,
                            style="Slim.Vertical.TScrollbar")
        lsb.pack(side="right", fill="y", padx=(0, 6), pady=(0, 8))
        self.log_text.configure(yscrollcommand=lsb.set, state="disabled")

        # 底部那行「就绪」灰字删掉了（用户要求）：状态消息改成写进右边的运行日志面板
        self.status = None

        self._refresh_tree()
        try:
            if self.conv_rows:                # 用上次保存的数据把会话 ID 绑到老目标上
                self._backfill_conv_ids(self.conv_rows)
        except Exception:
            pass
        self._fill_conv_panel()               # 没缓存也要跑一次：给出"还没有数据"的提示
        self._stripes(self.tree)
        self._stripes(self.conv_tree)
        self._apply_theme()                   # 整窗刷成浅色（放在最后，避免被后面的 configure 覆盖）
        self._titlebar(self.root)             # 标题栏也用浅色，别留一条黑边
        self.root.after(300, self._init_sash)   # 开局把上下分隔条放到合适位置
        for _d in (120, 500, 1200):             # 布局稳定后补算列宽（时序问题兜底）
            self.root.after(_d, self._relayout_tables)

    def _relayout_tables(self):
        try:
            self._last_target_w = None
            self._last_conv_w = None
            self._apply_col_widths(self.tree, self.TARGET_COL_SPEC, "content", self.tree.winfo_width())
            self._apply_col_widths(self.conv_tree, self.CONV_COL_SPEC, "name",
                                   self.conv_tree.winfo_width())
        except Exception:
            pass

    def _init_sash(self):
        """开局把右侧上下分隔条放到 ~58%，让会话表一次能显示 8 行。之后用户拖了就随用户。"""
        if getattr(self, "_sash_done", False):
            return
        try:
            h = self.pane.winfo_height()
            if h > 260:
                self.pane.sashpos(0, int(h * 0.66))   # 会话表要能一次看到 6~7 行
                self._sash_done = True
        except Exception:
            pass

    # ------------------------------------------------------------ 深色主题
    @staticmethod
    def _c(color, default=None):
        """旧配色 -> 当前主题的配色；认不出就返回 default。"""
        if color is None or color == "":
            return default
        c = str(color).strip().lower()
        if c in COLOR_MAP:
            return COLOR_MAP[c]
        if c in _CURRENT_COLORS:          # 已经是当前主题的颜色，原样返回
            return c
        return default

    @staticmethod
    def _frame_bg(w, cls="Frame"):
        """容器底色：首次按颜色认角色并记住，之后按角色取当前主题的颜色。"""
        col = {"main": BG_MAIN, "side": BG_SIDE, "line": BG_LINE,
               "head": BG_HEAD, "input": BG_INPUT, "card": BG_CARD}
        role = getattr(w, "_dsk_role", None)
        if role is None:
            try:
                cur = str(w.cget("bg")).strip().lower()
            except Exception:
                cur = ""
            hits = [r for r, c in col.items() if str(c).lower() == cur]
            if cls in ("Tk", "Toplevel"):
                role = "main"
            elif len(hits) == 1:
                role = hits[0]                      # 颜色唯一 -> 就按颜色认
            else:
                # 颜色在浅色里是"多义"的（#ffffff 既是侧栏又是卡片）：
                # 继承父容器的角色——侧栏里的白框还是侧栏，卡片里的白框还是卡片。
                prole = getattr(getattr(w, "master", None), "_dsk_role", None)
                if prole == "side":
                    role = "side"
                elif prole in ("card", "line", "head", "input"):
                    role = "card"
                else:
                    role = "card"
            try:
                w._dsk_role = role
            except Exception:
                pass
        return col.get(role, BG_CARD)

    def _apply_theme(self, widget=None, bg=None):
        """把整棵控件树刷成深色（新增的对话框建完后也调一次）。

        规则：容器换底色并把底色传给子控件；文字/勾选框底色跟随父容器；
        ttk 控件由 _style_ttk() 统一处理，这里跳过。
        """
        w = widget if widget is not None else self.root
        bg = bg or BG_MAIN
        if isinstance(w, (RoundedButton, ToggleSwitch)):
            w.apply_theme()          # Canvas 自绘：底色跟父容器，配色重算
            return
        try:
            cls = w.winfo_class()
        except Exception:
            return
        if cls in ("Frame", "Labelframe", "Tk", "Toplevel"):
            # 容器保留自己的"语义底色"（页面底/侧栏/卡片/描边）。不能一律刷成卡片白——
            # 之前 content/mid/top 这些本该是页面底色的容器被刷白，右边和左边就分不出来。
            # 判断方式：**首次**（此时主题是正确的）拿它当时的颜色跟当前调色板比对，记住角色；
            # 之后换主题一律按角色取色（白色侧栏/白色卡片在深色里要变成不同的颜色，
            # 靠"颜色->颜色"反查会二义：深色主题里也有 #ffffff）。
            new_bg = self._frame_bg(w, cls)
            w.configure(bg=new_bg)
            if cls in ("Labelframe", "Toplevel"):
                try:
                    w.configure(fg=FG)
                except Exception:
                    pass
            bg = new_bg
        elif cls == "Label":
            try:
                fg = self._c(w.cget("fg"), FG)
                w.configure(bg=bg, fg=fg)
            except Exception:
                pass
        elif cls == "Button":
            try:
                cur_bg = self._c(w.cget("bg"), None)
                is_accent = cur_bg == ACCENT
                w.configure(bg=(ACCENT if is_accent else BG_BTN),
                            fg="#ffffff" if is_accent else FG,
                            activebackground=ACCENT_HOVER if is_accent else BG_HEAD,
                            activeforeground="#ffffff" if is_accent else FG,
                            relief="flat", borderwidth=0, highlightthickness=0)
            except Exception:
                pass
        elif cls in ("Checkbutton", "Radiobutton"):
            try:
                w.configure(bg=bg, fg=FG, selectcolor=BG_INPUT,
                            activebackground=bg, activeforeground=FG,
                            highlightthickness=0, borderwidth=0)
            except Exception:
                pass
        elif cls in ("Entry", "Text", "Listbox"):
            try:
                w.configure(bg=BG_INPUT, fg=FG, insertbackground=FG,
                            selectbackground=ACCENT, selectforeground="#ffffff",
                            relief="flat", highlightthickness=0)
                if cls == "Text":
                    w.configure(state=w.cget("state"))
            except Exception:
                pass
        elif cls == "Menu":
            try:
                w.configure(bg=BG_PANEL, fg=FG, activebackground=ACCENT,
                            activeforeground="#ffffff", borderwidth=0, relief="flat")
            except Exception:
                pass
        for ch in w.winfo_children():
            self._apply_theme(ch, bg)

    def _style_ttk(self):
        """ttk 控件（表格/滚动条/下拉/分隔条）的深色样式。"""
        try:
            st = ttk.Style()
            try:
                st.theme_use("clam")          # clam 才允许改底色
            except Exception:
                pass
            st.configure("Treeview", background=BG_CARD, fieldbackground=BG_CARD,
                         foreground=FG, borderwidth=0, relief="flat")
            # 细滚动条：去掉上下箭头，只留一条拇指（系统粗箭头很"廉价"）
            st.layout("Slim.Vertical.TScrollbar", [
                ('Vertical.Scrollbar.trough', {'children': [
                    ('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})],
                    'sticky': 'ns'})])
            st.configure("Slim.Vertical.TScrollbar", background="#d5d8de", troughcolor=BG_CARD,
                         bordercolor=BG_CARD, arrowcolor=BG_CARD, width=8, relief="flat")
            st.map("Slim.Vertical.TScrollbar", background=[("active", "#b9bdc6")])
            # 横向的那条（只在窗口小到放不下时才出现，见 _set_hscroll）
            st.layout("Slim.Horizontal.TScrollbar", [
                ('Horizontal.Scrollbar.trough', {'children': [
                    ('Horizontal.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})],
                    'sticky': 'ew'})])
            st.configure("Slim.Horizontal.TScrollbar", background="#d5d8de", troughcolor=BG_CARD,
                         bordercolor=BG_CARD, arrowcolor=BG_CARD, width=8, relief="flat")
            st.map("Slim.Horizontal.TScrollbar", background=[("active", "#b9bdc6")])
            # 只 map 背景（选中态变浅粉底）。**千万不要 map foreground**：
            # ttk 里 style 的 foreground map 会盖掉每一行的 tag 颜色，
            # 于是"已点亮=绿 / 待续=红"全部渲染成普通黑字（浅色改版那一轮就这么丢的，实测确认）。
            st.map("Treeview", background=[("selected", ACCENT_SOFT), ("!selected", BG_CARD)])
            st.configure("Treeview.Heading", background=BG_HEAD, foreground=FG_DIM,
                         relief="flat", borderwidth=0, padding=(6, 8),
                         font=("Microsoft YaHei", 9))
            st.map("Treeview.Heading", background=[("active", BG_HEAD), ("!active", BG_HEAD)])
            try:
                st.layout("Treeview.Heading", [
                    ('Treeheading.cell', {'sticky': 'nswe', 'children': [
                        ('Treeheading.border', {'sticky': 'nswe', 'children': [
                            ('Treeheading.padding', {'sticky': 'nswe', 'children': [
                                ('Treeheading.image', {'side': 'right', 'sticky': ''}),
                                ('Treeheading.text', {'sticky': 'we'})]})]})]})])
            except Exception:
                pass
            st.configure("TScrollbar", background=BG_BTN, troughcolor=BG_MAIN,
                         bordercolor=BG_MAIN, arrowcolor=FG_DIM, relief="flat")
            st.map("TScrollbar", background=[("active", BG_HEAD)])
            st.configure("TPanedwindow", background=BG_MAIN)
            st.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_BTN,
                         foreground=FG, arrowcolor=FG_DIM, bordercolor=BG_BTN,
                         lightcolor=BG_INPUT, darkcolor=BG_INPUT, selectbackground=BG_INPUT,
                         selectforeground=FG)
            # readonly 的下拉框要用 map 单独压色，否则还是系统浅色
            st.map("TCombobox",
                   fieldbackground=[("readonly", BG_INPUT), ("disabled", BG_INPUT)],
                   foreground=[("readonly", FG), ("disabled", FG_DIM)],
                   background=[("readonly", BG_BTN), ("active", BG_HEAD)],
                   arrowcolor=[("readonly", FG_DIM)])
            # 下拉展开的那张列表（Tk listbox）用 option 数据库设色
            for opt, val in (("*TCombobox*Listbox.background", BG_INPUT),
                             ("*TCombobox*Listbox.foreground", FG),
                             ("*TCombobox*Listbox.selectBackground", ACCENT),
                             ("*TCombobox*Listbox.selectForeground", "#ffffff"),
                             ("*TCombobox*Listbox.borderWidth", 0)):
                try:
                    self.root.option_add(opt, val)
                except Exception:
                    pass
            st.configure("TSizegrip", background=BG_MAIN)
        except Exception:
            pass

    def _btn(self, parent, text, cmd, accent=False, kind=None):
        """顶部/工具栏按钮（圆角自绘）。accent=True 或 kind="accent" 用主色。"""
        if kind is None:
            kind = "accent" if accent else "normal"
        b = RoundedButton(parent, text, cmd, kind=kind, font=FONT, padx=16, height=32)
        b.pack(side="left", padx=(0, 8))
        return b

    def _titlebar(self, win, dark=False):
        """标题栏跟着主题走（Win10 1809+ 的 DWMWA_USE_IMMERSIVE_DARK_MODE）。

        界面是浅色后台风，标题栏也必须浅色——之前从深色版留下来的一直是黑色标题栏，
        整窗截图里就是"上面一条黑边"。
        """
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())   # 别在这里 update_idletasks，会递归
            val = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):        # 新老系统属性号不同，都试一遍
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val),
                                                           ctypes.sizeof(val))
        except Exception:
            pass

    def _stripes(self, tree):
        """表格斑马纹：隔行一个很浅的底色，比纯色块精致。"""
        try:
            tree.tag_configure("odd", background=BG_ROW_ODD)
            tree.tag_configure("even", background=BG_ROW_EVEN)
            tree.tag_configure("added", background=BG_ROW_ADDED)
        except Exception:
            pass

    # ------------------------------------------------------------ 卡片
    def _card(self, parent, title, hint=""):
        """卡片：白底 + 1px 描边 + 内边距。

        （试过用 Canvas 画圆角底再把内容 create_window/place 上去，但画布会把内容裁掉——
        Tk 报的宽度和实际可见宽度不一致，表格后两列直接看不见。这里退一步用普通 Frame，
        靠描边 + 留白做出层次，稳定优先。）
        """
        outer = tk.Frame(parent, bg=BG_LINE)          # 1px 描边
        outer._dsk_role = "line"
        inner = tk.Frame(outer, bg=BG_CARD)
        inner._dsk_role = "card"
        inner.card_outer = outer
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        head = tk.Frame(inner, bg=BG_CARD)
        head.pack(fill="x", padx=16, pady=(14, 10))
        tk.Label(head, text=title, font=FONT_B, fg=FG, bg=BG_CARD).pack(side="left")
        lbl = tk.Label(head, text=hint, font=FONT_S, fg=FG_DIM, bg=BG_CARD)
        if hint:
            lbl.pack(side="left", padx=(8, 0), pady=(3, 0))
        inner.card_hint_label = lbl       # 卡片的副标题（后面可以被动态改写）
        return inner

    # ------------------------------------------------------------ 列表
    def _refresh_tree(self):
        try:
            self._apply_col_widths(self.tree, self.TARGET_COL_SPEC, "content",
                                   self.tree.winfo_width())
        except Exception:
            pass
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.target_photos = {}
        for idx, t in enumerate(self.cfg["targets"]):
            content = t.get("text") or ""
            if t.get("sticker_id"):
                content = (content + "＋表情").strip()        # 后缀尽量短，把宽度留给昵称
            if t.get("image_path"):
                content = (content + "＋图").strip()
            photo = self._load_avatar({"avatar_file": self._target_avatar_file(t)},
                                      self._target_metrics[0], self._target_metrics[1])
            if photo is not None:
                self.target_photos[str(idx)] = photo      # 留引用，否则图片被回收
            stripe = "added" if not t.get("enabled") else ("odd" if idx % 2 else "even")
            self.tree.insert("", "end", iid=str(idx), image=(photo or ""), tags=(stripe,),
                             values=(
                "✓" if t.get("enabled") else "✗",
                t["name"],
                "群聊" if t["kind"] == "group" else "好友",
                ", ".join(t.get("times") or []),
                content[:60]))

    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一个目标")
            return None
        return int(sel[0])

    def on_add(self):
        self._open_picker()

    # ------------------------------------------------------------ 操作说明
    HELP_TEXT = """【第一步】扫码登录（只需一次）
  1. 点左边那一栏的「扫码登录」
  2. 会弹出一个浏览器窗口，用手机抖音「扫一扫」扫上面的二维码
  3. 登录成功后程序会自己用它（窗口平时最小化在任务栏，读完/发完会自动关掉）

【第二步】把你的好友读进来
  4. 点右边「抖音会话」面板顶部的「读取抖音列表」
  5. 右边就会列出你抖音里的好友和群，带头像、连续天数、今日火花（读完会自动关掉浏览器窗口）

【第三步】把要续火花的人加到左边
  6. 在右边列表里「双击」某个好友就加进来了
  7. 他就出现在左边的「发送目标」里了；重复多少个就加多少个

【第四步】给他配上"发什么"和"几点发"
  8. 在左边「双击」那一行，打开编辑窗口
  9. 「发送文字」写你要发的话（比如：我来和你续火花了）
 10. 想带表情就点「附带超级表情」挑一个（没有的话先点左边那栏的「同步超级表情」）
 11. 「发送时间」写 09:30 这样的时间，想一天发两次就写 09:30, 21:00
 12. 点「确定」保存

【第五步】试一次
 13. 点左边那栏的「一键续火花」，它会立刻给左边所有启用的目标各发一条
 14. 发完右边会自动刷新，你能看到"最近消息"变成刚刚
 15. 怕发错？先去「设置」勾上「演练模式」，它只输入不发送，试完再取消勾选

【第六步】让它每天自己跑
 16. 点左边那栏的「启动自动模式」，按钮会变成「停止自动模式」
 17. 之后每天到点自动发；想停就点一下那个按钮

—————————————————————————————
其它常用操作
 · 右键左边某一行：编辑内容 / 续火花（只发这一个）/ 停用 / 复制昵称或会话 ID / 删除
 · 右键右边某一行：加入发送目标 / 复制昵称 / 复制会话 ID
 · 想少发某个人：右键他 →「停用」（灰色勾变成叉，就不会发了）
 · 右边列表里冒出来一堆不认识的人（认证账号/陌生人，没有火花）：
   勾上「只读有火花的」再读一次，或者右键某一行 →「从列表里删掉」
 · 想改时间或内容：双击左边那一行
 · 出问题：点左边那栏的「抖音窗口校准」，把 logs 文件夹里生成的那份报告发给开发者
 · 电脑重启后：双击 DouyinSparkKeeper.exe 即可，登录态和设置都还在
 · 设置/编辑这类弹窗内容多时右边会出现细滚动条，滚一下就能看到下面的项
 · 想开机自动跑：设置 → 浏览器卡片里勾「开机自启」，开机后直接待在托盘里、自动模式也会一并开始跑（不用再右键点）
 · 浏览器窗口：默认最小化在任务栏，不挡屏幕；要看得见就在「设置 → 浏览器窗口」里改成「正常弹出来」
"""

    def _show_help(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("操作说明")
        dlg.transient(self.root)
        dlg.geometry("640x620")
        head = tk.Frame(dlg)
        head.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(head, text="操作说明", font=FONT_TITLE).pack(side="left")
        tk.Label(head, text="  照着从上到下做一遍就行", font=FONT_S, fg="#888").pack(side="left")
        box = tk.Frame(dlg)
        box.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        txt = tk.Text(box, wrap="word", font=("Microsoft YaHei", 10), relief="flat",
                      padx=10, pady=8)
        sb = ttk.Scrollbar(box, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.insert("1.0", self.HELP_TEXT)
        txt.configure(state="disabled")
        foot = tk.Frame(dlg)
        foot.pack(fill="x", padx=14, pady=(0, 12))
        tk.Label(foot, text="同一份说明也在程序目录的《使用说明.md》里", font=FONT_S, fg="#888").pack(side="left")
        tk.Button(foot, text="知道了", font=FONT, width=10, command=dlg.destroy,
                  padx=8, pady=3, relief="groove").pack(side="right")
        self._apply_theme(dlg)
        self._titlebar(dlg)
        dlg.update_idletasks()
        try:
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - 640) // 2)
            y = self.root.winfo_rooty() + 60
            dlg.geometry("+%d+%d" % (max(0, x), max(0, y)))
        except Exception:
            pass
        dlg.lift()
        return dlg

    # ------------------------------------------------------------ 右键菜单
    @staticmethod
    def _short(s, n=14):
        s = (s or "").strip()
        return s if len(s) <= n else s[:n] + "…"

    def _copy_to_clipboard(self, text, what="内容"):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text or "")
            msg = "已复制%s：%s" % (what, self._short(text, 24))
        except Exception as e:
            msg = "复制失败：%s" % e
        # 菜单是在 Tk 主线程里触发的，直接写日志面板（不用走 ui_queue）
        self._append_log("· " + msg)

    def _popup(self, menu, event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _build_target_menu(self, idx=None):
        """目标列表的右键菜单。idx=None 表示点在空白处。"""
        m = tk.Menu(self.root, tearoff=0)
        if idx is not None and 0 <= idx < len(self.cfg["targets"]):
            t = self.cfg["targets"][idx]
            bound = bool((t.get("conv_id") or "").strip())
            m.add_command(label="编辑内容", command=self.on_edit)
            m.add_command(label="续火花", command=self.on_send_one)
            m.add_command(label="%s" % ("停用" if t.get("enabled") else "启用"),
                          command=self.on_toggle)
            m.add_separator()
            m.add_command(label="复制昵称", command=lambda: self._copy_to_clipboard(t.get("name"), "昵称"))
            m.add_command(label="复制会话 ID",
                          command=lambda: self._copy_to_clipboard(t.get("conv_id") or "", "会话 ID"),
                          state=("normal" if bound else "disabled"))
            m.add_command(label="会话 ID：%s" % ("已绑定" if bound else "未绑定（读一次列表会自动绑定）"),
                          state="disabled")
            m.add_separator()
            m.add_command(label="删除此目标", command=self.on_delete)
        return m

    def on_target_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return "break"                  # 空白处不弹（菜单项都已挪走）
        self.tree.selection_set(iid)
        try:
            idx = int(iid)
        except Exception:
            idx = None
        self._popup(self._build_target_menu(idx), event)
        return "break"

    def _build_conv_menu(self, iid=None):
        """右侧会话列表的右键菜单。"""
        m = tk.Menu(self.root, tearoff=0)
        row = None
        for r in (getattr(self, "conv_rows", None) or []):
            if self._row_key(r) == iid:
                row = r
                break
        if row is not None:
            added = self._is_added(row)
            m.add_command(label="加入发送目标", command=self.on_conv_add,
                          state=("disabled" if added else "normal"))
            m.add_command(label="复制昵称", command=lambda: self._copy_to_clipboard(row.get("name"), "昵称"))
            m.add_command(label="复制会话 ID",
                          command=lambda: self._copy_to_clipboard(row.get("conv_id") or "", "会话 ID"),
                          state=("normal" if (row.get("conv_id") or "") else "disabled"))
            days = (row.get("days") or "").strip()
            m.add_command(label="火花：%s%s" % (days or "无", " 天" if days else ""), state="disabled")
            m.add_separator()
            m.add_command(label="从列表里删掉", command=self.on_conv_delete)
        return m

    def on_conv_delete(self):
        """把选中的会话从右侧列表里删掉（只删本地这份列表，抖音里的会话不动）。"""
        tree = getattr(self, "conv_tree", None)
        if tree is None:
            return 0
        keys = set(tree.selection())
        if not keys:
            return 0
        names = []
        keep = []
        for r in (getattr(self, "conv_rows", None) or []):
            if self._row_key(r) in keys:
                names.append(r.get("name") or "")
            else:
                keep.append(r)
        if not names:
            return 0
        self.conv_rows = keep
        try:
            self._save_conversations(keep)
        except Exception:
            pass
        self._fill_conv_panel(keep)
        logger.info("已从列表里删掉 %d 个会话：%s（只删本地这份列表，抖音里没动）"
                    % (len(names), "、".join(n for n in names[:5])), self.logs_dir)
        self._append_log("· 已从列表里删掉 %d 个会话（重新「读取抖音列表」还会回来）" % len(names))
        return len(names)

    def on_conv_right_click(self, event):
        tree = getattr(self, "conv_tree", None)
        if tree is None:
            return "break"
        iid = tree.identify_row(event.y)
        if not iid:
            return "break"                  # 空白处不弹
        tree.selection_set(iid)
        self._popup(self._build_conv_menu(iid), event)
        return "break"

    def on_edit(self):
        idx = self._selected_index()
        if idx is None:
            return
        self._edit_dialog(idx)

    # ------------------------------------------------------------ 选人弹窗
    @staticmethod
    def _norm_name(s):
        """昵称归一化：去掉零宽字符与所有空白（含 \\xa0），统一小写。"""
        s = re.sub(r"[\u200b-\u200f\ufe0e\ufe0f\u2060]", "", s or "")
        return re.sub(r"\s+", "", s).lower()

    def _row_key(self, row):
        """会话列表行的唯一键（Treeview 的 iid）：优先稳定会话 ID。"""
        return row.get("conv_id") or ("name:" + self._norm_name(row.get("name") or ""))

    def _row_match_keys(self, row):
        """一行能用来判断"是不是已经添加过"的所有键：会话 ID + 归一化昵称。"""
        keys = set()
        cid = (row.get("conv_id") or "").strip()
        if cid:
            keys.add(cid)
        n = self._norm_name(row.get("name") or "")
        if n:
            keys.add("name:" + n)
        return keys

    def _target_key(self, t):
        cid = (t.get("conv_id") or "").strip()
        return cid or ("name:" + self._norm_name(t.get("name") or ""))

    def _existing_keys(self):
        """已添加过的东西的键集合。

        老配置只有昵称、没有会话 ID，所以这里同时放"ID 键"和"昵称键"，
        否则它们会跟列表行匹配不上，被当成未添加而重复导入。
        """
        keys = set()
        for t in self.cfg["targets"]:
            cid = (t.get("conv_id") or "").strip()
            if cid:
                keys.add(cid)
            n = self._norm_name(t.get("name") or "")
            if n:
                keys.add("name:" + n)
        return keys

    def _is_added(self, row, existing=None):
        keys = existing if existing is not None else self._existing_keys()
        return bool(self._row_match_keys(row) & keys)

    def _open_picker(self):
        old = getattr(self, "picker_dlg", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass

        dlg = tk.Toplevel(self.root)
        self.picker_dlg = dlg
        dlg.title("添加目标 · 从抖音会话列表选")
        dlg.transient(self.root)

        head = tk.Frame(dlg)
        head.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(head, text="添加目标", font=FONT_TITLE).pack(side="left")
        tk.Label(head, text="  直接读取你的抖音会话列表，点一行即勾选（不用手打昵称）",
                 font=FONT, fg="#666").pack(side="left")

        bar = tk.Frame(dlg)
        bar.pack(fill="x", padx=12, pady=4)
        tk.Label(bar, text="过滤", font=FONT).pack(side="left")
        self.picker_filter = tk.StringVar(value="")
        ent = tk.Entry(bar, textvariable=self.picker_filter, font=FONT, width=22)
        ent.pack(side="left", padx=6)
        ent.bind("<KeyRelease>", lambda e: self._fill_picker())
        tk.Button(bar, text="刷新抖音列表", font=FONT, command=self._picker_refresh).pack(side="left", padx=4)
        tk.Button(bar, text="在抖音搜索…", font=FONT, command=self._picker_search).pack(side="left", padx=4)
        tk.Button(bar, text="手动输入昵称", font=FONT,
                  command=lambda: (dlg.destroy(), self._edit_dialog(None))).pack(side="left", padx=4)

        wrap = tk.Frame(dlg)
        wrap.pack(fill="both", expand=True, padx=12, pady=6)
        cols = ("mark", "name", "kind", "days", "today", "added")
        self.picker_tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                        height=14, selectmode="browse")
        for c, t, w, a in (("mark", "选", 34, "center"),
                           ("name", "昵称 / 群名", 240, "w"),
                           ("kind", "类型", 66, "center"),
                           ("days", "连续天数", 80, "center"),
                           ("today", "今日火花", 150, "center"),
                           ("added", "状态", 90, "center")):
            self.picker_tree.heading(c, text=t, anchor=a)
            self.picker_tree.column(c, width=w, anchor=a, stretch=(c == "name"))
        self.picker_tree.pack(side="left", fill="both", expand=True)
        psb = ttk.Scrollbar(wrap, orient="vertical", command=self.picker_tree.yview)
        psb.pack(side="right", fill="y")
        self.picker_tree.configure(yscrollcommand=psb.set)
        self.picker_tree.bind("<Button-1>", self._picker_click)
        self.picker_tree.tag_configure("ok", foreground=OK_C)
        self.picker_tree.tag_configure("waiting", foreground=WAIT_C)
        self.picker_tree.tag_configure("sent", foreground=SENT_C)
        self.picker_tree.tag_configure("dim", foreground=DIM_C)
        self.picker_tree.tag_configure("added", background=BG_ROW_ADDED)

        self.picker_hint = tk.Label(dlg, text="正在读取抖音会话列表…", font=FONT, fg="#666", anchor="w")
        self.picker_hint.pack(fill="x", padx=12)

        foot = tk.Frame(dlg)
        foot.pack(fill="x", padx=12, pady=(4, 10))

        tk.Label(foot, text="导入时统一填写的文字", font=FONT).pack(side="left")
        self.picker_text = tk.StringVar(value="")
        tk.Entry(foot, textvariable=self.picker_text, font=FONT, width=18).pack(side="left", padx=6)
        tk.Label(foot, text="（可留空，之后再编辑）", font=("Microsoft YaHei", 8), fg="#888").pack(side="left")

        tk.Button(foot, text="关闭", font=FONT, command=dlg.destroy).pack(side="right", padx=4)
        tk.Button(foot, text="一键导入全部未添加", font=FONT,
                  command=self._picker_import_all).pack(side="right", padx=4)
        tk.Button(foot, text="添加所选", font=FONT,
                  command=self._picker_add_selected).pack(side="right", padx=4)
        tk.Button(foot, text="清空选择", font=FONT,
                  command=self._picker_clear_marks).pack(side="right", padx=4)
        tk.Button(foot, text="全选未添加", font=FONT,
                  command=self._picker_mark_all).pack(side="right", padx=4)

        self.picker_marks = set()
        self._apply_theme(dlg)
        self._titlebar(dlg)
        dlg.minsize(760, 420)
        dlg.update_idletasks()
        try:
            w, h = 880, 560
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - w) // 2)
            y = self.root.winfo_rooty() + 60
            dlg.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))
        except Exception:
            dlg.geometry("880x560")
        dlg.lift()

        # 列表数据可能已经有缓存，先画一遍再后台刷新
        if getattr(self, "conv_rows", None):
            self._fill_picker()
        self._picker_refresh()

    def _picker_refresh(self):
        self._refresh_conversations()

    async def _close_browser(self, c, why=""):
        """活干完了就把浏览器窗口关掉（用户要求：读完列表 / 确认发出消息之后）。

        下次点按钮时 `_ensure_client()` 会自动重开（登录态在 browser_data 里，不用重新扫码）。
        """
        if c is None:
            return
        try:
            await c.close()
        except Exception as e:
            logger.warn("关闭浏览器时出错（可忽略）：%s"
                        % friendly_error(e).replace("\n", " "), self.logs_dir)
            return
        if self.client is c:
            self.client = None
        logger.info("已自动关闭浏览器窗口%s（下次点按钮会重新打开，登录态还在）"
                    % ("（%s）" % why if why else ""), self.logs_dir)

    # ------------------------------------------------------------ 只留有火花的会话
    def _only_spark(self):
        v = getattr(self, "only_spark_v", None)
        if v is not None:
            try:
                return bool(v.get())
            except Exception:
                pass
        return bool((self.cfg.get("ui") or {}).get("only_spark", True))

    def _on_only_spark_toggle(self):
        on = self._only_spark()
        try:
            self.cfg.setdefault("ui", {})["only_spark"] = on
            self.cfg = config.save(self.cfg)
        except Exception:
            pass
        logger.info("读取抖音列表：%s" % ("只留有火花的会话（没有火花的不读进来）" if on
                                          else "把全部会话都读进来"), self.logs_dir)

    def _filter_conv_rows(self, rows, when=""):
        """按"只读有火花的"筛一遍：没有火花图标的（认证账号、陌生人）读进来也没法续。"""
        rows = rows or []
        if not self._only_spark():
            return rows
        kept = [r for r in rows if self._spark_bucket(r.get("state")) != "none"]
        dropped = len(rows) - len(kept)
        if dropped:
            logger.info("%s读到 %d 个会话，其中 %d 个没有火花（认证账号/陌生人等），已按设置跳过，保留 %d 个"
                        % (when, len(rows), dropped, len(kept)), self.logs_dir)
        return kept

    async def _load_conversations(self):
        c = await self._ensure_client()
        if not await c.ensure_logged_in(120):
            self._set_login_state(False)
            raise RuntimeError("尚未登录，请先点「扫码登录」")
        self._set_login_state(True)
        await c.goto_chat()
        rows = await c.list_conversations()
        logger.info("已读取抖音会话列表：%d 项" % len(rows), self.logs_dir)
        rows = self._filter_conv_rows(rows, "")
        try:
            avatars = await c.fetch_avatars(rows, config.AVATARS_DIR)
        except Exception as e:
            avatars = {}
            logger.warn("头像获取失败（不影响发送）：%s"
                        % friendly_error(e).replace("\n", " "), self.logs_dir)
        for r in rows:
            key = self._row_key(r)
            r["avatar_file"] = avatars.get(key, "")
        logger.info("头像缓存：%d/%d 个（%s）"
                    % (len(avatars), len(rows), config.AVATARS_DIR), self.logs_dir)
        for r in rows:
            if self._spark_bucket(r.get("state")) == "unknown":
                logger.warn("「%s」的火花图标不认识，状态未知：%s"
                            % (r.get("name"), r.get("icon") or "(无图标)"), self.logs_dir)
        self.ui_queue.put(("convlist", rows))
        if rows:
            # 确认真的读到了才关（用户要求），读了个空不关，方便接着排查
            await self._close_browser(c, "读取列表完成")

    # ----- 右侧「抖音会话」面板 -----
    def on_load_conversations(self):
        self._refresh_conversations()

    def _load_saved_conversations(self):
        """读取上次保存的会话列表；头像路径按文件名重新解析（程序目录搬了也不怕）。"""
        data = config.load_conversations()
        rows = []
        for r in (data.get("rows") or []):
            if not isinstance(r, dict) or not (r.get("name") or "").strip():
                continue
            f = r.get("avatar_file") or ""
            base = os.path.basename(f)
            if base:
                p = os.path.join(config.AVATARS_DIR, base)
                r["avatar_file"] = p if os.path.exists(p) else ""
            else:
                r["avatar_file"] = ""
            rows.append(r)
        self.conv_rows = rows
        self.conv_updated_at = data.get("updated_at") or ""
        return rows

    def _save_conversations(self, rows):
        """把会话列表存盘（头像只存文件名，路径由程序目录推导）。"""
        out = []
        for r in rows:
            out.append({
                "conv_id": r.get("conv_id") or "",
                "sec_uid": r.get("sec_uid") or "",
                "name": r.get("name") or "",
                "days": r.get("days") or "",
                "state": r.get("state") or "none",
                "time": r.get("time") or "",
                "hint": r.get("hint") or "",
                "icon": r.get("icon") or "",
                "avatar_file": os.path.basename(r.get("avatar_file") or ""),
            })
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            config.save_conversations({"updated_at": stamp, "rows": out})
            self.conv_updated_at = stamp
        except Exception as e:
            logger.warn("会话列表保存失败（不影响本次使用）：%s" % e, self.logs_dir)
        return stamp

    def _refresh_conversations(self):
        if getattr(self, "conv_loading", False):
            return
        self.conv_loading = True
        self._set_status("正在读取抖音会话列表…")
        if getattr(self, "picker_hint", None) is not None:
            try:
                self.picker_hint.configure(text="正在读取抖音会话列表…（首次会打开 Edge）")
            except Exception:
                pass
        if getattr(self, "conv_hint", None) is not None:
            try:
                self.conv_hint.configure(text="正在读取…（首次会打开 Edge）")
            except Exception:
                pass
        self._submit(self._load_conversations, "读取抖音列表")

    def _on_conv_rows(self, rows):
        self._avatar_cache = {}                # 头像可能换了，丢掉旧的位图缓存
        try:
            self._save_conversations(rows)     # 存盘：关掉再打开右侧列表还在
        except Exception:
            pass
        # 顺手把稳定会话 ID 回填到只有名字的老目标上（用户不用做任何事）
        try:
            self._backfill_conv_ids(rows)
        except Exception:
            pass
        self._fill_picker(rows)        # 选人弹窗开着的话一起刷新
        self._fill_conv_panel(rows)

    # 两张表的列宽都按面板宽度等比分配：写死列宽在不同窗口大小下会把后面的列切掉
    # 最小宽度是照"表头和最长内容都放得下"量出来的（用真实字体量，别凭感觉）
    TARGET_COL_SPEC = (("#0", 0.10, AVATAR_COL_W), ("enabled", 0.07, 44), ("name", 0.33, 208),
                       ("kind", 0.06, 46), ("times", 0.12, 80), ("content", 0.0, 120))

    @staticmethod
    def _set_col(tree, col, w, minwidth):
        """列宽没变就不设——设置列宽会再触发 <Configure>，无脑设会形成死循环。"""
        try:
            if int(tree.column(col, "width")) != int(w):
                tree.column(col, width=int(w), minwidth=int(minwidth), stretch=False)
        except Exception:
            pass

    @classmethod
    def _apply_col_widths(cls, tree, spec, absorber, total):
        """按面板宽度分配列宽，分三档：

        1. 宽裕（放得下各列的"舒适宽度"）→ 按比例给，absorber 列吃掉余量；
        2. 有点挤 → 在"可读下限"和"舒适宽度"之间按同一比例插值；
        3. 连可读下限都放不下 → 保留下限，**打开横向滚动条**。

        "可读下限"= 表头文字宽度 + 内边距（保证表头不会互相压字）。
        以前只有第 1、3 档，而第 3 档的下限是写死的 18px —— 窗口一缩，
        昵称/类型/时间全被压成 18px，表头叠在一起、内容只剩一个字（用户截图里的样子）。
        """
        if tree is None or total < 120:
            return
        # Treeview 的"请求宽度" = 列宽合计 + 边框开销，而请求宽度又决定它拿到多少宽度；
        # 开销估小了就会"每轮长大 2px"死循环，所以这里按当前状态实测开销。
        overhead = 4
        try:
            cols = [c for c, _r, _m in spec]
            cur = sum(int(tree.column(c, "width")) for c in cols)
            # 注意：列宽合计 > 表格宽度时，winfo_reqwidth() 会滞后在旧的大值上（Tk 的请求尺寸
            # 下一次 idle 才重算），直接拿它当开销会算出上百像素的假开销，把表格误判成"放不下"——
            # 实测缩窄一次以后就再也回不到正常列宽。所以夹在 2..12 之间。
            overhead = max(2, min(12, int(tree.winfo_reqwidth()) - cur))
        except Exception:
            pass
        total -= max(2, overhead)
        # 头像列：#0 按比例给，但夹在 [最小列宽, 列宽上限] 之间——
        # 这样窗口变窄时它不会跟着压到把头像裁掉，而是让头像自己缩小（见 _avatar_metrics）
        w0 = None
        for col, ratio, minimum in spec:
            if col == "#0":
                w0 = max(AVATAR_MIN_COL_W, min(minimum, int(total * ratio)))
        if w0 is None:                      # 没有头像列（例如选人弹窗）就当普通表处理
            w0 = 0
        rest = total - w0
        others = [(c, r, m) for c, r, m in spec if c != "#0"]
        floors = cls._col_floors(tree, spec)
        need = sum(m for _c, _r, m in others)
        floor_sum = sum(floors.get(c, 24) for c, _r, _m in others)
        scroll = False
        if rest >= need:
            fixed = 0
            for col, ratio, minimum in others:
                if col == absorber:
                    continue
                fixed += max(minimum, int(rest * ratio))
            for col, ratio, minimum in others:
                w = max(minimum, rest - fixed) if col == absorber else max(minimum, int(rest * ratio))
                cls._set_col(tree, col, w, minimum)
        elif rest >= floor_sum:
            # 挤一挤：下限和舒适宽度之间线性插值（绝不越过下限）
            span = max(1, need - floor_sum)
            f = float(rest - floor_sum) / span
            for col, _ratio, minimum in others:
                fl = floors.get(col, 24)
                cls._set_col(tree, col, int(fl + (minimum - fl) * f), fl)
        else:
            # 连可读下限都放不下：保留下限，让表格可以左右滚（小屏/极窄窗口才会走到这里）
            for col, _ratio, _minimum in others:
                fl = floors.get(col, 24)
                cls._set_col(tree, col, fl, fl)
            scroll = True
        if w0:
            cls._set_col(tree, "#0", w0, AVATAR_MIN_COL_W)
        cls._set_hscroll(tree, scroll)

    # 文字列的可读下限（表头之外，至少还要看得见几个字）
    TEXT_FLOOR = {"name": 90, "content": 76}

    @classmethod
    def _col_floors(cls, tree, spec):
        """每列的"可读下限"：表头文字必须放得下，文字列再留几个字。"""
        try:
            f = tkfont.Font(font=ttk.Style().lookup("Conv.Treeview", "font"))
        except Exception:
            f = tkfont.Font(font=FONT)
        floors = {}
        for col, _ratio, _minimum in spec:
            if col == "#0":
                floors[col] = AVATAR_MIN_COL_W
                continue
            try:
                head = f.measure(tree.heading(col, "text"))
            except Exception:
                head = 24
            floors[col] = max(24, head + 12, cls.TEXT_FLOOR.get(col, 0))
        return floors

    @staticmethod
    def _set_hscroll(tree, on):
        """放不下时才出现的横向滚动条（正常窗口尺寸下永远不出现）。"""
        bar = getattr(tree, "dsk_hbar", None)
        if bar is None:
            return
        on = bool(on)
        if on == bool(getattr(tree, "dsk_hbar_on", False)):
            return
        try:
            if on:
                # 必须 before=tree：树是 fill="both"+expand 的，后 pack 的兄弟分不到空间，
                # 会变成 mapped=0、高 1px 的"隐形滚动条"（实测踩过）。
                bar.pack(side="bottom", fill="x", before=tree)
                tree.dsk_hbar_on = True
            else:
                bar.pack_forget()
                tree.dsk_hbar_on = False
                tree.xview_moveto(0)
        except Exception:
            pass

    def _on_target_tree_resize(self, event=None):
        tree = getattr(self, "tree", None)
        if tree is None:
            return
        w = int(event.width) if event is not None else tree.winfo_width()
        if w == getattr(self, "_last_target_w", None):
            return                      # 自己设列宽也会触发 Configure，宽度没变就别再算
        self._last_target_w = w
        self._apply_col_widths(tree, self.TARGET_COL_SPEC, "content", w)
        self._ensure_avatar_size("target")

    # 右侧会话表的列宽按面板宽度等比分配：写死列宽在不同窗口大小下会把后面的列切掉
    CONV_COL_SPEC = (("#0", 0.15, AVATAR_COL_W), ("name", 0.0, 96), ("days", 0.09, 44),
                     ("today", 0.15, 80), ("time", 0.20, 60))

    @staticmethod
    def _avatar_metrics(col_w):
        """头像列宽 -> (头像边长, 左右留白, 与名字的间隙)。

        窗口窄了就整体缩小：头像变小、留白和间隙也变小，**绝不会被列边界裁掉**。
        """
        col_w = max(AVATAR_MIN_COL_W, int(col_w))
        # 头像边长跟列宽挂钩（连续、单调），留白取"让左右视觉留白相等"的值：
        # 图片从 x=AVATAR_INSET 开始画，左留白 = INSET + pad，右留白 = col_w - INSET - pad - size，
        # 两者相等 -> pad = (col_w - 2*INSET - size) // 2
        avail = col_w - 2 * AVATAR_INSET
        size = max(AVATAR_MIN_SIZE, min(AVATAR_SIZE, int(round(col_w * 0.6))))
        if (avail - size) % 2:            # 奇数时把头像缩 1px，保证左右留白严格相等
            size = max(AVATAR_MIN_SIZE, size - 1)
        pad = max(2, (avail - size) // 2)
        gap = pad
        return size, pad, gap

    @staticmethod
    def _spark_bucket(state):
        """图标状态 -> 业务状态。统一用引擎那套判定，界面里不再自己写一遍
        （曾经界面用 startswith("normal") 判断，把"认不出的图标"也显示成已点亮）。"""
        try:
            return DouyinClient._classify_spark(state)
        except Exception:
            return "unknown"

    def _on_conv_tree_resize(self, event=None):
        tree = getattr(self, "conv_tree", None)
        if tree is None:
            return
        w = int(event.width) if event is not None else tree.winfo_width()
        if w == getattr(self, "_last_conv_w", None):
            return
        self._last_conv_w = w
        self._apply_col_widths(tree, self.CONV_COL_SPEC, "name", w)
        self._ensure_avatar_size("conv")

    def _ensure_avatar_size(self, which):
        """头像列宽变了就按新尺寸重画头像（重画会再次触发 Configure，所以用标志防递归）。"""
        if getattr(self, "_avatar_resizing", False):
            return
        tree = self.conv_tree if which == "conv" else self.tree
        old = self._conv_metrics if which == "conv" else self._target_metrics
        try:
            new = self._avatar_metrics(tree.column("#0", "width"))
        except Exception:
            return
        if new == old:
            return
        self._avatar_resizing = True
        try:
            if which == "conv":
                self._conv_metrics = new
                self._fill_conv_panel(self.conv_rows)
            else:
                self._target_metrics = new
                self._refresh_tree()
        except Exception:
            pass
        finally:
            self._avatar_resizing = False

    def _find_avatar_file(self, key):
        """按会话 ID 在头像缓存目录里找文件。"""
        if not key:
            return ""
        stem = avatar_stem(key)
        for ext in IMAGE_EXTS:
            p = os.path.join(config.AVATARS_DIR, stem + ext)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                return p
        return ""

    def _target_avatar_file(self, t):
        """目标对应的头像文件：先用会话 ID；没有 ID 就用昵称去会话缓存里配一个。"""
        path = self._find_avatar_file((t.get("conv_id") or "").strip())
        if path:
            return path
        n = self._norm_name(t.get("name") or "")
        if not n:
            return ""
        for r in (getattr(self, "conv_rows", None) or []):
            if self._norm_name(r.get("name") or "") != n:
                continue
            path = r.get("avatar_file") or ""
            if path and os.path.exists(path):
                return path
            path = self._find_avatar_file((r.get("conv_id") or "").strip())
            if path:
                return path
        return ""

    # ------------------------------------------------------------ 火花图片（超级表情）
    @staticmethod
    def _sticker_label(thumb_path):
        """贴纸缩略图文件名里藏着名字：<id>-ts-<hex(名字.png)>。"""
        try:
            hexpart = os.path.basename(thumb_path).split("-ts-", 1)[1].rsplit(".", 1)[0]
            return binascii.unhexlify(hexpart).decode("utf-8")
        except Exception:
            return ""

    def _spark_sticker_path(self):
        """找到「续火花」那张超级表情缩略图。"""
        items = (self.stickers or {}).get("items") or []
        for it in items:
            thumb = it.get("thumb") or ""
            if thumb and "续火花" in self._sticker_label(thumb):
                path = thumb if os.path.isabs(thumb) else os.path.join(config.BASE_DIR, thumb)
                if os.path.exists(path):
                    return path
        # 兜底：按已知 id（续火花）找
        for it in items:
            if "1687263281313" in (it.get("id") or ""):
                thumb = it.get("thumb") or ""
                path = thumb if os.path.isabs(thumb) else os.path.join(config.BASE_DIR, thumb)
                if os.path.exists(path):
                    return path
        return ""

    @staticmethod
    def _knock_out_bg(im, bg=(37, 38, 50), soft=26, tol=60):
        """把贴纸截图里的深色底抠成透明（深色面板下截的图，直接贴到浅色界面上会是一块黑方块）。"""
        px = im.load()
        w, h = im.size
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                d = max(abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2]))
                if d <= soft:
                    px[x, y] = (r, g, b, 0)
                elif d < tol:
                    px[x, y] = (r, g, b, int(255 * (d - soft) / float(tol - soft)))
        return im

    def _spark_photo(self, size):
        """「续火花」火花图（Tk 图片，缓存在 _avatar_cache 里防回收）。"""
        key = ("spark", int(size))
        if key in self._avatar_cache:
            return self._avatar_cache[key]
        photo = None
        path = self._spark_sticker_path()
        if path:
            try:
                from PIL import Image, ImageTk
                im = Image.open(path).convert("RGBA")
                im = self._knock_out_bg(im)
                im = im.resize((int(size), int(size)), getattr(Image, "Resampling", Image).LANCZOS)
                photo = ImageTk.PhotoImage(im)
            except Exception:
                photo = None
        self._avatar_cache[key] = photo
        return photo

    def _load_avatar(self, row, size=AVATAR_SIZE, pad=AVATAR_PAD):
        """把头像读成 Tk 图片。

        画布比头像大一圈（左右各 AVATAR_PAD、上下各 AVATAR_PAD_Y），头像画在正中间；
        **留白必须左右对称**：Treeview 居中时居中的是整张画布，留白不对称头像就会偏。
        头像与名字之间的空隙由头像列的宽度（AVATAR_COL_W 里的 AVATAR_GAP）提供。
        """
        path = row.get("avatar_file") or ""
        if not path or not os.path.exists(path):
            return None
        cache_key = (path, size, int(pad), min(AVATAR_PAD_Y, max(2, int(pad))))   # 留白也算特征，否则换留白会命中旧图
        cached = self._avatar_cache.get(cache_key)
        if cached is not None:
            return cached
        pad = max(2, int(pad))
        pad_y = min(AVATAR_PAD_Y, pad)
        cw = size + pad * 2
        ch = size + pad_y * 2
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            im = im.convert("RGBA")
            im.thumbnail((size, size), getattr(Image, "Resampling", Image).LANCZOS)
            canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            x = pad + (size - im.width) // 2
            y = pad_y + (size - im.height) // 2
            canvas.paste(im, (x, y), im)
            photo = ImageTk.PhotoImage(canvas)
            self._avatar_cache[cache_key] = photo
            return photo
        except Exception:
            pass
        try:
            img = tk.PhotoImage(file=path)
            factor = max(1, img.width() // size)
            small = img.subsample(factor, factor)
            canvas = tk.PhotoImage(width=cw, height=ch)
            canvas.tk.call(canvas, "copy", small, "-to", pad, pad_y)
            self._avatar_cache[cache_key] = canvas
            return canvas
        except Exception:
            return None

    def _fill_conv_panel(self, rows=None):
        tree = getattr(self, "conv_tree", None)
        if tree is None:
            return
        if rows is not None:
            self.conv_rows = rows
        rows = getattr(self, "conv_rows", None) or []
        for iid in tree.get_children():
            tree.delete(iid)
        self.avatar_photos = {}
        existing = self._existing_keys()
        shown = 0
        for row in rows:
            key = self._row_key(row)
            try:
                photo = self._load_avatar(row, self._conv_metrics[0], self._conv_metrics[1])
            except Exception:
                photo = None
            if photo is not None:
                self.avatar_photos[key] = photo      # 必须留引用，否则图片会被回收
            bucket = self._spark_bucket(row.get("state"))
            if bucket == "ok":
                tag, today = "ok", SPARK_SHORT_CN["ok"]
            elif bucket == "waiting":
                tag, today = "waiting", SPARK_SHORT_CN["waiting"]
            elif bucket == "sent":
                tag, today = "sent", SPARK_SHORT_CN["sent"]
            elif bucket == "unknown":
                tag, today = "dim", SPARK_SHORT_CN["unknown"]
            else:
                tag, today = "dim", SPARK_SHORT_CN.get(bucket, SPARK_SHORT_CN["none"])
            stripe = "added" if self._is_added(row, existing) else ("odd" if shown % 2 else "even")
            tags = (tag, stripe)
            tree.insert("", "end", iid=key, image=(photo or ""), tags=tags, values=(
                row.get("name") or "",
                (row.get("days") or "").strip() or "—",
                today,
                (row.get("time") or "").strip() or "—"))
            shown += 1
        try:
            self._apply_col_widths(tree, self.CONV_COL_SPEC, "name", tree.winfo_width())
        except Exception:
            pass
        # 工具栏那行是固定的操作提示；"共几个 / 什么时候读的"挪到卡片副标题，
        # 免得把提示挤掉（用户要求把那排按钮和灰字删掉，换成这行提示）
        if getattr(self, "conv_hint", None) is not None:
            self.conv_hint.configure(text="双击添加目标 · 右键更多操作")
        lbl = getattr(getattr(self, "conv_card", None), "card_hint_label", None)
        if lbl is not None:
            stamp = (getattr(self, "conv_updated_at", "") or "")[5:16]   # 只留 月-日 时:分
            if not shown:
                text = "读到的好友 / 群聊 · 还没有数据，点「读取抖音列表」读一次"
            elif stamp:
                text = "读到的好友 / 群聊 · 共 %d 个 · %s 读取" % (shown, stamp)
            else:
                text = "读到的好友 / 群聊 · 共 %d 个" % shown
            try:
                lbl.configure(text=text)
            except Exception:
                pass

    def on_conv_add(self):
        tree = getattr(self, "conv_tree", None)
        if tree is None:
            return
        keys = set(tree.selection())
        if not keys:
            messagebox.showinfo("提示", "先在右侧会话列表里选中一行（双击某一行的效果相同）")
            return
        rows = [r for r in (getattr(self, "conv_rows", None) or []) if self._row_key(r) in keys]
        n = self._add_rows(rows)
        self._fill_conv_panel()
        self._after_add(n)

    def _fill_picker(self, rows=None):
        if rows is not None:
            self.conv_rows = rows
            self.conv_loading = False
        tree = getattr(self, "picker_tree", None)
        if tree is None:
            return
        kw = (self.picker_filter.get() if getattr(self, "picker_filter", None) else "").strip().lower()
        for iid in tree.get_children():
            tree.delete(iid)
        existing = self._existing_keys()
        shown = 0
        for row in (getattr(self, "conv_rows", None) or []):
            name = row.get("name") or ""
            if kw and kw not in name.lower():
                continue
            key = self._row_key(row)
            already = self._is_added(row, existing)
            bucket = self._spark_bucket(row.get("state"))
            tag = {"ok": "ok", "waiting": "waiting", "sent": "sent"}.get(bucket, "dim")
            today = SPARK_TODAY_CN.get(bucket, SPARK_TODAY_CN["none"])
            tags = [tag] + (["added"] if already else [])
            tree.insert("", "end", iid=key, tags=tuple(tags), values=(
                "✓" if key in self.picker_marks else "",
                name,
                "群聊" if not (row.get("sec_uid") or "") else "好友",
                (row.get("days") or "").strip() or "—",
                today,
                "已添加" if already else "未添加"))
            shown += 1
        all_rows = getattr(self, "conv_rows", None) or []
        unadded = len([r for r in all_rows if not self._is_added(r, existing)])
        if getattr(self, "picker_hint", None) is not None:
            self.picker_hint.configure(
                text="列表共 %d 项，当前显示 %d 项，未添加 %d 项；已勾选 %d 项"
                     "　（点一行即勾选；没有 secUid 的一律标为群聊，仅作标签，不影响发送）"
                     % (len(all_rows), shown, unadded, len(self.picker_marks)))

    def _picker_click(self, event):
        tree = self.picker_tree
        iid = tree.identify_row(event.y)
        if not iid:
            return
        if iid in self.picker_marks:
            self.picker_marks.discard(iid)
        else:
            self.picker_marks.add(iid)
        self._fill_picker()
        return "break"

    def _picker_mark_all(self):
        existing = self._existing_keys()
        for row in (getattr(self, "conv_rows", None) or []):
            if not self._is_added(row, existing):
                self.picker_marks.add(self._row_key(row))
        self._fill_picker()

    def _picker_clear_marks(self):
        self.picker_marks = set()
        self._fill_picker()

    def _add_rows(self, rows):
        existing = self._existing_keys()
        text = (self.picker_text.get() if getattr(self, "picker_text", None) else "").strip()
        added = 0
        for row in rows:
            if self._is_added(row, existing):
                continue
            self.cfg["targets"].append({
                "name": row.get("name") or "",
                "kind": "friend" if (row.get("sec_uid") or "") else "group",
                "enabled": True,
                "times": ["09:30"],
                "text": text,
                "sticker_id": None,
                "image_path": None,
                "conv_id": row.get("conv_id") or None,
                "sec_uid": row.get("sec_uid") or None,
            })
            existing |= self._row_match_keys(row)
            added += 1
        if added:
            self.cfg = config.save(self.cfg)
            self._refresh_tree()
            logger.info("已从会话列表添加 %d 个目标（已绑定会话 ID，改名也不怕）" % added, self.logs_dir)
        return added

    def _picker_add_selected(self):
        rows = [r for r in (getattr(self, "conv_rows", None) or [])
                if self._row_key(r) in self.picker_marks]
        if not rows:
            messagebox.showinfo("提示", "先在列表里点几行勾选（点一下即勾选/取消）", parent=self.picker_dlg)
            return
        n = self._add_rows(rows)
        self.picker_marks = set()
        self._fill_picker()
        self._after_add(n)

    def _picker_import_all(self):
        existing = self._existing_keys()
        rows = [r for r in (getattr(self, "conv_rows", None) or []) if not self._is_added(r, existing)]
        if not rows:
            messagebox.showinfo("提示", "列表里的会话都已经添加过了", parent=self.picker_dlg)
            return
        if not messagebox.askyesno("确认", "把列表里剩下 %d 个会话都加为发送目标？\n"
                                          "（加完可以在主界面删掉不想要的）" % len(rows),
                                   parent=self.picker_dlg):
            return
        n = self._add_rows(rows)
        self.picker_marks = set()
        self._fill_picker()
        self._after_add(n)

    def _after_add(self, n):
        parent = getattr(self, "picker_dlg", None)
        try:
            if parent is not None and not parent.winfo_exists():
                parent = None
        except Exception:
            parent = None
        if not n:
            messagebox.showinfo("提示", "这些目标已经添加过了", parent=parent)
            return
        lacks = [t for t in self.cfg["targets"] if t.get("enabled") and not (t.get("text") or "").strip()
                 and not t.get("sticker_id")]
        msg = "已添加 %d 个目标。" % n
        if lacks:
            msg += "\n\n还有 %d 个目标没有发送内容（文字或表情），\n" \
                   "双击主界面里的目标行即可编辑，否则到点会发送失败。" % len(lacks)
        messagebox.showinfo("完成", msg, parent=parent)

    def _picker_search(self):
        kw = (self.picker_filter.get() if getattr(self, "picker_filter", None) else "").strip()
        if not kw:
            messagebox.showinfo("提示", "先在「过滤」框里输入要搜索的昵称", parent=self.picker_dlg)
            return
        self._submit(lambda: self._search_contacts(kw), "在抖音搜索")

    async def _search_contacts(self, kw):
        c = await self._ensure_client()
        if not await c.ensure_logged_in(120):
            raise RuntimeError("尚未登录，请先点「扫码登录」")
        await c.goto_chat()
        rows = await c.search_contacts(kw)
        logger.info("抖音搜索「%s」：%d 个结果" % (kw, len(rows)), self.logs_dir)
        for r in rows:
            logger.info("   %s" % r.get("raw", r.get("name")), self.logs_dir)
        self.ui_queue.put(("searchres", {"kw": kw, "rows": rows}))

    def _show_search_results(self, payload):
        rows = payload.get("rows") or []
        dlg = tk.Toplevel(self.picker_dlg if getattr(self, "picker_dlg", None) else self.root)
        dlg.title("抖音搜索：%s" % payload.get("kw", ""))
        dlg.transient(self.root)
        head = tk.Label(dlg, text="搜索「%s」共 %d 个结果：选中后加入目标（这类目标列表里没有，发送时走搜索打开）"
                                  % (payload.get("kw", ""), len(rows)),
                        font=FONT, fg="#444", anchor="w", justify="left")
        head.pack(fill="x", padx=12, pady=10)
        lb = tk.Listbox(dlg, font=FONT, width=60, height=max(4, min(12, len(rows) or 4)))
        for r in rows:
            lb.insert("end", "%s%s" % (r.get("name") or r.get("raw", ""),
                                       "" if r.get("has_send") else "   （无发消息按钮）"))
        lb.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        def add():
            sel = lb.curselection()
            if not sel:
                return
            r = rows[sel[0]]
            self._add_rows([{"name": r.get("name") or r.get("raw", ""), "conv_id": "",
                             "sec_uid": "", "state": "none", "days": ""}])
            self._fill_picker()
            dlg.destroy()

        bf = tk.Frame(dlg)
        bf.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(bf, text="加入目标", font=FONT, command=add).pack(side="right", padx=4)
        self._apply_theme(dlg)
        self._titlebar(dlg)
        tk.Button(bf, text="关闭", font=FONT, command=dlg.destroy).pack(side="right", padx=4)

    def on_delete(self):
        idx = self._selected_index()
        if idx is None:
            return
        if messagebox.askyesno("确认", "删除目标「%s」？" % self.cfg["targets"][idx]["name"]):
            self.cfg["targets"].pop(idx)
            self.cfg = config.save(self.cfg)
            self._refresh_tree()

    def on_toggle(self):
        idx = self._selected_index()
        if idx is None:
            return
        self.cfg["targets"][idx]["enabled"] = not self.cfg["targets"][idx].get("enabled", True)
        self.cfg = config.save(self.cfg)
        self._refresh_tree()

    # ------------------------------------------------------------ 表情缩略图
    def _sticker_thumb_path(self, sid):
        for it in (self.stickers.get("items") or []):
            if it.get("id") == sid:
                thumb = it.get("thumb") or ""
                return thumb if os.path.isabs(thumb) else os.path.join(config.BASE_DIR, thumb)
        return os.path.join(config.STICKERS_DIR, "%s.png" % sid)

    @staticmethod
    def _load_thumb(path, size):
        """加载并缩放缩略图；PIL 不可用时返回 None（界面自动退化为文字）。"""
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            im.thumbnail((size, size))
            return ImageTk.PhotoImage(im)
        except Exception:
            return None

    def _choose_sticker(self, parent, current_id):
        """缩略图网格选择器。返回选中的表情 id；取消返回 None，选「不发送」返回空串。"""
        items = self.stickers.get("items") or []
        if not items:
            messagebox.showinfo("提示", "超级表情库是空的，请先点主界面的「同步超级表情」", parent=parent)
            return None

        dlg = tk.Toplevel(parent)
        dlg.title("选择超级表情（点击图片选中）")
        dlg.transient(parent)
        dlg.grab_set()
        dlg.geometry("660x440")
        result = {"id": current_id, "done": False}
        keep = []  # 必须持有 PhotoImage 引用，否则会被回收导致空白

        outer = tk.Frame(dlg)
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        grid_frame = tk.Frame(canvas)
        grid_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=grid_frame, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        cols, size = 7, 64
        for i, it in enumerate(items):
            photo = self._load_thumb(self._sticker_thumb_path(it["id"]), size)
            keep.append(photo)
            r, c = divmod(i, cols)
            if photo is None:
                b = tk.Button(grid_frame, text=it["id"][:6], width=9, height=3)
            else:
                b = tk.Button(grid_frame, image=photo, width=size + 8, height=size + 8,
                              relief="sunken" if it["id"] == current_id else "raised")
            b.grid(row=r, column=c, padx=4, pady=4)
            b.configure(command=lambda sid=it["id"]: (result.update(id=sid, done=True), dlg.destroy()))
        dlg._keep = keep

        bar = tk.Frame(dlg)
        bar.pack(fill="x", pady=(0, 10))
        tk.Button(bar, text="不发送表情", font=FONT,
                  command=lambda: (result.update(id=None, done=True), dlg.destroy())
                  ).pack(side="left", padx=10)
        tk.Button(bar, text="取消", font=FONT, command=dlg.destroy).pack(side="right", padx=10)

        self._apply_theme(dlg)              # 控件都建完了再刷色
        self._titlebar(dlg)
        dlg.wait_window()
        return result["id"] if result["done"] else None

    # ------------------------------------------------------------ 弹窗外壳（Web 风格）
    def _dialog_shell(self, title, subtitle="", width=560, height=620, minw=500, minh=420):
        """统一的弹窗外壳：标题 + 副标题 + 可滚动内容区 + 底部按钮条。

        返回 (dlg, body, foot)：body 里放卡片，foot 里放右下角按钮。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=BG_MAIN)
        try:
            sh = int(self.root.winfo_screenheight())
        except Exception:
            sh = 900
        dlg.geometry("%dx%d" % (int(width), max(int(minh), min(int(height), sh - 80))))
        dlg.minsize(int(minw), int(minh))

        head = tk.Frame(dlg, bg=BG_MAIN)
        head.pack(fill="x", padx=24, pady=(18, 8))
        tk.Label(head, text=title, font=("Microsoft YaHei", 16, "bold"),
                 bg=BG_MAIN, fg=FG).pack(anchor="w")
        if subtitle:
            tk.Label(head, text=subtitle, font=FONT_S, bg=BG_MAIN,
                     fg=FG_DIM).pack(anchor="w", pady=(3, 0))

        foot = tk.Frame(dlg, bg=BG_MAIN)
        foot.pack(side="bottom", fill="x", padx=24, pady=(6, 16))

        outer = tk.Frame(dlg, bg=BG_MAIN)
        outer.pack(fill="both", expand=True)
        cvs = tk.Canvas(outer, bg=BG_MAIN, highlightthickness=0, bd=0)
        csb = ttk.Scrollbar(outer, orient="vertical", command=cvs.yview,
                            style="Slim.Vertical.TScrollbar")
        body = tk.Frame(cvs, bg=BG_MAIN)
        win = cvs.create_window((0, 0), window=body, anchor="nw")
        cvs.configure(yscrollcommand=csb.set)
        csb.pack(side="right", fill="y")
        cvs.pack(side="left", fill="both", expand=True)

        def _fit(_e=None):
            try:
                cvs.configure(scrollregion=cvs.bbox("all"))
                cvs.itemconfigure(win, width=cvs.winfo_width())
            except Exception:
                pass

        body.bind("<Configure>", _fit)
        cvs.bind("<Configure>", _fit)

        def _wheel(e):
            try:
                cvs.yview_scroll(-1 if e.delta > 0 else 1, "units")
            except Exception:
                pass

        cvs.bind_all("<MouseWheel>", _wheel)
        dlg.bind("<Destroy>", lambda e: cvs.unbind_all("<MouseWheel>")
                 if getattr(e, "widget", None) is dlg else None)
        dlg.after(60, _fit)
        dlg.body = body          # 方便自检读内容尺寸
        dlg.foot = foot
        dlg.scroll_canvas = cvs
        return dlg, body, foot

    def _edit_dialog(self, idx):
        cur = self.cfg["targets"][idx] if idx is not None else {
            "name": "", "kind": "friend", "enabled": True, "times": ["09:30"],
            "text": "", "sticker_id": None, "image_path": None,
            "conv_id": None, "sec_uid": None,
        }
        is_new = idx is None
        name0 = (cur.get("name") or "").strip()
        dlg, body, foot = self._dialog_shell(
            "添加目标" if is_new else "编辑目标",
            "填好「发什么」「几点发」就行；改完点右下角「确定」。" if is_new
            else "目标：%s" % (name0 or "（没写昵称）"),
            width=560, height=700, minw=500, minh=420)
        WRAP = 420

        def row(parent, label, width=11):
            r = tk.Frame(parent, bg=BG_CARD)
            r.pack(fill="x", padx=16, pady=(2, 4))
            tk.Label(r, text=label, font=FONT, bg=BG_CARD, fg=FG, width=width,
                     anchor="w").pack(side="left")
            return r

        def note(parent, text):
            tk.Label(parent, text=text, font=FONT_S, bg=BG_CARD, fg=FG_DIM, justify="left",
                     wraplength=WRAP, anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        # ---- 卡片 1：发给谁
        c1 = self._card(body, "发给谁", "昵称 / 群名要和抖音里对得上")
        c1.card_outer.pack(fill="x", padx=6, pady=(0, 8))
        name_v = tk.StringVar(value=cur["name"])
        r = row(c1, "昵称 / 群名")
        tk.Entry(r, textvariable=name_v, font=FONT, width=30).pack(side="left")
        kind_v = tk.StringVar(value=cur["kind"])
        r = row(c1, "类型")
        tk.Radiobutton(r, text="好友", variable=kind_v, value="friend",
                       font=FONT).pack(side="left")
        tk.Radiobutton(r, text="群聊", variable=kind_v, value="group",
                       font=FONT).pack(side="left", padx=(10, 0))
        bound = (cur.get("conv_id") or "").strip()
        r = row(c1, "会话 ID")
        tk.Label(r, text=(bound if bound else "未绑定（下次「读取抖音列表」或「一键续火花」会自动绑上）"),
                 font=("Consolas", 9) if bound else FONT_S,
                 bg=BG_CARD, fg=(OK_C if bound else WAIT_C), anchor="w",
                 justify="left", wraplength=WRAP).pack(side="left")
        en_v = tk.BooleanVar(value=cur.get("enabled", True))
        tk.Checkbutton(c1, text="启用该目标（停用的不会发）", variable=en_v,
                       font=FONT).pack(anchor="w", padx=16, pady=(2, 6))

        # ---- 卡片 2：发什么
        c2 = self._card(body, "发什么", "文字 / 超级表情 / 本地图片，至少一样")
        c2.card_outer.pack(fill="x", padx=6, pady=(0, 8))
        text_v = tk.StringVar(value=cur.get("text") or "")
        r = row(c2, "发送文字")
        tk.Entry(r, textvariable=text_v, font=FONT, width=30).pack(side="left")
        sticker_state = {"id": cur.get("sticker_id")}
        r = row(c2, "附带表情")
        preview_box = tk.Frame(r, width=64, height=64, bg=BG_CARD,
                               highlightthickness=1, highlightbackground=BG_LINE)
        preview_box.pack(side="left")
        preview_box.pack_propagate(False)
        preview = tk.Label(preview_box, text="无", font=FONT_S, fg=FG_DIM, bg=BG_CARD)
        preview.pack(fill="both", expand=True)

        def refresh_preview():
            sid = sticker_state["id"]
            if not sid:
                preview.configure(image="", text="无")
                preview.image = None
                return
            photo = self._load_thumb(self._sticker_thumb_path(sid), 58)
            if photo is None:
                preview.configure(image="", text="已选")
            else:
                preview.configure(image=photo, text="")
                preview.image = photo

        def choose_sticker():
            picked = self._choose_sticker(dlg, sticker_state["id"])
            if picked is not None:
                sticker_state["id"] = picked or None
                refresh_preview()

        bf = tk.Frame(r, bg=BG_CARD)
        bf.pack(side="left", padx=(10, 0))
        RoundedButton(bf, "选择表情", choose_sticker, kind="normal", padx=12,
                      height=30, bg=BG_CARD).pack(side="left")
        RoundedButton(bf, "清除", lambda: (sticker_state.update(id=None), refresh_preview()),
                      kind="ghost", padx=12, height=30, bg=BG_CARD).pack(side="left", padx=(6, 0))
        refresh_preview()
        note(c2, "点「选择表情」按图挑一个（先在主界面点一次「同步超级表情」把表情库拉下来）。")
        image_v = tk.StringVar(value=cur.get("image_path") or "")
        r = row(c2, "本地图片")
        tk.Entry(r, textvariable=image_v, font=FONT, width=30).pack(side="left")
        note(c2, "表情包图片路径，如 sticks/meme.png（相对程序目录）；不发图片就留空。")

        # ---- 卡片 3：什么时候发
        c3 = self._card(body, "什么时候发", "每天按这个时间自动发")
        c3.card_outer.pack(fill="x", padx=6, pady=(0, 16))
        times_v = tk.StringVar(value=", ".join(cur.get("times") or ["09:30"]))
        r = row(c3, "发送时间")
        tk.Entry(r, textvariable=times_v, font=FONT, width=30).pack(side="left")
        note(c3, "24 小时制，多个时间用逗号分隔，如 09:30, 21:00。")

        def ok():
            name = name_v.get().strip()
            if not name:
                messagebox.showwarning("提示", "昵称不能为空", parent=dlg)
                return
            times = [x.strip() for x in times_v.get().split(",") if x.strip()]
            for hm in times:
                parts = hm.split(":")
                if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                    messagebox.showwarning("提示", "时间格式应为 HH:MM，如 09:30", parent=dlg)
                    return
            sv = sticker_state["id"]
            item = {
                "name": name,
                "kind": kind_v.get(),
                "enabled": bool(en_v.get()),
                "times": times or ["09:30"],
                "text": text_v.get(),
                "sticker_id": sv or None,
                "image_path": image_v.get().strip() or None,
                "conv_id": cur.get("conv_id"),
                "sec_uid": cur.get("sec_uid"),
            }
            if name != cur.get("name"):
                # 名字被手动改过：原来的会话 ID 可能已经对不上，清掉让它重新按名字解析
                item["conv_id"] = None
                item["sec_uid"] = None
                logger.info("「%s」的名字被修改，已清除绑定的会话 ID（下次检测/发送会重新解析）" % name,
                            self.logs_dir)
            if is_new:
                self.cfg["targets"].append(item)
            else:
                self.cfg["targets"][idx] = item
            self.cfg = config.save(self.cfg)
            self._sync_client_cfg()
            logger.info("%s目标「%s」：时间 %s，内容 %s"
                        % ("新增" if is_new else "已保存", name, ", ".join(item["times"]),
                           (item["text"] or "") + ("＋表情" if item["sticker_id"] else "")
                           + ("＋图" if item["image_path"] else "")), self.logs_dir)
            self._refresh_tree()
            dlg.destroy()

        RoundedButton(foot, "取消", dlg.destroy, kind="normal", padx=18,
                      height=34, bg=BG_MAIN).pack(side="right", padx=(8, 0))
        RoundedButton(foot, "确定", ok, kind="accent", padx=24,
                      height=34, bg=BG_MAIN).pack(side="right")
        dlg.bind("<Return>", lambda e: ok())

        self._apply_theme(dlg)
        self._titlebar(dlg, dark=titlebar_is_dark())
        try:
            dlg.update_idletasks()
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - 560) // 2)
            y = self.root.winfo_rooty() + 40
            dlg.geometry("+%d+%d" % (max(0, x), max(0, y)))
        except Exception:
            pass
        dlg.lift()

    def _queue_log(self, line):
        self.ui_queue.put(("log", line))

    def _append_log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 800:
            self.log_text.delete("1.0", "200.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _pump(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self._append_log("· %s" % payload)      # 原来那行状态栏没了，改写到日志里
                elif kind == "sched":
                    self._set_sched_button(bool(payload))
                elif kind == "tree":
                    self._refresh_tree()
                elif kind == "convlist":
                    self._on_conv_rows(payload)
                elif kind == "searchres":
                    self._show_search_results(payload)
                elif kind == "error":
                    messagebox.showerror("出错了", payload)
                elif kind == "notice":
                    messagebox.showinfo("提示", payload)
                elif kind == "login":
                    self.login_state = payload
                    self._sync_login_label()
                elif kind == "tray":
                    self._handle_tray_action(payload)
                elif kind == "toast":
                    self._toast(payload[0], payload[1] if len(payload) > 1 else "")
                elif kind == "notify":
                    tag, rows = payload
                    self._notify_spark_done(rows, "自动" if "自动" in tag else "")
        except queue.Empty:
            pass
        self.root.after(150, self._pump)

    def _set_status(self, text):
        self.ui_queue.put(("status", text))

    # ------------------------------------------------------------ 异步任务
    async def _ensure_client(self):
        if self.client is not None:
            try:
                await self.client.ensure_alive()
                self._sync_client_cfg()
            except Exception:
                try:
                    await self.client.close()
                except Exception:
                    pass
                self.client = None
        if self.client is None:
            self.client = DouyinClient(self.cfg, self.logs_dir)
            await self.client.start()
        return self.client

    def _sync_client_cfg(self):
        """把最新配置同步给已启动的引擎（改设置后立即生效，无需重启）。"""
        if self.client is not None:
            self.client.cfg = self.cfg
            self.client.sched = self.cfg["schedule"]
            self.client.browser_cfg = self.cfg["browser"]

    # ------------------------------------------------------------ 全局设置
    # ------------------------------------------------------------ 主题（浅色 / 深色）
    def _set_theme(self, name, save=True):
        """切换浅色/深色，立即生效（不用重启）。"""
        name = use_palette(name)
        self.theme = name
        try:
            self._style_ttk()                       # ttk 样式（表格/滚动条/下拉）重配
        except Exception:
            pass
        self._apply_theme()                         # 递归重刷所有 Tk 控件（含自绘按钮/开关）
        self._restyle_tables()                      # 表格状态色 + 斑马纹重配并重画
        self._titlebar(self.root, dark=titlebar_is_dark())
        try:
            for w in self.root.winfo_children():
                if isinstance(w, tk.Toplevel):
                    self._titlebar(w, dark=titlebar_is_dark())
        except Exception:
            pass
        if save:
            try:
                self.cfg.setdefault("ui", {})["theme"] = name
                self.cfg = config.save(self.cfg)
            except Exception:
                pass
        self._sync_theme_labels()
        try:
            if getattr(self, "theme_switch", None) is not None:
                self.theme_switch.set(name == "dark", animate=False)   # 开关位置跟着主题走
        except Exception:
            pass
        # 有的窗口（尤其被对话框挡住一半时）改完颜色不一定整体重绘，硬刷一次
        self.root.after(30, self._force_redraw)
        logger.info("界面已切换成%s模式" % ("深色" if name == "dark" else "浅色"), self.logs_dir)
        return name

    def _force_redraw(self):
        """把整窗按当前尺寸重新摆一次，逼 Tk 全量重绘（换主题后偶尔只重绘一部分）。"""
        try:
            g = self.root.geometry()
            self.root.geometry(g)
            self.root.update_idletasks()
        except Exception:
            pass
        for w in list(self.root.winfo_children()):
            if isinstance(w, tk.Toplevel):
                try:
                    g = w.geometry()
                    w.geometry(g)
                    w.update_idletasks()
                except Exception:
                    pass

    def _sync_theme_labels(self):
        """左下角那两个字：当前模式加粗+正常色，另一个灰字。"""
        for key, lb in (getattr(self, "theme_labels", None) or {}).items():
            active = (key == self.theme)
            try:
                lb.configure(fg=(FG if active else FG_DIM),
                             font=("Microsoft YaHei", 8, "bold") if active else FONT_S)
            except Exception:
                pass

    def _restyle_tables(self):
        """主题变了：表格里的 tag 颜色（状态色/斑马纹）是建表时写死的，要重新配一遍。"""
        for tree in (getattr(self, "conv_tree", None), getattr(self, "picker_tree", None)):
            if tree is None:
                continue
            try:
                tree.tag_configure("ok", foreground=OK_C)
                tree.tag_configure("waiting", foreground=WAIT_C)
                tree.tag_configure("sent", foreground=SENT_C)
                tree.tag_configure("dim", foreground=DIM_C)
                tree.tag_configure("added", background=BG_ROW_ADDED)
            except Exception:
                pass
        for tree in (getattr(self, "tree", None), getattr(self, "conv_tree", None)):
            if tree is not None:
                self._stripes(tree)
        try:
            self._refresh_tree()
        except Exception:
            pass
        try:
            self._fill_conv_panel(self.conv_rows)
        except Exception:
            pass

    def _on_theme_toggle(self, on):
        self._set_theme("dark" if on else "light")

    # ------------------------------------------------------------ 设置弹窗（Web 风格）
    # ------------------------------------------------------------ 设置弹窗（Web 风格）
    def on_settings(self):
        sched = self.cfg["schedule"]
        dlg, body, foot = self._dialog_shell(
            "设置", "改完点右下角「保存」。深浅色在左下角「界面模式」那里切；换浏览器要重开程序。",
            width=560, height=640, minw=500, minh=440)

        iv = sched.get("target_interval_seconds") or [2, 6]
        td = sched.get("type_char_delay_ms") or [50, 140]
        interval_lo = tk.StringVar(value=str(iv[0]))
        interval_hi = tk.StringVar(value=str(iv[1]))
        jitter_v = tk.StringVar(value=str(sched.get("random_jitter_seconds", 0)))
        type_lo = tk.StringVar(value=str(td[0]))
        type_hi = tk.StringVar(value=str(td[1]))
        human_v = tk.BooleanVar(value=bool(sched.get("human_like_typing", True)))
        dry_v = tk.BooleanVar(value=bool(sched.get("dry_run", False)))
        sched_browser = tk.StringVar(value=str(self.cfg["browser"].get("channel") or "auto"))

        _detected = config.detect_browsers() or [("msedge", "Microsoft Edge", "")]
        _opts = ["自动（推荐）"] + ["%s（%s）" % (lb, ch) for ch, lb, _p in _detected]
        _cur = str(sched_browser.get() or "auto")
        _cur_label = "自动（推荐）"
        for ch, lb, _p in _detected:
            if ch == _cur:
                _cur_label = "%s（%s）" % (lb, ch)
        if _cur not in ("", "auto") and _cur not in [c for c, _l, _p in _detected]:
            _opts.append("%s（%s，本机没装）" % (config.BROWSER_LABELS.get(_cur, _cur), _cur))
            _cur_label = _opts[-1]
        browser_v = tk.StringVar(value=_cur_label)
        _WM_OPTS = [("minimized", "最小化到任务栏（推荐，不挡屏幕）"),
                    ("normal", "正常弹出来（能看见它在干什么）"),
                    ("hidden", "完全隐藏窗口（后台跑，看不到二维码）")]
        _wm_cur = str(self.cfg["browser"].get("window_mode") or "minimized")
        _wm_label = dict(_WM_OPTS).get(_wm_cur, _WM_OPTS[0][1])
        window_v = tk.StringVar(value=_wm_label)

        RoundedButton(foot, "取消", dlg.destroy, kind="normal", padx=18,
                      height=34, bg=BG_MAIN).pack(side="right", padx=(8, 0))
        cvs = dlg.scroll_canvas
        WRAP = 440      # 长说明的换行宽度（卡片宽度 - 内边距）

        def row(parent, text, hint=""):
            r = tk.Frame(parent, bg=BG_CARD)
            r.pack(fill="x", padx=16, pady=(2, 4))
            tk.Label(r, text=text, font=FONT, bg=BG_CARD, fg=FG, width=13,
                     anchor="w").pack(side="left")
            return r

        def note(parent, text, color=None, top=0, bottom=12):
            tk.Label(parent, text=text, font=FONT_S, bg=BG_CARD, fg=color or FG_DIM,
                     justify="left", wraplength=WRAP, anchor="w").pack(
                fill="x", padx=16, pady=(top, bottom))

        # ---- 卡片 1：发送节奏
        c1 = self._card(body, "发送节奏", "每个目标之间随机等一会儿，别发得像机器人")
        c1.card_outer.pack(fill="x", padx=6, pady=(0, 10))
        r = row(c1, "切换目标间隔")
        tk.Entry(r, textvariable=interval_lo, font=FONT, width=5).pack(side="left")
        tk.Label(r, text="~", font=FONT, bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=4)
        tk.Entry(r, textvariable=interval_hi, font=FONT, width=5).pack(side="left")
        tk.Label(r, text="秒（每位之间随机）", font=FONT_S, bg=BG_CARD, fg=FG_DIM).pack(
            side="left", padx=(6, 0))
        r = row(c1, "计划时间抖动")
        tk.Entry(r, textvariable=jitter_v, font=FONT, width=5).pack(side="left")
        tk.Label(r, text="秒（0 ~ N 秒随机偏移）", font=FONT_S, bg=BG_CARD, fg=FG_DIM).pack(
            side="left", padx=(6, 0))
        note(c1, "错过时间点不要紧：下次启动自动模式（或开机自启）时会先看一眼抖音，"
                 "只有「今天还没续」的才补一轮，不会一启动就把过去的时间点全发一遍。")
        r = row(c1, "逐字输入延迟")
        tk.Entry(r, textvariable=type_lo, font=FONT, width=5).pack(side="left")
        tk.Label(r, text="~", font=FONT, bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=4)
        tk.Entry(r, textvariable=type_hi, font=FONT, width=5).pack(side="left")
        tk.Label(r, text="毫秒（每个字之间停顿）", font=FONT_S, bg=BG_CARD, fg=FG_DIM).pack(
            side="left", padx=(6, 0))
        tk.Checkbutton(c1, text="逐字输入（更像真人，但明显更慢）", variable=human_v,
                       font=FONT).pack(anchor="w", padx=16, pady=(2, 4))
        note(c1, "间隔越小发得越快，但越容易触发风控；建议 3~12 秒，抖动给 30~120 秒。",
             color=WAIT_C)

        # ---- 卡片 2：运行
        c2 = self._card(body, "运行", "")
        c2.card_outer.pack(fill="x", padx=6, pady=(0, 10))
        tk.Checkbutton(c2, text="演练模式（只输入、只定位，绝不真的发送）", variable=dry_v,
                       font=FONT).pack(anchor="w", padx=16, pady=(2, 4))
        note(c2, "走完整个流程但不按回车、不点表情；换电脑或抖音改版后想安全自测时打开。")

        # ---- 卡片 4：浏览器
        c4 = self._card(body, "浏览器", "Edge / Chrome 都行，同一个内核")
        c4.card_outer.pack(fill="x", padx=6, pady=(0, 16))
        r = row(c4, "用哪个浏览器")
        ttk.Combobox(r, textvariable=browser_v, values=_opts, font=FONT,
                     state="readonly", width=26).pack(side="left")
        try:
            _auto_on = autostart.enabled()
        except Exception:
            _auto_on = bool((self.cfg.get("ui") or {}).get("autostart", False))
        auto_v = tk.BooleanVar(value=_auto_on)
        boot_sched_v = tk.BooleanVar(
            value=bool((self.cfg.get("ui") or {}).get("auto_sched_on_boot", True)))
        tray_v = tk.BooleanVar(value=bool((self.cfg.get("ui") or {}).get("close_to_tray", True)))
        r = row(c4, "开机自启")
        tk.Checkbutton(r, text="开机后自动运行（直接最小化在托盘里）", variable=auto_v,
                       font=FONT).pack(side="left")
        r = row(c4, "开机自启后")
        tk.Checkbutton(r, text="自动开始「自动模式」，并先看一眼今天续没续",
                       variable=boot_sched_v, font=FONT).pack(side="left")
        note(c4, "勾这一个就够：开机后按计划时间自动续；启动时会先只读核对抖音里的火花，"
                 "只有「今天还没续」的才补一轮，已经续过的不会重复发。")
        r = row(c4, "关闭窗口时")
        tk.Checkbutton(r, text="收进系统托盘继续后台跑（不勾就是直接退出）", variable=tray_v,
                       font=FONT).pack(side="left")
        r = row(c4, "浏览器窗口")
        ttk.Combobox(r, textvariable=window_v, values=[t for _k, t in _WM_OPTS], font=FONT,
                     state="readonly", width=30).pack(side="left")
        tk.Label(c4, text="默认最小化到任务栏、不抢屏幕；扫码时会自动弹出来。改完重开生效。",
                 font=FONT_S, bg=BG_CARD, fg=FG_DIM, justify="left", wraplength=WRAP).pack(
            anchor="w", padx=16, pady=(0, 10))
        note(c4, "默认「自动」：优先 Edge，没装就用 Chrome。换浏览器要重新扫码一次。")

        # ---- 保存
        def ok():
            def as_int(var, label):
                try:
                    return int(float(str(var.get()).strip()))
                except Exception:
                    raise ValueError(label)

            try:
                lo = max(0, as_int(interval_lo, "切换目标间隔下限"))
                hi = max(0, as_int(interval_hi, "切换目标间隔上限"))
                jit = max(0, as_int(jitter_v, "计划时间抖动"))
                tlo = max(0, as_int(type_lo, "逐字输入延迟下限"))
                thi = max(0, as_int(type_hi, "逐字输入延迟上限"))
            except ValueError as e:
                messagebox.showwarning("提示", "请输入数字：%s" % e, parent=dlg)
                return
            if hi < lo:
                messagebox.showwarning("提示", "间隔上限不能小于下限", parent=dlg)
                return
            if thi < tlo:
                messagebox.showwarning("提示", "逐字延迟上限不能小于下限", parent=dlg)
                return

            self.cfg["schedule"]["target_interval_seconds"] = [lo, hi]
            self.cfg["schedule"]["random_jitter_seconds"] = jit
            self.cfg["schedule"]["type_char_delay_ms"] = [tlo, thi]
            self.cfg["schedule"]["human_like_typing"] = bool(human_v.get())
            self.cfg["schedule"]["dry_run"] = bool(dry_v.get())
            old_channel = str(self.cfg["browser"].get("channel") or "auto")
            picked = browser_v.get()
            new_channel = "auto"
            for ch, lb, _p in _detected:
                if picked.startswith("%s（" % lb):
                    new_channel = ch
                    break
            self.cfg["browser"]["channel"] = new_channel
            _wm_pick = window_v.get()
            new_wm = "minimized"
            for _k, _t in _WM_OPTS:
                if _wm_pick == _t:
                    new_wm = _k
                    break
            old_wm = str(self.cfg["browser"].get("window_mode") or "minimized")
            self.cfg["browser"]["window_mode"] = new_wm
            self.cfg.setdefault("ui", {})["theme"] = self.theme
            self.cfg["ui"]["close_to_tray"] = bool(tray_v.get())
            self.cfg["ui"]["auto_sched_on_boot"] = bool(boot_sched_v.get())
            self.cfg["ui"]["boot_catchup"] = bool(boot_sched_v.get())   # 已合并，跟随同一个开关
            want_auto = bool(auto_v.get())
            ok_auto, tip_auto = autostart.set_enabled(want_auto)
            self.cfg["ui"]["autostart"] = bool(want_auto and ok_auto)
            if want_auto and not ok_auto:
                messagebox.showwarning("提示", "开机自启没设置成功：%s" % tip_auto, parent=dlg)
            elif ok_auto:
                logger.info("开机自启已%s%s" % (
                    "打开" if want_auto else "关闭",
                    ("（开机后直接最小化在托盘里：%s）" % tip_auto) if want_auto else ""), self.logs_dir)
            self.cfg = config.save(self.cfg)
            self._sync_client_cfg()
            logger.info("设置已保存：目标间隔 %d~%d 秒，抖动 %d 秒，"
                        "输入延迟 %d~%d 毫秒，逐字输入=%s，演练模式=%s"
                        % (lo, hi, jit, tlo, thi, bool(human_v.get()), bool(dry_v.get())),
                        self.logs_dir)
            if dry_v.get():
                logger.info("【演练模式已开启】发送时只会输入内容，不会真的发出去", self.logs_dir)
            if new_channel != old_channel:
                logger.info("浏览器改为 %s —— 关掉程序重开才生效（换浏览器需要重新扫码一次）"
                            % ("自动" if new_channel == "auto" else new_channel), self.logs_dir)
            if new_wm != old_wm:
                logger.info("浏览器窗口改为「%s」—— 关掉程序重开才生效"
                            % dict(_WM_OPTS).get(new_wm, new_wm), self.logs_dir)
            dlg.destroy()

        RoundedButton(foot, "保存", ok, kind="accent", padx=22,
                      height=34, bg=BG_MAIN).pack(side="right")

        self._apply_theme(dlg)          # 控件都建完了再刷色
        self._titlebar(dlg, dark=titlebar_is_dark())
        try:
            dlg.update_idletasks()
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - 560) // 2)
            y = self.root.winfo_rooty() + 40
            dlg.geometry("+%d+%d" % (max(0, x), max(0, y)))
        except Exception:
            pass
        dlg.lift()

    def _submit(self, factory, tag):
        self._set_status("%s…" % tag)

        def done(fut):
            self.conv_loading = False
            try:
                fut.result()
                self.ui_queue.put(("status", "%s 完成" % tag))
            except Exception as e:
                # 引擎的英文报错统一翻成中文（浏览器被关掉 / 超时 / 网络不通 …）
                msg = friendly_error(e)
                if is_browser_closed(e):
                    # 浏览器被关掉不是"程序出错"，是外部把窗口关了：给提示，不弹"出错了"
                    logger.warn("%s 已中止：%s" % (tag, msg.replace("\n", " ")), self.logs_dir)
                    self.ui_queue.put(("status", "%s 已中止（浏览器被关掉了）" % tag))
                    self.ui_queue.put(("notice", msg))
                else:
                    logger.error("%s 失败：%s" % (tag, msg.replace("\n", " ")), self.logs_dir)
                    self.ui_queue.put(("status", "%s 失败" % tag))
                    self.ui_queue.put(("error", "%s 失败：\n%s" % (tag, msg)))
            self.ui_queue.put(("tree", None))

        self.worker.submit(factory()).add_done_callback(done)

    def on_login(self):
        async def job():
            c = await self._ensure_client()
            ok = await c.ensure_logged_in()
            self._set_login_state(ok)
            if ok:
                logger.info("登录完成，登录态已保存", self.logs_dir)

        self._submit(job, "扫码登录")

    def _backfill_conv_ids(self, rows):
        """给只有名字的老目标补上稳定会话 ID（检测/发送时顺手做，用户无感）。"""
        if not rows:
            return 0
        by_name = {}
        sec_by_id = {}
        for r in rows:
            cid = (r.get("conv_id") or "").strip()
            n = (r.get("name") or "").strip()
            if cid:
                sec_by_id[cid] = r.get("sec_uid") or ""
            if n and n not in by_name:
                by_name[n] = cid
        changed = 0
        for t in self.cfg["targets"]:
            if (t.get("conv_id") or "").strip():
                continue
            hit = by_name.get((t.get("name") or "").strip())
            if not hit:
                continue
            t["conv_id"] = hit
            if sec_by_id.get(hit) and not (t.get("sec_uid") or ""):
                t["sec_uid"] = sec_by_id[hit]
            changed += 1
            logger.info("已为「%s」绑定会话 ID（以后改名也不怕）" % t.get("name"), self.logs_dir)
        if changed:
            self.cfg = config.save(self.cfg)
            self._sync_client_cfg()
            self._refresh_tree()
        return changed

    def _heal_target_name(self, client, target):
        """按会话 ID 打开后如果发现昵称被改过，就把配置里的名字纠正过来（在 worker 线程里调用）。"""
        info = getattr(client, "last_opened", None) or {}
        if info.get("by") != "id" or not info.get("title"):
            return
        title = re.sub(r"[（(]\s*\d+\s*[)）]$", "", info["title"].strip()).strip()
        old = (target.get("name") or "").strip()
        if not title or title == old or client._name_match(old, title):
            return
        target["name"] = title
        self.cfg = config.save(self.cfg)
        self.ui_queue.put(("tree", None))
        logger.info("检测到「%s」已改名为「%s」，配置已自动更新（认的是会话 ID，不影响发送）"
                    % (old, title), self.logs_dir)

    async def _send_all(self):
        targets = [t for t in self.cfg["targets"] if t.get("enabled")]
        if not targets:
            raise RuntimeError("没有启用的目标，请先添加并启用")
        logger.info("本轮将处理 %d 位目标" % len(targets), self.logs_dir)
        await self._send_targets(targets, by="一键续火花")

    async def _send_targets(self, targets, by="一键续火花"):
        if self._sending:
            logger.warn("上一轮还没发完，这次先跳过（免得同一批发两遍）", self.logs_dir)
            return
        self._sending = True
        try:
            await self._send_targets_inner(targets, by)
        finally:
            self._sending = False

    async def _send_targets_inner(self, targets, by="一键续火花"):
        c = await self._ensure_client()
        if not await c.ensure_logged_in(60):
            self._set_login_state(False)
            raise RuntimeError("尚未登录，请先点「扫码登录」")
        self._set_login_state(True)
        dry = bool(self.cfg["schedule"].get("dry_run"))
        if dry:
            logger.info("【演练模式】本轮只走流程并检查输入/定位，不会真的发出任何消息", self.logs_dir)
        await c.goto_chat()          # 整轮只打开一次聊天页
        await self._refresh_conv_rows(c, "发送前")   # 顺带回填会话 ID（走队列，见该方法说明）
        ok = 0
        skipped = 0
        aborted = None
        for t in targets:
            try:
                r = await c.send_to_target(t, ensure_chat=False, skip_if_sent_today=True)
                self._heal_target_name(c, t)
                if r == "skipped":
                    skipped += 1
                else:
                    ok += 1
            except Exception as e:
                if is_browser_closed(e):
                    # 浏览器被关掉了：后面每个目标都必然失败，别再一个个试、也别再空等间隔
                    aborted = BrowserClosedError(friendly_error(e))
                    logger.warn("发送「%s」时浏览器被关掉了，本轮提前结束（已成功 %d 个）"
                                % (t.get("name"), ok), self.logs_dir)
                    break
                logger.error("发送「%s」失败：%s" % (t.get("name"), friendly_error(e).replace("\n", " ")),
                             self.logs_dir)
            await asyncio.sleep(random.uniform(*self.cfg["schedule"]["target_interval_seconds"]))
        if aborted is not None:
            raise aborted          # 交给 _submit：状态显示"已中止（浏览器被关掉了）"+ 中文提示
        skip_note = ("，另有 %d 个今天已经发过、跳过不重复发" % skipped) if skipped else ""
        if dry:
            logger.info("演练结束：%d/%d 个目标演练通过（没有发送任何消息）%s"
                        % (ok, len(targets), skip_note), self.logs_dir)
        elif ok > 0:
            logger.info("完成：%d/%d 个目标发送成功%s" % (ok, len(targets), skip_note), self.logs_dir)
        elif skipped > 0:
            logger.info("本轮没有要发的：%d 个目标今天都已经发过了，没有重复发" % skipped, self.logs_dir)
        else:
            logger.info("完成：0/%d 个目标发送成功" % len(targets), self.logs_dir)
        # 发完刷新右侧面板：火花状态/最近消息都会变（刚发过的会话图标会点亮）
        rows_after = await self._refresh_conv_rows(c, "发送后")
        if (ok > 0 or skipped > 0) and not dry:
            self._mark_daily_done(by, ok)     # 记一笔"今天主动续过了"，开机补发靠它判断
            if ok > 0:
                title = "今日火花已续" if by == "一键续火花" else "今日火花已续（%s）" % by
            else:
                title = "今天已经续过了"
            self.ui_queue.put(("notify", (title, rows_after)))
            # 已经确认发出去了才关（用户要求）；演练模式什么都没发，不关
            await self._close_browser(c, "消息已确认送达" if ok > 0 else "今天已经续过了")

    async def _refresh_conv_rows(self, c, when=""):
        """读一遍会话列表并交给主线程刷新右侧面板（火花状态、最近消息、头像）。

        注意：`_on_conv_rows()` 里会碰 Tk 控件，**只能在主线程跑**，
        所以这里只把数据丢进 ui_queue，由 `_pump()` 去调它。
        """
        try:
            rows = await c.list_conversations()
            rows = self._filter_conv_rows(rows, ("%s" % when) if when else "")
            self.ui_queue.put(("convlist", rows))
            if when:
                logger.info("%s已刷新右侧会话状态（火花 / 最近消息）" % when, self.logs_dir)
            return rows
        except Exception as e:
            logger.warn("%s刷新会话列表失败（不影响发送结果）：%s"
                        % (when, friendly_error(e).replace("\n", " ")), self.logs_dir)
            return None

    def on_send_all(self):
        self._submit(self._send_all, "一键续火花")

    def on_send_one(self):
        """右键菜单：只给选中的这一个目标发一次。"""
        idx = self._selected_index()
        if idx is None:
            return
        t = self.cfg["targets"][idx]
        if not (t.get("text") or "").strip() and not t.get("sticker_id") and not t.get("image_path"):
            messagebox.showwarning("提示", "「%s」还没有发送内容，请先编辑它" % t["name"])
            return
        dry = bool(self.cfg["schedule"].get("dry_run"))
        tip = "【演练模式】只走流程、不会真的发出去。\n\n" if dry else ""
        if not messagebox.askyesno("确认", "%s现在就给「%s」发送一次？" % (tip, t["name"])):
            return
        self._submit(lambda: self._send_targets([t], by="手动单个"), "发送单个目标")

    async def _sync_stickers(self):
        c = await self._ensure_client()
        if not await c.ensure_logged_in(120):
            raise RuntimeError("尚未登录，请先点「扫码登录」")
        await c.goto_chat()
        items = await c.collect_stickers(config.STICKERS_DIR)
        data = {"updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "items": items}
        config.save_stickers(data)
        self.stickers = data
        logger.info("已同步 %d 个超级表情，保存在 stickers.json" % len(items), self.logs_dir)

    def on_sync_stickers(self):
        self._submit(self._sync_stickers, "同步超级表情")

    async def _calibrate(self):
        c = await self._ensure_client()
        if not await c.ensure_logged_in(120):
            raise RuntimeError("尚未登录，请先点「扫码登录」")
        names = [t["name"] for t in self.cfg["targets"] if t.get("enabled")]
        report = await c.calibrate(names[0] if names else None)
        os.makedirs(self.logs_dir, exist_ok=True)
        path = os.path.join(self.logs_dir,
                            "calibrate_%s.txt" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        for line in report.splitlines():
            logger.info("  " + line, self.logs_dir)
        logger.info("抖音窗口校准报告已保存：%s" % path, self.logs_dir)

    def on_calibrate(self):
        self._submit(self._calibrate, "抖音窗口校准")

    # ------------------------------------------------------------ 调度
    # ------------------------------------------------------------ 自动模式：计划
    def _sent_keys_today(self):
        """今天已经发过的"日期|时间"集合（存在配置里，重启也不重复发）。"""
        sl = self.cfg["schedule"].get("sent_log") or {}
        today = datetime.date.today().strftime("%Y-%m-%d")
        if str(sl.get("date") or "") != today:
            return set()
        return set(sl.get("keys") or [])

    def _mark_sent(self, key):
        sched = self.cfg["schedule"]
        sl = sched.get("sent_log") or {}
        today = datetime.date.today().strftime("%Y-%m-%d")
        keys = list(sl.get("keys") or []) if str(sl.get("date") or "") == today else []
        if key not in keys:
            keys.append(key)
        sched["sent_log"] = {"date": today, "keys": keys[-200:]}
        try:
            self.cfg = config.save(self.cfg)
        except Exception:
            pass

    def _build_plan(self, now=None):
        """按目标配置排出今天的发送计划（now 只给自检用，正常调用不传）。

        两条经验（用户踩过）：
        1. **错过太久的不在这里补**：22:44 打开自动模式时，今天的 00:01 已经过去 22 小时，
           原样排进去会立刻触发"一启动就发一遍"；超过容忍窗（默认 5 分钟，只吸收轮询延迟）
           的直接标成已完成，改由「启动时先看一眼抖音、没续才补」来兜（见 `_boot_catchup`）；
        2. **同一天同一时间点只发一次**：记在 `schedule.sent_log` 里，重启、重建计划都不再重复。
        """
        plan = []
        sched = self.cfg["schedule"]
        jitter = int(sched.get("random_jitter_seconds") or 0)
        try:
            grace = int(sched.get("miss_grace_minutes", 5))
        except Exception:
            grace = 5
        grace = max(5, grace)      # 下限 5 分钟：只吸收轮询/唤醒的延迟，真正"错过"的交给启动核对
        now = now or datetime.datetime.now()
        today = now.date()
        sent_keys = self._sent_keys_today()
        skipped_old, skipped_sent = [], []
        for t in self.cfg["targets"]:
            if not t.get("enabled"):
                continue
            for hm in t.get("times") or []:
                try:
                    h, m = [int(x) for x in hm.split(":")]
                except Exception:
                    continue
                at = datetime.datetime.combine(today, datetime.time(h, m))
                key = "%s|%s" % (today.strftime("%Y-%m-%d"), hm)
                done = False
                if key in sent_keys:
                    done = True
                    skipped_sent.append((hm, t.get("name")))
                elif (now - at).total_seconds() > grace * 60:
                    done = True
                    skipped_old.append((hm, t.get("name"), at))
                if jitter and not done:
                    at += datetime.timedelta(seconds=random.randint(0, jitter))
                plan.append({"target": t, "at": at, "done": done, "hm": hm, "key": key})
        plan.sort(key=lambda x: x["at"])
        for hm, name, at in skipped_old:
            mins = int((now - at).total_seconds() // 60)
            logger.info("「%s」的 %s 今天已经过去 %d 分钟，这里不再直接补；"
                        "下次启动自动模式/开机时会先看一眼，没续才补" % (name, hm, mins), self.logs_dir)
        for hm, name in skipped_sent:
            logger.info("「%s」的 %s 今天已经发过了，跳过（同一天同一时间点只发一次）" % (name, hm),
                        self.logs_dir)
        return plan

    @staticmethod
    def _due_items(plan, now=None):
        """此刻该发的项（抽出来方便自检，不用真等时间）。"""
        now = now or datetime.datetime.now()
        return [it for it in plan if not it.get("done") and now >= it["at"]]

    async def _scheduler(self):
        self.scheduler_on = True
        logger.info("自动模式已启动（每 10 秒检查一次计划）", self.logs_dir)
        plan = []
        day = None
        while self.scheduler_on:
            today = datetime.date.today()
            if day != today:
                day = today
                plan = self._build_plan()
                logger.info("今日发送计划 %d 项（已含随机抖动）" % len(plan), self.logs_dir)
                for it in plan:
                    logger.info("   %s -> %s" % (it["at"].strftime("%H:%M:%S"), it["target"]["name"]),
                                self.logs_dir)

            due = self._due_items(plan)
            if due:
                try:
                    c = await self._ensure_client()
                    if not await c.ensure_logged_in(120):
                        self._set_login_state(False)
                        logger.error("自动模式：尚未登录，本轮跳过", self.logs_dir)
                    else:
                        self._set_login_state(True)
                        if self.cfg["schedule"].get("dry_run"):
                            logger.info("【演练模式】自动模式到点，只走流程不发送", self.logs_dir)
                        await c.goto_chat()   # 本批次只打开一次
                        await self._refresh_conv_rows(c, "自动模式前")
                        for it in due:
                            it["done"] = True
                            if it.get("key"):
                                self._mark_sent(it["key"])   # 同一天同一时间点只发一次
                            try:
                                await c.send_to_target(it["target"], ensure_chat=False)
                                self._heal_target_name(c, it["target"])
                            except Exception as e:
                                logger.error("自动模式发送「%s」失败：%s"
                                             % (it["target"]["name"],
                                                friendly_error(e).replace("\n", " ")),
                                             self.logs_dir)
                                if is_browser_closed(e):
                                    # 剩下的目标不在这里硬试：它们仍是「未完成」，下一次 10 秒轮询
                                    # 会走 _ensure_client() → 自动重开浏览器 → 继续发
                                    logger.warn("自动模式：浏览器被关掉了，本轮先停；"
                                                "程序会自动重开浏览器，稍后继续这一批", self.logs_dir)
                                    self.ui_queue.put(("status", "自动模式已中止（浏览器被关掉了）"))
                                    break
                            await asyncio.sleep(random.uniform(
                                *self.cfg["schedule"]["target_interval_seconds"]))
                        else:                      # 没 break 才刷新面板（break 时浏览器已不可用）
                            rows_after = await self._refresh_conv_rows(c, "自动模式后")
                            if not self.cfg["schedule"].get("dry_run"):
                                self._mark_daily_done("自动模式", len(due))
                                self.ui_queue.put(("notify", ("今日火花已续（自动）", rows_after)))
                except Exception as e:
                    logger.error("自动模式循环异常（已捕获）：%s"
                                 % friendly_error(e).replace("\n", " "), self.logs_dir)
                    for it in due:
                        it["done"] = True
            await asyncio.sleep(10)
        logger.info("自动模式已停止", self.logs_dir)
        self.ui_queue.put(("sched", False))

    def _set_sched_button(self, running):
        """模式按钮 + 托盘菜单的两种样子：没跑=主色"启动自动模式"，跑着=普通"停止自动模式"。"""
        try:
            if getattr(self, "tray", None) is not None:
                self.tray.set_sched_on(bool(running))      # 托盘右键菜单也显示当前状态
        except Exception:
            pass
        try:
            if running:
                # Tk 里 configure(bg=None) 等于"不改"，所以两种状态都给明确颜色
                self.sched_btn.configure(text="■ 停止自动模式", kind="run")
            else:
                self.sched_btn.configure(text="▶ 启动自动模式", kind="accent")
        except Exception:
            pass

    def on_toggle_scheduler(self):
        if self.scheduler_on:
            self.on_stop_scheduler()
        else:
            self.on_start_scheduler()

    def on_start_scheduler(self):
        if self.scheduler_on:
            messagebox.showinfo("提示", "自动模式已在运行")
            return
        self.cfg = config.load()
        self._refresh_tree()
        if self.cfg["schedule"].get("dry_run"):
            logger.info("【演练模式】自动模式已启动，但到点只会走流程，不会真的发送", self.logs_dir)
        self._set_sched_button(True)
        self.worker.submit(self._scheduler())
        # 启动时顺手核对一次：今天还没续就补一轮（与开机自启同一条路，只有一套判据）
        self._boot_catchup_go(net_tries=2)

    def on_stop_scheduler(self):
        if not self.scheduler_on:
            messagebox.showinfo("提示", "自动模式没在运行")
            return
        self.scheduler_on = False
        self._set_sched_button(False)
        self._set_status("正在停止自动模式…")

    # ------------------------------------------------------------ 今日已续 + 开机补发
    @staticmethod
    def _today_str():
        return datetime.date.today().strftime("%Y-%m-%d")

    def _mark_daily_done(self, by, count=0):
        """记下"今天已经主动续过火花"，开机补发靠它判断要不要补。"""
        try:
            self.cfg.setdefault("ui", {})["daily_done"] = {
                "date": self._today_str(),
                "at": datetime.datetime.now().strftime("%H:%M:%S"),
                "count": int(count or 0),
                "by": str(by or ""),
            }
            self.cfg = config.save(self.cfg)
        except Exception as e:
            logger.warn("记录「今日已续」状态失败（不影响发送）：%s" % e, self.logs_dir)

    def _daily_done_today(self):
        """今天主动续过就返回那条记录（dict），没续过返回 None。"""
        d = (self.cfg.get("ui") or {}).get("daily_done") or {}
        return d if str(d.get("date") or "") == self._today_str() else None

    async def _network_ok(self, host="www.douyin.com", port=443, timeout=4.0):
        """能不能连上抖音 —— 连得上才谈得上去续火花。"""

        def _probe():
            try:
                socket.create_connection((host, port), timeout=timeout).close()
                return True
            except Exception:
                return False

        try:
            return await asyncio.get_running_loop().run_in_executor(None, _probe)
        except Exception:
            return False

    async def _boot_catchup(self, tries=40, gap=30):
        """开机自启后：等联网 → 今天还没主动续过 → 就自动补一轮。

        用户原话："开机能判断是否是联网状态，如果是的话就自动检查今日是否已经主动续火花，
        如果没有就自动续一遍。"
        """
        for i in range(int(tries)):
            if await self._network_ok():
                break
            if i == 0:
                logger.info("开机检查：现在还没连上网，每隔 %d 秒再看一次（最多等 %d 分钟）"
                            % (gap, max(1, int(tries) * int(gap) // 60)), self.logs_dir)
            await asyncio.sleep(gap)
        else:
            logger.warn("开机检查：一直没等到网络，这次就不补发了（联网后可以手动点「一键续火花」）",
                        self.logs_dir)
            return
        done = self._daily_done_today()
        if done:
            logger.info("开机检查：今天已经主动续过了（%s %s，来自%s），不用重复发"
                        % (done.get("date"), done.get("at"), done.get("by") or "未知"), self.logs_dir)
            return
        targets = [t for t in self.cfg["targets"] if t.get("enabled")]
        if not targets:
            logger.info("开机检查：没有启用的目标，跳过补发", self.logs_dir)
            return

        # 只看一眼抖音里今天的真实状态（只读，不发消息）。
        # 不能只信本地标记：标记可能压根没写上（例如凌晨那次跑的是还没加标记的旧版本），
        # 那样就会把"今天已经发过"误判成"没续过"，让人一天收到两遍。
        logger.info("开机检查：网络正常，先看看抖音里今天的火花状态（只读，不会发消息）", self.logs_dir)
        verdict, need = await self._boot_spark_check(targets)
        if verdict == "unknown":
            return
        if verdict == "done":
            self._mark_daily_done("开机检查", len(targets))
            logger.info("开机检查：%d 个目标今天都已经续过了，这次不补发" % len(targets), self.logs_dir)
            self.ui_queue.put(("toast", ("今天已经续过了",
                                         "开机检查：%d 个目标在抖音里都已经续过，这次不用补发。" % len(targets))))
            if self.client is not None:
                await self._close_browser(self.client, "开机检查完成")
            return
        logger.info("开机检查：其中 %d 个目标今天还没续，自动补一轮" % len(need), self.logs_dir)
        self.ui_queue.put(("toast", ("正在补发今日火花",
                                     "还有 %d 个目标没续，联网了，自动补一轮……" % len(need))))
        await self._send_targets(need, by="开机补发")

    async def _boot_spark_check(self, targets):
        """开机补发前只读核对：这些目标今天在抖音里是不是已经续过了。

        返回 (verdict, need)：
          "done"    全都续过了（火花已点亮，或者"最后一条消息是我今天发的"）
          "need"    还有没续的，need 就是要补发的目标列表
          "unknown" 读不出来（没登录／出错）—— 这时**不补发**，宁可漏一次也不重复发
        """
        c = await self._ensure_client()
        if not await c.ensure_logged_in(60):
            self._set_login_state(False)
            logger.warn("开机检查：没读到登录态，这次不补发（点「扫码登录」后再手动点「一键续火花」）",
                        self.logs_dir)
            return "unknown", []
        self._set_login_state(True)
        try:
            res = await c.check_sparks(targets, shallow=True)   # 只看图标，不进会话（不标已读）
        except Exception as e:
            if is_browser_closed(e):
                logger.warn("开机检查：浏览器被关掉了，这次不补发", self.logs_dir)
            else:
                logger.warn("开机检查：读取今天的火花状态失败（%s），这次不补发"
                            % friendly_error(e).replace("\n", " "), self.logs_dir)
            return "unknown", []

        got = {}
        for r in res or []:
            got[self._norm_name(r.get("name") or "")] = r
        need, detail, sts = [], [], []
        for t in targets:
            r = got.get(self._norm_name(t.get("name") or "")) or {}
            st = r.get("status")
            sts.append(st)
            nm = t.get("name") or ""
            if st in ("ok", "sent"):
                detail.append("%s（%s）" % (nm, "已点亮" if st == "ok" else "我今天已发"))
            else:
                need.append(t)
                detail.append("%s（%s）" % (nm, {"waiting": "还没续", "missing": "读不到",
                                                 "notlogin": "没登录", "unknown": "认不出"}.get(st, "读不到")))
        logger.info("开机检查：抖音里的今日火花 —— %s" % "、".join(detail), self.logs_dir)
        if not need:
            return "done", []
        if sts and all(x == "notlogin" for x in sts):
            logger.warn("开机检查：一个目标都没读到登录态，这次不补发", self.logs_dir)
            return "unknown", []
        return "need", need

    def _boot_catchup_go(self, net_tries=40):
        """主线程调用：按设置决定要不要"核对着补一轮"。

        开机自启时用默认的长等待（网络可能还没起来）；手动启动自动模式时只等一会儿。
        """
        try:
            _ui = self.cfg.get("ui") or {}
            # 这两个开关已经合并：只要"开机后自动运行"开着，启动时就顺手看一眼今天续没续
            if not (bool(_ui.get("boot_catchup", True)) or bool(_ui.get("auto_sched_on_boot", True))):
                logger.info("开机检查：按设置没有开启「今天没续就自动补一轮」", self.logs_dir)
                return
            if self._daily_done_today():
                logger.info("开机检查：今天已经主动续过了，不用补发", self.logs_dir)
                return
            self.worker.submit(self._boot_catchup(tries=net_tries))
        except Exception as e:
            logger.warn("开机检查出错（不影响其它功能）：%s" % e, self.logs_dir)

    def _sched_on_boot(self):
        """开机自启那一次启动时，顺带把自动模式跑起来。

        用户原话："开机自启的时候我还要右键它开启自动模式，有点麻烦。"
        只在开机自启（--tray）那次自动开；手动双击打开程序不会自动开。
        """
        try:
            if not bool((self.cfg.get("ui") or {}).get("auto_sched_on_boot", True)):
                logger.info("开机自启：按设置没有自动开始自动模式（窗口保持在托盘）", self.logs_dir)
                return
            if self.scheduler_on:
                return
            logger.info("开机自启：自动模式一并启动（不用再右键点一次）", self.logs_dir)
            self.on_start_scheduler()
            self._toast("自动模式已随开机自启运行",
                        "窗口保持收起，到点会自动续火花；想停就在托盘右键「停止自动模式」。")
        except Exception as e:
            logger.warn("开机自启没能自动开自动模式（可以手动在托盘里开）：%s" % e, self.logs_dir)

    # ------------------------------------------------------------ 生命周期
    def _on_close(self):
        # 点 X：默认收进托盘继续跑（自动模式还要用），托盘里选「退出程序」才真退
        if (not getattr(self, "_really_quit", False)
                and getattr(self, "tray", None) is not None
                and bool((self.cfg.get("ui") or {}).get("close_to_tray", True))):
            logger.info("窗口已收到系统托盘（程序继续在后台跑）；右键托盘图标可以打开或退出",
                        self.logs_dir)
            self._hide_to_tray()
            return
        self._really_quit = True
        if getattr(self, "tray", None) is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.scheduler_on = False
        if self.client is not None:
            try:
                fut = self.worker.submit(self.client.close())
                fut.result(timeout=15)
            except Exception:
                pass
        try:
            self.worker.loop.call_soon_threadsafe(self.worker.loop.stop)
        except Exception:
            pass
        logger.close_all()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def _abs(p):
    return p if os.path.isabs(p) else os.path.join(config.BASE_DIR, p)
