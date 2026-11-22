# -*- coding: utf-8 -*-
"""
桌面任务状态主动推送程序
=========================
常驻监控 WorkBuddy 桌面端所有「未删除」任务的状态，出现以下变化时通过
Server 酱 Turbo 主动推送到微信：

  1. 任务完成            (working/active -> completed)
  2. 任务异常终止        (working/active -> terminated/failed/cancelled)
  3. 需要你拍板/授权      (session_turn_interruptions 出现新记录)
  4. 疑似卡住            (仍运行中，但 last_activity_at 超过阈值无更新)

特点：
  - 只读数据库，绝不修改 workbuddy.db
  - 零第三方依赖（标准库 urllib 发 HTTP）
  - 会话名优先取 custom_title（用户在界面改过的名字）
  - 状态指纹去重 + 冷却，避免刷屏；每日发送条数限额
  - Windows 中文编码处理，全程写 monitor.log

用法：
  python task_monitor.py                # 常驻运行（循环巡检）
  python task_monitor.py --self-test    # 自检：读状态 + 发一条测试通知，不改 state
  python task_monitor.py --once         # 只跑一轮巡检（用于任务计划程序 triggers）
"""

import os
import sys
import json
import time
import re
import socket
import smtplib
import hashlib
import sqlite3
import traceback
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ---- Windows 中文控制台编码 ----
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "task_monitor_config.json")
STATE_PATH = os.path.join(BASE_DIR, "task-monitor-state.json")
LOG_PATH = os.path.join(BASE_DIR, "monitor.log")

# 运行中 / 完成 / 失败 的状态集合
RUNNING = {"working", "active"}
DONE = {"completed"}
FAILED = {"terminated", "failed", "cancelled"}

DEFAULTS = {
    "sendkey": "",
    # 默认指向当前用户主目录下的 WorkBuddy 数据库，可在 config 中覆盖
    "db_path": os.path.join(os.path.expanduser("~"), ".workbuddy", "workbuddy.db").replace("\\", "/"),
    "poll_seconds": 60,
    "stuck_minutes": 10,
    "stuck_cooldown_minutes": 30,
    "daily_limit": 4,
    "exclude_titles": [],
    "exclude_cwd": [],
    "email": {
        "enabled": True,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "use_ssl": True,
        "smtp_user": "",
        "smtp_pass": "",
        "mail_to": "",
        "sender_name": "任务监控助手"
    },
}


# ---------------- 基础设施 ----------------

def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config():
    cfg = dict(DEFAULTS)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg.update(json.load(f))
    # db_path 留空时回退到默认主目录路径，避免示例配置直接用时报错
    if not cfg.get("db_path"):
        cfg["db_path"] = DEFAULTS["db_path"]
    return cfg


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"sessions": {}, "notify_date": "", "notify_count": 0, "mail_count": 0}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("sessions", {})
        s.setdefault("notify_date", "")
        s.setdefault("notify_count", 0)
        s.setdefault("mail_count", 0)
        return s
    except Exception as e:
        log("读取 state 失败，重建：%s" % e)
        return {"sessions": {}, "notify_date": "", "notify_count": 0}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def norm_path(p):
    return (p or "").replace("\\", "/").lower()


def is_excluded(title, cwd, cfg):
    for kw in cfg.get("exclude_titles", []):
        if kw and kw in (title or ""):
            return True
    nc = norm_path(cwd)
    for kw in cfg.get("exclude_cwd", []):
        if kw and norm_path(kw) in nc:
            return True
    return False


def display_name(row):
    return row.get("custom_title") or row.get("title") or "(未命名任务)"


# ---------------- 数据读取 ----------------

def scan_sessions(cfg):
    """只读扫描所有未删除会话，返回 {id: snapshot}。"""
    db = cfg["db_path"]
    conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id,title,custom_title,status,last_activity_at,"
            "agent_dirty,cwd FROM sessions "
            "WHERE deleted_at IS NULL OR deleted_at=-1"
        )
        rows = cur.fetchall()

        # 读取中断表（等待用户确认/拍板）
        ints = {}
        try:
            cur.execute("SELECT session_id,reason_code,presentation "
                        "FROM session_turn_interruptions")
            for r in cur.fetchall():
                ints.setdefault(r["session_id"], []).append(
                    (r["reason_code"] or "", r["presentation"] or "")
                )
        except Exception:
            pass  # 表不存在则跳过

        result = {}
        for r in rows:
            title = r["title"] or ""
            cwd = r["cwd"] or ""
            if is_excluded(title, cwd, cfg):
                continue
            ilist = sorted(ints.get(r["id"], []))
            fp = hashlib.md5(json.dumps(ilist, ensure_ascii=False).encode("utf-8")).hexdigest() if ilist else ""
            result[r["id"]] = {
                "title": display_name(dict(r)),
                "status": r["status"] or "",
                "last_activity_at": r["last_activity_at"] or 0,
                "agent_dirty": 1 if r["agent_dirty"] else 0,
                "cwd": cwd,
                "interrupt_fp": fp,
                "interrupt_text": "；".join(x[1] for x in ilist if x[1])[:300],
            }
        return result
    finally:
        conn.close()


# ---------------- 事件判定 ----------------

def minute_ago_info(ts_ms):
    age_min = (time.time() * 1000 - ts_ms) / 60000.0
    return max(0, int(age_min))


def judge(cur_map, state, cfg):
    """对比上次状态，判定值得通知的事件。返回 (events, new_sessions_map)。"""
    prev_map = state["sessions"]
    now_ms = time.time() * 1000
    cooldown_ms = cfg["stuck_cooldown_minutes"] * 60000
    stuck_ms = cfg["stuck_minutes"] * 60000
    events = []
    new_map = {}

    for sid, cur in cur_map.items():
        # 继承上次记录里的冷却时间戳
        old = prev_map.get(sid)
        rec = {
            "title": cur["title"],
            "status": cur["status"],
            "last_activity_at": cur["last_activity_at"],
            "interrupt_fp": cur["interrupt_fp"],
            "last_stuck_notified": (old or {}).get("last_stuck_notified", 0),
        }

        if old is None:
            # 首次纳入监控：只写基线，不推送（避免把历史已完成误报）
            new_map[sid] = rec
            continue

        st = cur["status"]
        name = cur["title"]

        # 1) 完成
        if old["status"] in RUNNING and st in DONE:
            events.append({"kind": "done", "name": name,
                           "detail": "任务已完成"})

        # 2) 异常终止
        elif old["status"] in RUNNING and st in FAILED:
            events.append({"kind": "fail", "name": name,
                           "detail": "任务异常终止（状态：%s），请查看" % st})

        # 3) 需要拍板：中断指纹新出现或变化
        if cur["interrupt_fp"] and cur["interrupt_fp"] != old.get("interrupt_fp", ""):
            txt = cur["interrupt_text"] or "等待你的确认/回答"
            events.append({"kind": "ask", "name": name,
                           "detail": "需要你拍板：%s" % txt})

        # 4) 疑似卡住：仍运行中且长时间无活动，带冷却
        if st in RUNNING and cur["last_activity_at"] > 0:
            idle = now_ms - cur["last_activity_at"]
            if idle >= stuck_ms and (now_ms - rec["last_stuck_notified"]) >= cooldown_ms:
                rec["last_stuck_notified"] = now_ms
                events.append({"kind": "stuck", "name": name,
                               "detail": "已 %d 分钟无进展，疑似卡住，建议查看" % minute_ago_info(cur["last_activity_at"])})

        new_map[sid] = rec

    return events, new_map


# ---------------- 推送 ----------------

def notify(cfg, title, desp):
    sendkey = cfg["sendkey"]
    if not sendkey:
        log("未配置 sendkey，跳过推送")
        return False
    url = "https://sctapi.ftqq.com/%s.send" % sendkey
    data = urlencode({"title": title[:32], "desp": desp}).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for attempt in range(1, 3):
        try:
            with urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", "replace")
            result = json.loads(body)
            if str(result.get("code")) == "0":
                log("推送成功：%s" % title)
                return True
            log("推送返回异常：%s" % body[:200])
        except Exception as e:
            log("推送第%d次失败：%s" % (attempt, e))
            time.sleep(2)
    return False


def notify_email(cfg, title, desp):
    """通过 SMTP 发邮件（标准库 smtplib，零依赖）。返回是否成功。"""
    ec = cfg.get("email", {})
    if not ec.get("enabled"):
        return False
    host, user, pwd = ec.get("smtp_host", ""), ec.get("smtp_user", ""), ec.get("smtp_pass", "")
    to = ec.get("mail_to", "")
    if not (host and user and pwd and to):
        log("邮件未配置完整（smtp_user/smtp_pass/mail_to 不能为空），跳过")
        return False
    port = int(ec.get("smtp_port", 465))
    use_ssl = bool(ec.get("use_ssl", True))
    sender_name = ec.get("sender_name", "任务监控助手")

    # 把 Markdown 的 ###/-/** 粗略转成 HTML，邮件里更好看
    def md_to_html(text):
        h = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        out = []
        for line in h.split("\n"):
            if line.startswith("### "):
                out.append("<h3>%s</h3>" % line[4:])
            elif line.strip().startswith("- **"):
                line = re.sub(r"- \*\*(.+?)\*\*：(.+)", r"<li><b>\1</b>：\2</li>", line)
                out.append(line)
            elif line.strip().startswith("- "):
                out.append("<div style='color:#666;margin-left:16px'>%s</div>" % line.strip()[2:])
            elif not line.strip():
                out.append("")
            else:
                out.append("<p>%s</p>" % line)
        return "<br>".join(out)

    html = "<div style='font-family:Microsoft YaHei,Arial,sans-serif;font-size:14px'>%s</div>" % md_to_html(desp)
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = formataddr((sender_name, user))
    msg["To"] = to
    msg.set_content(desp)                      # 纯文本兜底
    msg.add_alternative(html, subtype="html")  # HTML 正文

    for attempt in range(1, 3):
        try:
            if use_ssl:
                srv = smtplib.SMTP_SSL(host, port, timeout=20)
            else:
                srv = smtplib.SMTP(host, port, timeout=20)
                srv.starttls()
            with srv:
                srv.login(user, pwd)
                srv.send_message(msg)
            log("邮件推送成功 -> %s：%s" % (to, title))
            return True
        except Exception as e:
            log("邮件推送第%d次失败：%s" % (attempt, e))
            time.sleep(2)
    return False


ICON = {"done": "✅ 完成", "fail": "⚠️ 异常", "ask": "🙋 待拍板", "stuck": "⏳ 卡住"}


def build_message(events):
    """把本轮事件组装成 (title, desp)。"""
    if len(events) == 1:
        e = events[0]
        title = "%s｜%s" % (ICON.get(e["kind"], "任务"), e["name"][:18])
    else:
        title = "任务状态提醒（%d项）" % len(events)
    lines = ["### 桌面任务状态通知", ""]
    for e in events:
        lines.append("- **%s**：%s" % (ICON.get(e["kind"], e["kind"]), e["name"]))
        lines.append("  - %s" % e["detail"])
    lines.append("")
    lines.append("—— 时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return title, "\n".join(lines)


def send_events(cfg, state, events):
    if not events:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    if state["notify_date"] != today:
        state["notify_date"] = today
        state["notify_count"] = 0
        state["mail_count"] = 0
    limit = cfg["daily_limit"]
    title, desp = build_message(events)

    # 优先走微信（Server 酱）；额度用尽或微信失败时，自动降级到邮件
    if state["notify_count"] < limit:
        if notify(cfg, title, desp):
            state["notify_count"] += 1
            return
        log("微信推送失败，本轮改走邮件兜底")
    else:
        log("微信今日额度已用尽（%d 条），本轮改走邮件兜底" % limit)

    if notify_email(cfg, title, desp):
        state["mail_count"] = state.get("mail_count", 0) + 1
    else:
        log("邮件兜底也未成功，本轮通知未能送达")


# ---------------- 主循环 ----------------

def run_once(cfg, dry=False):
    cur_map = scan_sessions(cfg)
    state = load_state()
    events, new_map = judge(cur_map, state, cfg)
    log("巡检完成：监控 %d 个任务，触发 %d 条事件" % (len(cur_map), len(events)))
    for e in events:
        log("  - [%s] %s：%s" % (e["kind"], e["name"], e["detail"]))
    if not dry:
        send_events(cfg, state, events)
        state["sessions"] = new_map
        save_state(state)
    return cur_map, events


def self_test(cfg):
    log("===== 自检开始 =====")
    cur_map, events = run_once(cfg, dry=True)
    log("--- 当前未删除任务（排除自身后）共 %d 个 ---" % len(cur_map))
    for sid, s in sorted(cur_map.items(), key=lambda x: x[1]["last_activity_at"], reverse=True):
        idle = minute_ago_info(s["last_activity_at"])
        log("  · [%s] %s ｜ 距最后活动 %d 分钟" % (s["status"], s["title"], idle))
    log("--- 发送测试通知（微信） ---")
    ok = notify(cfg, "【自检】任务监控程序测试",
                "如果你在微信收到这条，说明推送通道已打通。\n\n"
                "程序将开始监控 WorkBuddy 所有任务的完成/异常/待拍板/卡住状态。")
    log("微信自检结果：%s" % ("成功" if ok else "失败"))
    log("--- 发送测试通知（邮件兜底） ---")
    mok = notify_email(cfg, "【自检】任务监控邮件兜底测试",
                       "### 邮件兜底通道测试\n\n"
                       "- **说明**：如果你收到这封邮件，说明微信额度用尽后的邮件兜底通道已打通。\n\n"
                       "—— 任务监控程序")
    log("邮件自检结果：%s" % ("成功" if mok else "未配置或失败（填好邮箱配置后可重测）"))
    log("===== 自检结束 =====")


_SOCK = None  # 持有单实例锁的 socket，需保持引用不被回收


def acquire_single_instance():
    """用本地端口加单实例锁：已有一个实例在跑则返回 False。"""
    global _SOCK
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47831))
        s.listen(1)
        _SOCK = s  # 保持引用
        return True
    except OSError:
        s.close()
        return False


def main():
    cfg = load_config()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--self-test":
        self_test(cfg)
        return
    if mode == "--once":
        run_once(cfg)
        return
    if mode == "--test-email":
        ec = cfg.get("email", {})
        log("邮件配置：host=%s port=%s to=%s enabled=%s"
            % (ec.get("smtp_host"), ec.get("smtp_port"), ec.get("mail_to"), ec.get("enabled")))
        ok = notify_email(cfg, "【测试】任务监控邮件兜底通道",
                          "### 邮件兜底通道测试\n\n- **结果**：收到即表示配置成功。")
        log("邮件测试：%s" % ("成功" if ok else "失败"))
        return

    if not acquire_single_instance():
        log("已有监控实例在运行，本次启动退出。")
        return

    ec = cfg.get("email", {})
    mail_ready = bool(ec.get("enabled") and ec.get("smtp_user") and ec.get("smtp_pass") and ec.get("mail_to"))
    log("监控程序启动：轮询 %ds，卡住阈值 %d 分钟，微信每日上限 %d 条，邮件兜底=%s"
        % (cfg["poll_seconds"], cfg["stuck_minutes"], cfg["daily_limit"],
           "已就绪" if mail_ready else "未配置"))
    while True:
        try:
            run_once(cfg)
        except Exception:
            log("本轮巡检异常（下轮重试）：\n%s" % traceback.format_exc())
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
