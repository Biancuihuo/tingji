"""听记 MCP：让 Claude、Codex 读全时记、随口记、逐句原文、项目、觉察。只读。
注册名 tingji；工具对齐心力球官方 MCP（全时记、随口记、按 ID 取、搜索、画像与记忆），另外给原文和处理进度。
"""
import json
from datetime import datetime, timedelta

try:  # mcp 2.x 把 FastMCP 改名成 MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

from .config import TZ
from .db import kv_get, now_ms, ro

mcp = FastMCP("tingji", instructions="听记：这台 Mac 全天录下的谈话。全时记 = 自动切出来的一件件事（E 开头），随口记 = 按快捷键说的灵感/提问/待办（N 开头），项目 = 跨天的同一件事（P 开头）。要原话用 get_transcript 或 get_record 带原文。时间都是北京时间。")


def _ms(s, end=False):
    """'2026-10-25' / '2026-10-25 14:30' / 'today' / 'yesterday' → 毫秒"""
    s = (s or "").strip()
    now = datetime.now(TZ)
    if s in ("", "today", "今天"):
        d = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif s in ("yesterday", "昨天"):
        d = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        fmt = "%Y-%m-%d %H:%M" if " " in s else "%Y-%m-%d"
        d = datetime.strptime(s, fmt).replace(tzinfo=TZ)
        if end and fmt == "%Y-%m-%d":
            d += timedelta(days=1)
        return int(d.timestamp() * 1000)
    if end:
        d += timedelta(days=1)
    return int(d.timestamp() * 1000)


def _t(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S")


def _event(r, brief=False):
    e = {"id": f"E{r['id']}", "start": _t(r["start_ms"]), "end": _t(r["end_ms"]), "category": r["category"],
         "title": r["title"], "importance": r["importance"], "summary": r["summary"]}
    if not brief:
        for k in ("points", "todos", "decisions", "highlights", "people", "memories"):
            e[k] = json.loads(r[k]) if r[k] else []
        e["project_id"] = f"P{r['project_id']}" if r["project_id"] else None
    return e


def _note(r):
    return {"id": f"N{r['id']}", "time": _t(r["start_ms"]), "intent": r["intent"], "title": r["title"], "text": r["text"],
            "answer": r["answer"] or None, "due": r["due"] or None, "status": r["status"]}


def _progress(con):
    pu = kv_get(con, "processed_until")
    return {"processed_until": _t(pu) if pu else None,
            "note": "processed_until 之后的录音还在转写或整理，暂时查不到；不是没有。"}


@mcp.tool()
def list_records(start: str = "today", end: str = "", kind: str = "all", min_importance: int = 1) -> str:
    """列出一段时间里的全时记（自动切出来的一件件事）和随口记（按快捷键说的灵感/提问/待办）。
    start/end：'2026-10-25'、'2026-10-25 14:00'、'today'、'yesterday'；end 留空 = start 那天结束。
    kind：all / events / notes。min_importance：1 全部，2 去掉零碎小事，3 只看重要的。"""
    con = ro()
    a = _ms(start)
    b = _ms(end, end=True) if end else _ms(start, end=True) if " " not in start else now_ms()
    out = {"range": [_t(a), _t(b)], **_progress(con)}
    if kind in ("all", "events"):
        out["events"] = [_event(r, brief=True) for r in con.execute(
            "SELECT * FROM events WHERE start_ms>=? AND start_ms<? AND importance>=? ORDER BY start_ms", (a, b, min_importance))]
    if kind in ("all", "notes"):
        out["notes"] = [_note(r) for r in con.execute("SELECT * FROM notes WHERE start_ms>=? AND start_ms<? ORDER BY start_ms", (a, b))]
    pending = con.execute("SELECT COUNT(*) FROM sentences WHERE event_id IS NULL AND note_id IS NULL AND start_ms>=? AND start_ms<?", (a, b)).fetchone()[0]
    if pending:
        out["unorganized_sentences"] = f"{pending} 句已转写、还没整理成事件（谈话可能还没结束），可用 get_transcript 直接看原文"
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def get_record(record_id: str, include_transcript: bool = True) -> str:
    """按 ID 取一条记录的全部字段。E 开头是全时记（摘要、要点、待办、决定、重点、人、记忆，可带逐句原文）；N 开头是随口记；P 开头是项目。"""
    con = ro()
    rid = record_id.strip().upper()
    if rid.startswith("E"):
        r = con.execute("SELECT * FROM events WHERE id=?", (int(rid[1:]),)).fetchone()
        if not r:
            return "没有这条"
        e = _event(r)
        e["marks"] = [_t(m["ts"]) for m in con.execute("SELECT ts FROM marks WHERE event_id=?", (r["id"],))]
        if include_transcript:
            e["transcript"] = [f"{_t(s['start_ms'])[11:]} {s['speaker']}{'（通话）' if s['source'] == 'system' else ''}：{s['text']}"
                               for s in con.execute("SELECT * FROM sentences WHERE event_id=? ORDER BY start_ms", (r["id"],))]
        return json.dumps(e, ensure_ascii=False)
    if rid.startswith("N"):
        r = con.execute("SELECT * FROM notes WHERE id=?", (int(rid[1:]),)).fetchone()
        return json.dumps(_note(r), ensure_ascii=False) if r else "没有这条"
    if rid.startswith("P"):
        return get_project(rid)
    return "ID 要以 E、N 或 P 开头"


@mcp.tool()
def get_transcript(start: str, end: str = "", source: str = "all", speaker: str = "") -> str:
    """逐句原文（语音转写的原话，带时间和说话人），不管有没有整理成事件。
    start/end 同 list_records；source：all / mic（现场）/ system（通话对方）/ memo（导入的备忘录）；speaker：只看某人，如「我」「对方」。最多返回 1500 句。"""
    con = ro()
    a = _ms(start)
    b = _ms(end, end=True) if end else (_ms(start, end=True) if " " not in start else a + 3600_000)
    q = "SELECT * FROM sentences WHERE start_ms>=? AND start_ms<?"
    args = [a, b]
    if source != "all":
        q += " AND source=?"
        args.append(source)
    if speaker:
        q += " AND speaker=?"
        args.append(speaker)
    rows = con.execute(q + " ORDER BY start_ms LIMIT 1500", args).fetchall()
    lines = [f"{_t(s['start_ms'])} {s['speaker']}{'（通话）' if s['source'] == 'system' else ''}：{s['text']}"
             + (f"  〔E{s['event_id']}〕" if s["event_id"] else "") for s in rows]
    return json.dumps({"count": len(lines), **_progress(con), "lines": lines}, ensure_ascii=False)


@mcp.tool()
def search(query: str, start: str = "", end: str = "", limit: int = 30) -> str:
    """在全部原文、事件摘要、随口记里搜关键词（中文至少 3 个字，按原话匹配）。返回命中的句子（带所属事件 ID）和事件。"""
    con = ro()
    a = _ms(start) if start else 0
    b = _ms(end, end=True) if end else now_ms() + 1
    hits = []
    q = query.strip()
    if len(q) >= 3:
        for s in con.execute(
                "SELECT s.* FROM sentences_fts f JOIN sentences s ON s.id=f.rowid WHERE sentences_fts MATCH ? AND s.start_ms BETWEEN ? AND ? "
                "ORDER BY s.start_ms DESC LIMIT ?", (f'"{q}"', a, b, limit)):
            hits.append(f"{_t(s['start_ms'])} {s['speaker']}：{s['text']}" + (f"  〔E{s['event_id']}〕" if s["event_id"] else ""))
    else:
        for s in con.execute("SELECT * FROM sentences WHERE text LIKE ? AND start_ms BETWEEN ? AND ? ORDER BY start_ms DESC LIMIT ?",
                             (f"%{q}%", a, b, limit)):
            hits.append(f"{_t(s['start_ms'])} {s['speaker']}：{s['text']}" + (f"  〔E{s['event_id']}〕" if s["event_id"] else ""))
    like = f"%{q}%"
    evs = [_event(r, brief=True) for r in con.execute(
        "SELECT * FROM events WHERE (title LIKE ? OR summary LIKE ? OR points LIKE ? OR todos LIKE ? OR memories LIKE ?) AND start_ms BETWEEN ? AND ? "
        "ORDER BY start_ms DESC LIMIT ?", (like, like, like, like, like, a, b, limit))]
    notes = [_note(r) for r in con.execute("SELECT * FROM notes WHERE text LIKE ? ORDER BY start_ms DESC LIMIT ?", (like, limit))]
    return json.dumps({"sentences": hits, "events": evs, "notes": notes}, ensure_ascii=False)


@mcp.tool()
def todos(days: int = 7) -> str:
    """最近几天全时记里冒出来的待办和承诺（谁、做什么、什么时候），以及随口记里的待办。"""
    con = ro()
    since = now_ms() - days * 86400_000
    out = []
    for r in con.execute("SELECT id, start_ms, title, todos FROM events WHERE start_ms>=? AND todos != '[]' ORDER BY start_ms DESC", (since,)):
        for t in json.loads(r["todos"]):
            out.append({**t, "from": f"E{r['id']} {_t(r['start_ms'])}「{r['title']}」"})
    for n in con.execute("SELECT * FROM notes WHERE start_ms>=? AND intent='待办' ORDER BY start_ms DESC", (since,)):
        out.append({"who": "我", "what": n["text"], "due": n["due"], "from": f"N{n['id']} {_t(n['start_ms'])} 随口记"})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def list_projects() -> str:
    """跨天自动归集的项目（同一件事在不同时间的几次谈话串成一条线），按最近更新排序。"""
    con = ro()
    return json.dumps([{"id": f"P{r['id']}", "name": r["name"], "stage": r["stage"], "summary": r["summary"], "updated": _t(r["updated"])}
                       for r in con.execute("SELECT * FROM projects ORDER BY updated DESC")], ensure_ascii=False)


@mcp.tool()
def get_project(project_id: str) -> str:
    """一个项目的档案：现状、阶段、风险、下次该追问的问题、谁答应过什么，以及时间线上的每件事。"""
    con = ro()
    r = con.execute("SELECT * FROM projects WHERE id=?", (int(project_id.strip().upper().lstrip("P")),)).fetchone()
    if not r:
        return "没有这个项目"
    p = {"id": f"P{r['id']}", "name": r["name"], "summary": r["summary"], "stage": r["stage"]}
    for k in ("risks", "next_questions", "commitments"):
        p[k] = json.loads(r[k]) if r[k] else []
    p["timeline"] = [_event(e, brief=True) for e in con.execute("SELECT * FROM events WHERE project_id=? ORDER BY start_ms", (r["id"],))]
    return json.dumps(p, ensure_ascii=False)


@mcp.tool()
def day_summary(day: str = "yesterday") -> str:
    """某一天的总结：一句话概括、上午/下午/晚上各干了什么、待办、承诺、风险。当天的在 23:30 以后自动生成。"""
    con = ro()
    d = datetime.fromtimestamp(_ms(day) / 1000, TZ).strftime("%Y-%m-%d")
    r = con.execute("SELECT content FROM digests WHERE day=?", (d,)).fetchone()
    if r:
        return r["content"]
    return json.dumps({"day": d, "note": "这天还没有总结。可以用 list_records 看当天的事件自己汇总。"}, ensure_ascii=False)


@mcp.tool()
def insights(limit: int = 10) -> str:
    """觉察：每周从「我」说过的话里找出的沟通和做事习惯，每条带依据和建议。"""
    con = ro()
    return json.dumps([{"period": r["period"], "insight": r["insight"], "evidence": json.loads(r["evidence"] or "[]"), "suggestion": r["suggestion"]}
                       for r in con.execute("SELECT * FROM insights ORDER BY created DESC LIMIT ?", (limit,))], ensure_ascii=False)


@mcp.tool()
def memories(query: str = "", limit: int = 50) -> str:
    """长期记忆：从各件事里抽出来值得长期记住的事实（人、偏好、约定、数字等），带出处。query 留空返回最近的。"""
    con = ro()
    out = []
    for r in con.execute("SELECT id, start_ms, title, memories FROM events WHERE memories != '[]' ORDER BY start_ms DESC"):
        for m in json.loads(r["memories"]):
            if not query or query in m:
                out.append({"fact": m, "from": f"E{r['id']} {_t(r['start_ms'])}「{r['title']}」"})
        if len(out) >= limit:
            break
    return json.dumps(out[:limit], ensure_ascii=False)


@mcp.tool()
def recorder_status() -> str:
    """录音器现在在不在录、录哪个麦克风、哪些通话软件、整理到几点了。"""
    from .config import STATUS
    con = ro()
    st = json.loads(STATUS.read_text()) if STATUS.exists() else None
    if st:
        st["age_seconds"] = round((now_ms() - st["ts"]) / 1000)
    return json.dumps({"recorder": st, **_progress(con)}, ensure_ascii=False)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
