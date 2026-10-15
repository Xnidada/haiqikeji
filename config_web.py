#!/usr/bin/env python3
"""Local web UI for editing config.py and running main.py."""

from __future__ import annotations

import ast
import json
import pprint
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.py"
MAIN_PATH = ROOT / "main.py"
START_MARKER = "# === FRONTEND CONFIG START ==="
END_MARKER = "# === FRONTEND CONFIG END ==="

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
<title>海奇科技配置控制台</title>
<style>
:root{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#172033;background:#f5f7fb}
body{max-width:1000px;margin:32px auto;padding:0 16px}h1{font-size:26px;margin-bottom:8px}.hint{color:#667085}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 2px 8px #1720330d}
label{display:block;font-weight:600;margin:12px 0 6px}input,textarea{box-sizing:border-box;width:100%;font:inherit;border:1px solid #d0d5dd;border-radius:8px;padding:9px}textarea{min-height:130px;resize:vertical;font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}button{border:0;border-radius:8px;padding:10px 16px;cursor:pointer;font-weight:600}button.primary{background:#2563eb;color:#fff}button.danger{background:#dc2626;color:#fff}button.secondary{background:#e5e7eb;color:#172033}#message{min-height:22px;margin-top:10px}.ok{color:#15803d}.error{color:#b91c1c}pre{background:#101828;color:#d1fadf;border-radius:8px;padding:14px;min-height:220px;max-height:480px;overflow:auto;white-space:pre-wrap;word-break:break-word}
@media(max-width:700px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>海奇科技配置控制台</h1>
<p class="hint">填写配置后保存；启动按钮会运行当前目录的 <code>main.py</code>。</p>
<div class="card">
  <div class="grid">
    <div><label for="apiBase">API_BASE</label><input id="apiBase"></div>
    <div><label for="schoolId">SCHOOL_ID</label><input id="schoolId" type="number"></div>
    <div><label for="maxWorkers">MAX_WORKERS</label><input id="maxWorkers" type="number" min="1"></div>
  </div>
  <label for="headers">HEADERS（JSON 对象）</label>
  <textarea id="headers" spellcheck="false"></textarea>
  <label for="accounts">ACCOUNTS（JSON 数组）</label>
  <textarea id="accounts" spellcheck="false" placeholder='[{"number":"账号","password":"密码","course_ids":[1012365]}]'></textarea>
  <div class="actions">
    <button class="primary" onclick="saveConfig()">保存配置</button>
    <button class="primary" onclick="startProgram()">启动程序</button>
    <button class="danger" onclick="stopProgram()">停止程序</button>
  </div>
  <div id="message"></div>
</div>
<div class="card"><strong>运行日志</strong><pre id="logs">正在加载...</pre></div>
<script>
const $ = id => document.getElementById(id);
function message(text, ok=false){$('message').textContent=text;$('message').className=ok?'ok':'error'}
function fill(c){$('apiBase').value=c.API_BASE||'';$('schoolId').value=c.SCHOOL_ID??'';$('maxWorkers').value=c.MAX_WORKERS??'';$('headers').value=JSON.stringify(c.HEADERS||{},null,2);$('accounts').value=JSON.stringify(c.ACCOUNTS||[],null,2)}
async function loadConfig(){const r=await fetch('/api/config');const d=await r.json();if(!r.ok)throw Error(d.error);fill(d)}
function values(){let headers,accounts;try{headers=JSON.parse($('headers').value);accounts=JSON.parse($('accounts').value)}catch(e){throw Error('HEADERS 或 ACCOUNTS JSON 格式不正确')}return {API_BASE:$('apiBase').value,SCHOOL_ID:Number($('schoolId').value),MAX_WORKERS:Number($('maxWorkers').value),HEADERS:headers,ACCOUNTS:accounts}}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d}
async function saveConfig(){try{await post('/api/config',values());message('配置已保存',true)}catch(e){message(e.message)}}
async function startProgram(){try{await post('/api/config',values());await post('/api/start');message('配置已保存，程序已启动',true)}catch(e){message(e.message)}}
async function stopProgram(){try{await post('/api/stop');message('已发送停止请求',true)}catch(e){message(e.message)}}
async function refreshLogs(){try{const r=await fetch('/api/logs');const d=await r.json();$('logs').textContent=d.logs||'(暂无日志)';$('logs').scrollTop=$('logs').scrollHeight}catch(e){$('logs').textContent='日志连接失败：'+e.message}}
loadConfig().catch(e=>message(e.message));refreshLogs();setInterval(refreshLogs,1000);
</script>
</body></html>"""


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
                self._send(200, _read_config())
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
    parser.add_argument("--port", type=int, default=8000)
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
