"""路径、可调参数、密钥。参数可在 settings.json 里覆盖，密钥在 secrets.json（权限 600，不进仓库）。"""
import json
import os
from datetime import timedelta, timezone
from pathlib import Path

HOME = Path(os.environ.get("TINGJI_HOME", Path.home() / "Library/Application Support/Tingji"))
SPOOL = HOME / "spool"          # 录音器写的一分钟分片：spool/<来源>/<开始毫秒>.m4a
AUDIO = HOME / "audio"          # 有人声的片段（送去转写的那份），按天分目录
MODELS = HOME / "models"
DB_PATH = HOME / "tingji.db"
MARKS = HOME / "marks.jsonl"    # 录音器写：标重点、灵感/提问的起止
STATUS = HOME / "status.json"   # 录音器每 10 秒写一次
LOG_DIR = Path.home() / "Library/Logs"
TZ = timezone(timedelta(hours=8))  # 按北京时间算「今天」

DEFAULTS = {
    "owner": "用户",              # 声纹注册的那个人叫什么，写进给模型的提示词
    "vad_threshold": 0.5,
    "piece_max_speech_s": 180,    # 累计这么多秒人声就先送一段去转写
    "piece_idle_s": 20,           # 人声停了这么久，就把手上这段送去转写
    "event_gap_s": 300,           # 两段话隔开超过这么久，算两件事
    "event_split_min_s": 2700,    # 一件事超过这么长，让模型按话题再切
    "min_event_speech_s": 8,      # 一件事里人声不到这么多秒，不出摘要（只留原文）
    "voice_match": 0.6,           # 声纹相似度到这个值算同一个人（实测：对着电脑说 0.69–0.87，旁人 0.24–0.52）
    "llm_model": "deepseek-v4-pro",
    "llm_fast_model": "deepseek-v4-flash",
    "keep_audio_days": 30,        # 有人声的录音留多少天；0 = 一直留
    "web_port": 7788,
}


def settings():
    s = dict(DEFAULTS)
    f = HOME / "settings.json"
    if f.exists():
        s.update(json.loads(f.read_text()))
    return s


def secret(name):
    return json.loads((HOME / "secrets.json").read_text())[name]


def ensure_dirs():
    for d in (HOME, SPOOL, AUDIO, MODELS):
        d.mkdir(parents=True, exist_ok=True)
