"""声纹：3D-Speaker CAM++ 中文模型，本机算。和心力球一样，主要只分「我」和「其他人」。"""
import json

import numpy as np

from .config import MODELS, settings
from .db import now_ms

_ext = None


def extractor():
    global _ext
    if _ext is None:
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODELS / "campplus_zh.onnx"), num_threads=2)
        _ext = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
    return _ext


def embed(samples):
    if len(samples) < 16000 * 0.8:
        return None
    ext = extractor()
    st = ext.create_stream()
    st.accept_waveform(16000, samples[: 16000 * 30])
    st.input_finished()
    v = np.array(ext.compute(st), dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def known(con):
    """{名字: [样本向量, ...]}。一个人可以有几个样本（对着电脑说、离远了说、手机录的），比对时取最像的"""
    out = {}
    for r in con.execute("SELECT name, embedding FROM voices"):
        v = json.loads(r["embedding"])
        out[r["name"]] = [np.array(x, dtype=np.float32) for x in (v if v and isinstance(v[0], list) else [v])]
    return out


def match(con, emb):
    """返回 (名字或 None, 相似度)"""
    best, score = None, 0.0
    for name, vs in known(con).items():
        s = max(float(np.dot(emb, v)) for v in vs)
        if s > score:
            best, score = name, s
    return (best, score) if score >= settings()["voice_match"] else (None, score)


def enroll(con, name, samples):
    """加一个样本（最多留 12 个）"""
    emb = embed(samples)
    if emb is None:
        raise ValueError("样本太短，至少要 1 秒人声，建议 30 秒")
    vs = known(con).get(name, [])
    vs = (vs + [emb])[-12:]
    con.execute("INSERT INTO voices(name, embedding, samples, updated) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET embedding=excluded.embedding, samples=excluded.samples, updated=excluded.updated",
                (name, json.dumps([v.tolist() for v in vs]), len(vs), now_ms()))
    con.commit()
    return len(vs)
