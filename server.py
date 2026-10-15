#!/usr/bin/env python3
"""
大学星轨 · 创作台 / 本地服务

一个只用 Python 标准库的 HTTP 服务，负责：
  1. 静态托管整个项目（index.html / admin.html / game_default.js / data.js）
  2. 管理员界面（/admin）：密码登录，可视化查看整条线路
  3. 把脑图（XMind .xmind / FreeMind .mm / OPML / Markdown / 缩进文本）
     解析成剧本，预览后一键发布，生成前端读取的 data.js

启动：
    py server.py [端口=8090]

首次启动会在当前目录生成 admin_config.json，里面有初始密码，改它来更换。
"""
import base64
import datetime
import hashlib
import io
import json
import os
import re
import secrets
import sys
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "admin_config.json")
BACKUP_DIR = os.path.join(HERE, "backups")

DEFAULT_PASSWORD = "xingji2026"  # 首次运行使用的初始密码，登录后请到 admin_config.json 更改

# 属性中文名 -> 数据 key（8 维：学业 人际 心态 资源 金钱 技能 体魄 声望）
KEYMAP = {
    "学业": "gpa", "GPA": "gpa", "绩点": "gpa", "gpa": "gpa", "academic": "gpa",
    "人际": "social", "社交": "social", "social": "social",
    "心态": "mind", "心理": "mind", "mind": "mind",
    "资源": "resource", "机会": "resource", "resource": "resource",
    "金钱": "money", "钱": "money", "money": "money",
    "技能": "skill", "skill": "skill",
    "体魄": "body", "身体": "body", "body": "body",
    "声望": "fame", "影响力": "fame", "fame": "fame",
}

# 导出大纲时用中文写回去（KEYMAP 有多个别名，反查要固定用这一张）
KEY_CN = {"gpa": "学业", "social": "人际", "mind": "心态", "resource": "资源",
          "money": "金钱", "skill": "技能", "body": "体魄", "fame": "声望"}

DEFAULT_KEYS = ["gpa", "social", "mind", "resource", "money", "skill", "body", "fame"]
DEFAULT_LABEL = {"gpa": "学业", "social": "人际", "mind": "心态", "resource": "资源",
                 "money": "金钱", "skill": "技能", "body": "体魄", "fame": "声望"}
DEFAULT_FRESH = {"gpa": 60, "social": 30, "mind": 70, "resource": 10,
                 "money": 1500, "skill": 20, "body": 80, "fame": 0}
DEFAULT_STAGES = [
    {"k": "阶段一 · 入学军训", "s": "第 1-2 周"},
    {"k": "阶段二 · 大一上学期", "s": "适应期"},
    {"k": "阶段三 · 大一下 ~ 大二", "s": "成长期"},
    {"k": "阶段四 · 大三", "s": "关键方向抉择", "lanes": True},
    {"k": "阶段五 · 大四", "s": "毕业与结局"},
]


def stat_of(name):
    return KEYMAP.get((name or "").strip())


def jdumps(o):
    return json.dumps(o, ensure_ascii=False, indent=2)


def json_resp(handler, code, obj):
    body = jdumps(obj).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# ---------------------------------------------------------------------------
# 配置 / 口令 / 会话
# ---------------------------------------------------------------------------

def load_config():
    if os.path.exists(CONFIG):
        with io.open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    cfg = {"password": sha256(DEFAULT_PASSWORD), "hint": "默认密码 xingji2026，改这里即可更换"}
    with io.open(CONFIG, "w", encoding="utf-8") as f:
        f.write(jdumps(cfg))
    return cfg


def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


SESSION_FILE = os.path.join(HERE, ".sessions.json")


def load_sessions():
    if os.path.exists(SESSION_FILE):
        try:
            with io.open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_sessions(s):
    with io.open(SESSION_FILE, "w", encoding="utf-8") as f:
        f.write(jdumps(s))


def make_session():
    s = load_sessions()
    tok = secrets.token_hex(24)
    s[tok] = time.time() + 60 * 60 * 12  # 12 小时
    save_sessions(s)
    return tok


def is_authed(cookie):
    if not cookie:
        return False
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("co_admin="):
            tok = part[len("co_admin="):]
            s = load_sessions()
            exp = s.get(tok)
            if exp and exp > time.time():
                # 过期滑续
                s[tok] = time.time() + 60 * 60 * 12
                save_sessions(s)
                return True
    return False


# ---------------------------------------------------------------------------
# 静态文件
# ---------------------------------------------------------------------------

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon",
}


def serve_static(handler, path):
    clean = urlsplit(path).path
    clean = clean.lstrip("/")
    if clean == "":
        clean = "index.html"
    if ".." in clean or "\x00" in clean:
        handler.send_error(403)
        return
    full = os.path.join(HERE, clean)
    if not os.path.isfile(full):
        handler.send_error(404)
        return
    with io.open(full, "rb") as f:
        body = f.read()
    ext = os.path.splitext(full)[1].lower()
    handler.send_response(200)
    handler.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# ---------------------------------------------------------------------------
# 数据清单：当前生效的剧本
# ---------------------------------------------------------------------------

def current_game():
    """data.js 优先，否则回退到 game_default.js。两者都是 window.X = {...} 形式。"""
    for name in ("data.js", "game_default.js"):
        p = os.path.join(HERE, name)
        if not os.path.isfile(p):
            continue
        txt = io.open(p, "r", encoding="utf-8").read()
        m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
        try:
            if m:
                return json.loads(m.group(1))
        except Exception:
            continue
    raise RuntimeError("没有任何剧本数据")


# ---------------------------------------------------------------------------
# 脑图 / 大纲 → 剧本
# ---------------------------------------------------------------------------

KNOWN_KEYS = {"x", "d", "o", "lane", "year", "phase", "days", "onlyLane", "onlyCollege",
              "desc", "branch", "boss", "when", "title", "totalDays", "stage", "settle",
              "need", "needAny", "cap", "fallback", "f", "once", "tier", "s", "lanes", "text", "v"}


def split_meta(line):
    """把 '主标题 | k1=v1, k2=v2' 拆成 (标题, 元数据dict)。
    值内部允许出现逗号/等号（按已知键分段重组，避免把 x=社交+9 的 +9 丢掉）。"""
    title = line
    meta = {}
    if "|" in line:
        left, right = line.split("|", 1)
        title = left.strip()
        toks = [t.strip() for t in right.split(",")]
        comb = []
        for t in toks:
            if not t:
                continue
            head = t.split("=", 1)[0].strip()
            if "=" in t and head in KNOWN_KEYS:
                comb.append(t)
            elif comb:
                comb[-1] += " " + t   # 上一对值的延续
            else:
                comb.append(t)
        for pair in comb:
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            meta[k.strip()] = v.strip()
    return title, meta


STAT_RE = re.compile(r"(>=|<=|>|<)")

def parse_stats(x):
    """'社交+9 学术-3' / 'social:9' / '搞钱>=70' 之类的宽松解析。"""
    out = {}
    if not x:
        return out
    for token in re.split(r"[,，;\s；]+", x):
        token = token.strip()
        if not token:
            continue
        m = re.match(r"^(.+?)([+-])(\d+)$", token)
        if m:
            key = KEYMAP.get(m.group(1).strip())
            if key:
                out[key] = int(m.group(3)) * (1 if m.group(2) == "+" else -1)
            continue
        m = re.match(r"^(.+?)[:：]\s*([+-]?\d+)$", token)
        if m:
            key = KEYMAP.get(m.group(1).strip())
            if key:
                out[key] = int(m.group(2))
            continue
        m = re.match(r"^(.+?)(>=|<=|>|<)(\d+)$", token)
        if m:
            key = KEYMAP.get(m.group(1).strip())
            if key:
                out[key] = int(m.group(3))  # 作为 when 片段，这里只存正数占位
    return out


def parse_reqs(x):
    """'人际>40 声望>10' / '学业<60' -> (need 下限, cap 上限)。"""
    need, cap = {}, {}
    for token in re.split(r"[\s,，;；、]+", x or ""):
        token = token.strip()
        if not token:
            continue
        m = re.match(r"^(.+?)(>=|<=|>|<)\s*(\d+)$", token)
        if not m:
            continue
        k = stat_of(m.group(1))
        if not k:
            continue
        op, v = m.group(2), int(m.group(3))
        if op in (">=", ">"):
            need[k] = v + (1 if op == ">" else 0)
        else:
            cap[k] = v if op == "<" else v + 1
    return need, cap


def parse_any(x):
    """'技能>50 资源>40' -> needAny（满足任意一条即可）。"""
    anym = {}
    for token in re.split(r"[\s,，;；、]+", x or ""):
        m = re.match(r"^(.+?)(>=|>)\s*(\d+)$", token.strip())
        if m and stat_of(m.group(1)):
            anym[stat_of(m.group(1))] = int(m.group(3)) + (1 if m.group(2) == ">" else 0)
    return anym


def parse_flags(x):
    return [t for t in re.split(r"[\s,，;；、]+", x or "") if t]


def tree_from_text(text):
    """缩进大纲 / Markdown / 纯文本转成 [{level, text}]。"""
    if not text:
        return []
    nodes = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.strip().startswith(("//", "# comment", "/*")):
            continue
        # Markdown 标题：# / ## / ###
        m = re.match(r"^(#+)\s+(.*)", line)
        if m:
            level = len(m.group(1))
            txt = m.group(2).strip()
            nodes.append({"level": level, "text": strip_md(txt)})
            continue
        # 有序/无序列表
        m = re.match(r"^(\s*)[-*+>]?\s*(?:\d+[.、)]\s*)?(.*)", line)
        indent = len(m.group(1).replace("\t", "  "))
        level_txt = m.group(2).strip()
        nodes.append({"level": max(1, indent // 2 + 1), "text": strip_md(level_txt)})
    return nodes


def strip_md(t):
    t = re.sub(r"(\*\*|__|`|\*|_)(.*?)\1", r"\2", t)
    t = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", t)
    return t.strip()


def build_tree(nodes):
    """把扁平节点按 level 编排成嵌套树。"""
    root = {"text": "root", "children": []}
    stack = [(0, root)]

    def push(level, text):
        cur = {"text": text, "children": []}
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1]
        parent["children"].append(cur)
        stack.append((level, cur))

    for n in nodes:
        push(n["level"], n["text"])
    return root


def flatten_xmind(node, level=1):
    """XMind JSON 节点 → 扁平 [{level, text}]。"""
    out = []
    text = node.get("title") or node.get("text") or node.get("topic") or ""
    clean = re.sub(r"<[^>]+>", "", text).strip()
    if clean:
        out.append({"level": level, "text": clean})
    ch = node.get("children") or {}
    attached = ch.get("attached") if isinstance(ch, dict) else None
    if attached is None and isinstance(node.get("children"), list):
        attached = node["children"]
    for c in attached or []:
        out += flatten_xmind(c, level + 1)
    return out


def parse_file_to_nodes(filename, data):
    """支持 .xmind / .mm / .opml / .md / .txt。返回 [{level,text}]。"""
    name = (filename or "").lower()
    if name.endswith(".xmind"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "content.json" in z.namelist():
                sheets = json.loads(z.read("content.json"))
            else:
                sheets = []
        nodes = []
        for sheet in sheets:
            rt = sheet.get("rootTopic") or sheet.get("topic")
            if rt:
                nodes += flatten_xmind(rt)
        return nodes
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", "replace")
    if name.endswith(".mm") or name.endswith(".xml"):
        import xml.etree.ElementTree as ET
        try:
            rootel = ET.fromstring(text)
        except Exception:
            return tree_from_text(text)
        nodes = []
        def walk(el, level):
            t = el.get("text") or el.get("TEXT") or ""
            if t:
                nodes.append({"level": level, "text": t})
            for c in list(el):
                walk(c, level + 1)
        walk(rootel, 1)
        return nodes
    if name.endswith(".opml"):
        import xml.etree.ElementTree as ET
        try:
            rootel = ET.fromstring(text)
        except Exception:
            return tree_from_text(text)
        nodes = []
        def walk(el, level):
            t = el.get("text") or ""
            if t and level > 0:
                nodes.append({"level": level, "text": t})
            for c in list(el):
                walk(c, level + 1)
        for c in list(rootel):
            walk(c, 1)
        return nodes
    return tree_from_text(text)


def node_to_event(node, idx, warnings, pool=False):
    """一个节点 → 一个事件（池事件 / 特殊事件也用它）。"""
    title, meta_ = split_meta(node["text"])
    ev = {"id": ("p%d" if pool else "e%d") % idx, "title": title, "options": []}
    if pool:
        ev["pool"] = True
    for k in ("year", "desc"):
        if meta_.get(k):
            ev[k] = meta_[k]
    if meta_.get("days"):
        try:
            ev["days"] = int(meta_["days"])
        except Exception:
            pass
    if meta_.get("stage"):
        try:
            ev["stage"] = int(meta_["stage"])
        except Exception:
            pass
    if meta_.get("phase"):
        ev["phase"] = meta_["phase"]
    if meta_.get("onlyLane"):
        ev["onlyLane"] = meta_["onlyLane"]
    if meta_.get("onlyCollege"):
        ev["onlyCollege"] = meta_["onlyCollege"]
    if meta_.get("when"):
        ev["when"] = meta_["when"]
    if meta_.get("branch") in ("true", "1"):
        ev["branch"] = True
        ev["phase"] = ev.get("phase", "branch")
    if meta_.get("boss") in ("true", "1"):
        ev["boss"] = True
        ev["phase"] = ev.get("phase", "post")
    if meta_.get("settle") in ("true", "1"):
        ev["settle"] = True

    for oc in node.get("children") or []:
        ok, ometa = split_meta(oc["text"])
        opt = {"k": ok}
        if ometa.get("d"):
            opt["d"] = ometa["d"]
        if ometa.get("o"):
            opt["o"] = ometa["o"]
        if ometa.get("lane"):
            opt["lane"] = ometa["lane"]
        if ometa.get("x"):
            stat = parse_stats(ometa["x"])
            if stat:
                opt["x"] = stat
            else:
                warnings.append("选项「%s」的 x 数值没解析出来: %s" % (ok, ometa["x"]))
        need, cap = parse_reqs((ometa.get("need", "") + " " + ometa.get("cap", "")).strip())
        if need:
            opt["need"] = need
        if cap:
            opt["cap"] = cap
        if ometa.get("needAny"):
            anym = parse_any(ometa["needAny"])
            if anym:
                opt["needAny"] = anym
        if ometa.get("fallback") in ("true", "1"):
            opt["fallback"] = True
        if ometa.get("f"):
            opt["f"] = parse_flags(ometa["f"])
        ev["options"].append(opt)

    if not ev["options"]:
        warnings.append("事件「%s」没有任何选项。" % title)
    if not pool and "phase" not in ev:
        ev["phase"] = "pre"
    return ev, meta_


def section_of(title):
    """小节名判定：只认干净的节标题，"阶段结算：军训结束" 这类事件名不算。
    脑图里常写成「随机事件池（每学期概率触发）」，括号里的说明先摘掉。"""
    t = re.sub(r"[（(].*?[）)]", "", title or "").strip().lower()
    for name in ("阶段", "阶段列表", "stages", "stage"):
        if t == name:
            return "stages"
    for name in ("随机事件池", "事件池", "pool"):
        if t == name:
            return "pool"
    for name in ("特殊事件", "突发事件", "触发事件", "triggers", "trigger"):
        if t == name:
            return "triggers"
    for name in ("轨道", "线路", "lanes"):
        if t == name:
            return "lanes"
    if t.startswith("结局") or t == "endings":
        return "endings"
    if t in ("meta", "设置", "配置", "元数据"):
        return "meta"
    return None


def collect_endings(node, endings, tier=""):
    """结局节下面的孩子：可以是结局本身，也可以先按「顶级结局」分级再放结局。"""
    title, meta_ = split_meta(node["text"])
    clean = re.sub(r"[（(].*?[）)]", "", title or "").strip()
    kids = node.get("children") or []
    if kids and ("结局" in clean or clean.lower().endswith("ending")):
        for c in kids:
            collect_endings(c, endings, clean)
        return
    ending = {"lane": meta_.get("lane", ""), "title": title,
              "text": meta_.get("text", "") or meta_.get("o", "")}
    if meta_.get("tier"):
        ending["tier"] = meta_["tier"]
    elif tier:
        ending["tier"] = tier
    if meta_.get("when"):
        ending["when"] = re.sub(r"\s+", "&&", meta_["when"].strip())
    endings.append(ending)


def nodes_to_game(nodes):
    """整棵大纲树 → 剧本。约定见 admin.html 的说明。"""
    root = build_tree(nodes)

    kids = root.get("children") or []
    game = {"meta": {}, "lanes": [], "events": [], "endings": [], "allround": None}
    warnings = []

    meta = {"keys": list(DEFAULT_KEYS),
            "label": dict(DEFAULT_LABEL),
            "max": {"money": 5000},
            "fresh": {"stats": dict(DEFAULT_FRESH)},
            "poolRate": 0.45,
            "stages": [],
            "mapCopy": {}}
    # 把顶层拆成「游戏名 + 要处理的事件/章节」
    title = ""
    items_todo = kids
    if len(items_todo) == 1 and (items_todo[0].get("children") or []):
        # 脑图中心主题：标题即它的文字，真正的事件在它下面
        hub = items_todo[0]
        ht = split_meta(hub["text"])[0].lower()
        if not ht.startswith(("结局", "轨道", "线路", "meta", "设置", "配置", "元数据", "标题")):
            title = split_meta(hub["text"])[0]
        items_todo = hub["children"]
    elif items_todo and not (items_todo[0].get("children") or []):
        # 大纲格式：第一个无子节点的顶层节点当游戏名
        ft = split_meta(items_todo[0]["text"])[0].lower()
        if not ft.startswith(("结局", "轨道", "线路", "meta", "设置", "配置", "元数据", "标题")):
            title = split_meta(items_todo[0]["text"])[0]
            items_todo = items_todo[1:]
    if title:
        meta["title"] = title

    events = []
    endings = []
    lanes = []
    stages = []
    pool = []
    triggers = []

    for node in items_todo:
        title, meta_ = split_meta(node["text"])
        ltitle = title.lower()
        sec = section_of(title)
        if sec == "endings":
            for ec in node.get("children") or []:
                collect_endings(ec, endings)
            continue
        if sec == "lanes":
            for lc in node.get("children") or []:
                lname, _ = split_meta(lc["text"])
                if lname:
                    lanes.append(lname)
            continue
        if sec == "stages":
            for sc in node.get("children") or []:
                sk, sm = split_meta(sc["text"])
                st = {"k": sk}
                if sm.get("s"):
                    st["s"] = sm["s"]
                if sm.get("lanes") in ("true", "1") or "分叉" in sk:
                    st["lanes"] = True
                stages.append(st)
            continue
        if sec == "pool":
            for pc in node.get("children") or []:
                pev, _ = node_to_event(pc, len(pool) + 1, warnings, pool=True)
                pool.append(pev)
            continue
        if sec == "triggers":
            for tc in node.get("children") or []:
                tev, tm = node_to_event(tc, len(triggers) + 1, warnings)
                tev["id"] = "t%d" % (len(triggers) + 1)
                if tm.get("when"):
                    tev["when"] = tm["when"]
                if tm.get("once") not in ("false", "0", "no"):
                    tev["once"] = True
                triggers.append(tev)
            continue
        if sec == "meta":
            for mc in node.get("children") or []:
                mt, mm_ = split_meta(mc["text"])
                m = re.match(r"^\s*([A-Za-z\u4e00-\u9fff0-9]+)\s*=\s*(.*)$", mc["text"].strip())
                if m:
                    k, v = m.group(1).strip(), m.group(2).strip().strip('"\'')
                    if k in ("title", "标题"):
                        meta["title"] = v
                    elif k == "totalDays":
                        try:
                            meta["totalDays"] = int(v)
                        except Exception:
                            pass
                    elif k == "mapCopy":
                        try:
                            cp = json.loads(v)
                            if isinstance(cp, dict):
                                meta["mapCopy"] = cp
                        except Exception:
                            pass
                elif "title" in mm_:
                    meta["title"] = mm_["title"]
            continue

        # 默认当一个事件处理
        ev, _ = node_to_event(node, len(events) + 1, warnings)
        for o in ev.get("options", []):
            if o.get("lane") and o["lane"] not in lanes:
                lanes.append(o["lane"])
        events.append(ev)

    if not meta.get("mapCopy"):
        meta["mapCopy"] = {}
    if "totalDays" not in meta:
        meta["totalDays"] = 1461
    if not meta.get("title"):
        meta["title"] = "未命名剧本"
    if stages:
        meta["stages"] = stages
    else:
        meta["stages"] = [dict(s) for s in DEFAULT_STAGES]
    if not lanes:
        lanes = ["考研/保研", "就业", "考公/考编", "创业"]

    game["meta"] = meta
    game["lanes"] = lanes
    game["events"] = events
    game["endings"] = endings
    game["pool"] = pool
    game["triggers"] = triggers
    game["allround"] = {"title": "普通的四年", "text": "没有大起大落，你按自己的节奏走完了这四年。"}
    return game, warnings


def event_tags(ev):
    tags = []
    for k in ("year", "phase", "days", "stage", "onlyLane", "onlyCollege", "when"):
        if ev.get(k):
            tags.append("%s=%s" % (k, ev[k]))
    for k in ("branch", "boss", "settle", "pool"):
        if ev.get(k):
            tags.append("%s=true" % k)
    if ev.get("desc"):
        tags.append("desc=" + ev["desc"])
    return tags


def opt_tags(o):
    ometa = []
    if o.get("d"):
        ometa.append("d=" + str(o.get("d", "")))
    if o.get("o"):
        ometa.append("o=" + str(o.get("o", "")))
    if o.get("lane"):
        ometa.append("lane=" + o.get("lane", ""))
    if o.get("x"):
        ometa.append("x=" + ",".join("%s%s%d" % (KEY_CN.get(kk, kk), "+" if vv > 0 else "", vv)
                                     for kk, vv in o["x"].items()))
    if o.get("need"):
        ometa.append("need=" + " ".join("%s>%d" % (KEY_CN.get(kk, kk), vv - 1) for kk, vv in o["need"].items()))
    if o.get("cap"):
        ometa.append("cap=" + " ".join("%s<%d" % (KEY_CN.get(kk, kk), vv) for kk, vv in o["cap"].items()))
    if o.get("needAny"):
        ometa.append("needAny=" + " ".join("%s>%d" % (KEY_CN.get(kk, kk), vv - 1) for kk, vv in o["needAny"].items()))
    if o.get("fallback"):
        ometa.append("fallback=true")
    if o.get("f"):
        ometa.append("f=" + " ".join(o["f"]))
    return ometa


def game_to_outline(game):
    """剧本 → 大纲文本。顶层第一节是游戏名，其余顶层是内容；
    用 # / ## / - 分层。该格式与 import 解析尽量互逆（可回环）。"""
    gmeta = game.get("meta", {})
    L = []
    L.append(gmeta.get("title", "剧本") +
             " | totalDays=" + str(gmeta.get("totalDays", 1461)))
    # meta / 阶段 / 轨道
    L.append("# meta")
    L.append("## title = " + gmeta.get("title", ""))
    L.append("## totalDays = " + str(gmeta.get("totalDays", 1461)))
    if gmeta.get("mapCopy"):
        L.append("## mapCopy = " + json.dumps(gmeta.get("mapCopy", {}), ensure_ascii=False)[:200])
    if gmeta.get("stages"):
        L.append("# 阶段")
        for st in gmeta.get("stages", []):
            tags = []
            if st.get("s"):
                tags.append("s=" + st["s"])
            if st.get("lanes"):
                tags.append("lanes=true")
            L.append("## " + st.get("k", "") + (" | " + ", ".join(tags) if tags else ""))
    if game.get("lanes"):
        L.append("# 轨道")
        for ln in game["lanes"]:
            L.append("## " + ln)
    # 事件（顶层一项一个事件）
    for ev in game.get("events", []):
        tags = event_tags(ev)
        L.append(ev.get("title", "") + (" | " + ", ".join(tags) if tags else ""))
        for o in ev.get("options", []):
            om = opt_tags(o)
            L.append("  - " + o.get("k", "") + (" | " + ", ".join(om) if om else ""))
    # 随机事件池 / 特殊事件
    if game.get("pool"):
        L.append("# 随机事件池")
        for ev in game["pool"]:
            tags = event_tags(ev)
            L.append("## " + ev.get("title", "") + (" | " + ", ".join(tags) if tags else ""))
            for o in ev.get("options", []):
                om = opt_tags(o)
                L.append("    - " + o.get("k", "") + (" | " + ", ".join(om) if om else ""))
    if game.get("triggers"):
        L.append("# 特殊事件")
        for ev in game["triggers"]:
            tags = event_tags(ev)
            if ev.get("once"):
                tags.append("once=true")
            L.append("## " + ev.get("title", "") + (" | " + ", ".join(tags) if tags else ""))
            for o in ev.get("options", []):
                om = opt_tags(o)
                L.append("    - " + o.get("k", "") + (" | " + ", ".join(om) if om else ""))
    # 结局
    if game.get("endings"):
        L.append("# 结局")
        for e in game["endings"]:
            etags = []
            if e.get("lane"):
                etags.append("lane=" + e["lane"])
            if e.get("tier"):
                etags.append("tier=" + e["tier"])
            if e.get("when"):
                etags.append("when=" + e["when"])
            if e.get("text"):
                etags.append("text=" + e["text"])
            L.append("## " + e.get("title", "") + (" | " + ", ".join(etags) if etags else ""))
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 发布
# ---------------------------------------------------------------------------

def publish_game(game):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    # 备份旧 data.js
    dj = os.path.join(HERE, "data.js")
    if os.path.exists(dj):
        ts = time.strftime("%Y%m%d-%H%M%S")
        os.rename(dj, os.path.join(BACKUP_DIR, "data-%s.js" % ts))
    js = "/* 由创作台生成 · %s */\nwindow.GAME_DATA=%s;\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), jdumps(game))
    with io.open(dj, "w", encoding="utf-8") as f:
        f.write(js)
    return dj


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        return self.rfile.read(n)

    def _authed(self):
        return is_authed(self.headers.get("Cookie"))

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/api/login":
            json_resp(self, 200, {"need": True, "authed": self._authed()})
            return
        if path == "/api/status":
            if not self._authed():
                json_resp(self, 401, {"error": "未登录"})
                return
            published = os.path.exists(os.path.join(HERE, "data.js"))
            game = current_game()
            gg = game.get("meta", {})
            json_resp(self, 200, {
                "published": published,
                "title": gg.get("title", ""),
                "events": len(game.get("events", [])),
                "endings": len(game.get("endings", [])),
                "lanes": game.get("lanes", []),
                "pool": len(game.get("pool", [])),
                "triggers": len(game.get("triggers", [])),
                "updated": os.path.getmtime(os.path.join(HERE, "data.js")) if published else 0,
            })
            return
        if path == "/api/line":
            if not self._authed():
                json_resp(self, 401, {"error": "未登录"})
                return
            json_resp(self, 200, current_game())
            return
        if path == "/api/export":
            if not self._authed():
                json_resp(self, 401, {"error": "未登录"})
                return
            json_resp(self, 200, {"text": game_to_outline(current_game())})
            return

        if path == "/admin" or path == "/admin.html":
            serve_static(self, path.replace("/admin", "/admin.html"))
            return
        serve_static(self, path)

    def do_POST(self):
        path = urlsplit(self.path).path

        if path == "/api/login":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
            except Exception:
                json_resp(self, 400, {"error": "无效请求"})
                return
            pwd = (body.get("password") or "")
            if sha256(pwd) == load_config().get("password"):
                tok = make_session()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                # SameSite=Lax 允许 /admin 与前端游走
                self.send_header("Set-Cookie", "co_admin=" + tok + "; Path=/; HttpOnly; SameSite=Lax")
                self.end_headers()
                self.wfile.write(jdumps({"ok": True}).encode("utf-8"))
            else:
                json_resp(self, 401, {"error": "密码不对"})
            return

        if path == "/api/import":
            if not self._authed():
                json_resp(self, 401, {"error": "未登录"})
                return
            try:
                body = json.loads(self._read_body().decode("utf-8"))
            except Exception:
                json_resp(self, 400, {"error": "无效请求"})
                return
            text = (body.get("text") or "")
            filename = (body.get("filename") or "")
            if body.get("fileb64"):
                filebytes = base64.b64decode(body.get("fileb64"))
                try:
                    nodes = parse_file_to_nodes(filename, filebytes)
                except Exception as e:
                    json_resp(self, 400, {"error": "解析文件失败: %s" % e})
                    return
            else:
                nodes = tree_from_text(text)
            if not nodes:
                json_resp(self, 400, {"error": "内容为空，请粘贴大纲或将文件拖进来"})
                return
            try:
                game, warnings = nodes_to_game(nodes)
            except Exception as e:
                json_resp(self, 400, {"error": "解析失败: %s" % e})
                return
            json_resp(self, 200, {"game": game, "warnings": warnings})
            return

        if path == "/api/logout":
            cookie = self.headers.get("Cookie")
            if cookie:
                s = load_sessions()
                for part in cookie.split(";"):
                    part = part.strip()
                    if part.startswith("co_admin="):
                        tok = part[len("co_admin="):]
                        s.pop(tok, None)
                save_sessions(s)
            self.send_response(200)
            self.send_header("Set-Cookie", "co_admin=; Max-Age=0; Path=/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path == "/api/publish":
            if not self._authed():
                json_resp(self, 401, {"error": "未登录"})
                return
            try:
                body = json.loads(self._read_body().decode("utf-8"))
            except Exception:
                json_resp(self, 400, {"error": "无效请求"})
                return
            game = body.get("game")
            if not game or not game.get("events"):
                json_resp(self, 400, {"error": "没有可发布的剧本"})
                return
            try:
                dst = publish_game(game)
            except Exception as e:
                json_resp(self, 500, {"error": "发布失败: %s" % e})
                return
            json_resp(self, 200, {"ok": True, "file": os.path.basename(dst)})
            return

        json_resp(self, 404, {"error": "not found"})


def main():
    port = 8090
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    load_config()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("=" * 56)
    print(" 大学星轨 · 创作台运行中")
    print(" 游客页面 : http://127.0.0.1:%d/" % port)
    print(" 管理员界面: http://127.0.0.1:%d/admin" % port)
    print(" 默认密码 : %s （改 admin_config.json 更换）" % DEFAULT_PASSWORD)
    print(" 按 Ctrl+C 停止")
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        sys.exit(0)


if __name__ == "__main__":
    main()