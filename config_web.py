#!/usr/bin/env python3
"""Local web UI for editing config.py and running main.py."""

from __future__ import annotations

import ast
import json
import os
import pprint
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.py"
MAIN_PATH = ROOT / "main.py"
START_MARKER = "# === FRONTEND CONFIG START ==="
END_MARKER = "# === FRONTEND CONFIG END ==="
SECRET_KEY_PATH = ROOT / "config.secret.key"

_process: subprocess.Popen[str] | None = None
_process_lock = threading.Lock()
_log_lines: list[str] = []
_log_lock = threading.Lock()
_MAX_LOG_LINES = 2000


def _read_config() -> dict[str, Any]:
    tree = ast.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    values: dict[str, Any] = {}
    wanted = {"API_BASE", "HEADERS", "SCHOOL_ID", "ACCOUNTS", "MAX_WORKERS"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in wanted:
                values[target.id] = ast.literal_eval(node.value)
    return values


def _public_config() -> dict[str, Any]:
    values = _read_config()
    accounts = []
    for account in values.get("ACCOUNTS", []):
        accounts.append(
            {
                "number": account.get("number", ""),
                "course_ids": account.get("course_ids", []),
                "password_configured": bool(account.get("password_encrypted") or account.get("password")),
            }
        )
    values["ACCOUNTS"] = accounts
    return values


def _get_fernet() -> Fernet:
    if not SECRET_KEY_PATH.exists():
        key = Fernet.generate_key()
        try:
            fd = os.open(SECRET_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as key_file:
                key_file.write(key)
    SECRET_KEY_PATH.chmod(0o600)
    return Fernet(SECRET_KEY_PATH.read_bytes().strip())


def _encrypt_password(password: str) -> str:
    return _get_fernet().encrypt(password.encode()).decode() if password else ""


def _validate_config(values: dict[str, Any]) -> dict[str, Any]:
    required = {"API_BASE", "HEADERS", "SCHOOL_ID", "ACCOUNTS", "MAX_WORKERS"}
    if set(values) != required:
        raise ValueError("配置字段不完整")
    if not isinstance(values["API_BASE"], str) or not values["API_BASE"].strip():
        raise ValueError("API_BASE 不能为空")
    if not isinstance(values["SCHOOL_ID"], int) or isinstance(values["SCHOOL_ID"], bool):
        raise ValueError("SCHOOL_ID 必须是整数")
    if not isinstance(values["MAX_WORKERS"], int) or isinstance(values["MAX_WORKERS"], bool) or values["MAX_WORKERS"] < 1:
        raise ValueError("MAX_WORKERS 必须是大于 0 的整数")
    if not isinstance(values["HEADERS"], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values["HEADERS"].items()):
        raise ValueError("HEADERS 必须是字符串键值对象")
    if not isinstance(values["ACCOUNTS"], list):
        raise ValueError("ACCOUNTS 必须是数组")
    for index, account in enumerate(values["ACCOUNTS"], 1):
        if not isinstance(account, dict):
            raise ValueError(f"第 {index} 个账号必须是对象")
        if not isinstance(account.get("number"), str) or not isinstance(account.get("password"), str):
            raise ValueError(f"第 {index} 个账号的 number/password 必须是字符串")
        course_ids = account.get("course_ids", [])
        if not isinstance(course_ids, list) or any(not isinstance(cid, int) or isinstance(cid, bool) for cid in course_ids):
            raise ValueError(f"第 {index} 个账号的 course_ids 必须是整数数组")
    return values


def _save_config(values: dict[str, Any]) -> None:
    values = _validate_config(values)
    current_accounts = _read_config().get("ACCOUNTS", [])
    old_by_number = {account.get("number"): account for account in current_accounts}
    accounts = []
    for account in values["ACCOUNTS"]:
        old_account = old_by_number.get(account["number"], {})
        password = account.get("password", "")
        encrypted_password = _encrypt_password(password)
        if not encrypted_password:
            encrypted_password = old_account.get("password_encrypted", "")
            if not encrypted_password and old_account.get("password"):
                encrypted_password = _encrypt_password(old_account["password"])
        accounts.append(
            {
                "number": account["number"],
                "password_encrypted": encrypted_password,
                "course_ids": account.get("course_ids", []),
            }
        )
    values["ACCOUNTS"] = accounts
    source = CONFIG_PATH.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.S)
    if not pattern.search(source):
        raise ValueError("config.py 缺少前端配置标记")
    block = "\n".join(
        [
            START_MARKER,
            f"API_BASE = {values['API_BASE']!r}",
            "",
            f"HEADERS = {pprint.pformat(values['HEADERS'], width=100, sort_dicts=False)}",
            "",
            "# 学校ID",
            f"SCHOOL_ID = {values['SCHOOL_ID']}",
            "",
            "# 账号列表（支持多个账号同时刷课）",
            f"ACCOUNTS = {pprint.pformat(values['ACCOUNTS'], width=100, sort_dicts=False)}",
            "",
            "# 线程数（每个课程的并发线程数）",
            f"MAX_WORKERS = {values['MAX_WORKERS']}",
            END_MARKER,
        ]
    )
    CONFIG_PATH.write_text(pattern.sub(block, source, count=1), encoding="utf-8")


def _append_log(text: str) -> None:
    with _log_lock:
        _log_lines.extend(text.splitlines())
        del _log_lines[:-_MAX_LOG_LINES]


def _read_process_output(process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        _append_log(line)
    process.wait()
    _append_log(f"\n[进程已退出，退出码：{process.returncode}]\n")


def _is_running() -> bool:
    return _process is not None and _process.poll() is None


def _start_process() -> None:
    global _process
    with _process_lock:
        if _is_running():
            raise RuntimeError("程序已经在运行")
        with _log_lock:
            _log_lines.clear()
        _process = subprocess.Popen(
            [sys.executable, "-u", str(MAIN_PATH)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=_read_process_output, args=(_process,), daemon=True).start()


def _stop_process() -> None:
    with _process_lock:
        if not _is_running():
            return
        assert _process is not None
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>海奇科技 · 控制台</title>
<style>
:root{
  color-scheme:light;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  color:#172033;
  background:#f3f6fb;
  -webkit-font-smoothing:antialiased;
  --ink:#172033;
  --muted:#667085;
  --faint:#98a2b3;
  --line:#e4e7ec;
  --surface:#ffffff;
  --surface-soft:#f8fafc;
  --accent:#2f6fed;
  --accent-strong:#2158c8;
  --danger:#c4324b;
  --shadow:0 18px 48px rgba(16,24,40,.08),0 2px 8px rgba(16,24,40,.04);
}
*{box-sizing:border-box}
body{margin:0;min-width:320px;background:radial-gradient(circle at 8% 0%,#eaf2ff 0,transparent 34rem),var(--surface-soft);color:var(--ink)}
button,input,textarea{font:inherit}
button{min-height:44px;border:0;border-radius:12px;padding:0 16px;cursor:pointer;font-weight:700;transition-property:transform,background-color,border-color,box-shadow,color,opacity;transition-duration:160ms;transition-timing-function:cubic-bezier(.2,0,0,1)}
button:active{transform:scale(.96)}
button:focus-visible{outline:3px solid rgba(47,111,237,.28);outline-offset:2px}
button:disabled{cursor:wait;opacity:.62}
input,textarea{width:100%;border:1px solid #d0d5dd;border-radius:12px;background:#fff;color:var(--ink);padding:11px 13px;outline:0;transition-property:border-color,box-shadow,background-color;transition-duration:160ms;transition-timing-function:cubic-bezier(.2,0,0,1)}
input{min-height:44px}
textarea{min-height:148px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;line-height:1.55}
input::placeholder,textarea::placeholder{color:#98a2b3}
input:focus,textarea:focus{border-color:#78a4f5;box-shadow:0 0 0 4px rgba(47,111,237,.13);background:#fcfdff}
.shell{max-width:1160px;margin:0 auto;padding:40px 22px 32px}
.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:26px}
.eyebrow,.section-kicker{margin:0;color:var(--accent);font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}
h1{max-width:680px;margin:8px 0 8px;font-size:clamp(28px,4vw,42px);line-height:1.08;letter-spacing:-.035em;text-wrap:balance}
.subtitle{max-width:680px;margin:0;color:var(--muted);font-size:15px;line-height:1.65;text-wrap:pretty}
.status-pill{display:flex;align-items:center;gap:9px;min-height:40px;padding:0 13px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.8);color:var(--muted);font-size:13px;font-weight:700;white-space:nowrap;box-shadow:0 2px 8px rgba(16,24,40,.04)}
.status-dot{width:8px;height:8px;border-radius:50%;background:#98a2b3;box-shadow:0 0 0 4px rgba(152,162,179,.16)}
.status-pill.running{border-color:#b7e4c7;color:#167447;background:#f3fcf6}.status-pill.running .status-dot{background:#17a673;box-shadow:0 0 0 4px rgba(23,166,115,.14)}
.status-pill.error{border-color:#f3c3cb;color:var(--danger);background:#fff7f8}.status-pill.error .status-dot{background:#d92d49;box-shadow:0 0 0 4px rgba(217,45,73,.14)}
.layout{display:grid;grid-template-columns:minmax(0,1.18fr) minmax(340px,.82fr);gap:20px;align-items:start}
.card{min-width:0;border:1px solid rgba(228,231,236,.84);border-radius:24px;background:rgba(255,255,255,.94);padding:24px;box-shadow:var(--shadow)}
.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:20px}.section-kicker{color:var(--faint);font-size:10px}
h2{margin:5px 0 5px;font-size:19px;line-height:1.25;letter-spacing:-.015em}h3{margin:0;font-size:14px} .section-copy{margin:0;color:var(--muted);font-size:13px;line-height:1.55;text-wrap:pretty}
.field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.field{min-width:0}.field-wide{grid-column:1/-1}
label{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin:0 0 7px;font-size:13px;font-weight:750}.field-help{color:var(--faint);font-size:11px;font-weight:500}
.section-divider{height:1px;margin:26px 0;background:var(--line)}
.account-list{display:grid;gap:12px}.account-row{border:1px solid var(--line);border-radius:16px;background:var(--surface-soft);padding:16px;box-shadow:inset 0 1px 0 rgba(255,255,255,.8)}
.account-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1.2fr) auto;gap:12px;align-items:end}.account-row label{margin-top:0}.password-hint{min-height:18px;margin-top:5px;color:var(--muted);font-size:11px;line-height:1.4}.remove-account{background:#fff0f2;color:#a1293d}.remove-account:hover{background:#ffe3e7}.empty-state{border:1px dashed #cfd7e5;border-radius:16px;padding:20px;text-align:center;color:var(--muted);font-size:13px;background:#fbfcfe}
.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:18px}.actions-secondary{margin-top:12px}.primary{background:var(--accent);color:#fff;box-shadow:0 7px 16px rgba(47,111,237,.22)}.primary:hover{background:var(--accent-strong);box-shadow:0 9px 20px rgba(47,111,237,.27)}.secondary{background:#eef2f7;color:#344054}.secondary:hover{background:#e4e9f1}.danger{background:#fff0f2;color:#a1293d}.danger:hover{background:#ffe3e7}.ghost{background:transparent;color:var(--muted);padding-inline:10px}.ghost:hover{background:#f2f4f7;color:var(--ink)}
#message{min-height:22px;margin:12px 0 0;color:var(--muted);font-size:13px}#message.ok{color:#167447}#message.error{color:var(--danger)}
.logs-card{position:sticky;top:20px}.log-tools{display:flex;align-items:center;gap:8px;color:var(--faint);font-size:11px;font-variant-numeric:tabular-nums}.log-tools .log-count{padding:4px 8px;border-radius:999px;background:#f2f4f7;color:var(--muted)}pre{min-height:520px;max-height:calc(100vh - 220px);margin:0;border-radius:16px;background:#111827;color:#d1fadf;padding:16px;overflow:auto;white-space:pre-wrap;word-break:break-word;font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums;box-shadow:inset 0 1px 2px rgba(0,0,0,.25)}
.footer{margin-top:16px;color:var(--faint);font-size:12px;text-align:center}
@media(max-width:900px){.layout{grid-template-columns:1fr}.logs-card{position:static}.log-tools{margin-top:2px}}
@media(max-width:680px){.shell{padding:24px 14px}.topbar{display:block}.status-pill{display:inline-flex;margin-top:18px}.card{padding:18px;border-radius:20px}.field-grid,.account-grid{grid-template-columns:1fr}pre{min-height:320px;max-height:55vh}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition-duration:0ms!important}}
</style>
</head>
<body>
<main class="shell">
  <header class="topbar">
    <div>
      <p class="eyebrow">Local control console</p>
      <h1>海奇科技配置控制台</h1>
      <p class="subtitle">集中管理运行参数、账号和课程，并在同一个页面启动任务与查看日志。</p>
    </div>
    <div id="serviceStatus" class="status-pill" aria-live="polite"><span class="status-dot"></span><span id="serviceStatusText">检查中...</span></div>
  </header>

  <div class="layout">
    <section class="card" aria-labelledby="config-title">
      <div class="card-head">
        <div><p class="section-kicker">01 · Runtime</p><h2 id="config-title">运行配置</h2><p class="section-copy">修改后点击保存；启动按钮会先保存当前表单，再运行 <code>main.py</code>。</p></div>
      </div>
      <div class="field-grid">
        <div class="field field-wide"><label for="apiBase">API 地址<span class="field-help">API_BASE</span></label><input id="apiBase" autocomplete="url"></div>
        <div class="field"><label for="schoolId">学校 ID<span class="field-help">SCHOOL_ID</span></label><input id="schoolId" type="number" inputmode="numeric" min="0"></div>
        <div class="field"><label for="maxWorkers">并发线程数<span class="field-help">MAX_WORKERS</span></label><input id="maxWorkers" type="number" inputmode="numeric" min="1"></div>
        <div class="field field-wide"><label for="headers">请求头<span class="field-help">HEADERS · JSON</span></label><textarea id="headers" spellcheck="false"></textarea></div>
      </div>

      <div class="section-divider"></div>
      <div class="card-head">
        <div><p class="section-kicker">02 · Accounts</p><h2>账号与课程</h2><p class="section-copy">密码只写入本地密文，刷新页面不会再次显示；留空密码会保留原密码。</p></div>
        <button class="secondary" type="button" onclick="addAccount()">添加账号</button>
      </div>
      <div id="accounts" class="account-list"></div>
      <div class="actions">
        <button class="primary" type="button" onclick="saveConfig()">保存配置</button>
        <button class="primary" type="button" onclick="startProgram()">启动程序</button>
        <button class="danger" type="button" onclick="stopProgram()">停止程序</button>
      </div>
      <div id="message" role="status" aria-live="polite"></div>
    </section>

    <section class="card logs-card" aria-labelledby="logs-title">
      <div class="card-head">
        <div><p class="section-kicker">03 · Output</p><h2 id="logs-title">运行日志</h2><p class="section-copy">日志每秒刷新一次，最新内容会自动滚动到末尾。</p></div>
        <div class="log-tools"><span id="logCount" class="log-count">0 行</span><button class="ghost" type="button" onclick="copyLogs()">复制</button></div>
      </div>
      <pre id="logs" aria-live="polite">正在加载...</pre>
    </section>
  </div>
  <p class="footer">服务监听 0.0.0.0 · 配置保存在当前项目目录</p>
</main>
<script>
const $ = id => document.getElementById(id);
function message(text, ok=false){$('message').textContent=text;$('message').className=ok?'ok':'error'}
function syncEmptyState(){if(!$('accounts').querySelector('.account-row'))$('accounts').innerHTML='<div class="empty-state">暂未添加账号，点击右上角“添加账号”开始配置。</div>'}
function addAccount(account={}){const empty=$('accounts').querySelector('.empty-state');if(empty)empty.remove();const row=document.createElement('div');row.className='account-row';row.innerHTML='<div class="account-grid"><div><label>账号</label><input class="account-number" autocomplete="username"></div><div><label>密码</label><input class="account-password" type="password" autocomplete="new-password"><div class="password-hint"></div></div><div><label>course_ids<span class="field-help">逗号分隔</span></label><input class="account-courses" placeholder="1012365, 1012305"></div><button class="remove-account" type="button">删除</button></div>';row.querySelector('.account-number').value=account.number||'';row.querySelector('.account-courses').value=(account.course_ids||[]).join(', ');row.querySelector('.password-hint').textContent=account.password_configured?'已设置，留空保持原密码':'未设置，保存时填写密码';row.querySelector('.remove-account').onclick=()=>{row.remove();syncEmptyState()};$('accounts').appendChild(row)}
function fill(c){$('apiBase').value=c.API_BASE||'';$('schoolId').value=c.SCHOOL_ID??'';$('maxWorkers').value=c.MAX_WORKERS??'';$('headers').value=JSON.stringify(c.HEADERS||{},null,2);$('accounts').innerHTML='';(c.ACCOUNTS||[]).forEach(addAccount);syncEmptyState()}
async function loadConfig(){const r=await fetch('/api/config');const d=await r.json();if(!r.ok)throw Error(d.error);fill(d)}
function values(){let headers;try{headers=JSON.parse($('headers').value)}catch(e){throw Error('HEADERS JSON 格式不正确')}const rows=[...document.querySelectorAll('.account-row')];const accounts=rows.map((row,index)=>{const courseText=row.querySelector('.account-courses').value.trim();const courseIds=courseText?courseText.split(/[，,\s]+/).filter(Boolean).map(Number):[];if(courseIds.some(id=>!Number.isInteger(id)))throw Error('第 '+(index+1)+' 个账号的 course_ids 必须是整数');return {number:row.querySelector('.account-number').value,password:row.querySelector('.account-password').value,course_ids:courseIds}});return {API_BASE:$('apiBase').value,SCHOOL_ID:Number($('schoolId').value),MAX_WORKERS:Number($('maxWorkers').value),HEADERS:headers,ACCOUNTS:accounts}}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d}
function setBusy(busy){document.querySelectorAll('button').forEach(button=>{button.disabled=busy})}
async function saveConfig(){try{setBusy(true);await post('/api/config',values());message('配置已保存',true)}catch(e){message(e.message)}finally{setBusy(false)}}
async function startProgram(){try{setBusy(true);await post('/api/config',values());await post('/api/start');message('配置已保存，程序已启动',true)}catch(e){message(e.message)}finally{setBusy(false)}}
async function stopProgram(){try{setBusy(true);await post('/api/stop');message('已发送停止请求',true)}catch(e){message(e.message)}finally{setBusy(false)}}
async function copyLogs(){try{await navigator.clipboard.writeText($('logs').textContent);message('日志已复制',true)}catch(e){message('当前浏览器不支持复制日志')}}
function setStatus(running){const pill=$('serviceStatus');pill.className='status-pill '+(running?'running':'');$('serviceStatusText').textContent=running?'程序运行中':'程序已停止'}
async function refreshLogs(){try{const r=await fetch('/api/logs');const d=await r.json();if(!r.ok)throw Error(d.error);const logs=d.logs||'';$('logs').textContent=logs||'(暂无日志)';$('logCount').textContent=(logs?logs.split('\n').length:0)+' 行';$('logs').scrollTop=$('logs').scrollHeight;setStatus(d.running)}catch(e){$('logs').textContent='日志连接失败：'+e.message;$('logCount').textContent='连接失败';$('serviceStatus').className='status-pill error';$('serviceStatusText').textContent='服务不可用'}}
loadConfig().catch(e=>message(e.message));refreshLogs();setInterval(refreshLogs,1000);
</script>
</body>
</html>"""



class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else (json.dumps(payload, ensure_ascii=False).encode("utf-8") if content_type == "application/json" else payload.encode("utf-8"))
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._send(200, HTML, "text/html")
            elif path == "/api/config":
                self._send(200, _public_config())
            elif path == "/api/logs":
                with _log_lock:
                    logs = "\n".join(_log_lines)
                self._send(200, {"running": _is_running(), "logs": logs})
            else:
                self._send(404, {"error": "页面不存在"})
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/config":
                _save_config(self._json_body())
                self._send(200, {"ok": True})
            elif path == "/api/start":
                _start_process()
                self._send(200, {"ok": True})
            elif path == "/api/stop":
                _stop_process()
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "接口不存在"})
        except (ValueError, json.JSONDecodeError, RuntimeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="启动本地配置与运行控制台")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11181)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"配置控制台：http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop_process()
        server.server_close()


if __name__ == "__main__":
    main()
