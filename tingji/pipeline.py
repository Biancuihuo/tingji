"""后台处理一条龙：
分片 → 人声检测 → 拼成段 → Fun-ASR 转写（分说话人）→ 声纹认「我」→ 按话题切事件 → 摘要/待办/重点 → 归项目
另外：随口记（快捷键）、标重点、声纹注册、每日总结、每周觉察、旧录音清理。
"""
import json
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import asr, audio, llm, prompts, voice
from .config import AUDIO, HOME, MARKS, SPOOL, TZ, settings
from .db import kv_get, kv_set, now_ms

SR = audio.SR
LIVE_SOURCES = ("mic", "system")
GAP_KEEP_S = 0.8  # 段里两句人声之间最多留这么长的静音


def log(*a):
    print(datetime.now(TZ).strftime("%m-%d %H:%M:%S"), *a, flush=True)


def fmt_time(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%H:%M:%S")


def day_of(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d")


def notify(title, text):
    """系统通知，不出声、不抢焦点"""
    t = text.replace('"', "'")[:200]
    subprocess.run(["osascript", "-e", f'display notification "{t}" with title "{title}"'], capture_output=True)


# ---------------------------------------------------------------- 拼段
class Assembler:
    """一个来源手上正在攒的一段人声"""

    def __init__(self, source):
        self.source = source
        self.reset()

    def reset(self):
        self.segs = []      # [(绝对开始毫秒, samples)]
        self.chunks = []    # 用到的分片文件
        self.last_end = 0
        self.speech_ms = 0

    @property
    def open(self):
        return bool(self.segs)

    @property
    def start_ms(self):
        return self.segs[0][0] if self.segs else None

    def add(self, abs_ms, samples):
        self.segs.append((abs_ms, samples))
        self.last_end = abs_ms + int(len(samples) / SR * 1000)
        self.speech_ms += int(len(samples) / SR * 1000)

    def build(self):
        """拼成一段音频，返回 (samples, timemap)；timemap 每项 [段内偏移毫秒, 绝对毫秒, 时长毫秒]"""
        parts, tm, off, prev_end = [], [], 0, None
        for abs_ms, s in self.segs:
            if prev_end is not None:
                gap = min(GAP_KEEP_S, max(0.0, (abs_ms - prev_end) / 1000))
                if gap > 0:
                    parts.append(np.zeros(int(gap * SR), dtype=np.float32))
                    off += int(gap * 1000)
            dur = int(len(s) / SR * 1000)
            tm.append([off, abs_ms, dur])
            parts.append(s)
            off += dur
            prev_end = abs_ms + dur
        return np.concatenate(parts), tm


class Pipeline:
    def __init__(self, con, worker=False):
        self.con = con
        self.cfg = settings()
        self.asm = {}
        if worker:  # 只有后台进程能动分片表；命令行只读写自己的段
            self._recover()

    # ------------------------------------------------------------ 分片
    def _recover(self):
        """重启后：还挂在未完成段里的分片重新读一遍"""
        rows = self.con.execute("SELECT path, source FROM chunks WHERE status='vad' ORDER BY start_ms").fetchall()
        for r in rows:
            p = Path(r["path"])
            if p.exists():
                self.con.execute("DELETE FROM chunks WHERE path=?", (r["path"],))
        self.con.commit()

    def a(self, source):
        if source not in self.asm:
            self.asm[source] = Assembler(source)
        return self.asm[source]

    def ingest_chunks(self):
        cfg = self.cfg
        idle_ms = cfg["piece_idle_s"] * 1000
        done = 0
        for src_dir in sorted(p for p in SPOOL.iterdir() if p.is_dir()) if SPOOL.exists() else []:
            source = src_dir.name
            files = sorted(src_dir.glob("*.m4a"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
            for f in files:
                if not f.stem.isdigit():
                    continue
                if self.con.execute("SELECT 1 FROM chunks WHERE path=?", (str(f),)).fetchone():
                    continue
                start_ms = int(f.stem)
                try:
                    samples = audio.decode(f)
                except subprocess.CalledProcessError:
                    log("坏分片，删掉", f.name)
                    f.unlink(missing_ok=True)
                    continue
                dur_ms = int(len(samples) / SR * 1000)
                segs = audio.speech_segments(samples)
                asm = self.a(source)
                for sa, sb in segs:
                    abs_a = start_ms + int(sa * 1000)
                    if asm.open and (abs_a - asm.last_end > idle_ms or asm.speech_ms >= cfg["piece_max_speech_s"] * 1000):
                        self.close_piece(source)
                    asm.add(abs_a, samples[int(sa * SR):int(sb * SR)])
                speech = int(sum(b - a for a, b in segs) * 1000)
                if segs:
                    asm.chunks.append(f)
                    self.con.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?)", (str(f), source, start_ms, dur_ms, speech, "vad", now_ms()))
                else:
                    f.unlink(missing_ok=True)  # 没人说话：和心力球一样，什么也不留
                if asm.open and start_ms + dur_ms - asm.last_end >= idle_ms:
                    self.close_piece(source)
                kv_set(self.con, f"watermark:{source}", start_ms + dur_ms)
                self.con.commit()
                done += 1
        # 录音器停了、后面不再来分片：等够时间也收尾
        for source, asm in self.asm.items():
            if asm.open and now_ms() - asm.last_end > idle_ms + 75_000:
                self.close_piece(source)
        return done

    def close_piece(self, source, kind="stream"):
        asm = self.a(source)
        if not asm.open:
            return
        samples, tm = asm.build()
        start, end = tm[0][1], asm.last_end
        day_dir = AUDIO / day_of(start)
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{source}-{start}.mp3"
        audio.encode_mp3(samples, path)
        cur = self.con.execute(
            "INSERT INTO pieces(source, start_ms, end_ms, speech_ms, audio_path, timemap, kind, status, created) VALUES(?,?,?,?,?,?,?,?,?)",
            (source, start, end, asm.speech_ms, str(path), json.dumps(tm), kind, "encoded", now_ms()))
        for f in asm.chunks:
            Path(f).unlink(missing_ok=True)
            self.con.execute("UPDATE chunks SET status='done' WHERE path=?", (str(f),))
        self.con.commit()
        log(f"段 #{cur.lastrowid} {source} {fmt_time(start)}–{fmt_time(end)} 人声 {asm.speech_ms / 1000:.0f}s")
        asm.reset()

    # ------------------------------------------------------------ 转写
    def submit_asr(self):
        rows = self.con.execute("SELECT * FROM pieces WHERE status IN ('encoded','retry') ORDER BY start_ms LIMIT 8").fetchall()
        for p in rows:
            if p["status"] == "retry" and now_ms() - (p["done"] or 0) < 60_000:
                continue
            try:
                url = asr.upload(p["audio_path"])
                tid = asr.submit(url, diarize=True)
                self.con.execute("UPDATE pieces SET status='asr', task_id=?, error=NULL WHERE id=?", (tid, p["id"]))
            except Exception as e:
                self.con.execute("UPDATE pieces SET status='retry', error=?, done=? WHERE id=?", (str(e)[:300], now_ms(), p["id"]))
                log("提交转写失败", p["id"], e)
            self.con.commit()

    def poll_asr(self):
        n = 0
        for p in self.con.execute("SELECT * FROM pieces WHERE status='asr' ORDER BY start_ms").fetchall():
            try:
                sents = asr.check(p["task_id"])
            except Exception as e:
                self.con.execute("UPDATE pieces SET status='retry', error=?, done=? WHERE id=?", (str(e)[:300], now_ms(), p["id"]))
                self.con.commit()
                log("转写失败，稍后重试", p["id"], e)
                continue
            if sents is None:
                continue
            self.store_sentences(p, sents)
            self.con.execute("UPDATE pieces SET status='done', done=? WHERE id=?", (now_ms(), p["id"]))
            self.con.commit()
            n += 1
        return n

    def store_sentences(self, p, sents):
        tm = json.loads(p["timemap"])

        def to_abs(off):
            seg = tm[0]
            for s in tm:
                if s[0] <= off:
                    seg = s
            return seg[1] + min(max(0, off - seg[0]), seg[2])

        labels = self.label_speakers(p, sents)
        for s in sents:
            text = s["text"].strip()
            if not text:
                continue
            k = s.get("speaker_id", 0)
            label, score = labels.get(k, ("其他人", 0.0))
            self.con.execute(
                "INSERT INTO sentences(piece_id, source, start_ms, end_ms, text, speaker, spk_local, spk_score) VALUES(?,?,?,?,?,?,?,?)",
                (p["id"], p["source"], to_abs(s["begin_time"]), to_abs(s["end_time"]), text, label, k, round(score, 3)))
        log(f"转写完 #{p['id']} {len(sents)} 句")

    def label_speakers(self, p, sents):
        """只分「我」和「其他人」：段里每个说话人的声音拼起来，和注册过的「我」比。
        参数 sents 的时间是段内毫秒（Fun-ASR 原样）。返回 {说话人编号: (标签, 相似度)}"""
        if not sents or not voice.known(self.con) or not p["audio_path"]:
            return {}
        try:
            samples = audio.decode(p["audio_path"])
        except Exception:
            return {}
        parts = {}
        for s in sents:
            a, b = int(s["begin_time"] * SR / 1000), int(s["end_time"] * SR / 1000)
            parts.setdefault(s.get("speaker_id", 0), []).append(samples[a:b])
        out = {}
        for k, ps in parts.items():
            emb = voice.embed(np.concatenate(ps)[: SR * 20])
            name, score = voice.match(self.con, emb) if emb is not None else (None, 0.0)
            out[k] = ("我" if name else "其他人", score)
        return out

    def relabel(self, since_ms=0):
        """注册或补了声纹样本以后，把已有的句子重新认一遍"""
        n = 0
        for p in self.con.execute("SELECT * FROM pieces WHERE status='done' AND start_ms>=? AND audio_path IS NOT NULL AND audio_path != ''",
                                  (since_ms,)).fetchall():
            tm = json.loads(p["timemap"])
            rows = self.con.execute("SELECT * FROM sentences WHERE piece_id=?", (p["id"],)).fetchall()

            def to_off(abs_ms):
                o = 0
                for a, b, d in tm:
                    if b <= abs_ms:
                        o = a + min(abs_ms - b, d)
                return o
            sents = [{"begin_time": to_off(r["start_ms"]), "end_time": to_off(r["end_ms"]), "speaker_id": r["spk_local"]} for r in rows]
            labels = self.label_speakers(p, sents)
            for r in rows:
                label, score = labels.get(r["spk_local"], ("其他人", 0.0))
                self.con.execute("UPDATE sentences SET speaker=?, spk_score=? WHERE id=?", (label, round(score, 3), r["id"]))
                n += 1
        self.con.commit()
        return n

    # ------------------------------------------------------------ 处理进度
    def watermark(self):
        """这个时间点之前的录音都已经转写完（网页上的「已整理至」）"""
        now = now_ms()
        w = now
        for source in set(LIVE_SOURCES) | set(self.asm):
            asm = self.asm.get(source)
            if asm and asm.open:
                w = min(w, asm.start_ms)
                continue
            last = kv_get(self.con, f"watermark:{source}")
            # 这一路最近 3 分钟有分片进来，说明在录，还没处理到的那一截要等
            if last and now - last < 180_000:
                w = min(w, last)
        r = self.con.execute("SELECT MIN(start_ms) FROM pieces WHERE status IN ('encoded','retry','asr')").fetchone()[0]
        if r:
            w = min(w, r)
        return w

    # ------------------------------------------------------------ 标记
    def ingest_marks(self):
        if not MARKS.exists():
            return
        off = kv_get(self.con, "marks_offset", 0)
        data = MARKS.read_bytes()
        if len(data) < off:
            off = 0
        lines = data[off:].decode("utf-8", "ignore").splitlines()
        for line in lines:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts, kind = int(m["ts"]), m["kind"]
            if kind == "highlight":
                self.con.execute("INSERT OR IGNORE INTO marks(ts, kind) VALUES(?,?)", (ts, kind))
            elif kind == "note_start":
                self.con.execute("INSERT INTO notes(start_ms, status, created) VALUES(?,?,?)", (ts, "recording", now_ms()))
            elif kind == "note_end":
                self.con.execute("UPDATE notes SET end_ms=?, status='waiting', updated=? WHERE id=(SELECT MAX(id) FROM notes WHERE status='recording')",
                                 (ts, now_ms()))
            elif kind == "vp_start":
                kv_set(self.con, "vp_pending", {"start": ts})
            elif kind == "vp_end":
                vp = kv_get(self.con, "vp_pending") or {}
                if vp.get("start"):
                    vp["end"] = ts
                    kv_set(self.con, "vp_pending", vp)
        kv_set(self.con, "marks_offset", len(data))
        self.con.commit()

    def handle_voiceprint(self):
        """声纹注册：录音器菜单里「录我的声纹」那 30 秒，取麦克风那段人声"""
        vp = kv_get(self.con, "vp_pending")
        if not vp or not vp.get("end"):
            return
        asm = self.asm.get("mic")
        if asm and asm.open and asm.start_ms <= vp["end"] and kv_get(self.con, "watermark:mic", 0) >= vp["end"] - 1000:
            self.close_piece("mic")
        if not self._note_ready(vp["end"]):
            return
        rows = self.con.execute("SELECT * FROM pieces WHERE source='mic' AND end_ms>=? AND start_ms<=? AND status='done'",
                                (vp["start"], vp["end"])).fetchall()
        parts = []
        for p in rows:
            s = audio.decode(p["audio_path"])
            for off, abs_ms, dur in json.loads(p["timemap"]):
                a, b = max(abs_ms, vp["start"]), min(abs_ms + dur, vp["end"])
                if b > a:
                    parts.append(s[int((off + a - abs_ms) * SR / 1000):int((off + b - abs_ms) * SR / 1000)])
        kv_set(self.con, "vp_pending", None)
        if not parts or sum(len(x) for x in parts) < SR * 3:
            notify("听记", "声纹没录上：这段时间麦克风里没听到足够的人声，再录一次")
            self.con.commit()
            return
        n = voice.enroll(self.con, "我", np.concatenate(parts))
        notify("听记", f"声纹已记下（第 {n} 次），以后会把你说的话标成「我」")
        log("声纹注册", n)

    # ------------------------------------------------------------ 随口记
    def _note_ready(self, end_ms):
        """这条随口记的麦克风录音已经全部转写完"""
        if kv_get(self.con, "watermark:mic", 0) < end_ms - 1000:
            return False
        asm = self.asm.get("mic")
        if asm and asm.open and asm.start_ms <= end_ms:
            return False
        return not self.con.execute("SELECT 1 FROM pieces WHERE source='mic' AND status IN ('encoded','retry','asr') AND start_ms<=?",
                                    (end_ms,)).fetchone()

    def handle_notes(self):
        # 快捷键松开时录音器会立刻交出分片；手上那段麦克风人声马上收尾送转写，不等 20 秒
        for n in self.con.execute("SELECT * FROM notes WHERE status='waiting'").fetchall():
            asm = self.asm.get("mic")
            if asm and asm.open and asm.start_ms <= n["end_ms"] and kv_get(self.con, "watermark:mic", 0) >= n["end_ms"] - 1000:
                self.close_piece("mic")
        for n in self.con.execute("SELECT * FROM notes WHERE status='waiting'").fetchall():
            if not self._note_ready(n["end_ms"]):
                continue
            rows = self.con.execute(
                "SELECT * FROM sentences WHERE source='mic' AND note_id IS NULL AND end_ms>=? AND start_ms<=? ORDER BY start_ms",
                (n["start_ms"] - 500, n["end_ms"] + 1500)).fetchall()
            text = "".join(r["text"] for r in rows)
            for r in rows:
                self.con.execute("UPDATE sentences SET note_id=? WHERE id=?", (n["id"], r["id"]))
            if not text:
                self.con.execute("UPDATE notes SET status='empty', updated=? WHERE id=?", (now_ms(), n["id"]))
                self.con.commit()
                notify("听记", "随口记没听到声音")
                continue
            ctx = self.search_context(text)
            try:
                out = llm.chat_json("只输出 JSON。", prompts.NOTE.format(owner=self.cfg["owner"], text=text, context=ctx or "（无）"))
            except Exception as e:
                log("随口记理解失败", e)
                out = {"intent": "记录", "title": text[:10], "answer": "", "due": ""}
            self.con.execute("UPDATE notes SET status='done', text=?, intent=?, title=?, answer=?, due=?, updated=? WHERE id=?",
                             (text, out.get("intent"), out.get("title"), out.get("answer"), out.get("due"), now_ms(), n["id"]))
            self.con.commit()
            if out.get("intent") == "提问" and out.get("answer"):
                notify(f"听记 · {out.get('title', '')}", out["answer"])
            else:
                notify(f"听记 · 已记下（{out.get('intent', '记录')}）", out.get("title") or text[:40])
            log("随口记", n["id"], out.get("intent"), text[:30])

    def search_context(self, text, limit=12):
        """给提问找相关记录：抽 3 个字以上的词在原文和事件摘要里搜"""
        terms = [t for t in re.split(r"[\s，。？！、,.?!：:；;“”\"'（）()]+", text) if len(t) >= 3][:6]
        lines = []
        seen = set()
        for t in terms:
            for r in self.con.execute(
                    "SELECT s.start_ms, s.speaker, s.text FROM sentences_fts f JOIN sentences s ON s.id=f.rowid "
                    "WHERE sentences_fts MATCH ? ORDER BY s.start_ms DESC LIMIT 5", (f'"{t[:12]}"',)):
                if r["start_ms"] in seen:
                    continue
                seen.add(r["start_ms"])
                lines.append(f"{day_of(r['start_ms'])} {fmt_time(r['start_ms'])} {r['speaker']}：{r['text']}")
        for e in self.con.execute("SELECT start_ms, title, summary FROM events WHERE status='done' ORDER BY start_ms DESC LIMIT 8"):
            lines.append(f"{day_of(e['start_ms'])} {fmt_time(e['start_ms'])} 事件「{e['title']}」：{e['summary']}")
        for n in self.con.execute("SELECT start_ms, text FROM notes WHERE status='done' ORDER BY start_ms DESC LIMIT 10"):
            lines.append(f"{day_of(n['start_ms'])} {fmt_time(n['start_ms'])} 随口记：{n['text']}")
        return "\n".join(lines[: limit * 3])

    # ------------------------------------------------------------ 切事件
    def build_events(self):
        w = self.watermark()
        gap = self.cfg["event_gap_s"] * 1000
        rows = self.con.execute(
            "SELECT * FROM sentences WHERE event_id IS NULL AND note_id IS NULL AND start_ms < ? ORDER BY start_ms", (w,)).fetchall()
        # 还在录的随口记时间段里的句子先不动
        recording = self.con.execute("SELECT MIN(start_ms) FROM notes WHERE status IN ('recording','waiting')").fetchone()[0]
        clusters, cur = [], []
        for r in rows:
            if recording and r["start_ms"] >= recording - 500:
                break
            if cur and r["start_ms"] - max(x["end_ms"] for x in cur) > gap:
                clusters.append(cur)
                cur = []
            cur.append(r)
        if cur:
            clusters.append(cur)
        made = 0
        flush = kv_get(self.con, "flush_before") or 0  # 命令行 flush：这个时间点前的话不等 5 分钟，马上整理
        for c in clusters:
            end = max(x["end_ms"] for x in c)
            if end + gap > w and end > flush:  # 这件事可能还没说完
                continue
            for part in self._split_budget(c):
                self.finalize_event(part)
                made += 1
        return made

    @staticmethod
    def _split_budget(rows, budget=40000):
        """太长的（几小时）按字数在最大的停顿处切成几份，再分别交给模型"""
        total = sum(len(r["text"]) for r in rows)
        if total <= budget:
            return [rows]
        k = total // budget + 1
        size = len(rows) // k
        parts, start = [], 0
        for i in range(1, k):
            lo, hi = max(start + 1, i * size - size // 4), min(len(rows) - 1, i * size + size // 4)
            cut = max(range(lo, hi), key=lambda j: rows[j]["start_ms"] - rows[j - 1]["end_ms"])
            parts.append(rows[start:cut])
            start = cut
        parts.append(rows[start:])
        return [p for p in parts if p]

    def _dedup_echo(self, rows):
        """通话时喇叭里的声音会被麦克风再录一遍：和同一时间的通话声音内容重复的麦克风句子去掉"""
        sys_rows = [r for r in rows if r["source"] == "system"]
        if not sys_rows:
            return rows

        def grams(t):
            t = re.sub(r"\W", "", t)
            return {t[i:i + 2] for i in range(len(t) - 1)}

        drop = set()
        for m in (r for r in rows if r["source"] == "mic"):
            gm = grams(m["text"])
            for s in sys_rows:
                if s["end_ms"] < m["start_ms"] - 2000 or s["start_ms"] > m["end_ms"] + 2000:
                    continue
                gs = grams(s["text"])
                if gm and gs and len(gm & gs) / len(gm) > 0.5:
                    drop.add(m["id"])
                    break
        for i in drop:
            self.con.execute("DELETE FROM sentences WHERE id=?", (i,))
        return [r for r in rows if r["id"] not in drop]

    def finalize_event(self, rows):
        rows = self._dedup_echo(rows)
        if not rows:
            return
        start, end = rows[0]["start_ms"], max(r["end_ms"] for r in rows)
        marks = [m["ts"] for m in self.con.execute("SELECT ts FROM marks WHERE event_id IS NULL AND ts BETWEEN ? AND ?",
                                                    (start - 5000, end + self.cfg["event_gap_s"] * 1000))]
        lines, mi = [], 0
        for i, r in enumerate(rows):
            lines.append(f"[{i}] {fmt_time(r['start_ms'])} {r['speaker']}：{r['text']}")
            nxt = rows[i + 1]["start_ms"] if i + 1 < len(rows) else end + 10**9
            while mi < len(marks) and marks[mi] < nxt:
                if marks[mi] >= r["start_ms"]:
                    lines.append("【★重点】")
                mi += 1
        speech = sum(r["end_ms"] - r["start_ms"] for r in rows)
        projects = self.con.execute("SELECT id, name, summary FROM projects WHERE updated > ? ORDER BY updated DESC LIMIT 30",
                                    (now_ms() - 30 * 86400_000,)).fetchall()
        plist = "\n".join(f"{{\"id\": {p['id']}}} {p['name']}：{(p['summary'] or '')[:120]}" for p in projects) or "（还没有）"
        fast = speech < self.cfg["min_event_speech_s"] * 1000
        sys_prompt = prompts.EVENTS.format(owner=self.cfg["owner"], projects=plist)
        try:
            out = llm.chat_json(sys_prompt, "\n".join(lines), fast=fast)
            evs = out.get("events") or []
        except Exception as e:
            log("事件整理失败，先按一件事存原文", e)
            evs = []
        if not evs:
            evs = [{"start_idx": 0, "end_idx": len(rows) - 1, "title": "未整理的对话", "importance": 1, "summary": ""}]
        # 区间修正：首尾相接、覆盖全部
        evs.sort(key=lambda e: int(e.get("start_idx", 0)))
        evs[0]["start_idx"] = 0
        for a, b in zip(evs, evs[1:]):
            a["end_idx"] = int(b["start_idx"]) - 1
        evs[-1]["end_idx"] = len(rows) - 1
        touched = set()
        for e in evs:
            si, ei = int(e["start_idx"]), int(e["end_idx"])
            if ei < si:
                continue
            seg = rows[si:ei + 1]
            s_ms, e_ms = seg[0]["start_ms"], max(r["end_ms"] for r in seg)
            pid = self._project_ref(e.get("project"))
            cur = self.con.execute(
                "INSERT INTO events(start_ms, end_ms, status, category, title, summary, importance, points, todos, decisions, highlights, people, memories, project_id, speech_ms, created, updated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (s_ms, e_ms, "done", e.get("category"), e.get("title"), e.get("summary"), int(e.get("importance") or 2),
                 *(json.dumps(e.get(k) or [], ensure_ascii=False) for k in ("points", "todos", "decisions", "highlights", "people", "memories")),
                 pid, sum(r["end_ms"] - r["start_ms"] for r in seg), now_ms(), now_ms()))
            eid = cur.lastrowid
            self.con.executemany("UPDATE sentences SET event_id=? WHERE id=?", [(eid, r["id"]) for r in seg])
            self.con.execute("UPDATE marks SET event_id=? WHERE event_id IS NULL AND ts BETWEEN ? AND ?",
                             (eid, s_ms - 5000, e_ms + self.cfg["event_gap_s"] * 1000))
            if pid:
                touched.add(pid)
            log(f"事件 #{eid} {fmt_time(s_ms)}–{fmt_time(e_ms)} [{e.get('category')}] {e.get('title')}")
        self.con.commit()
        for pid in touched:
            self.update_project(pid)

    def _project_ref(self, ref):
        if not isinstance(ref, dict):
            return None
        if ref.get("id"):
            r = self.con.execute("SELECT id FROM projects WHERE id=?", (int(ref["id"]),)).fetchone()
            return r["id"] if r else None
        name = (ref.get("new") or "").strip()
        if not name:
            return None
        r = self.con.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
        if r:
            return r["id"]
        return self.con.execute("INSERT INTO projects(name, created, updated) VALUES(?,?,?)", (name, now_ms(), now_ms())).lastrowid

    def update_project(self, pid):
        p = self.con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        evs = self.con.execute("SELECT * FROM events WHERE project_id=? ORDER BY start_ms DESC LIMIT 12", (pid,)).fetchall()
        old = {k: p[k] for k in ("name", "summary", "stage")}
        for k in ("risks", "next_questions", "commitments"):
            old[k] = json.loads(p[k]) if p[k] else []
        body = "原档案：\n" + json.dumps(old, ensure_ascii=False) + "\n\n相关的事（新到旧）：\n" + "\n".join(
            f"{day_of(e['start_ms'])} {fmt_time(e['start_ms'])}「{e['title']}」{e['summary']} 待办：{e['todos']} 决定：{e['decisions']}" for e in evs)
        try:
            out = llm.chat_json(prompts.PROJECT.format(owner=self.cfg["owner"]), body)
        except Exception as e:
            log("项目更新失败", pid, e)
            return
        self.con.execute("UPDATE projects SET summary=?, stage=?, risks=?, next_questions=?, commitments=?, updated=? WHERE id=?",
                         (out.get("summary"), out.get("stage"), *(json.dumps(out.get(k) or [], ensure_ascii=False) for k in ("risks", "next_questions", "commitments")),
                          now_ms(), pid))
        self.con.commit()
        log("项目更新", pid, p["name"])

    # ------------------------------------------------------------ 每日总结、觉察
    def digest(self, day):
        a = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ).timestamp() * 1000)
        evs = self.con.execute("SELECT * FROM events WHERE start_ms>=? AND start_ms<? AND status='done' ORDER BY start_ms",
                               (a, a + 86400_000)).fetchall()
        notes = self.con.execute("SELECT * FROM notes WHERE start_ms>=? AND start_ms<? AND status='done'", (a, a + 86400_000)).fetchall()
        if not evs and not notes:
            return None
        body = "\n".join(f"{fmt_time(e['start_ms'])}–{fmt_time(e['end_ms'])} [{e['category']}]「{e['title']}」{e['summary']} 待办：{e['todos']}"
                         for e in evs)
        body += "\n随口记：\n" + "\n".join(f"{fmt_time(n['start_ms'])} {n['intent']}：{n['text']}" for n in notes)
        out = llm.chat_json(prompts.DIGEST.format(owner=self.cfg["owner"], day=day), body)
        self.con.execute("INSERT INTO digests(day, content, created) VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET content=excluded.content, created=excluded.created",
                         (day, json.dumps(out, ensure_ascii=False), now_ms()))
        self.con.commit()
        log("日总结", day)
        return out

    def maybe_digest(self):
        """每天 23:30 以后写当天的；漏了的前一天补上"""
        now = datetime.now(TZ)
        days = [(now - timedelta(days=1)).strftime("%Y-%m-%d")]
        if now.hour == 23 and now.minute >= 30:
            days.append(now.strftime("%Y-%m-%d"))
        for d in days:
            if not self.con.execute("SELECT 1 FROM digests WHERE day=?", (d,)).fetchone():
                if kv_get(self.con, f"digest_tried:{d}") and d != now.strftime("%Y-%m-%d"):
                    continue
                kv_set(self.con, f"digest_tried:{d}", True)
                self.con.commit()
                try:
                    self.digest(d)
                except Exception as e:
                    log("日总结失败", d, e)

    def insights(self, days=7):
        since = now_ms() - days * 86400_000
        mine = self.con.execute("SELECT start_ms, text FROM sentences WHERE speaker='我' AND start_ms>=? ORDER BY start_ms", (since,)).fetchall()
        if len(mine) < 30:
            return None
        step = max(1, len(mine) // 400)
        said = "\n".join(f"{day_of(r['start_ms'])} {fmt_time(r['start_ms'])} {r['text']}" for r in mine[::step])
        evs = "\n".join(f"{day_of(e['start_ms'])}「{e['title']}」{e['summary']}" for e in
                        self.con.execute("SELECT * FROM events WHERE start_ms>=? AND importance>=2 ORDER BY start_ms", (since,)))
        out = llm.chat_json(prompts.INSIGHT.format(owner=self.cfg["owner"]), f"他说过的话：\n{said}\n\n这段时间的事：\n{evs}")
        period = f"{day_of(since)}～{day_of(now_ms())}"
        for x in out.get("insights") or []:
            self.con.execute("INSERT INTO insights(period, insight, evidence, suggestion, created) VALUES(?,?,?,?,?)",
                             (period, x.get("insight"), json.dumps(x.get("evidence") or [], ensure_ascii=False), x.get("suggestion"), now_ms()))
        self.con.commit()
        log("觉察", period)
        return out

    def maybe_insights(self):
        """每周日 23:45 以后做一次"""
        now = datetime.now(TZ)
        week = now.strftime("%G-W%V")
        if now.weekday() == 6 and now.hour == 23 and now.minute >= 45 and kv_get(self.con, "insight_week") != week:
            kv_set(self.con, "insight_week", week)
            self.con.commit()
            try:
                self.insights()
            except Exception as e:
                log("觉察失败", e)

    def cleanup(self):
        days = self.cfg["keep_audio_days"]
        if not days:
            return
        cutoff = now_ms() - days * 86400_000
        # 按进系统的时间算，不按录音发生的时间（导入的旧备忘录不能一进来就被当成过期）
        for p in self.con.execute("SELECT id, audio_path FROM pieces WHERE status='done' AND created < ? AND audio_path IS NOT NULL AND audio_path != ''", (cutoff,)):
            Path(p["audio_path"]).unlink(missing_ok=True)
            self.con.execute("UPDATE pieces SET audio_path=NULL WHERE id=?", (p["id"],))
        self.con.commit()

    # ------------------------------------------------------------ 一轮
    def step(self):
        self.cfg = settings()
        self.ingest_marks()
        self.ingest_chunks()
        self.handle_notes()
        self.submit_asr()
        self.poll_asr()
        self.handle_voiceprint()
        self.handle_notes()
        self.build_events()
        w = self.watermark()
        kv_set(self.con, "processed_until", w)
        kv_set(self.con, "worker_heartbeat", now_ms())
        self.con.commit()
        t0 = int(datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        n = self.con.execute("SELECT COUNT(*) FROM events WHERE start_ms>=?", (t0,)).fetchone()[0]
        (HOME / "progress.json").write_text(json.dumps({"processed_until": fmt_time(w)[:5], "today_events": n}, ensure_ascii=False))
