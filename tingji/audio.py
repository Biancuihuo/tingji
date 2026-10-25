"""解码、人声检测（Silero VAD）、编码。全部 16 kHz 单声道 float32。"""
import os
import shutil
import subprocess

import numpy as np

from .config import MODELS, settings

SR = 16000
# launchd 里没有 Homebrew 的 PATH，所以 which 找不到时再看两个常见位置
FFMPEG = (os.environ.get("TINGJI_FFMPEG") or shutil.which("ffmpeg")
          or next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg") if os.path.exists(p)), "ffmpeg"))
_vad_cfg = None


def decode(path, start_s=None, dur_s=None):
    cmd = [FFMPEG, "-nostdin", "-loglevel", "error"]
    if start_s is not None:
        cmd += ["-ss", f"{start_s:.3f}"]
    if dur_s is not None:
        cmd += ["-t", f"{dur_s:.3f}"]
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def encode_mp3(samples, path):
    pcm = np.clip(samples, -1, 1).astype(np.float32).tobytes()
    subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                    "-b:a", "32k", str(path)], input=pcm, check=True)


def speech_segments(samples, pad_s=0.3):
    """返回 [(开始秒, 结束秒)]，前后各留 pad_s 秒，重叠的合并"""
    import sherpa_onnx
    global _vad_cfg
    if _vad_cfg is None:
        c = sherpa_onnx.VadModelConfig()
        c.silero_vad.model = str(MODELS / "silero_vad.onnx")
        c.silero_vad.threshold = settings()["vad_threshold"]
        c.silero_vad.min_silence_duration = 0.5
        c.silero_vad.min_speech_duration = 0.25
        c.silero_vad.max_speech_duration = 30
        c.sample_rate = SR
        _vad_cfg = c
    vad = sherpa_onnx.VoiceActivityDetector(_vad_cfg, buffer_size_in_seconds=max(30, len(samples) / SR + 5))
    win = _vad_cfg.silero_vad.window_size
    segs = []

    def drain():
        while not vad.empty():
            f = vad.front
            segs.append((f.start / SR, (f.start + len(f.samples)) / SR))
            vad.pop()

    for i in range(0, len(samples) - win + 1, win):
        vad.accept_waveform(samples[i:i + win])
        drain()
    vad.flush()
    drain()
    total = len(samples) / SR
    merged = []
    for a, b in segs:
        a, b = max(0.0, a - pad_s), min(total, b + pad_s)
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged
