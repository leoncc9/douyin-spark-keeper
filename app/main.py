# -*- coding: utf-8 -*-
"""抖音续火花 —— 入口。

启动器（DouyinSparkKeeper.exe）会以 pythonw.exe 运行本文件。
支持参数：
    run        常驻调度（默认）
    --tray     开机自启用：启动后直接最小化在系统托盘（不弹主窗口）
    --selftest 环境自检
"""
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(BASE_DIR) if os.path.basename(BASE_DIR) == "app" else BASE_DIR
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def _selftest():
    """被 --selftest 调用：不依赖图形界面，打印环境信息。"""
    import platform
    import version
    print("version:", version.full_title())
    print("python :", sys.version.replace("\n", " "))
    print("app_dir:", APP_DIR)
    try:
        import playwright
        from playwright.async_api import async_playwright  # noqa: F401
        print("playwright : OK")
    except Exception as e:
        print("playwright : FAIL", e)
    try:
        import pyee  # noqa: F401
        print("pyee       : OK")
    except Exception as e:
        print("pyee       : FAIL", e)
    try:
        import greenlet  # noqa: F401
        print("greenlet   : OK (stub)")
    except Exception as e:
        print("greenlet   : FAIL", e)
    try:
        import tkinter
        print("tkinter    : OK")
    except Exception as e:
        print("tkinter    : FAIL", e)
    print("platform   :", platform.platform())


def main():
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        _selftest()
        return 0

    import autostart
    import single_instance

    # 单实例：已经在跑就不再开第二个（否则会抢同一个浏览器用户目录、托盘里堆图标）
    if not single_instance.acquire():
        if not single_instance.notify_existing():
            single_instance.warn_already_running()
        return 0

    from gui import SparkKeeperApp

    app = SparkKeeperApp(start_hidden=autostart.tray_arg_requested(args))
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
