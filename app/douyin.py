# -*- coding: utf-8 -*-
"""抖音网页版私信发送引擎（基于 Playwright async API，接管系统 Edge）。

设计原则：
1. 不接触账号密码：使用独立浏览器用户目录保存登录态，首次扫码登录即可；
2. 只模拟"人点鼠标、敲键盘"的正常操作，不破解协议；
3. 页面元素采用"多候选选择器 + 文本兜底"，页面改版后可在 config.json 的
   selectors 中补充选择器，无需改代码；
4. 任何一步失败都会截图到 logs 目录，便于排查。
"""
import asyncio
import base64
import hashlib
import os
import random
import re
import time

from playwright.async_api import async_playwright

import logger
from config import detect_browsers as config_detect_browsers
from config import BROWSER_LABELS as config_browser_labels


def config_browser_label(channel):
    return config_browser_labels.get(channel, channel)


# ---------------------------------------------------------------- 报错汉化
# Playwright 的异常全是英文（"Target page, context or browser has been closed" 之类），
# 直接弹给用户看不懂、也不知道下一步做什么。这里统一翻成能照做的中文。
class BrowserClosedError(RuntimeError):
    """浏览器窗口被关掉 / 浏览器进程退出。

    这类错误**重试没有意义**，整轮应当立刻结束（用户重新点一次按钮即可，
    `_ensure_client()` 会自动重开浏览器，登录态还在）。
    """


BROWSER_CLOSED_CN = (
    "浏览器窗口已经被关掉了（被手动关闭，或浏览器自己退出了）。\n"
    "重新点一次刚才那个按钮就行：程序会自动重新打开浏览器，登录态还在，不用重新扫码。")

_CLOSED_MARKS = (
    "has been closed",
    "target closed",
    "browser closed",
    "context or browser has been closed",
    "connection closed",
    "page closed",
    "target page, context or browser has been closed",
    "browser has been disconnected",
)


def _first_line(s, n=200):
    s = str(s).strip().splitlines()
    return (s[0] if s else "")[:n]


def is_browser_closed(exc):
    """这个异常是不是"浏览器/页面被关掉了"。"""
    if isinstance(exc, BrowserClosedError):
        return True
    low = str(exc).lower()
    return any(m in low for m in _CLOSED_MARKS)



NET_ERROR_MARKS = ("net::err", "err_connection", "err_name_not_resolved",
                   "err_internet_disconnected", "err_timed_out", "err_network_changed")


def is_network_error(exc):
    """是不是"网"的问题（不是页面结构变了）。"""
    low = str(exc).lower()
    return any(m in low for m in NET_ERROR_MARKS)

def friendly_error(exc):
    """Playwright 英文报错 → 中文提示；认不出来的保留原文（别把线索弄丢）。"""
    if isinstance(exc, BrowserClosedError):
        return str(exc)
    s = str(exc)
    low = s.lower()
    if is_browser_closed(exc):
        return BROWSER_CLOSED_CN
    if "timeout" in low and ("exceeded" in low or "waiting for" in low):
        return ("操作超时（页面没在预期时间内响应）。\n"
                "常见原因：网速慢、抖音改版、或页面弹了安全验证。\n"
                "先重试一次；一直不行就点「页面校准」，把 logs 里生成的报告发出来。\n"
                "原始错误：%s" % _first_line(s))
    if "err_connection_reset" in low or "err_connection_closed" in low or "err_connection_aborted" in low:
        return ("连接被重置了（打不开抖音）。常见原因：\n"
                "· 网络不稳 / 路由器或宽带抖动；\n"
                "· 开了代理、VPN、加速器，或安全软件/防火墙拦了这条连接；\n"
                "· 短时间请求太频繁，被抖音那边掐了连接。\n"
                "等 1~2 分钟再试；一直不行就先用浏览器手动打开 douyin.com 看看通不通。\n"
                "原始错误：%s" % _first_line(s))
    if "err_name_not_resolved" in low or "err_internet_disconnected" in low or "err_network_changed" in low:
        return ("网络不通 / 域名解析失败，打不开抖音。\n"
                "检查一下网络、代理和 DNS，然后重试。\n原始错误：%s" % _first_line(s))
    if "err_timed_out" in low or "err_proxy" in low:
        return ("连抖音超时（可能是代理/VPN 或网络太慢）。\n"
                "关掉代理或换个网络再试。\n原始错误：%s" % _first_line(s))
    if "net::err" in low:
        return ("打不开抖音页面（网络错误）。检查网络和代理后重试。\n原始错误：%s" % _first_line(s))
    if "executable doesn't exist" in low:
        return ("找不到浏览器本体。本机需要装 Microsoft Edge 或 Google Chrome（任一），\n"
                "然后在「设置 → 浏览器」里选一个。\n原始错误：%s" % _first_line(s))
    return s

# ---------------------------------------------------------------- 选择器候选
SELECTORS = {
    # 会话搜索框
    "search_box": [
        'input[placeholder*="搜索"]',
        'input[type="search"]',
        "aside input",
        "input",
    ],
    # 搜索结果条目
    "session_item": [
        '[class*="SearchPanelitembox"]',
        '[class*="SearchPanelgrouplist"] > div',
        '[class*="search_result"] [class*="itembox"]',
        '[role="option"]',
        '[role="listbox"] li',
    ],
    # 搜索面板右上角的「取消」（收起面板、恢复左侧会话列表的正确出口）
    "search_cancel": [
        '[class*="searchSearchInputsearch_cancel"]',
        '[class*="search_cancel"]',
    ],
    # 消息输入框
    "message_input": [
        '[class*="messageEditorinputArea"][contenteditable="true"]',
        'div[contenteditable="true"]',
        '[role="textbox"]',
        "textarea",
    ],
    # 会话列表项（左侧）
    "conversation_item": ['[data-e2e="conversation-item"]',
                          '[class*="conversationConversationItemwrapper"]'],
    "conversation_title": ['[class*="conversationConversationItemtitle"]'],
    # 当前会话标题（聊天窗口头部，用于发送前核对"确实打开了这个人"）
    "chat_title": ['[class*="RightPanelHeadertitle"]', '[class*="RightPanelHeadertitleContainer"]'],
    "streak": ['[class*="commonStreakstreakContainer"]'],
    "right_header": ['[class*="RightPanelHeaderuser"]', '[class*="RightPanelHeader"]'],
    "message_box": ['[class*="messageMessageBoxcontentBox"]'],
    "message_time": ['[class*="MessageBoxTimetimeLayout"]'],
    "sticker_panel": ['[class*="emojitabPanel"]'],
}

LOGIN_HINTS = [
    'img[class*="qrcode" i]',
    'canvas[class*="qrcode" i]',
    "text=扫码登录",
    "text=验证码登录",
    "text=密码登录",
    "text=登录后免费畅享",
]

# ---------------------------------------------------------------- 反自动化检测
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""

# 定位搜索结果里的「发消息」按钮，返回屏幕坐标（由 Playwright 发真实鼠标点击）。
# 完全基于可见文本；关键点：只取「最内层」按钮——外层容器 SearchPanelitemtail
# 的文本同样是"发消息"，点到它不会生效。
JS_LOCATE_RESULT = """
(name) => {
  const all = Array.from(document.querySelectorAll('*'));
  const txt = (e) => (e.innerText || '').trim();
  const norm = (s) => (s || '').replace(/\\s+/g, '');
  const want = norm(name);
  const isDeepestBtn = (e) => txt(e) === '发消息'
      && !Array.from(e.querySelectorAll('*')).some(c => txt(c) === '发消息');

  // 昵称锚点：双向包含，兼容高亮片段只覆盖部分文字（如「好友A」vs「好友A 🔥423」）
  let anchor = null;
  for (const e of all) {
    if (e.children.length === 0 && norm(txt(e)) === want) { anchor = e; break; }
  }
  if (!anchor) {
    for (const e of all) {
      if (e.children.length > 0) continue;
      const t = norm(txt(e));
      if (!t) continue;
      if (t.indexOf(want) >= 0 || want.indexOf(t) >= 0) { anchor = e; break; }
    }
  }

  const box = (el, via) => {
    el.scrollIntoView({block: 'center'});
    const r = el.getBoundingClientRect();
    return {ok: true, via: via, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
            cls: String(el.className || '')};
  };

  // 1) 昵称所在的祖先容器里，最内层的「发消息」
  if (anchor) {
    let node = anchor;
    for (let i = 0; i < 10 && node; i++) {
      const hits = Array.from(node.querySelectorAll('*')).filter(isDeepestBtn);
      if (hits.length) return box(hits[hits.length - 1], 'nearest-btn');
      node = node.parentElement;
    }
  }
  // 2) 整页最内层的「发消息」（取最后一个 = 最深）
  const btns = all.filter(isDeepestBtn);
  if (btns.length) return box(btns[btns.length - 1], 'last-btn');
  // 3) 退化：直接点昵称
  if (anchor) return box(anchor, 'anchor');
  return {ok: false, reason: 'not-found'};
}
"""

# 输入栏操作按钮，返回屏幕坐标（按 x 升序）。
#
# 主路径用输入栏专属容器 messageMsgInputinputAction —— 它是语义化 class，
# 且只存在于输入栏，因此不受窗口尺寸/分辨率影响，也不会误命中聊天记录里的图标。
# 兜底路径才退回"相对输入框"的几何定位。
JS_FIND_ICONS = """
() => {
  const box = document.querySelector('[class*="messageMsgInputinputAction"]');
  if (box) {
    const svgs = Array.from(box.querySelectorAll('svg')).filter(s =>
      !s.closest('[class*="semi-upload"]') && !s.closest('[class*="Upload"]'));
    const pts = [];
    svgs.forEach(s => {
      const r = s.getBoundingClientRect();
      if (r.width < 10 || r.width > 60 || r.height < 10 || r.height > 60) return;
      pts.push({x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                inBox: true,
                cls: String((s.parentElement && s.parentElement.className) || '')});
    });
    if (pts.length) {
      pts.sort((a, b) => a.x - b.x);
      return pts;
    }
  }
  // 兜底：相对输入框定位（页面改版后仍可能可用）
  const ed = document.querySelector('[class*="messageEditorinputArea"], div[contenteditable="true"]');
  const er = ed ? ed.getBoundingClientRect() : null;
  const res = [];
  document.querySelectorAll('svg').forEach(s => {
    if (s.closest('[class*="Upload"]') || s.closest('[class*="semi-upload"]')) return;
    if (s.closest('[class*="douyin-player"]')) return;
    if (s.closest('[class*="MessageItem"]')) return;
    const r = s.getBoundingClientRect();
    if (r.width < 12 || r.width > 44 || r.height < 12 || r.height > 44) return;
    if (er && Math.abs((r.y + r.height / 2) - (er.y + er.height / 2)) > 80) return;
    res.push({x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), inBox: false});
  });
  const seen = {};
  return res.filter(a => {
    const k = a.x + ':' + a.y;
    if (seen[k]) return false;
    seen[k] = 1;
    return true;
  }).sort((a, b) => a.x - b.x);
}
"""

# 表情面板（超级表情，面板默认的第一个标签页）里的图片
JS_STICKER_LIST = """
() => {
  const panel = document.querySelector('[class*="emojitabPanel"]');
  if (!panel) return [];
  const seen = {};
  const out = [];
  panel.querySelectorAll('img').forEach(im => {
    if (!/im-resource/.test(im.src) || seen[im.src]) return;
    seen[im.src] = 1;
    const r = im.getBoundingClientRect();
    out.push({src: im.src, w: Math.round(r.width), h: Math.round(r.height)});
  });
  out.sort((a, b) => a.w - b.w);
  return out;
}
"""

JS_STICKER_DIAG = """
() => {
  const p = document.querySelector('[class*="emojitabPanel"]');
  if (!p) return {panel: false};
  const imgs = Array.from(p.querySelectorAll('img'));
  return {
    panel: true,
    imgCount: imgs.length,
    resource: imgs.filter(i => /im-resource/.test(i.src || '')).length,
    sample: imgs.slice(0, 3).map(i => (i.src || '').slice(0, 70)),
    text: (p.innerText || '').replace(/\\s+/g, ' ').slice(0, 100)
  };
}
"""

JS_EDITOR_TEXT = """
() => {
  const el = document.querySelector('[class*="messageEditorinputArea"], div[contenteditable="true"]');
  if (!el) return '';
  // 抖音输入框空置时会有一个零宽空格占位，必须剔除，否则会被误判成"没发出去"
  return (el.innerText || el.textContent || '')
    .replace(/[\\u200b\\u200c\\u200d\\ufeff]/g, '')
    .trim();
}
"""

# 发送按钮是否已"就绪"。
# 输入框空置时发送 svg 只有 messageMsgInputpublishBtn；一旦有内容，抖音会额外加上
# messageMsgInputpublishRedBtn（变红=可发送）。演练模式用它确认"这一步真的会生效"。
JS_SEND_READY = """
() => {
  const box = document.querySelector('[class*="messageMsgInputinputAction"]');
  if (!box) return {found: false, ready: false, cls: ''};
  const svgs = Array.from(box.querySelectorAll('svg'));
  const cand = svgs.filter(s => /publishBtn/i.test(String(s.getAttribute('class') || '')));
  if (!cand.length) return {found: false, ready: false, cls: ''};
  const cls = String(cand[cand.length - 1].getAttribute('class') || '');
  return {found: true, ready: /publishRedBtn/i.test(cls), cls: cls};
}
"""

# 会话列表项的读取：火花天数/状态沿用下面这套图标推导规则。
# 名字必须精确取到不含 Wrapper 的那个 conversationConversationItemtitle 节点，
# 否则 innerText 里会混进"连续天数 + 最后消息时间"，名字永远匹配不上。
#
# 选择器说明（实测）：
#   会话项本身带 data-e2e="conversation-item"（比 class 稳）
#   conv_id 在 React 虚拟列表的 virtualItem.id 上（好友/群聊都有）
#   sec_uid 在 conversation.toParticipantSecUserId 上（只有好友有）
JS_LIST_CONVERSATIONS = """
() => {
  const findUp = (el, want) => {
    const keys = Object.keys(el).filter(k => k.indexOf('__reactFiber$') === 0);
    if (!keys.length) return null;
    let f = el[keys[0]];
    for (let i = 0; i < 16 && f; i++) {
      const p = f.memoizedProps;
      if (p) {
        try { const v = want(p); if (v) return v; } catch (e) {}
      }
      f = f.return;
    }
    return null;
  };
  const titleOf = (item) => {
    let te = null;
    item.querySelectorAll('[class*="conversationConversationItemtitle"]').forEach(e => {
      if (te) return;
      if (/wrapper/i.test(String(e.className || ''))) return;
      te = e;
    });
    return te ? (te.innerText || '').trim().split('\\n')[0].trim() : '';
  };
  const out = [];
  const items = Array.from(document.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
  const pickAvatar = (item) => {
    // 好友：commonIMAvataravatarContainer > span.semi-avatar > img
    const a = item.querySelector('[class*="Avatar"] img');
    if (a && a.src) return String(a.src);
    // 群聊：img.commonConversationIconnoDrag（直接挂在 rowArea1 下）
    const g = item.querySelector('img[class*="ConversationIcon"], img[class*="icon"]:not([class*="Streak"])');
    if (g && g.src) return String(g.src);
    for (const im of Array.from(item.querySelectorAll('img'))) {
      if (/streak/i.test(String(im.className || ''))) continue;
      if (im.src) return String(im.src);
    }
    return '';
  };
  items.forEach((item, index) => {
    const name = titleOf(item);
    if (!name) return;
    const s = item.querySelector('[class*="commonStreakstreakContainer"]');
    let days = '', state = 'none', icon = '';
    if (s) {
      days = (s.innerText || '').trim();
      const im = s.querySelector('img');
      icon = im ? String(im.src || '') : '';
      // 取 /flame_icon/ 之后那一段（目录/文件名）；不认识就标 unknown。
      // 以前抓不到就默认 'normal'，会把"灰火（今天没点亮）"错报成"已点亮"。
      const m = icon.match(/flame_icon\\/(.+?)(?:\\?|$)/);
      state = m ? m[1] : (icon ? 'unknown' : 'none');
    }
    const convId = findUp(item, p => (p.virtualItem && p.virtualItem.id) ? String(p.virtualItem.id) : null) || '';
    const secUid = findUp(item, p => {
      const c = p.conversation;
      return c ? (c.toParticipantSecUserId || c._toParticipantSecUserId || null) : null;
    }) || '';
    const t = item.querySelector('[class*="ConversationItemTagNextToTitletimeStr"]');
    const hint = item.querySelector('[class*="ConversationItemHinttextBox"]');
    out.push({conv_id: convId, sec_uid: secUid, name: name, days: days, state: state, icon: icon,
              index: index, avatar: pickAvatar(item),
              time: t ? (t.innerText || '').trim() : '',
              hint: hint ? (hint.innerText || '').trim().slice(0, 40) : ''});
  });
  return out;
}
"""

# 在页面里把头像原图取回来（base64）。跨域被拦时由 Python 侧退回"元素截图"。
JS_FETCH_BASE64 = """
async (u) => {
  const resp = await fetch(u, {credentials: 'omit'});
  if (!resp.ok) return null;
  const buf = await resp.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let bin = '';
  const chunk = 8192;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}
"""

# 在左侧列表里按「稳定会话 ID 优先、其次精确昵称」找到某一项并打上标记。
#
# 为什么不直接返回坐标再点：这个列表会滚动/重排，JS 算出的坐标等 Python 真的点下去时
# 已经过期，会点到相邻的会话上（实测踩过：点 emoji 群却打开了别人）。
# 打标记后交给 Playwright 的 locator.click()——它会自己滚到可见位置并在点击瞬间算坐标。
JS_MARK_CONV = """
(arg) => {
  const wantId = (arg && arg.convId) || '';
  const wantName = (arg && arg.name) || '';
  document.querySelectorAll('[data-dsk-pick]').forEach(e => e.removeAttribute('data-dsk-pick'));
  const findUp = (el, want) => {
    const keys = Object.keys(el).filter(k => k.indexOf('__reactFiber$') === 0);
    if (!keys.length) return null;
    let f = el[keys[0]];
    for (let i = 0; i < 16 && f; i++) {
      const p = f.memoizedProps;
      if (p) { try { const v = want(p); if (v) return v; } catch (e) {} }
      f = f.return;
    }
    return null;
  };
  const norm = (s) => String(s || '').replace(/[\\u200b-\\u200f\\ufe0e\\ufe0f\\u2060]/g, '').replace(/\\s+/g, '');
  const items = Array.from(document.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
  const nameOf = (item) => {
    let te = null;
    item.querySelectorAll('[class*="conversationConversationItemtitle"]').forEach(e => {
      if (te) return;
      if (/wrapper/i.test(String(e.className || ''))) return;
      te = e;
    });
    return te ? (te.innerText || '').trim().split('\\n')[0].trim() : '';
  };
  if (wantId) {
    for (const item of items) {
      const cid = findUp(item, p => (p.virtualItem && p.virtualItem.id) ? String(p.virtualItem.id) : null);
      if (cid && cid === wantId) {
        item.setAttribute('data-dsk-pick', '1');
        return {by: 'id', row: nameOf(item)};
      }
    }
  }
  if (wantName) {
    for (const item of items) {
      const n = nameOf(item);
      if (n && norm(n) === norm(wantName)) {
        item.setAttribute('data-dsk-pick', '1');
        return {by: 'name', row: n};
      }
    }
  }
  return null;
}
"""

# 头像取法的统计（校准用）：好友走 Avatar 容器，群聊走 ConversationIcon
JS_AVATAR_STATS = """
() => {
  let avatar = 0, convIcon = 0, none = 0, total = 0;
  document.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]').forEach(it => {
    total++;
    if (it.querySelector('[class*="Avatar"] img')) avatar++;
    else if (it.querySelector('img[class*="ConversationIcon"], img[class*="icon"]:not([class*="Streak"])')) convIcon++;
    else none++;
  });
  return {total: total, avatar: avatar, convIcon: convIcon, none: none};
}
"""

# 搜索面板里的候选（联系人搜索结果），用于"列表里没有的人"这条兜底路径。
JS_SEARCH_RESULTS = """
() => {
  const panel = document.querySelector('[class*="SearchPanel"]');
  if (!panel) return [];
  const out = [];
  panel.querySelectorAll('[class*="SearchPanelitembox"]').forEach((it, i) => {
    let te = null;
    it.querySelectorAll('[class*="conversationConversationItemtitle"]').forEach(e => {
      if (te) return;
      if (/wrapper/i.test(String(e.className || ''))) return;
      te = e;
    });
    let name = te ? (te.innerText || '').trim().split('\\n')[0].trim() : '';
    if (!name) {
      const lines = (it.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
      name = lines.length ? lines[0] : '';
    }
    const hasBtn = !!Array.from(it.querySelectorAll('*')).find(e => (e.innerText || '').trim() === '发消息'
        && !Array.from(e.querySelectorAll('*')).some(c => (c.innerText || '').trim() === '发消息'));
    out.push({index: i, name: name, has_send: hasBtn,
              raw: (it.innerText || '').replace(/\\n/g, '|').slice(0, 80)});
  });
  return out;
}
"""

JS_SPARK_SCROLL = """
() => {
  const w = document.querySelector('[class*="conversationConversationItemwrapper"]');
  if (!w) return -1;
  let sc = w.parentElement;
  for (let i = 0; i < 10 && sc; i++) {
    if (sc.scrollHeight > sc.clientHeight + 4) break;
    sc = sc.parentElement;
  }
  if (!sc) return -1;
  const before = sc.scrollTop;
  sc.scrollTop = before + 420;
  return [before, sc.scrollTop, sc.scrollHeight, sc.clientHeight];
}
"""

JS_SPARK_SCROLL_TOP = """
() => {
  const w = document.querySelector('[class*="conversationConversationItemwrapper"]');
  if (!w) return;
  let sc = w.parentElement;
  for (let i = 0; i < 10 && sc; i++) {
    if (sc.scrollHeight > sc.clientHeight + 4) break;
    sc = sc.parentElement;
  }
  if (sc) sc.scrollTop = 0;
}
"""

# 聊天窗口头部（当前会话）的火花
JS_SPARK_HEADER = """
() => {
  const h = document.querySelector('[class*="RightPanelHeaderuser"],[class*="RightPanelHeader"]');
  if (!h) return {days: '', state: 'none'};
  const s = h.querySelector('[class*="commonStreakstreakContainer"]');
  if (!s) return {days: '', state: 'none'};
  const days = (s.innerText || '').trim();
  const im = s.querySelector('img');
  const icon = im ? String(im.src || '') : '';
  const m = icon.match(/flame_icon\\/(.+?)(?:\\?|$)/);
  return {days: days, state: m ? m[1] : (icon ? 'unknown' : 'none'), icon: icon};
}
"""

# 最后一条消息：方向 + 时间文案
JS_LAST_MSG = """
() => {
  const pickBottom = (sel) => {
    let best = null, bestY = -Infinity;
    document.querySelectorAll(sel).forEach(e => {
      const r = e.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.y > bestY) { bestY = r.y; best = e; }
    });
    return best;
  };
  const result = {me: false, time: '', text: ''};
  const last = pickBottom('[class*="messageMessageBoxcontentBox"]');
  if (last) {
    result.me = /isFromMe/i.test(String(last.className || ''));
    result.text = (last.innerText || '').trim().replace(/\\n/g, ' ').slice(0, 30);
  }
  const t = pickBottom('[class*="MessageBoxTimetimeLayout"]');
  if (t) result.time = (t.innerText || '').trim();
  return result;
}
"""

JS_BODY_TEXT = "() => document.body ? document.body.innerText.slice(0, 200) : ''"

# 聊天记录里"最下面那条消息"的特征：用于发送前/后对比，确认消息真的发出去了。
# 取 y 最大且有尺寸的消息气泡；同时给出消息气泡总数（新消息到达时通常会 +1）。
JS_LAST_BUBBLE = """
() => {
  const boxes = Array.from(document.querySelectorAll('[class*="messageMessageBoxcontentBox"]'));
  let best = null, bestY = -Infinity;
  boxes.forEach(e => {
    const r = e.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.y > bestY) { bestY = r.y; best = e; }
  });
  if (!best) return {count: boxes.length, me: false, text: '', img: ''};
  const img = best.querySelector('img');
  return {
    count: boxes.length,
    me: /isFromMe/i.test(String(best.className || '')),
    text: (best.innerText || '').trim().replace(/\\n/g, ' ').slice(0, 40),
    img: img ? String(img.src || '').slice(0, 160) : '',
    y: Math.round(bestY)
  };
}
"""

# 表情面板里某个超级表情的屏幕坐标（发送时用真实鼠标点击，DOM 的 el.click() 不被信任）
JS_STICKER_POS = """
(sid) => {
  const panel = document.querySelector('[class*="emojitabPanel"]');
  if (!panel) return null;
  const imgs = Array.from(panel.querySelectorAll('img'));
  for (const im of imgs) {
    if (im.src && im.src.indexOf(sid) >= 0) {
      im.scrollIntoView({block: 'center'});
      const r = im.getBoundingClientRect();
      const target = im.closest('[class*="emojiEmojiItem"]') || im;
      const tr = target.getBoundingClientRect();
      return {x: Math.round(tr.x + tr.width / 2), y: Math.round(tr.y + tr.height / 2),
              w: Math.round(r.width), h: Math.round(r.height),
              cls: String(target.className || '')};
    }
  }
  return null;
}
"""

# 火花图标状态 -> 业务状态
SPARK_STATUS_CN = {
    "ok": "今日已续",
    "waiting": "今日待续（轮到我发）",
    "sent": "我今日已发，对方未续",
    "none": "未建立火花",
    "missing": "未找到会话",
    "notlogin": "尚未登录",
    "unknown": "状态未知（图标不认识，点刷新再试）",
}


class DouyinClient:
    def __init__(self, cfg, log_dir=None):
        self.cfg = cfg
        self.log_dir = log_dir
        self.browser_cfg = cfg["browser"]
        self.sched = cfg["schedule"]
        self.user_data_dir = _abs(self.browser_cfg["user_data_dir"])
        self.page = None
        self.ctx = None
        self._pw = None
        # 最近一次成功打开会话的记录（open_target 填），供上层自动纠正昵称
        self.last_opened = None

    # -------------------------------------------------------------- 工具
    def log(self, level, msg):
        getattr(logger, level)(msg, self.log_dir)

    def sel(self, key):
        base = list(SELECTORS.get(key, []))
        custom = (self.cfg.get("selectors") or {}).get(key)
        if custom:
            if isinstance(custom, str):
                custom = [custom]
            return list(custom) + base
        return base

    async def _first_visible(self, selectors, timeout_ms=None):
        """按候选列表返回第一个可见元素；全部失败返回 None。"""
        deadline = time.monotonic() + (timeout_ms or self.browser_cfg["action_timeout_ms"]) / 1000.0
        while True:
            page = await self._live_page()
            for s in selectors:
                try:
                    loc = page.locator(s).first
                    if await loc.count() and await loc.is_visible():
                        return loc
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.3)

    async def _live_page(self):
        """返回一个仍然存活的页面；页面被抖音关闭时自动接管或新建。

        注意：浏览器**整个被关掉**是另一回事——这时新建标签页必然失败，
        直接抛 BrowserClosedError（中文），让整轮立刻结束，别去白等/白重试。
        """
        try:
            if self.page is not None and not self.page.is_closed():
                return self.page
        except Exception:
            pass
        await self._adopt_new_page()
        try:
            if self.page is not None and not self.page.is_closed():
                return self.page
        except Exception:
            pass
        try:
            self.page = await self.ctx.new_page()
        except Exception as e:
            if is_browser_closed(e):
                self.ctx = None
                self.page = None
                raise BrowserClosedError(BROWSER_CLOSED_CN)
            raise
        self.page.set_default_timeout(self.browser_cfg["action_timeout_ms"])
        self.log("warn", "页面已全部关闭，已新建标签页")
        return self.page

    async def _adopt_new_page(self):
        """抖音会在新标签页打开会话，甚至关掉旧标签；这里接管存活的最新页面。"""
        try:
            pages = [p for p in self.ctx.pages if not p.is_closed()]
            if not pages:
                return
            cur_dead = self.page is None or self.page.is_closed()
            last = pages[-1]
            if cur_dead:
                self.page = last
                self.page.set_default_timeout(self.browser_cfg["action_timeout_ms"])
                self.log("info", "原页面已关闭，切换到：%s" % (last.url or "about:blank"))
            elif last is not self.page and "douyin.com" in (last.url or ""):
                self.page = last
                self.page.set_default_timeout(self.browser_cfg["action_timeout_ms"])
                self.log("info", "已切换到新打开的标签页：%s" % last.url)
        except Exception:
            pass

    async def _screenshot(self, tag):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            name = "debug_%s_%s.png" % (time.strftime("%Y%m%d_%H%M%S"), tag)
            path = os.path.join(self.log_dir, name)
            page = await self._live_page()
            await page.screenshot(path=path)
            self.log("info", "已保存排障截图：%s" % path)
            return path
        except Exception:
            return None

    async def _body_text(self):
        try:
            page = await self._live_page()
            return await page.evaluate(JS_BODY_TEXT) or ""
        except Exception:
            return ""

    # -------------------------------------------------------------- 生命周期
    def resolve_channel(self):
        """定下用哪个浏览器：auto 自动挑、配的那个没装就回退，全都没有才报错。

        同时把用户数据目录按浏览器分开：Edge 继续用原来的 browser_data（不动现有登录态），
        换成 Chrome 时用 browser_data_chrome —— 两个浏览器的用户目录格式虽然相近，
        但混用有把现有登录态搞坏的风险，宁可让用户换浏览器后重扫一次码。
        """
        want = str(self.browser_cfg.get("channel") or "auto").strip()
        avail = config_detect_browsers()
        channels = [c for c, _l, _p in avail]
        if not channels:
            raise RuntimeError(
                "本机没有找到 Microsoft Edge 或 Google Chrome。\n"
                "Windows 10/11 一般都自带 Edge，出现这个提示通常是：\n"
                "  1) Edge 被卸载了；或 2) 系统是 Windows 10 早期版本、只带旧版（非 Chromium）Edge。\n"
                "解决办法：装一个 Chrome（或重新装 Edge）即可，程序本身不需要装 Python 之类的东西。")
        if want in ("", "auto"):
            channel = channels[0]
            self.log("info", "浏览器：自动选择 %s（%s）"
                     % (config_browser_label(channel), channel))
        elif want in channels:
            channel = want
        else:
            channel = channels[0]
            self.log("warn", "配置的浏览器「%s」本机没装，改用 %s（%s）"
                     % (want, config_browser_label(channel), channel))
        self.channel = channel
        base = self.user_data_dir
        if channel == "msedge":
            self.effective_user_data_dir = base            # 保持老路径，现有登录态照用
        else:
            self.effective_user_data_dir = base + "_" + channel.replace("-", "_")
            self.log("info", "用 %s：登录态单独放在 %s（首次需要在这个浏览器里重新扫码一次）"
                     % (config_browser_label(channel),
                        os.path.basename(self.effective_user_data_dir)))
        return channel

    # -------------------------------------------------------------- 窗口显示
    def _has_saved_login(self):
        """这个用户目录里有没有登录过（Chromium 的 Cookie 库存在即认为登录过）。"""
        try:
            for rel in (os.path.join("Default", "Cookies"), "Cookies",
                        os.path.join("Default", "Network", "Cookies")):
                if os.path.exists(os.path.join(self.effective_user_data_dir, rel)):
                    return True
        except Exception:
            pass
        return False

    async def set_window_state(self, state):
        """最小化 / 还原窗口（走 CDP；headless 没有窗口，直接跳过）。"""
        if getattr(self, "window_mode", "") == "hidden" or self.page is None:
            return False
        try:
            cdp = await self.ctx.new_cdp_session(self.page)
            try:
                info = await cdp.send("Browser.getWindowForTarget")
                await cdp.send("Browser.setWindowBounds",
                               {"windowId": info["windowId"],
                                "bounds": {"windowState": state}})
                if state == "normal":
                    try:
                        await self.page.bring_to_front()
                    except Exception:
                        pass
            finally:
                try:
                    await cdp.detach()
                except Exception:
                    pass
            return True
        except Exception as e:
            self.log("warn", "调整浏览器窗口状态失败（不影响发送）：%s"
                     % friendly_error(e).replace("\n", " "))
            return False

    async def start(self):
        self._pw = await async_playwright().start()
        channel = self.resolve_channel()
        self.user_data_dir = self.effective_user_data_dir
        os.makedirs(self.user_data_dir, exist_ok=True)
        headless = bool(self.browser_cfg["headless"])
        mode = str(self.browser_cfg.get("window_mode") or "").strip().lower()
        if mode not in ("normal", "minimized", "hidden"):
            mode = "hidden" if headless else "minimized"
        if mode == "hidden" and not self._has_saved_login():
            # 从没登录过：隐藏了就没法扫码，先按最小化（窗口在任务栏）跑，登录完下次再隐藏
            self.log("warn", "还没登录过，这次先不隐藏窗口（窗口会最小化到任务栏），登录成功后下次生效")
            mode = "minimized"
        headless = (mode == "hidden")
        self.window_mode = mode
        kwargs = {
            "user_data_dir": self.user_data_dir,
            "channel": channel,
            "headless": headless,
            "locale": "zh-CN",
            # 最小化/被挡住时 Chromium 会节流计时器和渲染，抖音的列表就可能"不渲染"——
            # 这三个开关是官方给自动化用的，关掉节流。
            "args": ["--disable-infobars",
                     "--disable-background-timer-throttling",
                     "--disable-backgrounding-occluded-windows",
                     "--disable-renderer-backgrounding"],
            "ignore_default_args": ["--enable-automation"],
            # 不加 --no-sandbox：否则 Edge 顶部会出现安全警告横幅，还会挤占页面空间
            "chromium_sandbox": True,
        }
        window_size = self.browser_cfg.get("window_size")
        if headless:
            kwargs["viewport"] = {"width": 1440, "height": 900}
        elif isinstance(window_size, (list, tuple)) and len(window_size) == 2:
            # 同时给 viewport，否则 Edge 会沿用上次记住的窗口尺寸
            kwargs["viewport"] = {"width": int(window_size[0]), "height": int(window_size[1])}
            kwargs["args"].append("--window-size=%d,%d" % (int(window_size[0]), int(window_size[1])))
            self.log("info", "浏览器窗口固定为 %dx%d" % (int(window_size[0]), int(window_size[1])))
        else:
            kwargs["viewport"] = None
            if mode == "minimized":
                kwargs["args"].append("--start-minimized")
            else:
                kwargs["args"].append("--start-maximized")

        self.log("info", "启动浏览器（%s = %s），用户数据目录：%s"
                 % (config_browser_label(channel), channel, self.user_data_dir))
        try:
            self.ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            raise RuntimeError(
                "浏览器启动失败（%s）。\n"
                "常见原因：Edge 版本太旧（Windows 10 早期版本自带的旧版 Edge 用不了）、\n"
                "或该浏览器被安全软件拦住。装一个最新版 Chrome 或 Edge 再试即可。\n"
                "原始错误：%s" % (config_browser_label(channel), str(e).split("\n")[0][:200]))
        await self.ctx.add_init_script(STEALTH_JS)
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        self.page.set_default_timeout(self.browser_cfg["action_timeout_ms"])
        if mode == "minimized":
            await self.set_window_state("minimized")
            self.log("info", "浏览器窗口已最小化到任务栏（不会挡屏幕；要看得见就在「设置 → 浏览器窗口」里改）")

    async def close(self):
        try:
            if self.ctx:
                await self.ctx.close()
        except Exception as e:
            self.log("warn", "关闭浏览器上下文时出现（可忽略）：%s" % e)
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self.ctx = None
        self.page = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def ensure_alive(self):
        """确保浏览器上下文与页面可用；不可用则整体重启。"""
        try:
            if self.ctx is not None:
                pages = [p for p in self.ctx.pages if not p.is_closed()]
                if pages:
                    if self.page is None or self.page.is_closed():
                        self.page = pages[-1]
                        self.page.set_default_timeout(self.browser_cfg["action_timeout_ms"])
                    return True
        except Exception:
            pass
        self.log("warn", "浏览器已不可用，正在重新启动…")
        await self.close()
        await self.start()
        return True

    async def _safe_goto(self, url, tries=3):
        """带重试的跳转：页面被关闭时自动换页，仍失败则重启浏览器。

        浏览器整个被关掉时不重试（重试必然还是失败，只会拖时间）——直接抛中文错。
        """
        last = None
        for i in range(tries):
            page = await self._live_page()
            try:
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=self.browser_cfg["page_load_timeout_ms"])
                return page
            except Exception as e:
                if is_browser_closed(e):
                    raise BrowserClosedError(BROWSER_CLOSED_CN)
                last = e
                if is_network_error(e):
                    # 网络类错误：换页没用，等久一点直接重试同一个地址
                    wait = 2.0 * (i + 1)
                    self.log("warn", "打开 %s 失败（第 %d/%d 次，网络问题，等 %.0f 秒再试）：%s"
                             % (url, i + 1, tries, wait, friendly_error(e).replace("\n", " ")))
                    await asyncio.sleep(wait)
                    continue
                self.log("warn", "打开 %s 失败（第 %d/%d 次）：%s"
                         % (url, i + 1, tries, friendly_error(e).replace("\n", " ")))
                await asyncio.sleep(1.5)
                await self._adopt_new_page()
        self.log("warn", "多次打开失败，重启浏览器后重试")
        await self.ensure_alive()
        page = await self._live_page()
        await page.goto(url, wait_until="domcontentloaded",
                        timeout=self.browser_cfg["page_load_timeout_ms"])
        return page

    # -------------------------------------------------------------- 页面导航
    async def _wait_conversation_list(self, timeout_ms=12000):
        """等左侧会话列表真正渲染出来。

        聊天页刚打开时搜索框已经可见、但会话列表还没渲染，
        火花检测/批量发送都依赖这个列表，不等待就会白跑一轮。
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                page = await self._live_page()
                for sel in self.sel("conversation_item"):
                    if await page.locator(sel).count():
                        return True
            except BrowserClosedError:
                raise                      # 浏览器被关掉了，别再空等这十几秒
            except Exception:
                pass
            await asyncio.sleep(0.4)
        self.log("warn", "左侧会话列表在 %.0f 秒内没有渲染出来" % (timeout_ms / 1000.0))
        return False

    async def goto_chat(self, force=False):
        """打开聊天页；已在聊天页且控件可用时不重新加载（避免无谓的整页刷新）。"""
        url = self.browser_cfg["chat_url"]
        if not force:
            try:
                page = await self._live_page()
                if (page.url or "").rstrip("/").endswith("/chat"):
                    if await self._first_visible(self.sel("search_box"), 2500):
                        await self._wait_conversation_list(6000)
                        return
            except BrowserClosedError:
                raise                      # 浏览器被关掉了：别吞掉，交给上层结束本轮
            except Exception:
                pass
        await self._safe_goto(url)
        await asyncio.sleep(3)
        await self._adopt_new_page()
        try:
            if "页面不见啦" in (await self._body_text()):
                self.log("warn", "检测到 404 页面，重试 %s" % url)
                await self._safe_goto(url)
                await asyncio.sleep(3)
                await self._adopt_new_page()
        except Exception:
            pass
        await self._wait_conversation_list()
        await self.widen_left_panel()

    async def widen_left_panel(self):
        """把左侧会话列表拖宽，避免长群名/长昵称被截断。

        抖音这个面板是 Semi Design 的 Resizable：min-width 300px、max-width 500px，
        右侧有 semi-resizable-resizableHandler-right 拖拽手柄。
        这里模拟真实拖拽，抖音自己会记住宽度。
        """
        try:
            info = await self.page.evaluate("""() => {
                const h = document.querySelector('.semi-resizable-resizableHandler-right');
                const box = document.querySelector('.semi-resizable-resizable');
                if (!h || !box) return null;
                const hr = h.getBoundingClientRect();
                const br = box.getBoundingClientRect();
                const st = window.getComputedStyle(box);
                return {
                    hx: Math.round(hr.x + hr.width / 2),
                    hy: Math.round(hr.y + hr.height / 2),
                    cur: Math.round(br.width),
                    min: parseInt(st.minWidth) || 300,
                    max: parseInt(st.maxWidth) || 500
                };
            }""")
            if not info:
                return False

            want = self.browser_cfg.get("left_panel_width")
            target = int(want) if want else info["max"]
            target = max(info["min"], min(target, info["max"]))
            if target <= info["cur"] + 5:
                return True

            await self.page.mouse.move(info["hx"], info["hy"])
            await self.page.mouse.down()
            delta = target - info["cur"]
            steps = 12
            for i in range(1, steps + 1):
                await self.page.mouse.move(info["hx"] + delta * i / steps, info["hy"])
                await asyncio.sleep(0.02)
            await self.page.mouse.up()
            await asyncio.sleep(0.6)

            now = await self.page.evaluate(
                "() => { const e = document.querySelector('.semi-resizable-resizable');"
                " return e ? Math.round(e.getBoundingClientRect().width) : -1; }")
            self.log("info", "左侧会话栏宽度 %d → %s px" % (info["cur"], now))
            return True
        except Exception as e:
            if is_browser_closed(e):
                raise BrowserClosedError(BROWSER_CLOSED_CN)
            self.log("warn", "调整左侧栏宽度失败（不影响发送）：%s" % friendly_error(e))
            return False

    async def is_logged_in(self):
        try:
            cookies = await self.ctx.cookies()
            for c in cookies:
                if c.get("name") in ("sessionid", "sessionid_ss") and c.get("value"):
                    return True
        except Exception:
            pass
        try:
            page = await self._live_page()
            for s in LOGIN_HINTS:
                try:
                    loc = page.locator(s).first
                    if await loc.count() and await loc.is_visible():
                        return False
                except Exception:
                    continue
        except Exception:
            pass
        return True

    async def ensure_logged_in(self, wait_seconds=None):
        """打开聊天页；未登录则等待用户扫码，登录后自动继续。"""
        await self.ensure_alive()
        await self.goto_chat()
        if await self.is_logged_in():
            self.log("info", "登录态有效")
            return True

        wait_seconds = wait_seconds or self.browser_cfg["login_wait_seconds"]
        await self.set_window_state("normal")      # 要扫码，把窗口还给用户
        self.log("info", "检测到尚未登录：请在弹出的浏览器窗口里扫码（最多等待 %d 秒）" % wait_seconds)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            await self._adopt_new_page()
            if await self.is_logged_in():
                self.log("info", "检测到登录成功")
                await asyncio.sleep(1.5)
                return True
        self.log("error", "等待登录超时（%d 秒内未检测到登录成功）" % wait_seconds)
        return False

    # -------------------------------------------------------------- 会话列表（选人用）
    async def list_conversations(self, scroll=True):
        """读取左侧会话列表：好友 + 群聊，带稳定 conv_id / sec_uid / 火花天数。

        返回 [{conv_id, sec_uid, name, days, state, time, hint}]，按界面顺序。
        """
        await self._live_page()
        await self._wait_conversation_list()
        rows = {}
        order = []
        try:
            await self.page.evaluate(JS_SPARK_SCROLL_TOP)
            await asyncio.sleep(0.5)
        except Exception:
            pass
        last_top = None
        for _ in range(40):
            for r in (await self.page.evaluate(JS_LIST_CONVERSATIONS) or []):
                key = r.get("conv_id") or ("name:" + (r.get("name") or ""))
                if key not in rows:
                    rows[key] = r
                    order.append(key)
            if not scroll:
                break
            info = await self.page.evaluate(JS_SPARK_SCROLL)
            if not isinstance(info, list) or len(info) < 2:
                break
            if last_top is not None and info[0] == info[1]:
                break
            last_top = info[1]
            await asyncio.sleep(0.4)
        return [rows[k] for k in order]

    async def fetch_avatars(self, rows, out_dir, force=False):
        """把会话头像缓存成本地图片，返回 {会话键: 文件路径}。

        - 好友头像是 `[class*=Avatar] img`，群头像是 `img.commonConversationIconnoDrag`；
        - 群头像给的是 http:// 地址，https 页面直接 fetch 会被拦，所以先试 https 再退回原地址，
          仍失败就对头像元素截图（PNG）；
        - 已经缓存过的直接复用，所以第二次刷新很快。
        """
        os.makedirs(out_dir, exist_ok=True)
        out = {}
        for r in rows:
            key = r.get("conv_id") or ("name:" + (r.get("name") or ""))
            stem = avatar_stem(key)
            path = None
            if not force:
                for e in IMAGE_EXTS:
                    p = os.path.join(out_dir, stem + e)
                    if os.path.exists(p) and os.path.getsize(p) > 0:
                        path = p
                        break
            if path is None:
                url = (r.get("avatar") or "").strip()
                urls = []
                if url.startswith("http://"):
                    urls.append("https://" + url[len("http://"):])
                if url:
                    urls.append(url)
                data = None
                for u in urls:
                    try:
                        data = await self.page.evaluate(JS_FETCH_BASE64, u)
                    except Exception:
                        data = None
                    if data:
                        break
                if data:
                    try:
                        path = os.path.join(out_dir, stem + image_ext(base64.b64decode(data[:16])))
                        with open(path, "wb") as f:
                            f.write(base64.b64decode(data))
                    except Exception as e:
                        path = None
                        self.log("warn", "头像写入失败（%s）：%s" % (r.get("name"), e))
            if path is None:
                # 兜底：直接对头像元素截图
                try:
                    idx = r.get("index")
                    if idx is not None:
                        el = self.page.locator('[data-e2e="conversation-item"]').nth(int(idx)) \
                            .locator('[class*="Avatar"] img, img[class*="ConversationIcon"], '
                                     '.semi-avatar img').first
                        if await el.count():
                            path = os.path.join(out_dir, stem + ".png")
                            await el.screenshot(path=path)
                except Exception as e:
                    path = None
                    self.log("warn", "头像截图失败（%s）：%s" % (r.get("name"), e))
            if path:
                out[key] = path
        return out

    async def _click_conversation_in_list(self, name=None, conv_id=None):
        """在左侧列表里找到并点开某个会话（好友/群聊都适用，绕开搜索能力的限制）。

        返回 {"row": 行上的名字, "by": "id"|"name"}；找不到返回 None。

        定位用 JS 打标记、点击交给 Playwright 的 locator.click()：
        这个列表会滚动重排，"JS 取坐标 + mouse.click" 会点到别的会话上（实测踩过）。
        """
        # 搜索下拉面板会盖在列表上、把点击吃掉，先把它收起来（实测踩过）
        try:
            await self.clear_search()
        except Exception:
            pass
        try:
            await self.page.evaluate(JS_SPARK_SCROLL_TOP)
            await asyncio.sleep(0.4)
        except Exception:
            pass
        for attempt in range(8):
            try:
                hit = await self.page.evaluate(JS_MARK_CONV,
                                               {"name": name or "", "convId": conv_id or ""})
            except Exception:
                hit = None
            if hit:
                loc = self.page.locator('[data-dsk-pick="1"]').first
                try:
                    if not await loc.is_visible():
                        # 列表被搜索面板/遮罩挡住了：收起来再来一次
                        await self.clear_search()
                        await self._dismiss_overlays()
                        await asyncio.sleep(0.5)
                        if attempt < 5:
                            continue
                        return None
                    await loc.click(timeout=5000)
                    return {"row": (hit or {}).get("row") or "", "by": (hit or {}).get("by") or "name"}
                except Exception as e:
                    self.log("warn", "点击会话行失败：%s" % str(e).split("\n")[0])
                    return None
            info = await self.page.evaluate(JS_SPARK_SCROLL)
            if not isinstance(info, list) or len(info) < 2 or info[0] == info[1]:
                break
            await asyncio.sleep(0.4)
        return None

    async def search_contacts(self, keyword):
        """在抖音搜索框里搜人，返回候选（列表里没有的人走这条路）。只读，不发送。"""
        await self._live_page()
        await self._dismiss_overlays()
        box = await self._first_visible(self.sel("search_box"), 8000)
        if box is None:
            raise RuntimeError("找不到会话搜索框")
        await box.click()
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Delete")
        try:
            await box.fill(keyword)
        except Exception:
            await box.fill(keyword, force=True)
        await asyncio.sleep(2.0)
        return await self.page.evaluate(JS_SEARCH_RESULTS) or []

    async def _conversation_list_visible(self):
        try:
            return bool(await self.page.locator(
                '[data-e2e="conversation-item"]:visible, '
                '[class*="conversationConversationItemwrapper"]:visible').count())
        except Exception:
            return False

    async def clear_search(self):
        """收起搜索面板，把左侧恢复成「会话列表」视图。

        搜索框一旦被点开，左侧就整个变成搜索面板（会话项可见数 = 0），
        Esc、点别处都不管用；面板里的「取消」（div.searchSearchInputsearch_cancel）
        才是正确出口——实测空关键词、有结果、搜不到 三种状态下点它都能立刻恢复列表。
        实在点不到才退回"重新加载聊天页"（慢，但一定能恢复）。
        """
        try:
            if await self._conversation_list_visible():
                return True
            for sel in self.sel("search_cancel"):
                try:
                    loc = self.page.locator(sel).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(timeout=3000)
                        await asyncio.sleep(0.8)
                        if await self._conversation_list_visible():
                            self.log("info", "已点「取消」收起搜索面板，会话列表恢复")
                            return True
                except Exception:
                    continue
            try:
                await self.page.get_by_text("取消", exact=True).first.click(timeout=3000)
                await asyncio.sleep(0.8)
                if await self._conversation_list_visible():
                    self.log("info", "已点「取消」收起搜索面板，会话列表恢复")
                    return True
            except Exception:
                pass
            self.log("warn", "「取消」没能收起搜索面板，改为重新加载聊天页…")
            await self._safe_goto(self.browser_cfg["chat_url"])
            await asyncio.sleep(2.5)
            await self._adopt_new_page()
            await self._wait_conversation_list()
            return await self._conversation_list_visible()
        except Exception as e:
            self.log("warn", "恢复会话列表失败：%s" % e)
            return False

    async def open_target(self, target):
        """打开目标会话：**列表直点优先**（稳定 ID → 精确昵称），列表里没有才用搜索兜底。

        返回实际走的路径："list" / "search"。
        每次成功打开都会把 {"name","by","title"} 记到 self.last_opened 上，
        供上层在昵称被改过时自动纠正配置。
        """
        name = (target.get("name") or "").strip()
        conv_id = (target.get("conv_id") or "").strip()
        self.last_opened = None
        await self._dismiss_overlays()
        try:
            hit = await self._click_conversation_in_list(name=name, conv_id=conv_id or None)
            if hit:
                await asyncio.sleep(1.8)
                await self._adopt_new_page()
                if await self._first_visible(self.sel("message_input"), 8000):
                    title = await self.chat_title()
                    self.last_opened = {"name": name, "by": hit.get("by"),
                                        "title": title, "row": hit.get("row") or ""}
                    if hit.get("by") == "id":
                        # 会话 ID 是唯一的：按 ID 打开就是对的人，即使昵称已经改过
                        if title and not self._name_match(name, title):
                            self.log("warn", "「%s」的昵称似乎已改为「%s」（按会话 ID 打开的，仍然是对的）"
                                     % (name, title))
                        else:
                            self.log("info", "已从左侧列表打开「%s」（按会话 ID 匹配）" % name)
                        return "list"
                    await self._assert_session(name)
                    self.log("info", "已从左侧列表打开「%s」（按昵称匹配）" % name)
                    return "list"
                self.log("warn", "点击了列表里的「%s」，但输入框没出现" % name)
        except Exception as e:
            self.log("warn", "列表直点「%s」失败，改用搜索：%s" % (name, e))
        await self.open_session(name)
        self.last_opened = {"name": name, "by": "search", "title": await self.chat_title(), "row": ""}
        return "search"

    # -------------------------------------------------------------- 会话
    async def chat_title(self):
        """当前打开会话的标题（取不到返回空字符串）。"""
        try:
            for s in self.sel("chat_title"):
                loc = self.page.locator(s).first
                if await loc.count() and await loc.is_visible():
                    t = (await loc.inner_text() or "").strip().split("\n")[0].strip()
                    if t:
                        return t
        except Exception:
            pass
        return ""

    @staticmethod
    def _name_match(want, got):
        """昵称与页面标题是否算同一个会话（宽松比较，容忍空格/群人数后缀）。"""
        def norm(s):
            s = re.sub(r"[（(]\s*\d+\s*[)）]", "", s or "")
            return re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", s).lower()
        a, b = norm(want), norm(got)
        if not a or not b:
            return True          # 标题没读到就不判断，交给输入框校验
        return a == b or a in b or b in a

    async def _assert_session(self, name):
        """确认当前会话确实是目标对象——发错人比发不出去严重得多。"""
        title = await self.chat_title()
        if title and not self._name_match(name, title):
            await self._screenshot("wrong_session_" + _safe(name))
            raise RuntimeError("当前打开的会话是「%s」，与目标「%s」不一致，已中止（避免发错人）"
                               % (title, name))
        return title

    async def open_session(self, name):
        """在聊天页搜索昵称并打开对应会话。"""
        await self._live_page()
        await self._dismiss_overlays()
        box = await self._first_visible(self.sel("search_box"), 8000)
        if box is None:
            raise RuntimeError("找不到会话搜索框，可在 config.json 的 selectors.search_box 中补充选择器")

        await box.click()
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Delete")
        try:
            await box.fill(name)
        except Exception:
            await box.fill(name, force=True)
        await asyncio.sleep(1.5)

        mismatch = None
        try:
            res = await self.page.evaluate(JS_LOCATE_RESULT, name)
            if isinstance(res, dict) and res.get("ok"):
                pages_before = len(self.ctx.pages)
                await self.page.mouse.click(res["x"], res["y"])
                await asyncio.sleep(2.0)
                if len(self.ctx.pages) > pages_before:
                    await self._adopt_new_page()
                if await self._first_visible(self.sel("message_input"), 10000):
                    title = None
                    try:
                        title = await self._assert_session(name)
                    except RuntimeError as e:
                        mismatch = e
                    if mismatch is None:
                        self.log("info", "已点开「%s」会话（via=%s，标题=%s）"
                                 % (name, res.get("via"), title or "?"))
                        return True
                else:
                    self.log("warn", "已点击「发消息」（via=%s）但输入框未出现" % res.get("via"))
            elif isinstance(res, dict):
                self.log("warn", "搜索结果未定位到：%s" % res.get("reason"))
        except Exception as e:
            self.log("warn", "点击搜索结果异常：%s" % e)

        if mismatch is None:
            for s in self.sel("session_item"):
                try:
                    items = self.page.locator(s)
                    count = await items.count()
                except Exception:
                    continue
                for i in range(min(count, 8)):
                    it = items.nth(i)
                    try:
                        if not await it.is_visible():
                            continue
                        txt = await it.inner_text()
                        if name in txt:
                            await it.click()
                            await asyncio.sleep(1.5)
                            await self._adopt_new_page()
                            if await self._first_visible(self.sel("message_input"), 10000):
                                title = await self._assert_session(name)
                                self.log("info", "已打开会话「%s」（标题=%s）" % (name, title or "?"))
                                return True
                    except Exception:
                        continue

        if mismatch is not None:
            raise mismatch
        await self._screenshot("session_" + _safe(name))
        raise RuntimeError("搜索结果中找不到「%s」，请核对昵称是否与抖音聊天列表完全一致" % name)


    # -------------------------------------------------------------- 输入与发送
    async def _editor_text(self):
        try:
            return await self.page.evaluate(JS_EDITOR_TEXT) or ""
        except Exception:
            return ""

    async def _find_input_box(self):
        box = await self._first_visible(self.sel("message_input"), 8000)
        if box is None:
            await self._screenshot("no_input")
            raise RuntimeError("找不到消息输入框（contenteditable/textarea）")
        return box

    async def type_message(self, text):
        box = await self._find_input_box()
        await box.click()
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Delete")
        await asyncio.sleep(0.2)

        if self.sched.get("human_like_typing"):
            lo, hi = self.sched["type_char_delay_ms"]
            for ch in text:
                await self.page.keyboard.type(ch)
                await asyncio.sleep(random.uniform(lo, hi) / 1000.0)
            if not await self._editor_text():
                self.log("warn", "逐字输入未生效，改用整句插入")
                await box.fill(text)
        else:
            await box.fill(text)
        return box

    async def _click_send_button(self):
        icons = await self.page.evaluate(JS_FIND_ICONS)
        if icons:
            await self.page.mouse.click(icons[-1]["x"], icons[-1]["y"])
            return True
        for s in ('button:has-text("发送")', '[class*="send"]'):
            try:
                loc = self.page.locator(s).last
                if await loc.count() and await loc.is_visible():
                    await loc.click()
                    return True
            except Exception:
                continue
        return False

    # ----- 发送结果确认：对比聊天记录底部那条消息，确认"真的发出去了" -----
    async def _last_bubble(self):
        try:
            return await self.page.evaluate(JS_LAST_BUBBLE)
        except Exception:
            return None

    @staticmethod
    def _bubble_sig(b):
        if not b:
            return None
        return (b.get("count"), bool(b.get("me")), b.get("text") or "", b.get("img") or "")

    async def _wait_new_bubble(self, before, want_text=None, timeout_ms=10000):
        """等聊天记录底部出现一条新的、由我发出的消息。

        want_text 给出时要求文本命中；发的是表情/图片时用 img 命中。
        返回命中的气泡信息，超时返回 None。
        """
        want_text = (want_text or "").strip()
        before_sig = self._bubble_sig(before)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            cur = await self._last_bubble()
            if cur and self._bubble_sig(cur) != before_sig and cur.get("me"):
                if want_text:
                    if want_text[:12] in (cur.get("text") or ""):
                        return cur
                else:
                    # 表情/图片没有文本，底部出现"我发出的新气泡"本身就是证据
                    return cur
            await asyncio.sleep(0.4)
        return None

    async def _wait_send_ready(self, timeout_ms=6000):
        """等"发送按钮已激活"（抖音在有内容时会把发送图标标红）。"""
        deadline = time.monotonic() + timeout_ms / 1000.0
        last = None
        while time.monotonic() < deadline:
            try:
                last = await self.page.evaluate(JS_SEND_READY)
            except Exception:
                last = None
            if last and last.get("ready"):
                return last
            await asyncio.sleep(0.3)
        return last

    async def _clear_editor(self):
        """清空输入框（演练模式用，避免留下半截草稿）。"""
        try:
            box = await self._find_input_box()
            await box.click()
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Delete")
            await asyncio.sleep(0.3)
        except Exception:
            pass
        left = await self._editor_text()
        if left:
            self.log("warn", "演练后输入框没清干净：%r" % left[:20])
        return not left

    async def _rehearse(self, what="内容"):
        """演练模式：只输入、不发送。

        用抖音自己的"发送按钮变红"来确认这一步在真实发送时确实会生效，
        然后把草稿清掉——全程不产生任何真实消息。
        """
        info = await self._wait_send_ready(6000)
        ok = bool(info and info.get("ready"))
        if info is None or not info.get("found"):
            self.log("warn", "演练：找不到发送按钮，无法确认可发送状态")
        elif ok:
            self.log("info", "演练通过：%s已就绪，发送按钮已变红（本次不发送）" % what)
        else:
            self.log("warn", "演练未通过：%s已输入，但发送按钮仍是未激活状态（cls=%s）"
                     % (what, info.get("cls")))
        await self._clear_editor()
        return ok

    async def send_text(self, text):
        """输入并发送文字，然后用聊天记录确认真的发出去了。"""
        box = await self.type_message(text)
        if self.sched.get("dry_run"):
            return await self._rehearse("文字「%s」" % text[:20])

        lo, hi = self.sched["pause_before_send_ms"]
        await asyncio.sleep(random.uniform(lo, hi) / 1000.0)

        before = await self._last_bubble()
        key = ((self.cfg.get("selectors") or {}).get("send_key")) or "Enter"
        await self.page.keyboard.press(key)
        await asyncio.sleep(0.8)

        if await self._editor_text():
            self.log("warn", "回车未发出，改用发送按钮")
            await self._click_send_button()
            await asyncio.sleep(0.8)
        if await self._editor_text():
            await self._screenshot("send_failed")
            raise RuntimeError("文字未发出（输入框仍有内容）")

        hit = await self._wait_new_bubble(before, want_text=text)
        if hit:
            self.log("info", "已确认文字送达：「%s」" % hit.get("text"))
        else:
            # 输入框已清空说明大概率发出去了（虚拟列表可能导致读不到气泡）
            self.log("warn", "输入框已清空，但未能在聊天记录里确认这条文字（可能被虚拟滚动隐藏）")
        return True

    # -------------------------------------------------------------- 表情包
    async def _sticker_panel_visible(self):
        for s in self.sel("sticker_panel"):
            try:
                loc = self.page.locator(s).first
                if await loc.count() and await loc.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _wait_panel(self, tries=16):
        for _ in range(tries):
            if await self._sticker_panel_visible():
                return True
            await asyncio.sleep(0.25)
        return False

    async def open_sticker_panel(self):
        """打开表情弹层（须已进入某个会话）。

        只用几何定位：class/文本选择器会误命中聊天记录里的表情图片
        （MessageItemEmoji...），点到那里不会弹出面板。
        """
        if await self._sticker_panel_visible():
            return True
        try:
            icons = await self.page.evaluate(JS_FIND_ICONS) or []
        except Exception:
            icons = []
        for ic in icons[:3]:
            try:
                await self.page.mouse.click(ic["x"], ic["y"])
                if await self._wait_panel():
                    self.log("info", "已打开表情面板")
                    return True
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception:
                continue
        if not icons:
            raise RuntimeError("找不到输入栏的表情入口（请确认已打开某个会话）")
        raise RuntimeError("点击输入栏图标后表情面板没有弹出")

    async def _overlay_present(self):
        """是否存在挡住点击的弹层遮罩。"""
        try:
            return bool(await self.page.evaluate("""() => {
                const m = document.querySelector('.semi-modal-wrap, .semi-modal-mask');
                if (!m) return false;
                const r = m.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }"""))
        except Exception:
            return False

    async def close_sticker_panel(self):
        """关闭表情面板。"""
        await self._dismiss_overlays()

    async def _dismiss_overlays(self):
        """清除可能挡住点击的残留弹层。

        表情弹层（.semi-modal-wrap）有时按 Esc 也关不掉，会拦截后续所有真实点击；
        这种情况下把它的 pointer-events 设为 none——只是让它不再截获点击，不改动页面结构。
        """
        for _ in range(3):
            if not await self._overlay_present() and not await self._sticker_panel_visible():
                return
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                break
            await asyncio.sleep(0.35)
        try:
            n = await self.page.evaluate("""() => {
                let n = 0;
                document.querySelectorAll('.semi-modal-wrap, .semi-modal-mask').forEach(e => {
                    e.style.pointerEvents = 'none';
                    n++;
                });
                return n;
            }""")
            if n:
                self.log("info", "已禁用 %d 个残留遮罩的点击拦截" % n)
                await asyncio.sleep(0.2)
        except Exception:
            pass

    @staticmethod
    def _sticker_id_from_url(url):
        """从表情图片 URL 提取稳定标识：im-resource/ 与 ? 之间的部分。"""
        tail = url.split("im-resource/", 1)[1] if "im-resource/" in url else url.rsplit("/", 1)[-1]
        tail = tail.split("?", 1)[0]
        return tail or url

    async def _enter_any_session(self):
        """进入任意一个会话——输入栏和表情面板只在会话内才存在。"""
        if await self._first_visible(self.sel("message_input"), 2500):
            return True
        for s in self.sel("conversation_item"):
            try:
                items = self.page.locator(s)
                count = await items.count()
            except Exception:
                continue
            for i in range(min(count, 5)):
                it = items.nth(i)
                try:
                    if not await it.is_visible():
                        continue
                    await it.click()
                    await asyncio.sleep(1.8)
                    await self._adopt_new_page()
                    if await self._first_visible(self.sel("message_input"), 8000):
                        self.log("info", "已进入一个会话（用于读取表情）")
                        return True
                except Exception:
                    continue
        return False

    async def send_image(self, image_path):
        """通过输入栏的图片上传入口发送表情包图片（jpg / png / gif）。"""
        path = _abs(image_path)
        if not os.path.exists(path):
            raise RuntimeError("图片文件不存在：%s" % path)
        page = await self._live_page()
        loc = page.locator('input[type="file"]')
        if not await loc.count():
            raise RuntimeError("找不到图片上传入口（请确认已打开某个会话）")
        if self.sched.get("dry_run"):
            self.log("info", "演练通过：已找到图片上传入口（%s），本次不上传" % os.path.basename(path))
            return True
        await loc.first.set_input_files(path)
        self.log("info", "已选择图片 %s，等待上传…" % os.path.basename(path))
        await asyncio.sleep(2.5)
        lo, hi = self.sched["pause_before_send_ms"]
        await asyncio.sleep(random.uniform(lo, hi) / 1000.0)
        # 抖音选完图会弹「发送给 xxx」预览框：得点框里的「发送」，按回车不生效
        if not await self._click_dialog_send():
            self.log("warn", "没找到图片预览框里的「发送」按钮，改按回车试试")
            await self.page.keyboard.press("Enter")
        await asyncio.sleep(1.5)
        return True

    async def _click_dialog_send(self):
        """在抖音的图片预览框里点「发送」；找到就点，8 秒内没等到返回 False。"""
        js = """
        () => {
          const hits = [];
          for (const el of document.querySelectorAll('*')) {
            if (el.children.length) continue;
            if ((el.textContent || '').trim() !== '发送') continue;
            const r = el.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) continue;
            hits.push([r.x + r.width / 2, r.y + r.height / 2]);
          }
          if (!hits.length) return null;
          const last = hits[hits.length - 1];
          return {n: hits.length, x: last[0], y: last[1]};
        }
        """
        for _ in range(8):
            pos = await self.page.evaluate(js)
            if pos:
                await self.page.mouse.click(pos["x"], pos["y"])
                self.log("info", "已点图片预览框里的「发送」（候选 %d 个）" % pos["n"])
                return True
            await asyncio.sleep(1.0)
        return False

    async def collect_stickers(self, stickers_dir):
        """读取当前账号的「超级表情」，并把缩略图截到 stickers/。

        超级表情 = 表情面板里的第一个标签页（续火花/比心/V我50… 那一页），
        打开面板时默认就在这一页。普通表情、收藏表情以后再说。
        """
        await self._live_page()
        if not await self._enter_any_session():
            raise RuntimeError("无法进入任何会话；表情面板只在会话内可用")
        await self.open_sticker_panel()
        try:
            return await self._collect_stickers_inner(stickers_dir)
        finally:
            await self.close_sticker_panel()

    async def _collect_stickers_inner(self, stickers_dir):
        items = await self.page.evaluate(JS_STICKER_LIST) or []
        try:
            diag = await self.page.evaluate(JS_STICKER_DIAG)
        except Exception as e:
            diag = {"diag_error": str(e)}
        labels = (diag.get("text") or "").strip()
        if items:
            # 把面板文字打出来，万一面板默认停在别的标签页也能一眼看出来
            self.log("info", "超级表情面板：读到 %d 个%s"
                     % (len(items), ("，内容：" + labels[:70]) if labels else ""))
        else:
            self.log("warn", "表情面板里没有读到超级表情，诊断：%s" % diag)
        os.makedirs(stickers_dir, exist_ok=True)
        out = []
        seen = set()
        for it in items:
            src = it.get("src") or ""
            sid = self._sticker_id_from_url(src)
            if not sid or sid in seen:
                continue
            seen.add(sid)
            thumb_rel = "stickers/%s.png" % sid
            try:
                el = self.page.locator('img[src*="%s"]' % sid).first
                await el.scroll_into_view_if_needed(timeout=3000)
                await el.screenshot(path=os.path.join(stickers_dir, sid + ".png"))
            except Exception as e:
                self.log("warn", "表情 %s 缩略图截取失败（不影响使用）：%s" % (sid, e))
            out.append({"id": sid, "url": src, "thumb": thumb_rel})
        return out

    async def send_sticker(self, sticker_id):
        """在已打开的会话中发送一个超级表情。

        用真实鼠标点击（DOM 的 el.click() 不是可信事件，抖音可能不响应），
        点完再用聊天记录底部气泡确认表情确实发出去了。
        """
        before = await self._last_bubble()
        await self.open_sticker_panel()
        try:
            pos = await self.page.evaluate(JS_STICKER_POS, sticker_id)
        except Exception:
            pos = None
        if not pos:
            await self._screenshot("sticker_missing")
            raise RuntimeError("超级表情「%s」在表情面板中找不到，请重新「同步超级表情」" % sticker_id)
        if self.sched.get("dry_run"):
            self.log("info", "演练通过：已定位到超级表情（x=%s, y=%s, %s），本次不点击"
                     % (pos.get("x"), pos.get("y"), pos.get("cls")))
            await self._dismiss_overlays()
            return True
        self.log("info", "点击超级表情（x=%s, y=%s, %s）" % (pos.get("x"), pos.get("y"), pos.get("cls")))
        await self.page.mouse.click(pos["x"], pos["y"])
        await asyncio.sleep(1.0)

        hit = await self._wait_new_bubble(before, want_text=None, timeout_ms=8000)
        if hit:
            self.log("info", "已确认表情送达（气泡=%s）" % (hit.get("text") or hit.get("img", "")[:60]))
        else:
            self.log("warn", "点击表情后未能在聊天记录里确认这条表情")
        await self._dismiss_overlays()
        return bool(hit)

    # -------------------------------------------------------------- 业务动作
    async def send_to_target(self, target, ensure_chat=True, skip_if_sent_today=False):
        """完整发送一次（文字 + 可选表情包）。

        ensure_chat=False 用于「一轮多目标」：外层已打开聊天页，
        这里直接搜索并发送，避免每个目标都整页刷新一次。
        skip_if_sent_today=True：打开会话后先看最后一条，今天已经发过就返回 "skipped"
        （正常发送返回 True）。判据是"我发的最后一条是不是今天的"，比看火苗图标准。
        """
        text = (target.get("text") or "").strip()
        sticker_id = target.get("sticker_id")
        image_path = target.get("image_path")
        if not text and not sticker_id and not image_path:
            raise RuntimeError("文字、超级表情、图片至少要有一样")

        name = target["name"]
        dry = bool(self.sched.get("dry_run"))
        self.log("info", "%s开始处理「%s」，内容：%s" % (
            "【演练模式】" if dry else "",
            name, text or ("[超级表情]" if sticker_id else ("[图片]" if image_path else "（空）"))))

        if ensure_chat:
            await self.goto_chat()
        await self.open_target(target)
        if skip_if_sent_today:
            # 今天已经给他发过就别再发一遍（手动点按钮 / 到点 / 开机补一轮都走这里）
            last = {}
            try:
                last = await self.page.evaluate(JS_LAST_MSG) or {}
            except Exception as e:
                self.log("warn", "读取「%s」最后一条消息失败（这次照常发）：%s" % (name, e))
            if self._already_sent_today(last):
                self.log("info", "「%s」今天已经发过了（最后一条是我 %s 发的），这次跳过，不重复发"
                         % (name, (last.get("time") or "").strip() or "刚才"))
                return "skipped"
        if text:
            await self.send_text(text)
        if sticker_id:
            await self.send_sticker(sticker_id)
        if image_path:
            await self.send_image(image_path)
        self.log("info", "已向「%s」%s" % (name, "演练完成（未发送任何消息）" if dry else "发送完成"))
        return True

    # -------------------------------------------------------------- 火花检测
    @staticmethod
    def _classify_spark(state):
        """火花图标 -> 业务状态。

        实测（2026-09-14）图标地址形如：
            flame_icon/<皮肤>/<颜色>_<皮肤>.png
            flame_icon/normal/gray_normal.png     ← 普通皮肤，灰火（今天没点亮）
            flame_icon/normal/normal_normal.png   ← 普通皮肤，已点亮
            flame_icon/bosom/gray_bosom.png       ← 另一种皮肤，同样是灰火
        **皮肤（normal / bosom / …）跟是否点亮无关，文件名里的颜色段才是状态。**
        以前把皮肤写死在正则里（flame_icon/normal/），皮肤一变就抓不到，
        还 fallback 成 'normal'，于是把"今天没点亮"错报成"今日已续"。
        认不出来时必须返回 unknown，绝不能默认成已点亮。
        """
        s = (state or "").lower()
        if not s or s == "none":
            return "none"
        if s == "unknown":
            return "unknown"
        fname = s.rsplit("/", 1)[-1].split("?")[0]
        color = fname.split("_", 1)[0].replace(".png", "")
        if color in ("gray", "grey"):
            return "waiting"
        if color in ("normal", "blue"):
            return "ok"
        # 没有下划线的老格式：退回"整段里找颜色词"
        if "gray" in s or "grey" in s:
            return "waiting"
        if "normal" in s or "blue" in s:
            return "ok"
        return "unknown"

    @staticmethod
    def _lastmsg_time_is_before_today(text):
        """判断最后一条消息的时间文案是否"明确早于今天"。"""
        t = (text or "").strip()
        if not t:
            return False
        if "昨天" in t or "前天" in t:
            return True
        if re.fullmatch(r"周[一二三四五六日天]", t):
            return True
        if re.fullmatch(r"\d{1,2}/\d{1,2}", t):
            return True
        if re.match(r"\d{4}", t):
            return True
        return False

    async def _spark_rows(self):
        """会话列表里的火花行，按稳定会话 ID 索引（没有 ID 的老页面退回按名字）。"""
        rows = {}
        for r in (await self.list_conversations() or []):
            key = r.get("conv_id") or ("name:" + (r.get("name") or ""))
            rows.setdefault(key, r)
        return rows

    @staticmethod
    def _row_key(target):
        """目标在会话列表里的索引键：优先稳定 ID，其次名字。"""
        if isinstance(target, dict):
            cid = (target.get("conv_id") or "").strip()
            if cid:
                return cid
            return "name:" + (target.get("name") or "").strip()
        return "name:" + str(target)

    async def check_sparks(self, targets, shallow=False):
        """只读取、不发送，返回 [{name,status,days,state,last}]。

        targets 可以是名字列表，也可以是目标 dict 列表（有 conv_id 时用它精确匹配）。
        shallow=True 时只看会话列表里的火苗图标，**绝不进会话**（不会把对方的消息
        标成已读），列表里找不到的按 "unknown" 处理 —— 开机前扫一眼用它。
        """
        items = []
        for t in targets:
            if isinstance(t, dict):
                items.append({"name": (t.get("name") or "").strip(),
                              "conv_id": (t.get("conv_id") or "").strip() or None})
            else:
                items.append({"name": str(t).strip(), "conv_id": None})

        results = []
        await self.goto_chat()
        if not await self.is_logged_in():
            self.log("warn", "未检测到登录态，本次检测跳过")
            return [{"name": it["name"], "status": "notlogin", "days": "", "state": "none", "last": ""}
                    for it in items]

        try:
            rows = await self._spark_rows()
        except Exception as e:
            self.log("warn", "读取会话列表火花失败：%s" % e)
            rows = {}

        for it in items:
            name = it["name"]
            row = rows.get(self._row_key(it))
            if row is None and it["conv_id"]:
                # 老配置可能有 ID 但对不上（例如换了账号），退回按名字找
                row = None
                for r in rows.values():
                    if r.get("name") == name:
                        row = r
                        break
            if row is None:
                if shallow:
                    results.append({"name": name, "status": "unknown", "days": "",
                                    "state": "none", "last": ""})
                    continue
                # 会话列表没有：打开会话直接读头部
                try:
                    await self.open_target(it)
                    head = await self.page.evaluate(JS_SPARK_HEADER)
                    last = await self.page.evaluate(JS_LAST_MSG)
                    status = self._classify_spark(head.get("state"))
                    status = await self._refine(status, last)
                    results.append({"name": name, "status": status, "days": head.get("days", ""),
                                    "state": head.get("state", "none"), "last": last.get("text", "")})
                    continue
                except Exception as e:
                    self.log("warn", "读取「%s」火花失败：%s" % (name, e))
                    results.append({"name": name, "status": "missing", "days": "", "state": "none", "last": ""})
                    continue

            status = self._classify_spark(row.get("state"))
            last = {}
            if status == "waiting" and not shallow:
                # 灰火时进会话确认最后一条消息方向
                try:
                    await self.open_target(it)
                    last = await self.page.evaluate(JS_LAST_MSG) or {}
                except Exception as e:
                    self.log("warn", "确认「%s」最后消息失败，按待续处理：%s" % (name, e))
                status = await self._refine(status, last)
            results.append({"name": name, "status": status, "days": row.get("days", ""),
                            "state": row.get("state", "none"), "last": last.get("text", ""),
                            "conv_id": row.get("conv_id", "")})
        return results

    def _already_sent_today(self, last):
        """最后一条消息是我今天发的 -> 今天已经续过了（不必再发）。"""
        try:
            if not last:
                return False
            return bool(last.get("me")) and not self._lastmsg_time_is_before_today(last.get("time", ""))
        except Exception:
            return False

    async def _refine(self, status, last):
        """灰火（今日尚未互发）时进一步区分：
        - 最后一条是我发的、且不是"昨天及更早" -> sent（我已发，等对方续）
        - 否则 -> waiting（轮到我续 / 今天双方都还没发）
        """
        if status != "waiting":
            return status
        return "sent" if self._already_sent_today(last) else "waiting"

    # -------------------------------------------------------------- 页面校准
    async def calibrate(self, sample_name=None):
        """输出选择器命中情况与关键 DOM 结构，用于排查抖音改版。"""
        r = ["时间：" + time.strftime("%Y-%m-%d %H:%M:%S")]
        try:
            await self.goto_chat()
        except Exception as e:
            r.append("打开聊天页失败：%s" % e)
            return "\n".join(r)

        r.append("当前 URL：%s" % self.page.url)
        try:
            r.append("登录态：%s" % ("已登录" if await self.is_logged_in() else "未登录"))
        except Exception:
            pass

        for key in ("search_box", "message_input", "conversation_item", "conversation_title",
                    "streak", "sticker_panel"):
            r.append("")
            r.append("[%s]" % key)
            for s in self.sel(key):
                try:
                    loc = self.page.locator(s).first
                    n = await loc.count()
                    vis = await loc.is_visible() if n else False
                    r.append("  %-58s count=%s visible=%s" % (s, n, vis))
                except Exception as e:
                    r.append("  %-58s ERROR %s" % (s, e))

        try:
            classes = await self.page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('[class]').forEach(e => {
                    const c = String(e.className || '');
                    if (c && /conversation|Conversation|Streak|streak|Session|session|message|Message|Search|search/i.test(c)) {
                        c.split(/\\s+/).forEach(x => { if (x && out.indexOf(x) < 0) out.push(x); });
                    }
                });
                return out.slice(0, 60);
            }""")
            r.append("")
            r.append("[页面上出现的相关 class]")
            r.append("  " + "\n  ".join(classes or ["(无)"]))
        except Exception as e:
            r.append("读取 class 失败：%s" % e)

        if sample_name:
            r.append("")
            r.append("[搜索「%s」的诊断]" % sample_name)
            try:
                box = await self._first_visible(self.sel("search_box"), 8000)
                if box is None:
                    r.append("  找不到搜索框")
                else:
                    await box.click()
                    await self.page.keyboard.press("Control+A")
                    await self.page.keyboard.press("Delete")
                    await box.fill(sample_name)
                    await asyncio.sleep(2.5)
                    diag = await self.page.evaluate("""(name) => {
                        const all = Array.from(document.querySelectorAll('*'));
                        const txt = (e) => (e.innerText || '').trim();
                        const cls = (e) => String(e.className || '').trim().replace(/\\s+/g, '.');
                        const out = {sendButtons: [], nameNodes: []};
                        all.forEach(e => {
                            if (txt(e) === '发消息') {
                                const chain = [];
                                let n = e;
                                for (let i = 0; i < 8 && n; i++) {
                                    chain.push(n.tagName + (cls(n) ? '.' + cls(n) : ''));
                                    n = n.parentElement;
                                }
                                out.sendButtons.push(chain);
                            }
                        });
                        all.forEach(e => {
                            const t = txt(e);
                            if (e.children.length === 0 && t && t.length < name.length + 12 && t.indexOf(name) >= 0) {
                                out.nameNodes.push(e.tagName + (cls(e) ? '.' + cls(e) : '') + ' :: ' + t.slice(0, 40));
                            }
                        });
                        return out;
                    }""", sample_name)
                    r.append("  「发消息」按钮祖先链（%d 个）：" % len(diag.get("sendButtons") or []))
                    for chain in (diag.get("sendButtons") or [])[:3]:
                        r.append("    " + " < ".join(chain[:6]))
                    r.append("  含昵称的叶子节点（%d 个）：" % len(diag.get("nameNodes") or []))
                    for n in (diag.get("nameNodes") or [])[:5]:
                        r.append("    " + n)
                    try:
                        shot = os.path.join(self.log_dir or ".", "calibrate_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
                        await self.page.screenshot(path=shot)
                        r.append("  截图：" + shot)
                    except Exception:
                        pass
            except Exception as e:
                r.append("  搜索诊断失败：%s" % e)

        r.append("")
        r.append("========== 新增依赖自检（页面改版时重点看这一段）==========")
        try:
            r.extend(await self._calibrate_deps(sample_name))
        except Exception as e:
            r.append("  自检失败：%s" % e)

        return "\n".join(r)

    async def _calibrate_deps(self, sample_name=None):
        """校准后半段：把"后来新增的依赖"逐项验证一遍，改版时一眼看出坏在哪。"""
        out = []

        def mark(ok, label, extra=""):
            out.append("  [%s] %-46s %s" % ("OK  " if ok else "差！", label, extra))

        # 1) 会话项的选择器钩子
        try:
            n_new = await self.page.locator('[data-e2e="conversation-item"]').count()
        except Exception as e:
            n_new = -1
            out.append("      data-e2e 查询异常：%s" % e)
        try:
            n_old = await self.page.locator('[class*="conversationConversationItemwrapper"]').count()
        except Exception:
            n_old = -1
        mark(n_new > 0, '会话项 [data-e2e="conversation-item"]', "命中 %s 个" % n_new)
        mark(n_old > 0, "会话项旧 class 兜底", "命中 %s 个" % n_old)

        # 2) 会话列表能读到什么（ID / sec_uid / 头像）
        rows = []
        try:
            rows = await self.list_conversations()
            mark(bool(rows), "list_conversations() 读取会话列表", "读到 %d 条" % len(rows))
        except Exception as e:
            mark(False, "list_conversations() 读取会话列表", "异常：%s" % e)
        if rows:
            ids = [r for r in rows if r.get("conv_id")]
            secs = [r for r in rows if r.get("sec_uid")]
            avs = [r for r in rows if r.get("avatar")]
            mark(len(ids) == len(rows), "稳定会话 ID（React virtualItem.id）",
                 "%d/%d 条读到" % (len(ids), len(rows)))
            mark(bool(secs), "好友 sec_uid（conversation.toParticipantSecUserId）",
                 "%d/%d 条读到（群聊本来就没有，属正常）" % (len(secs), len(rows)))
            mark(bool(avs), "头像 URL", "%d/%d 条读到" % (len(avs), len(rows)))
            if ids:
                out.append("      例：%s -> %s" % (ids[0].get("name"), ids[0].get("conv_id")))
            out.append("      前 5 行的火花图标（状态判定全靠它，改版时重点看这里）：")
            for r in rows[:5]:
                out.append("        %-28s state=%-22s icon=%s"
                           % (r.get("name"), r.get("state"), (r.get("icon") or "(无)")))
        try:
            st = await self.page.evaluate(JS_AVATAR_STATS)
            mark(isinstance(st, dict) and st.get("none", 1) == 0, "头像元素定位",
                 "Avatar 容器 %s 个 / ConversationIcon %s 个 / 都没找到 %s 个（共 %s）"
                 % (st.get("avatar"), st.get("convIcon"), st.get("none"), st.get("total")))
        except Exception as e:
            mark(False, "头像元素定位", "异常：%s" % e)

        # 3) 搜索面板的「取消」按钮（收起面板、恢复会话列表的唯一出口）
        found_cancel = False
        for s in self.sel("search_cancel"):
            try:
                loc = self.page.locator(s).first
                n = await loc.count()
                vis = await loc.is_visible() if n else False
                out.append("      取消按钮候选 %-44s count=%s visible=%s" % (s, n, vis))
                found_cancel = found_cancel or bool(n)
            except Exception as e:
                out.append("      取消按钮候选 %-44s ERROR %s" % (s, e))
        out.append("      （「取消」只有搜索框被点开后才会存在；上面样例搜索之后这里通常能命中。"
                   "两行都 0 且列表正常 = 面板没被打开；如果点开了它却消失，才说明改版了）")

        # 4) 打开样例会话后，逐项验证发送链路上的依赖
        if not sample_name:
            out.append("  [跳过] 没有启用的目标，未做「打开会话后」的那几项检查")
            return out
        try:
            via = await self.open_target({"name": sample_name, "conv_id": None})
            title = await self.chat_title()
            mark(bool(title), "会话已打开 + 标题读取",
                 "via=%s 标题=%r 与目标一致=%s" % (via, title, self._name_match(sample_name, title)))
        except Exception as e:
            mark(False, "打开样例会话「%s」" % sample_name, "异常：%s" % e)
            return out

        try:
            icons = await self.page.evaluate(JS_FIND_ICONS) or []
            mark(len(icons) >= 2, "输入栏图标定位（JS_FIND_ICONS）",
                 "命中 %d 个：%s" % (len(icons), [(i.get("x"), i.get("y")) for i in icons]))
        except Exception as e:
            mark(False, "输入栏图标定位（JS_FIND_ICONS）", "异常：%s" % e)

        try:
            ready = await self.page.evaluate(JS_SEND_READY)
            mark(bool(ready and ready.get("found")), "发送按钮（JS_SEND_READY）",
                 "found=%s ready=%s cls=%s" % (ready.get("found"), ready.get("ready"), ready.get("cls")))
        except Exception as e:
            mark(False, "发送按钮（JS_SEND_READY）", "异常：%s" % e)

        try:
            b = await self._last_bubble()
            mark(isinstance(b, dict), "聊天记录底部气泡（JS_LAST_BUBBLE）",
                 "count=%s me=%s text=%r" % (b.get("count"), b.get("me"), (b.get("text") or "")[:16]))
        except Exception as e:
            mark(False, "聊天记录底部气泡（JS_LAST_BUBBLE）", "异常：%s" % e)

        try:
            head = await self.page.evaluate(JS_SPARK_HEADER)
            mark(isinstance(head, dict), "会话头火花（JS_SPARK_HEADER）",
                 "days=%s state=%s" % (head.get("days"), head.get("state")))
        except Exception as e:
            mark(False, "会话头火花（JS_SPARK_HEADER）", "异常：%s" % e)

        try:
            await self.open_sticker_panel()
            lst = await self.page.evaluate(JS_STICKER_LIST) or []
            mark(bool(lst), "超级表情面板（emojitabPanel + im-resource）", "读到 %d 个超级表情" % len(lst))
        except Exception as e:
            mark(False, "表情面板（emojitabPanel + im-resource）", "异常：%s" % e)
        finally:
            try:
                await self._dismiss_overlays()
            except Exception:
                pass

        return out


def _abs(p):
    # 从资源管理器「复制文件地址」粘来的路径会带一层引号，先剥掉再判断绝对/相对
    p = (p or "").strip().strip("\"'“”").strip()
    if os.path.isabs(p):
        return p
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), p)


def _safe(name):
    return re.sub(r"[^\w\-]", "_", name)[:24]


# 头像可能是 png / webp / jpg，按内容判断扩展名（抖音返回的其实是 webp）
IMAGE_EXTS = (".webp", ".png", ".jpg", ".jpeg", ".gif")


def image_ext(data):
    if not data:
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ".png"


def avatar_stem(key):
    """头像缓存文件名（不含扩展名）：可读前缀 + 短哈希，避免截断后重名。"""
    digest = hashlib.md5(str(key).encode("utf-8")).hexdigest()[:8]
    readable = re.sub(r"[^\w\-]", "", str(key))[:40]
    return "%s_%s" % (readable, digest)
