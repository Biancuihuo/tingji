"""DeepSeek：整理事件、更新项目、日总结、觉察都用它。只用 JSON 输出。"""
import json
import time

import requests

from .config import secret, settings

S = requests.Session()
S.trust_env = False


def chat_json(system, user, fast=False, max_tokens=16000, retries=2):
    cfg = settings()
    model = cfg["llm_fast_model"] if fast else cfg["llm_model"]
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": max_tokens if not fast else min(max_tokens, 8000),
        "response_format": {"type": "json_object"},
    }
    last = None
    for i in range(retries + 1):
        try:
            r = S.post("https://api.deepseek.com/chat/completions",
                       headers={"Authorization": f"Bearer {secret('deepseek')}"}, json=payload, timeout=300)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:  # 网络、空内容、JSON 坏了都重试
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"DeepSeek 失败：{last}")
