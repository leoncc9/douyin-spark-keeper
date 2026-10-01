# -*- coding: utf-8 -*-
"""Windows 系统托盘图标（纯 ctypes 调 Shell_NotifyIcon，不依赖第三方库）。

为什么自己写：这台机器没有外网，装不了 pystray；而托盘要的就是"窗口关掉/最小化之后
程序还在后台跑"（每天定时续火花必须常驻）。

用法：
    tray = TrayIcon(icon_path, tooltip="抖音续火花", on_action=cb)
    tray.start()          # False = 这个环境用不了（非 Windows / API 失败），调用方自己兜底
    tray.balloon("标题", "内容")
    tray.stop()

`on_action(name)` 在**托盘线程**里被调用，name 是 "show" / "send" / "sched" / "quit"；
调用方只应该把它丢进队列（不能直接碰 Tk 控件）。
"""
import ctypes
import os
import threading
from ctypes import wintypes

WM_APP = 0x8000
WM_TRAY = WM_APP + 20
WM_TRAY_SHOW = WM_APP + 21          # 第二个实例发过来：把你的主界面显示出来
TRAY_CLASS = "DouyinSparkKeeperTray"
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_COMMAND = 0x0111

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIF_SHOWTIP = 0x80
NOTIFYICON_VERSION_4 = 4
NIIF_INFO = 0x01

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512
HWND_MESSAGE = -3

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class TrayIcon:
    """托盘图标 + 右键菜单。任何一步失败都不会抛给调用方（返回 False）。"""

    # 菜单是"现算"的：自动模式开着还是停着，标签不一样（不然用户分不清点下去会发生什么）
    def menu_items(self):
        return [("show", "打开主界面"),
                ("sep", None),
                ("send", "一键续火花"),
                ("sched", "■ 停止自动模式（正在跑）" if self.sched_on else "▶ 开始自动模式"),
                ("sep", None),
                ("quit", "退出程序")]

    def set_sched_on(self, on):
        """主线程告诉托盘：自动模式现在是开还是停（托盘线程弹菜单时读它）。"""
        self.sched_on = bool(on)

    def __init__(self, icon_path="", tooltip="抖音续火花", on_action=None):
        self.icon_path = icon_path or ""
        self.tooltip = tooltip[:120]
        self.on_action = on_action
        self.ok = False
        self.hwnd = None
        self.hicon = None
        self._thread = None
        self._cls = None
        self._wndproc = None
        self._nid = None
        self._ready = threading.Event()
        self.sched_on = False

    # ------------------------------------------------------------ 对外
    def start(self):
        if os.name != "nt":
            return False
        try:
            self._thread = threading.Thread(target=self._run, name="dsh-tray", daemon=True)
            self._thread.start()
            self._ready.wait(3.0)
        except Exception:
            self.ok = False
        return self.ok

    def balloon(self, title, text):
        """弹一个气泡提示（Win10/11 会变成通知）。"""
        if not self.ok or self._nid is None:
            return False
        try:
            nid = self._nid
            nid.uFlags = NIF_INFO | NIF_ICON | NIF_TIP | NIF_MESSAGE
            nid.szInfo = text[:255]
            nid.szInfoTitle = title[:63]
            nid.dwInfoFlags = NIIF_INFO
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            return True
        except Exception:
            return False

    def stop(self):
        try:
            if self.hwnd:
                ctypes.windll.user32.PostMessageW(self.hwnd, WM_DESTROY, 0, 0)
        except Exception:
            pass
        self.ok = False

    @staticmethod
    def _bind(user32, shell32, kernel32):
        """显式声明参数/返回类型：64 位下 HWND/HANDLE 是 8 字节指针，
        ctypes 默认按 c_int 返回会被截断（CreateWindowExW 直接返回 0 就是这么来的）。"""
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.RegisterClassExW.restype = wintypes.ATOM
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.LoadIconW.restype = wintypes.HICON
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.TrackPopupMenu.restype = wintypes.UINT
        user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
        user32.GetMessageW.restype = ctypes.c_int
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    # ------------------------------------------------------------ 托盘线程
    def _run(self):
        try:
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32
            self._bind(user32, shell32, kernel32)

            self._wndproc = WNDPROC(self._on_message)
            hinst = kernel32.GetModuleHandleW(None)
            cls_name = TRAY_CLASS
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = cls_name
            self._cls = wc
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                self.last_error = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
            # 父窗口传 None：一个从不显示的顶层窗口（比 HWND_MESSAGE 更省事、兼容性更好）
            self.hwnd = user32.CreateWindowExW(0, cls_name, "DSKTray", 0, 0, 0, 0, 0,
                                               None, None, hinst, None)
            if not self.hwnd:
                return

            self.hicon = self._load_icon(user32)
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = self.hicon
            nid.szTip = self.tooltip
            self._nid = nid
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                self._nid = None
                return
            nid.uVersion = NOTIFYICON_VERSION_4
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
            self.ok = True
            self._ready.set()

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self.ok = False
        finally:
            try:
                if self._nid is not None:
                    ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass
            self._nid = None
            self._ready.set()

    def _load_icon(self, user32):
        if self.icon_path and os.path.exists(self.icon_path):
            for flags in (LR_LOADFROMFILE | LR_DEFAULTSIZE, LR_LOADFROMFILE):
                h = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0, flags)
                if h:
                    return h
        return user32.LoadIconW(None, ctypes.cast(IDI_APPLICATION, wintypes.LPCWSTR))

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            user32 = ctypes.windll.user32
            if msg == WM_TRAY_SHOW:
                self._emit("show")
                return 0
            if msg == WM_TRAY:
                low = lparam & 0xFFFF
                if low in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._emit("show")
                elif low in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._popup(user32, hwnd)
            elif msg == WM_COMMAND:
                cmd = wparam & 0xFFFF
                for idx, (name, _label) in enumerate(self.menu_items()):
                    if name != "sep" and (1000 + idx) == cmd:
                        self._emit(name)
                        break
            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup(self, user32, hwnd):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            for idx, (name, label) in enumerate(self.menu_items()):
                if name == "sep":
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                else:
                    user32.AppendMenuW(menu, MF_STRING, 1000 + idx, label)
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(hwnd)
            cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                                        pt.x, pt.y, 0, hwnd, None)
            user32.PostMessageW(hwnd, 0, 0, 0)
            if cmd:
                for idx, (name, _label) in enumerate(self.menu_items()):
                    if name != "sep" and (1000 + idx) == cmd:
                        self._emit(name)
                        break
        finally:
            user32.DestroyMenu(menu)

    def _emit(self, name):
        if self.on_action:
            try:
                self.on_action(name)
            except Exception:
                pass


def signal_show():
    """找到已经在跑的那个实例的托盘窗口，让它把主界面显示出来。"""
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        hwnd = user32.FindWindowW(TRAY_CLASS, None)
        if not hwnd:
            return False
        user32.PostMessageW(hwnd, WM_TRAY_SHOW, 0, 0)
        return True
    except Exception:
        return False


def make_ico(png_path, ico_path, sizes=((16, 16), (32, 32), (48, 48))):
    """把火花 PNG 转成 .ico（托盘图标用）。失败返回 ""。"""
    try:
        from PIL import Image
        im = Image.open(png_path).convert("RGBA")
        w, h = im.size
        side = min(w, h)
        im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        os.makedirs(os.path.dirname(ico_path), exist_ok=True)
        im.save(ico_path, format="ICO", sizes=list(sizes))
        return ico_path
    except Exception:
        return ""
