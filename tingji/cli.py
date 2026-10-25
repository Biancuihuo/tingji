"""命令行：tingji <命令>（或 python -m tingji.cli <命令>）
  status                      录音器和后台的状态、今天记了什么
  events [--day 2026-10-25]   某天的全时记
  pieces [--limit 20]         最近录下的段：编号、时间、人声多长、第一句话（挑一段只有自己在说的去 enroll）
  enroll --file X | --piece N  给「我」加一个声纹样本（一段只有本人在说的录音；或已经录下的第 N 段）
  relabel                     加了声纹样本以后，把已有的句子重新认一遍「我／其他人」
  flush                       不等停够 5 分钟，现在就把已经转写的话整理成事件（后台下一轮做）
  redo --day D                删掉某天的事件重新整理（改了提示词以后用）
  import-memos [--limit N] [--since 2026-06-01] [--dry-run]   把 iPhone 语音备忘录（iCloud 同步到这台 Mac 的）导进来
  digest [--day D]            立即写某天的总结
  insights [--days 7]         立即做一次觉察
"""
import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import audio, voice
from .config import HOME, STATUS, TZ
from .db import connect, kv_get, now_ms
from .pipeline import Pipeline, day_of, fmt_time

MEMOS = Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"


def cmd_status(con, a):
    st = json.loads(STATUS.read_text()) if STATUS.exists() else None
    if st:
        age = (now_ms() - st["ts"]) / 1000
        state = ("已关闭" if not st.get("enabled") else "录音中" if st.get("capturing")
                 else f"电脑在放声音，暂停（{', '.join(st.get('paused_for_media') or [])}）" if st.get("paused_for_media")
                 else st["mic"].get("error") or "麦克风没开")
        print(f"录音器：{'在线' if age < 15 else f'{age:.0f} 秒没更新（可能没在运行）'}，{state}，{st['mic']['device']}")
    else:
        print("录音器：没有状态文件（没启动过）")
    hb = kv_get(con, "worker_heartbeat")
    print(f"后台：{'在线' if hb and now_ms() - hb < 30_000 else '不在线'}；已整理至 {fmt_time(kv_get(con, 'processed_until', 0) or 0)}")
    q = con.execute("SELECT status, COUNT(*) n FROM pieces GROUP BY status").fetchall()
    print("段：", {r["status"]: r["n"] for r in q})
    print("声纹：", [r["name"] for r in con.execute("SELECT name FROM voices")] or "未注册")
    a.day = a.day or datetime.now(TZ).strftime("%Y-%m-%d")
    cmd_events(con, a)


def cmd_events(con, a):
    day = a.day or datetime.now(TZ).strftime("%Y-%m-%d")
    t0 = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ).timestamp() * 1000)
    rows = con.execute("SELECT * FROM events WHERE start_ms>=? AND start_ms<? ORDER BY start_ms", (t0, t0 + 86400_000)).fetchall()
    print(f"{day} 全时记 {len(rows)} 件")
    for e in rows:
        print(f"  #{e['id']} {fmt_time(e['start_ms'])}–{fmt_time(e['end_ms'])} [{e['category']}] {e['title']}  ★{e['importance']}")
        if e["summary"]:
            print("     ", e["summary"][:160])
    for n in con.execute("SELECT * FROM notes WHERE start_ms>=? AND start_ms<? ORDER BY start_ms", (t0, t0 + 86400_000)):
        print(f"  随口记 {fmt_time(n['start_ms'])} [{n['intent'] or n['status']}] {n['text'] or ''} {('→ ' + n['answer']) if n['answer'] else ''}")


def cmd_pieces(con, a):
    for p in con.execute("SELECT * FROM pieces ORDER BY id DESC LIMIT ?", (a.limit,)):
        first = con.execute("SELECT speaker, text FROM sentences WHERE piece_id=? ORDER BY start_ms LIMIT 1", (p["id"],)).fetchone()
        said = f"{first['speaker']}：{first['text'][:30]}" if first else ""
        print(f"  #{p['id']} {day_of(p['start_ms'])} {fmt_time(p['start_ms'])} 人声 {p['speech_ms'] / 1000:.0f}s [{p['status']}] {said}")


def cmd_enroll(con, a):
    if a.piece:
        a.file = con.execute("SELECT audio_path FROM pieces WHERE id=?", (a.piece,)).fetchone()[0]
    s = audio.decode(a.file)
    segs = audio.speech_segments(s)
    import numpy as np
    sp = np.concatenate([s[int(x * 16000):int(y * 16000)] for x, y in segs]) if segs else s
    n = voice.enroll(con, "我", sp)
    print(f"「我」现在有 {n} 个声纹样本，这次用了 {len(sp) / 16000:.0f} 秒人声。跑 relabel 把旧句子重新认一遍。")


def cmd_flush(con, a):
    from .db import kv_set
    kv_set(con, "flush_before", now_ms())
    con.commit()
    print("好了，后台下一轮会把现在之前的话整理成事件（约 1 分钟）")


def cmd_redo(con, a):
    from .db import kv_set
    t0 = int(datetime.strptime(a.day, "%Y-%m-%d").replace(tzinfo=TZ).timestamp() * 1000)
    ids = [r[0] for r in con.execute("SELECT id FROM events WHERE start_ms>=? AND start_ms<?", (t0, t0 + 86400_000))]
    for i in ids:
        con.execute("UPDATE sentences SET event_id=NULL WHERE event_id=?", (i,))
        con.execute("UPDATE marks SET event_id=NULL WHERE event_id=?", (i,))
        con.execute("DELETE FROM events WHERE id=?", (i,))
    gone = con.execute("DELETE FROM projects WHERE id NOT IN (SELECT DISTINCT project_id FROM events WHERE project_id IS NOT NULL)").rowcount
    kv_set(con, "flush_before", now_ms())
    con.commit()
    print(f"删了 {len(ids)} 件事、{gone} 个没有事件了的项目，后台下一轮重新整理")


def cmd_relabel(con, a):
    print(f"重新认了 {Pipeline(con).relabel()} 句")


def cmd_import_memos(con, a):
    db = sqlite3.connect(f"file:{MEMOS / 'CloudRecordings.db'}?mode=ro", uri=True)
    rows = db.execute("SELECT ZDATE, ZDURATION, ZPATH, ZCUSTOMLABEL FROM ZCLOUDRECORDING WHERE ZPATH IS NOT NULL ORDER BY ZDATE DESC").fetchall()
    todo = []
    for zdate, dur, path, label in rows:
        start_ms = int((zdate + 978307200) * 1000)
        if a.since and day_of(start_ms) < a.since:
            continue
        if a.path and a.path not in path:
            continue
        f = MEMOS / path
        if not f.exists() or con.execute("SELECT 1 FROM kv WHERE k=?", (f"memo:{path}",)).fetchone():
            continue
        todo.append((start_ms, dur or 0, f, label))
    todo = todo[: a.limit] if a.limit else todo
    hours = sum(t[1] for t in todo) / 3600
    print(f"待导入 {len(todo)} 条，共 {hours:.1f} 小时（转写约 ¥{hours * 0.79:.1f}）")
    if a.dry_run:
        for t in todo[:20]:
            print(" ", day_of(t[0]), fmt_time(t[0]), f"{t[1] / 60:.1f} 分", t[3])
        return
    p = Pipeline(con)
    for start_ms, dur, f, label in todo:
        s = audio.decode(f)
        segs = audio.speech_segments(s)
        asm = p.a("memo")
        for x, y in segs:
            abs_a = start_ms + int(x * 1000)
            if asm.open and (abs_a - asm.last_end > 20_000 or asm.speech_ms >= 600_000):
                p.close_piece("memo", kind="import")
            asm.add(abs_a, s[int(x * 16000):int(y * 16000)])
        p.close_piece("memo", kind="import")
        con.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)", (f"memo:{f.relative_to(MEMOS)}", json.dumps(label or "")))
        con.commit()
        print("  已排队", day_of(start_ms), fmt_time(start_ms), label or f.name)
    print("后台会接着转写、整理成事件。")


def cmd_digest(con, a):
    out = Pipeline(con).digest(a.day or datetime.now(TZ).strftime("%Y-%m-%d"))
    print(json.dumps(out, ensure_ascii=False, indent=2) if out else "这天没有记录")


def cmd_insights(con, a):
    out = Pipeline(con).insights(a.days)
    print(json.dumps(out, ensure_ascii=False, indent=2) if out else "「我」说的话太少，做不了觉察（先注册声纹）")


def main():
    ap = argparse.ArgumentParser(prog="tingji")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "events", "digest"):
        s = sub.add_parser(name)
        s.add_argument("--day")
    s = sub.add_parser("pieces")
    s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("enroll")
    s.add_argument("--file")
    s.add_argument("--piece", type=int)
    sub.add_parser("relabel")
    sub.add_parser("flush")
    s = sub.add_parser("redo")
    s.add_argument("--day", required=True)
    s = sub.add_parser("import-memos")
    s.add_argument("--limit", type=int)
    s.add_argument("--since")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--path", help="只导入文件名里含这段字的那条")
    s = sub.add_parser("insights")
    s.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    con = connect()
    {"status": cmd_status, "events": cmd_events, "pieces": cmd_pieces, "enroll": cmd_enroll, "import-memos": cmd_import_memos,
     "digest": cmd_digest, "insights": cmd_insights, "relabel": cmd_relabel, "flush": cmd_flush, "redo": cmd_redo}[a.cmd](con, a)


if __name__ == "__main__":
    main()
