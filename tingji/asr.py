"""Fun-ASR 录音文件识别。录音先传百炼自带的临时存储（48 小时后自动删除），不进公开的 COS 桶。"""
import os
import time

import requests

from .config import secret

S = requests.Session()
S.trust_env = False  # 不走系统代理：百炼在国内，直连最稳
BASE = "https://dashscope.aliyuncs.com/api/v1"


def _req(method, url, **kw):
    """百炼偶尔断开空闲连接（实测一下午 8 次 RemoteDisconnected / 读超时）：马上换新连接再试两次"""
    for i in range(3):
        try:
            return S.request(method, url, **kw)
        except (requests.ConnectionError, requests.Timeout):
            if i == 2:
                raise
            time.sleep(1 + i)


def _h():
    return {"Authorization": f"Bearer {secret('dashscope')}"}


def upload(path, model="fun-asr"):
    """传到百炼临时存储，返回 oss:// 地址（只有百炼自己能读）。"""
    r = _req("GET", f"{BASE}/uploads", params={"action": "getPolicy", "model": model}, headers=_h(), timeout=30)
    r.raise_for_status()
    p = r.json()["data"]
    key = f"{p['upload_dir']}/{os.path.basename(path)}"
    def post():  # 每次重试都重新打开文件，不然重传的是读到末尾的空内容
        with open(path, "rb") as f:
            files = {
                "OSSAccessKeyId": (None, p["oss_access_key_id"]),
                "Signature": (None, p["signature"]),
                "policy": (None, p["policy"]),
                "x-oss-object-acl": (None, p["x_oss_object_acl"]),
                "x-oss-forbid-overwrite": (None, p["x_oss_forbid_overwrite"]),
                "key": (None, key),
                "success_action_status": (None, "200"),
                "file": (os.path.basename(path), f),
            }
            return S.post(p["upload_host"], files=files, timeout=300)

    for i in range(3):
        try:
            post().raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout):
            if i == 2:
                raise
            time.sleep(1 + i)
    return f"oss://{key}"


def submit(url, diarize=True):
    params = {"diarization_enabled": True} if diarize else {}
    r = _req("POST", f"{BASE}/services/audio/asr/transcription",
               headers={**_h(), "X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable"},
               json={"model": "fun-asr", "input": {"file_urls": [url]}, "parameters": params}, timeout=30)
    r.raise_for_status()
    return r.json()["output"]["task_id"]


def check(task_id):
    """查一次：还在跑返回 None；完成返回 sentences（没有人声是 []）；失败抛异常"""
    out = _req("GET", f"{BASE}/tasks/{task_id}", headers=_h(), timeout=30).json()["output"]
    if out["task_status"] in ("PENDING", "RUNNING"):
        return None
    res = out["results"][0]
    if res.get("code") == "ASR_RESPONSE_HAVE_NO_WORDS":
        return []
    if out["task_status"] != "SUCCEEDED" or res.get("subtask_status") != "SUCCEEDED":
        raise RuntimeError(res.get("code") or res.get("message") or str(out)[:300])
    data = _req("GET", res["transcription_url"], timeout=60).json()
    return data["transcripts"][0].get("sentences", [])


def poll(task_id, timeout=900):
    """返回 sentences 列表；没有人声返回 []。"""
    t0 = time.time()
    while True:
        out = S.get(f"{BASE}/tasks/{task_id}", headers=_h(), timeout=30).json()["output"]
        if out["task_status"] in ("PENDING", "RUNNING"):
            if time.time() - t0 > timeout:
                raise TimeoutError(task_id)
            time.sleep(2)
            continue
        res = out["results"][0]
        if res.get("code") == "ASR_RESPONSE_HAVE_NO_WORDS":
            return []
        if out["task_status"] != "SUCCEEDED" or res.get("subtask_status") != "SUCCEEDED":
            raise RuntimeError(res.get("code") or res.get("message") or out)
        data = S.get(res["transcription_url"], timeout=60).json()  # OSS 签名地址，不带鉴权头
        return data["transcripts"][0].get("sentences", [])


def transcribe(path, diarize=True):
    return poll(submit(upload(path), diarize))
