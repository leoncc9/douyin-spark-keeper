# -*- coding: utf-8 -*-
"""开机自启（写 HKCU 的 Run 键，不需要管理员权限、离线可用）。

开机启动命令末尾带 `--tray`：开机后程序直接**最小化在托盘**跑，不弹主窗口。
注册表位置：HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
"""
import os
import sys

import config

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "DouyinSparkKeeper"
TRAY_ARG = "--tray"


def launch_command():
    """开机要执行的命令行：优先用启动器 exe（和用户双击的是同一个），没有就退回 pythonw。"""
    base = config.BASE_DIR
    exe = os.path.join(base, "DouyinSparkKeeper.exe")
    if os.path.exists(exe):
        return '"%s" %s' % (exe, TRAY_ARG)
    pyw = os.path.join(base, "runtime", "pythonw.exe")
    script = os.path.join(base, "app", "main.py")
    if os.path.exists(pyw) and os.path.exists(script):
        return '"%s" "%s" %s' % (pyw, script, TRAY_ARG)
    return ""


def current_value():
    """注册表里现在的值（没有就是 ""）。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _t = winreg.QueryValueEx(k, RUN_NAME)
            return str(v or "")
    except Exception:
        return ""


def enabled():
    return bool(current_value())


def set_enabled(on):
    """打开/关闭开机自启。返回 (成功?, 说明文字)。"""
    if os.name != "nt":
        return False, "只有 Windows 支持开机自启"
    try:
        import winreg
    except Exception as e:
        return False, "拿不到注册表接口：%s" % e
    try:
        if on:
            cmd = launch_command()
            if not cmd:
                return False, "找不到启动程序（DouyinSparkKeeper.exe 或 runtime\\pythonw.exe）"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, cmd)
            return True, cmd
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
        return True, ""
    except Exception as e:
        return False, str(e)


def tray_arg_requested(argv=None):
    """这次启动是不是"开机自启"那种（带 --tray）。"""
    argv = sys.argv[1:] if argv is None else argv
    return any(a.lower() in (TRAY_ARG, "--hidden", "--minimized") for a in argv)
