# -*- coding: utf-8 -*-
"""单实例保护：同一时间只允许跑一个「抖音续火花」。

为什么需要：重复双击（或者开机自启 + 手动又开一次）会同时跑好几个实例，
它们各自开一个浏览器、抢同一个 `browser_data`（Chromium 用户目录），
结果就是"浏览器窗口被关掉了"这种报错，托盘里还会堆好几个火花图标。

做法：命名互斥体 `CreateMutexW`（进程活着就一直持有）+ 找到已有实例的托盘窗口，
发消息让它把主界面显示出来，然后第二个实例自己退出。
"""
import ctypes
import os
from ctypes import wintypes

# 名字可以用环境变量覆盖：自检跑的时候换个名字，免得被用户正在跑的实例挡下来
MUTEX_NAME = os.environ.get("DSK_MUTEX_NAME") or "Local\\DouyinSparkKeeper_SingleInstance"
ERROR_ALREADY_EXISTS = 183

_handle = None          # 留着引用，进程活着就一直持有这个互斥体


def acquire():
    """抢到返回 True；已经有实例在跑返回 False。

    拿不到互斥体（极少数环境）时返回 True —— 宁可多开，也别让程序打不开。
    """
    global _handle
    if _handle:
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        h = k32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.get_last_error()
        if not h:
            return True
        if err == ERROR_ALREADY_EXISTS:
            try:
                k32.CloseHandle(h)
            except Exception:
                pass
            return False
        _handle = h
        return True
    except Exception:
        return True


def notify_existing():
    """让已经在跑的那个实例把主界面显示出来（成功返回 True）。"""
    try:
        import tray
        return tray.signal_show()
    except Exception:
        return False


def warn_already_running():
    """找不到已有实例的窗口（比如托盘没起来）时，给个原生提示。"""
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "抖音续火花已经在运行了。\n\n"
            "它可能收在右下角的系统托盘里 —— 点一下托盘里的火花图标就能打开主界面。",
            "抖音续火花", 0x40)
    except Exception:
        pass
