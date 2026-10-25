"""本机时间线网页 http://127.0.0.1:7788/ ：今日 / 项目 / 觉察 / 搜索。只读，只监听本机。
接口直接复用 MCP 的函数，网页看到的和 Agent 读到的是同一份。"""
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import mcp_server as M
from .config import settings
from .db import ro

WEB = Path(__file__).resolve().parent.parent / "web"


def event_full(eid):
    """网页用：带每句话在哪个录音文件、第几秒，能点着听"""
    con = ro()
    e = json.loads(M.get_record(f"E{eid}", include_transcript=False))
    pieces = {}
    lines = []
    for s in con.execute("SELECT * FROM sentences WHERE event_id=? ORDER BY start_ms", (eid,)):
        pid = s["piece_id"]
        if pid not in pieces:
            p = con.execute("SELECT timemap, audio_path FROM pieces WHERE id=?", (pid,)).fetchone()
            pieces[pid] = (json.loads(p["timemap"]), bool(p["audio_path"] and os.path.exists(p["audio_path"])))
        tm, has_audio = pieces[pid]
        off = 0
        for o, a, d in tm:
            if a <= s["start_ms"]:
                off = o + (s["start_ms"] - a)
        lines.append({"t": M._t(s["start_ms"])[11:], "speaker": s["speaker"], "source": s["source"], "text": s["text"],
                      "piece": pid if has_audio else None, "offset": off / 1000})
    e["lines"] = lines
    return e


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/day":
                d = q.get("d", "today")
                return self._send(200, json.dumps({"records": json.loads(M.list_records(d)), "summary": json.loads(M.day_summary(d)),
                                                   "status": json.loads(M.recorder_status())}, ensure_ascii=False))
            if u.path == "/api/event":
                return self._send(200, json.dumps(event_full(int(q["id"])), ensure_ascii=False))
            if u.path == "/api/transcript":
                return self._send(200, M.get_transcript(q.get("d", "today")))
            if u.path == "/api/projects":
                return self._send(200, M.list_projects())
            if u.path == "/api/project":
                return self._send(200, M.get_project(q["id"]))
            if u.path == "/api/insights":
                return self._send(200, M.insights())
            if u.path == "/api/search":
                return self._send(200, M.search(q.get("q", "")))
            if u.path == "/api/audio":
                return self._audio(int(q["piece"]))
        except Exception as e:  # 网页别崩
            return self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
        self._send(404, "{}")

    def _audio(self, pid):
        r = ro().execute("SELECT audio_path FROM pieces WHERE id=?", (pid,)).fetchone()
        if not r or not r["audio_path"] or not os.path.exists(r["audio_path"]):
            return self._send(404, "{}")
        data = Path(r["audio_path"]).read_bytes()
        ctype = mimetypes.guess_type(r["audio_path"])[0] or "audio/mpeg"
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            a = int(a or 0)
            b = int(b) if b else len(data) - 1
            part = data[a:b + 1]
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {a}-{a + len(part) - 1}/{len(data)}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(part)))
            self.end_headers()
            self.wfile.write(part)
            return
        self._send(200, data, ctype, {"Accept-Ranges": "bytes"})


def main():
    port = settings()["web_port"]
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
