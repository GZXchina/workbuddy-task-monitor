# -*- coding: utf-8 -*-
"""
WorkBuddy 任务监控 · 可视化面板
================================
给 task_monitor.py 配的本地网页版控制台（标准库实现，零第三方依赖）。

打开方式：
  双击「启动面板.bat」，会自动在浏览器打开 http://127.0.0.1:47832

能做什么：
  · 打开即自动检测后台监控程序是否在运行（读心跳文件）
  · 实时显示：运行状态、WorkBuddy 是否在跑、监控任务数、今日推送量、
    最后巡检时间、运行时长、邮件兜底是否就绪、最近动态
  · 一键开启 / 关闭监控（关闭＝后台待命，不巡检不推送，随时恢复）
  · 立即巡检、发送测试通知、打开配置、查看日志、开机自启开关
  · 关闭浏览器标签页后，面板服务会在 2 分钟内自动退出，不留后台垃圾

安全：只监听 127.0.0.1，仅本机可访问，不对外网开放。
"""

import os
import sys
import json
import time
import threading
import subprocess
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "task_monitor_config.json")
EXAMPLE_PATH = os.path.join(BASE_DIR, "task_monitor_config.example.json")
STATE_PATH = os.path.join(BASE_DIR, "task-monitor-state.json")
STATUS_PATH = os.path.join(BASE_DIR, "monitor_status.json")
CONTROL_PATH = os.path.join(BASE_DIR, "monitor_control.json")
LOG_PATH = os.path.join(BASE_DIR, "monitor.log")
SCRIPT_PATH = os.path.join(BASE_DIR, "task_monitor.py")

TASK_NAME = "WorkBuddyTaskMonitor"
PORT = 47832
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

_last_ping = [time.time()]
_auto_cache = {"at": 0.0, "val": None}


# ---------------- 文件工具 ----------------

def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else (default or {})
    except Exception:
        return default or {}


def write_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def mask_email(addr):
    if not addr or "@" not in addr:
        return ""
    name, domain = addr.split("@", 1)
    return "%s***@%s" % (name[:3], domain)


def pythonw_path():
    exe = sys.executable or ""
    folder, name = os.path.split(exe)
    if name.lower() == "python.exe":
        cand = os.path.join(folder, "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe or "pythonw.exe"


def ps_run(cmd, timeout=90):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
        capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )


def ps_text(proc):
    out = proc.stdout or b""
    for enc in ("mbcs", "utf-8"):
        try:
            return out.decode(enc)
        except Exception:
            continue
    return out.decode("utf-8", "ignore")


# ---------------- 监控程序控制 ----------------

def daemon_running():
    st = read_json(STATUS_PATH, {})
    hb = st.get("last_heartbeat_at") or 0
    if not hb:
        return False
    return (time.time() * 1000 - hb) / 1000.0 <= 120


def spawn_daemon():
    try:
        subprocess.Popen(
            [pythonw_path(), SCRIPT_PATH], cwd=BASE_DIR,
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        )
        return True, ""
    except Exception as e:
        return False, str(e)


def config_ready():
    cfg = read_json(CONFIG_PATH, {})
    key = (cfg.get("sendkey") or "").strip()
    return bool(key) and not key.startswith("YOUR_")


def ensure_config_file():
    if not os.path.exists(CONFIG_PATH) and os.path.exists(EXAMPLE_PATH):
        try:
            with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
                write_json(CONFIG_PATH, json.load(f))
            return True
        except Exception:
            return False
    return os.path.exists(CONFIG_PATH)


def autostart_status(force=False):
    if not force and (time.time() - _auto_cache["at"]) < 20:
        return _auto_cache["val"]
    val = None
    try:
        proc = ps_run("Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue "
                      "| Select-Object -ExpandProperty TaskName" % TASK_NAME, timeout=25)
        val = TASK_NAME in ps_text(proc)
    except Exception:
        val = None
    _auto_cache["at"] = time.time()
    _auto_cache["val"] = val
    return val


def set_autostart(on):
    pyw = pythonw_path().replace("'", "''")
    script = SCRIPT_PATH.replace("'", "''")
    user = os.environ.get("USERNAME", "")
    if on:
        ps = (
            "if (Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue) { "
            "Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false }; "
            "$a = New-ScheduledTaskAction -Execute '{pyw}' -Argument '\"{script}\"'; "
            "$t1 = New-ScheduledTaskTrigger -AtLogOn -User '{user}'; "
            "$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date) "
            "-RepetitionInterval (New-TimeSpan -Minutes 5) "
            "-RepetitionDuration (New-TimeSpan -Days 3650); "
            "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) "
            "-ExecutionTimeLimit (New-TimeSpan -Days 3650) -MultipleInstances IgnoreNew; "
            "$p = New-ScheduledTaskPrincipal -UserId '{user}' -LogonType Interactive -RunLevel Limited; "
            "Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger @($t1,$t2) "
            "-Settings $s -Principal $p -Description 'WorkBuddy 任务监控' -Force | Out-Null"
        )
    else:
        ps = "Unregister-ScheduledTask -TaskName '%s' -Confirm:$false" % TASK_NAME

    ps = ps.replace("{name}", TASK_NAME).replace("{pyw}", pyw) \
           .replace("{script}", script).replace("{user}", user)
    try:
        proc = ps_run(ps)
        if proc.returncode != 0:
            return False, ps_text(proc)[-400:]
        _auto_cache["at"] = 0
        return True, ""
    except Exception as e:
        return False, str(e)


def run_self_test():
    py = sys.executable or "python"
    if py.lower().endswith("pythonw.exe"):
        cand = os.path.join(os.path.dirname(py), "python.exe")
        if os.path.exists(cand):
            py = cand
    try:
        proc = subprocess.run([py, SCRIPT_PATH, "--self-test"], cwd=BASE_DIR,
                              capture_output=True, timeout=120,
                              creationflags=CREATE_NO_WINDOW)
        text = (proc.stdout or b"").decode("utf-8", "ignore") or \
               (proc.stderr or b"").decode("utf-8", "ignore")
        ok = "微信自检结果：成功" in text
        return ok, text[-1500:]
    except Exception as e:
        return False, str(e)


# ---------------- 状态汇总 ----------------

def tail_log(n=70):
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f.readlines()[-n:]]
    except Exception:
        return []


def build_state():
    cfg = read_json(CONFIG_PATH, {})
    st = read_json(STATUS_PATH, {})
    ctrl = read_json(CONTROL_PATH, {})
    state = read_json(STATE_PATH, {})

    now_ms = int(time.time() * 1000)
    hb = st.get("last_heartbeat_at") or 0
    age = round((now_ms - hb) / 1000.0, 1) if hb else None
    running = bool(hb) and age is not None and age <= 120
    paused = bool(ctrl.get("paused"))
    today = datetime.now().strftime("%Y-%m-%d")
    same_day = state.get("notify_date") == today
    used = (state.get("notify_count") or 0) if same_day else 0
    mail_used = (state.get("mail_count") or 0) if same_day else 0

    ec = cfg.get("email") or {}
    mail_ready = bool(ec.get("enabled") and ec.get("smtp_user")
                      and ec.get("smtp_pass") and ec.get("mail_to"))
    db_ok = bool(cfg.get("db_path")) and os.path.exists(
        (cfg.get("db_path") or "").replace("/", os.sep))

    return {
        "now": now_ms,
        "running": running,
        "paused": paused,
        "heart_age": age,
        "pid": st.get("pid"),
        "started_at": st.get("started_at"),
        "last_poll_at": st.get("last_poll_at"),
        "monitored": st.get("monitored"),
        "events": st.get("events"),
        "workbuddy": st.get("workbuddy_alive"),
        "loop": st.get("loop"),
        "notify": {"used": used, "limit": cfg.get("daily_limit") or 4, "mail": mail_used},
        "config": {
            "poll": cfg.get("poll_seconds") or 60,
            "stuck": cfg.get("stuck_minutes") or 10,
            "limit": cfg.get("daily_limit") or 4,
            "sendkey_ready": config_ready(),
            "mail_ready": mail_ready,
            "mail_to": mask_email(ec.get("mail_to")),
            "db_ok": db_ok,
            "db_path": cfg.get("db_path") or "",
        },
        "autostart": autostart_status(),
        "log": tail_log(),
    }


# ---------------- HTTP 服务 ----------------

class Handler(BaseHTTPRequestHandler):
    server_version = "WorkBuddyPanel/1.0"

    def log_message(self, *args):
        pass  # 静默，不刷控制台

    # ---- 基础输出 ----
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "")

    # ---- 路由 ----
    def do_GET(self):
        if not self._host_ok():
            self._send(403, "forbidden", "text/plain; charset=utf-8")
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/api/state":
            _last_ping[0] = time.time()
            self._json(build_state())
        elif path == "/api/ping":
            _last_ping[0] = time.time()
            self._json({"ok": True})
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._host_ok():
            self._send(403, "forbidden", "text/plain; charset=utf-8")
            return
        _last_ping[0] = time.time()
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            payload = {}

        try:
            self._json(self._dispatch(path, payload))
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _dispatch(self, path, payload):
        if path == "/api/start":
            ensure_config_file()
            if not config_ready():
                return {"ok": False, "need_config": True,
                        "error": "还没有填写 Server 酱 SendKey，请先在配置文件里填写"}
            ctrl = read_json(CONTROL_PATH, {})
            ctrl["paused"] = False
            ctrl["updated_at"] = int(time.time() * 1000)
            write_json(CONTROL_PATH, ctrl)
            spawned = False
            if not daemon_running():
                ok, err = spawn_daemon()
                if not ok:
                    return {"ok": False, "error": "启动监控程序失败：%s" % err}
                spawned = True
            return {"ok": True, "msg": "监控已开启" + ("（正在启动…）" if spawned else "")}

        if path == "/api/stop":
            ctrl = read_json(CONTROL_PATH, {})
            ctrl["paused"] = True
            ctrl["updated_at"] = int(time.time() * 1000)
            write_json(CONTROL_PATH, ctrl)
            return {"ok": True, "msg": "监控已关闭：不再巡检、不再推送"}

        if path == "/api/poll":
            ctrl = read_json(CONTROL_PATH, {})
            ctrl["poll_request_at"] = int(time.time() * 1000)
            write_json(CONTROL_PATH, ctrl)
            return {"ok": True, "msg": "已请求立即巡检"}

        if path == "/api/test":
            ok, text = run_self_test()
            return {"ok": ok, "msg": "测试通知已发送，请查看微信" if ok else
                    "测试未确认成功，详见日志", "detail": text}

        if path == "/api/autostart":
            want = bool(payload.get("on"))
            ok, err = set_autostart(want)
            if not ok:
                return {"ok": False, "error": "设置开机自启失败：%s" % err}
            return {"ok": True, "on": want,
                    "msg": "已开启开机自启" if want else "已关闭开机自启"}

        if path == "/api/open":
            what = payload.get("target")
            if what == "config":
                ensure_config_file()
                os.startfile(CONFIG_PATH)
            elif what == "log":
                if os.path.exists(LOG_PATH):
                    os.startfile(LOG_PATH)
                else:
                    return {"ok": False, "error": "还没有日志文件"}
            elif what == "dir":
                os.startfile(BASE_DIR)
            return {"ok": True}

        if path == "/api/shutdown":
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return {"ok": True, "msg": "面板已关闭"}

        return {"ok": False, "error": "unknown action"}


# ---------------- 前端页面 ----------------

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WorkBuddy 任务监控面板</title>
<style>
:root{
  --bg:#0e1117; --card:#161b26; --card2:#1b2230; --line:#242c3b;
  --txt:#e8eef8; --sub:#8b97ac; --dim:#59647a;
  --blue:#5b8def; --green:#3ecf8e; --amber:#f0b429; --red:#f2595e;
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0; color:var(--txt); background:#0e1117;
  background-image:radial-gradient(1100px 520px at 50% -12%, #1a2334 0%, #0e1117 60%);
  font:14px/1.6 "Microsoft YaHei UI","Segoe UI",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1020px;margin:0 auto;padding:30px 26px 46px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;flex-wrap:wrap}
h1{margin:0;font-size:21px;letter-spacing:.4px}
h1 small{font-size:12px;color:var(--dim);font-weight:400;margin-left:8px}
.sub{color:var(--sub);font-size:12.5px;margin-top:5px}
.pill{display:inline-flex;align-items:center;gap:10px;padding:10px 20px;border-radius:999px;
  background:var(--card);border:1px solid var(--line);font-weight:700;font-size:15px;white-space:nowrap}
.pill .dot{width:9px;height:9px;border-radius:50%;background:var(--dim);transition:.25s}
.pill .txt{transition:.25s}
.pill .meta{font-weight:400;font-size:11.5px;color:var(--dim)}
.pill.on{border-color:rgba(62,207,142,.35);box-shadow:0 0 0 4px rgba(62,207,142,.06)}
.pill.on .dot{background:var(--green);box-shadow:0 0 10px rgba(62,207,142,.8)}
.pill.on .txt{color:var(--green)}
.pill.pause{border-color:rgba(240,180,41,.32)}
.pill.pause .dot{background:var(--amber)}
.pill.pause .txt{color:var(--amber)}
.pill.idle .dot{background:var(--blue)}
.pill.idle .txt{color:var(--blue)}
.pill.off .dot{background:var(--dim)}
.pill.off .txt{color:var(--sub)}

.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 17px 14px;
  transition:border-color .2s, transform .2s}
.card:hover{border-color:#2f3a4d;transform:translateY(-1px)}
.card .k{font-size:11.5px;color:var(--sub);letter-spacing:.3px}
.card .v{font-size:19px;font-weight:700;margin-top:4px;letter-spacing:.2px}
.card .s{font-size:11.5px;color:var(--dim);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.bar{margin-top:22px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.btn{border:1px solid var(--line);background:var(--card);color:var(--txt);border-radius:10px;
  padding:11px 20px;font-size:13.5px;font-weight:600;cursor:pointer;transition:.18s;
  font-family:inherit}
.btn:hover{background:var(--card2);border-color:#334054}
.btn:active{transform:translateY(1px)}
.btn[disabled]{opacity:.38;cursor:not-allowed;transform:none}
.btn.primary{background:linear-gradient(180deg,#46dda3,#2fbf85);color:#07231a;border-color:transparent;
  box-shadow:0 6px 18px -8px rgba(62,207,142,.75)}
.btn.primary:hover{background:linear-gradient(180deg,#52e9b0,#33cc8f)}
.btn.danger{color:#ff9a9d;border-color:rgba(242,89,94,.28)}
.btn.danger:hover{background:rgba(242,89,94,.1)}
.btn.ghost{background:transparent}
.spacer{flex:1}

.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.chip{font-size:11.5px;color:var(--sub);background:rgba(255,255,255,.03);border:1px solid var(--line);
  border-radius:999px;padding:5px 12px}
.chip b{color:var(--txt);font-weight:600}
.chip.warn{color:var(--amber);border-color:rgba(240,180,41,.25)}
.chip.ok{color:var(--green);border-color:rgba(62,207,142,.22)}

.panelbox{margin-top:22px;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.panelbox .head{display:flex;justify-content:space-between;align-items:center;
  padding:12px 17px;border-bottom:1px solid var(--line);font-size:12.5px;color:var(--sub)}
.panelbox .head b{color:var(--txt);font-size:12.5px}
pre#log{margin:0;padding:13px 17px;height:240px;overflow:auto;font:12px/1.75 Consolas,"Courier New",monospace;
  color:#b9c5d6;white-space:pre-wrap;word-break:break-all}
pre#log .ok{color:var(--green)}
pre#log .warn{color:var(--amber)}
pre#log .err{color:var(--red)}
pre#log .dim{color:var(--dim)}

footer{margin-top:20px;display:flex;gap:16px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--dim)}
footer a{color:var(--sub);text-decoration:none;cursor:pointer}
footer a:hover{color:var(--txt)}
.switch{display:inline-flex;align-items:center;gap:9px;cursor:pointer;user-select:none}
.track{width:40px;height:22px;border-radius:999px;background:#232b3a;border:1px solid var(--line);
  position:relative;transition:.2s}
.track i{position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;
  background:#6d798f;transition:.2s}
.switch.on .track{background:rgba(62,207,142,.25);border-color:rgba(62,207,142,.45)}
.switch.on .track i{left:20px;background:var(--green)}

#toast{position:fixed;left:50%;bottom:34px;transform:translate(-50%,20px);opacity:0;
  background:#1d2534;border:1px solid var(--line);color:var(--txt);padding:11px 20px;border-radius:12px;
  font-size:13px;box-shadow:0 18px 40px -18px #000;transition:.28s;pointer-events:none;max-width:80vw}
#toast.show{opacity:1;transform:translate(-50%,0)}
#toast.err{border-color:rgba(242,89,94,.45);color:#ffb3b5}
#toast.ok{border-color:rgba(62,207,142,.4);color:#a8f0d2}
@media (max-width:720px){
  .grid{grid-template-columns:repeat(2,1fr)}
  .wrap{padding:22px 16px 40px}
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>WorkBuddy 任务监控 <small>本地控制台</small></h1>
      <div class="sub">任务完成 · 异常终止 · 等待拍板 · 疑似卡住，自动推送到微信</div>
    </div>
    <div class="pill off" id="pill">
      <span class="dot"></span>
      <span class="txt" id="pillText">检测中…</span>
      <span class="meta" id="pillMeta"></span>
    </div>
  </header>

  <div class="grid">
    <div class="card"><div class="k">WorkBuddy</div><div class="v" id="vWb">—</div><div class="s" id="sWb">—</div></div>
    <div class="card"><div class="k">监控任务</div><div class="v" id="vTasks">—</div><div class="s" id="sTasks">上一轮扫描结果</div></div>
    <div class="card"><div class="k">今日推送</div><div class="v" id="vNotify">—</div><div class="s" id="sNotify">微信额度</div></div>
    <div class="card"><div class="k">最后巡检</div><div class="v" id="vPoll">—</div><div class="s" id="sPoll">—</div></div>
    <div class="card"><div class="k">运行时长</div><div class="v" id="vUp">—</div><div class="s" id="sUp">—</div></div>
    <div class="card"><div class="k">邮件兜底</div><div class="v" id="vMail">—</div><div class="s" id="sMail">—</div></div>
  </div>

  <div class="bar">
    <button class="btn primary" id="btnStart">开启监控</button>
    <button class="btn danger" id="btnStop">关闭监控</button>
    <button class="btn" id="btnPoll">立即巡检</button>
    <button class="btn" id="btnTest">发送测试通知</button>
    <span class="spacer"></span>
    <span class="sub" id="hint" style="font-size:12px"></span>
  </div>

  <div class="chips" id="chips"></div>

  <div class="panelbox">
    <div class="head"><b>最近动态</b><span id="updated">—</span></div>
    <pre id="log">加载中…</pre>
  </div>

  <footer>
    <a id="lnkConfig">打开配置文件</a>
    <a id="lnkLog">查看完整日志</a>
    <a id="lnkDir">打开程序目录</a>
    <span class="switch" id="swAuto"><span class="track"><i></i></span><span id="swText">开机自启</span></span>
    <span class="spacer"></span>
    <a id="lnkQuit">关闭面板</a>
  </footer>
</div>
<div id="toast"></div>

<script>
const $ = id => document.getElementById(id);
let busy = false;

function toast(msg, kind){
  const t = $('toast');
  t.textContent = msg;
  t.className = 'show' + (kind ? ' ' + kind : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.className = ''; }, 3600);
}

function esc(s){
  return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

function ago(ms){
  if(!ms) return '—';
  const sec = Math.max(0, (Date.now() - ms) / 1000);
  if(sec < 60) return Math.round(sec) + ' 秒前';
  if(sec < 3600) return Math.floor(sec/60) + ' 分钟前';
  if(sec < 86400) return Math.floor(sec/3600) + ' 小时前';
  return Math.floor(sec/86400) + ' 天前';
}

function dur(ms){
  if(!ms) return '—';
  const sec = Math.max(0, (Date.now() - ms)/1000);
  const h = Math.floor(sec/3600), m = Math.floor(sec%3600/60);
  if(h) return h + ' 小时 ' + m + ' 分';
  if(m) return m + ' 分钟';
  return Math.round(sec) + ' 秒';
}

function hhmmss(ms){
  if(!ms) return '—';
  const d = new Date(ms);
  const p = n => String(n).padStart(2,'0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

function render(d){
  const pill = $('pill'), txt = $('pillText'), meta = $('pillMeta');
  pill.className = 'pill';
  let label, cls, sub;
  if(!d.running){ label='未运行'; cls='off'; sub='监控程序未启动'; }
  else if(d.paused){ label='已暂停'; cls='pause'; sub='不巡检 · 不推送'; }
  else if(d.workbuddy){ label='运行中'; cls='on'; sub='正在巡检'; }
  else { label='待机中'; cls='idle'; sub='等待 WorkBuddy 启动'; }
  pill.classList.add(cls);
  txt.textContent = label;
  meta.textContent = d.pid ? 'PID ' + d.pid : '';

  // WorkBuddy
  $('vWb').textContent = d.running ? (d.workbuddy ? '运行中' : '未运行') : '未知';
  $('vWb').style.color = d.running && d.workbuddy ? 'var(--green)' : (d.running ? 'var(--sub)' : 'var(--dim)');
  $('sWb').textContent = d.running ? (d.workbuddy ? '任务状态实时可见' : '打开后自动开始巡检') : '监控程序未运行';

  // 任务数
  $('vTasks').textContent = (d.running && !d.paused && d.monitored != null) ? d.monitored + ' 个' : '—';
  $('sTasks').textContent = d.paused ? '暂停中' : (d.monitored != null ? '上一轮扫描结果' : '暂无数据');

  // 推送额度
  const used = d.notify.used, limit = d.notify.limit;
  $('vNotify').textContent = used + ' / ' + limit;
  $('vNotify').style.color = used >= limit ? 'var(--amber)' : 'var(--txt)';
  $('sNotify').textContent = d.notify.mail ? ('邮件已兜底 ' + d.notify.mail + ' 条') : '微信剩余 ' + Math.max(0, limit-used) + ' 条';

  // 最后巡检
  $('vPoll').textContent = d.running && d.last_poll_at ? hhmmss(d.last_poll_at) : '—';
  $('sPoll').textContent = d.running && d.last_poll_at ? ago(d.last_poll_at) : (d.running ? '尚未巡检' : '监控程序未运行');

  // 运行时长
  $('vUp').textContent = d.running ? dur(d.started_at) : '—';
  $('sUp').textContent = d.running ? ('心跳 ' + (d.heart_age != null ? Math.round(d.heart_age) + ' 秒前' : '—')) : '进程未运行';

  // 邮件兜底
  $('vMail').textContent = d.config.mail_ready ? '已就绪' : '未配置';
  $('vMail').style.color = d.config.mail_ready ? 'var(--green)' : 'var(--dim)';
  $('sMail').textContent = d.config.mail_ready ? d.config.mail_to : '仅微信通道';

  // 按钮状态
  $('btnStart').disabled = busy || (d.running && !d.paused);
  $('btnStop').disabled = busy || !d.running || d.paused;
  $('btnPoll').disabled = busy || !d.running || d.paused || !d.workbuddy;
  $('btnTest').disabled = busy || !d.running || d.paused;

  // 提示
  $('hint').textContent = !d.running ? '监控未运行，点「开启监控」' :
    (d.paused ? '已暂停：不读任务、不发通知' :
    (!d.workbuddy ? 'WorkBuddy 未启动，程序在待机' : '每 ' + d.config.poll + ' 秒自动巡检'));

  // 参数胶囊
  const chips = [];
  chips.push('<span class="chip">巡检间隔 <b>' + d.config.poll + ' 秒</b></span>');
  chips.push('<span class="chip">卡住阈值 <b>' + d.config.stuck + ' 分钟</b></span>');
  chips.push('<span class="chip">每日上限 <b>' + d.config.limit + ' 条</b></span>');
  chips.push(d.config.sendkey_ready
    ? '<span class="chip ok">SendKey 已配置</span>'
    : '<span class="chip warn">未填 SendKey</span>');
  chips.push(d.config.mail_ready
    ? '<span class="chip ok">邮件兜底 已就绪</span>'
    : '<span class="chip warn">邮件兜底 未配置</span>');
  if(!d.config.db_ok) chips.push('<span class="chip warn">数据库路径未找到</span>');
  $('chips').innerHTML = chips.join('');

  // 开机自启
  const sw = $('swAuto');
  if(d.autostart === true){ sw.classList.add('on'); $('swText').textContent = '开机自启 已开启'; }
  else if(d.autostart === false){ sw.classList.remove('on'); $('swText').textContent = '开机自启 未开启'; }
  else { sw.classList.remove('on'); $('swText').textContent = '开机自启 未知'; }

  // 日志
  const pre = $('log');
  const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  pre.innerHTML = d.log.length
    ? d.log.map(line => {
        let cls = '';
        if(/推送成功|邮件推送成功|已完成|开始巡检|监控已恢复/.test(line)) cls = 'ok';
        else if(/异常|失败|Traceback|error/i.test(line)) cls = 'err';
        else if(/暂停|待机|未检测到 WorkBuddy/.test(line)) cls = 'warn';
        return '<div class="' + cls + '">' + esc(line) + '</div>';
      }).join('')
    : '<div class="dim">（暂无日志，监控程序尚未运行过）</div>';
  if(atBottom) pre.scrollTop = pre.scrollHeight;

  $('updated').textContent = '刷新于 ' + hhmmss(Date.now()) + ' · 每 2 秒自动更新';
}

async function tick(){
  try{
    const r = await fetch('/api/state', {cache:'no-store'});
    render(await r.json());
  }catch(e){
    $('pillText').textContent = '面板已断开';
    $('pill').className = 'pill off';
  }
}

async function post(path, body){
  const r = await fetch(path, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body || {})
  });
  return r.json();
}

async function act(path, body){
  if(busy) return;
  busy = true;
  document.querySelectorAll('.btn').forEach(b => b.disabled = true);
  try{
    const d = await post(path, body);
    if(d.ok) toast(d.msg || '完成', 'ok');
    else if(d.need_config){
      toast(d.error || '请先配置 SendKey', 'err');
      await post('/api/open', {target:'config'});
    } else {
      toast(d.error || '操作失败', 'err');
    }
  }catch(e){
    toast('请求失败：' + e, 'err');
  }finally{
    busy = false;
    await tick();
  }
}

$('btnStart').onclick = () => act('/api/start');
$('btnStop').onclick = () => act('/api/stop');
$('btnPoll').onclick = () => act('/api/poll');
$('btnTest').onclick = () => { toast('正在发送测试通知…'); act('/api/test'); };

$('lnkConfig').onclick = async () => { await post('/api/open', {target:'config'}); toast('已打开配置文件'); };
$('lnkLog').onclick = async () => { const d = await post('/api/open', {target:'log'}); if(d.ok) toast('已打开日志'); else toast(d.error, 'err'); };
$('lnkDir').onclick = async () => { await post('/api/open', {target:'dir'}); toast('已打开程序目录'); };

$('swAuto').onclick = async () => {
  const on = !$('swAuto').classList.contains('on');
  $('swText').textContent = '处理中…';
  await act('/api/autostart', {on: on});
};

$('lnkQuit').onclick = async () => {
  if(!confirm('关闭面板？（监控程序会继续在后台运行）')) return;
  await post('/api/shutdown');
  document.body.innerHTML = '<div style="padding:60px;text-align:center;color:#8b97ac;font:15px/1.8 Microsoft YaHei UI,sans-serif">' +
    '面板已关闭，可以关闭这个标签页了。<br>监控程序仍在后台正常运行。</div>';
};

setInterval(tick, 2000);
tick();
</script>
</body>
</html>
"""


def main():
    global _last_ping

    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # 端口被占用：说明面板已在运行，直接把浏览器叫起来
        webbrowser.open("http://127.0.0.1:%d/" % PORT)
        return

    server.daemon_threads = True

    def idle_watch():
        # 浏览器标签页关闭后不再有心跳，2 分钟自动退出，不留后台进程
        while True:
            time.sleep(10)
            if time.time() - _last_ping[0] > 120:
                server.shutdown()
                return

    threading.Thread(target=idle_watch, daemon=True).start()

    webbrowser.open("http://127.0.0.1:%d/" % PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
