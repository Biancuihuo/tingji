"""后台常驻：每 3 秒跑一轮。launchd 守着（com.biancuihuo.tingji.worker）。"""
import fcntl
import time
import traceback

from .config import HOME, ensure_dirs
from .db import connect
from .pipeline import Pipeline, log


def main():
    ensure_dirs()
    lock = open(HOME / "worker.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("已经有一个在跑")
        return
    con = connect()
    p = Pipeline(con, worker=True)
    log("后台启动")
    last_hourly = 0
    while True:
        try:
            p.step()
            if time.time() - last_hourly > 600:
                p.maybe_digest()
                p.maybe_insights()
                p.cleanup()
                last_hourly = time.time()
        except Exception:
            log("出错：\n" + traceback.format_exc())
            time.sleep(10)
        time.sleep(3)


if __name__ == "__main__":
    main()
