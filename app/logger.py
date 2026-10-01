# -*- coding: utf-8 -*-
"""日志：控制台 + logs/keeper_YYYYMM.log（线程安全，按月缓存句柄）。"""
import datetime
import os
import sys
import threading

_lock = threading.Lock()
_handles = {}
_listeners = []


def add_listener(fn):
    """注册一个回调，用于把日志同步显示到 GUI 日志区。"""
    _listeners.append(fn)


def _month_file(log_dir):
    return os.path.join(log_dir, "keeper_%s.log" % datetime.datetime.now().strftime("%Y%m"))


def _real_dir(log_dir):
    """DSK_LOG_DIR：自检/探针把日志写到临时目录，免得污染用户的 logs\。"""
    try:
        env = os.environ.get("DSK_LOG_DIR")
    except Exception:
        env = None
    return env or log_dir


def log(level, msg, log_dir=None):
    log_dir = _real_dir(log_dir)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] [%s] %s" % (ts, level, msg)
    with _lock:
        try:
            print(line, flush=True)
        except Exception:
            pass
        if log_dir:
            try:
                os.makedirs(log_dir, exist_ok=True)
                key = _month_file(log_dir)
                fh = _handles.get(key)
                if fh is None:
                    fh = open(key, "a", encoding="utf-8")
                    _handles[key] = fh
                fh.write(line + "\n")
                fh.flush()
            except Exception:
                pass
    for fn in list(_listeners):
        try:
            fn(line)
        except Exception:
            pass


def info(msg, log_dir=None):
    log("INFO", msg, log_dir)


def warn(msg, log_dir=None):
    log("WARN", msg, log_dir)


def error(msg, log_dir=None):
    log("ERROR", msg, log_dir)


def close_all():
    with _lock:
        for fh in _handles.values():
            try:
                fh.close()
            except Exception:
                pass
        _handles.clear()
