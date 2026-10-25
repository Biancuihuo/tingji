"""不装录音器、不填密钥，先看看时间线长什么样：
    .venv/bin/python scripts/demo.py          # 在 ./demo-home 造一天示例数据，打开 http://127.0.0.1:7799/
里面的人和事都是编的。数据放在单独的目录，不碰真正的 ~/Library/Application Support/Tingji。
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = Path(os.environ.get("TINGJI_HOME") or ROOT / "demo-home")
os.environ["TINGJI_HOME"] = str(DEMO)
sys.path.insert(0, str(ROOT))
PORT = 7799

from tingji import config  # noqa: E402  要先设好 TINGJI_HOME 再导入
from tingji.db import connect, kv_set  # noqa: E402

TZ = config.TZ
J = lambda x: json.dumps(x, ensure_ascii=False)  # noqa: E731


def at(day, hm):
    h, m = map(int, hm.split(":")[:2])
    s = int(hm.split(":")[2]) if hm.count(":") == 2 else 0
    return int(day.replace(hour=h, minute=m, second=s, microsecond=0).timestamp() * 1000)


TODAY = [
    dict(start="09:02", end="09:31", category="工作会议", title="例会：定本周投放预算", importance=3, project="双十一投放",
         summary="例会过了上周投放数据和本周预算。小林汇报上周点击率掉到 1.2%，大家判断是素材疲劳。最后定下本周预算先控制在每天 600 元，周三前换一批新素材再决定加不加。",
         points=["上周点击率从 2.1% 掉到 1.2%，主要是老素材跑了三周", "转化成本没怎么涨，说明落地页没问题", "本周先控预算，等新素材上线再看"],
         todos=[{"who": "小林", "what": "出三版新素材", "due": "周三前"}, {"who": "我", "what": "把上周的投放数据发到群里", "due": "今天"}],
         decisions=["本周预算每天 600 元，周三看新素材效果再决定加不加"], highlights=["素材疲劳了就换，不要硬加预算"],
         people=["小林", "阿杰"], memories=["小林负责素材，阿杰负责投放后台"],
         lines=[("09:02:10", "我", "先看上周的数据吧，小林你说一下。"),
                ("09:02:18", "其他人", "上周一共花了四千二，点击率从二点一掉到一点二。"),
                ("09:02:31", "其他人", "转化成本倒是没怎么涨，主要是点进来的人少了。"),
                ("09:02:44", "我", "那就是素材的问题，老素材已经跑了三周了。"),
                ("09:02:52", "其他人", "对，我这边周三前能出三版新的。"),
                ("09:03:05", "我", "行，这周预算先别加，每天控制在六百。"),
                ("09:03:14", "其他人", "那周三新素材上了之后再看要不要加？"),
                ("09:03:20", "我", "对，素材疲劳了就换，不要硬加预算。"),
                ("09:31:02", "我", "我待会把上周的数据发群里，大家都看一下。")]),
    dict(start="09:48", end="09:50", category="口述给AI", title="让AI改落地页标题", importance=2,
         summary="让 AI 把落地页标题改短，突出「三天出方案」，给三个版本。",
         points=["标题控制在 15 字以内", "重点突出三天出方案"], todos=[{"who": "AI", "what": "给落地页标题写三个版本", "due": ""}],
         lines=[("09:48:05", "我", "你帮我把落地页的标题改一下，现在太长了。"),
                ("09:48:16", "我", "控制在十五个字以内，重点突出三天出方案，给我三个版本。")]),
    dict(start="10:15", end="10:42", category="客户沟通", title="和王总对合同付款条款", importance=3, project="华东渠道合作",
         summary="和王总过了一遍渠道合作合同。王总希望首付款从 50% 降到 30%，我答应回去和财务商量，周三前给答复。双方同意先在华东三个城市试点三个月，按月结算。",
         points=["王总要求首付款从 50% 降到 30%", "试点范围：华东三个城市，三个月", "结算按月"],
         todos=[{"who": "我", "what": "和财务商量首付款比例，给王总答复", "due": "周三前"}, {"who": "王总", "what": "发三个试点城市的门店名单", "due": "本周"}],
         decisions=["先在华东三个城市试点三个月"], highlights=["首付款我回去和财务商量，周三前给您答复"],
         people=["王总", "小陈"], memories=["王总那边的对接人是助理小陈", "王总习惯下午开会，上午不太回消息"],
         lines=[("10:15:08", "其他人", "合同我看了，别的都没问题，就是首付款比例有点高。"),
                ("10:15:16", "我", "您那边希望是多少？"),
                ("10:15:21", "其他人", "三成吧，五成对我们现金流压力太大了。"),
                ("10:15:33", "我", "这个我得回去和财务商量一下，周三前给您答复。"),
                ("10:16:02", "其他人", "行，试点的话我们先做三个城市。"),
                ("10:16:10", "我", "可以，就按三个月来，按月结算。"),
                ("10:41:50", "其他人", "门店名单我这周让小陈发给你。")]),
    dict(start="12:10", end="12:38", category="生活闲聊", title="午饭聊装修", importance=1,
         summary="午饭时聊了新家装修和地板怎么选。",
         lines=[("12:10:20", "其他人", "你家地板最后选的实木还是复合的？"), ("12:10:26", "我", "复合的，家里有猫，实木太怕划了。")]),
    dict(start="14:05", end="14:40", category="同事讨论", title="新人培训怎么排", importance=2,
         summary="和阿杰商量下周两个新人的培训。第一周跟岗看投放后台，第二周开始各自管一个小计划。阿杰负责写一份上手清单。",
         points=["第一周跟岗，只看不动", "第二周每人独立管一个日预算 100 元的小计划"],
         todos=[{"who": "阿杰", "what": "写一份新人上手清单", "due": "下周一前"}], decisions=["第一周跟岗，第二周独立管一个小计划"], people=["阿杰"],
         lines=[("14:05:12", "其他人", "下周来的两个人，我想先让他们跟一周。"),
                ("14:05:20", "我", "可以，第二周就让他们自己管一个小计划，日预算一百就行。"),
                ("14:05:31", "其他人", "那我写个上手清单，周一前给你。")]),
    dict(start="15:30", end="15:32", category="口述给AI", title="让AI汇总上周素材数据", importance=2, project="双十一投放",
         summary="让 AI 把上周每条素材的花费、点击率和转化成本整理成表，按点击率排好，发到群里。",
         todos=[{"who": "AI", "what": "整理上周素材数据表，按点击率排序", "due": ""}],
         lines=[("15:30:04", "我", "把上周每条素材的花费、点击率、转化成本整理成一张表，按点击率从高到低排，弄好发群里。")]),
    dict(start="16:20", end="16:55", category="工作会议", title="和设计对双十一主视觉", importance=2, project="双十一投放",
         summary="和设计过了双十一主视觉的三个方向，选了暖色那版。设计周四前出完整套图。",
         points=["三个方向：暖色、黑金、插画", "暖色那版和店铺现在的风格最接近"],
         todos=[{"who": "设计", "what": "出双十一整套主视觉", "due": "周四前"}], decisions=["主视觉用暖色方向"], people=["设计"],
         lines=[("16:20:15", "其他人", "三个方向都在这了，暖色、黑金、插画。"),
                ("16:21:02", "我", "暖色这版跟店里现在的风格最搭，就它吧。"),
                ("16:21:10", "其他人", "好，那我周四前把整套出完。")]),
]

EARLIER = [  # 前几天的事，给项目时间线用
    dict(days=3, start="15:00", end="15:40", category="工作会议", title="双十一投放启动会", importance=2, project="双十一投放",
         summary="定了双十一投放的节奏：本周准备素材，下周开始小预算测试，11 月 1 日起加量。"),
    dict(days=5, start="16:10", end="16:35", category="客户沟通", title="和王总第一次通话", importance=2, project="华东渠道合作",
         summary="王总有意做我们的华东渠道，聊了合作方式和分成比例，约好下周过合同。"),
]

NOTES = [
    dict(t="11:20", intent="记录", title="直播请老学员", text="下个月的直播可以请两个老学员来讲讲他们是怎么用的。"),
    dict(t="17:40", intent="待办", title="寄合同给王总", text="提醒我周五前把盖好章的合同寄给王总。", due="周五前"),
]

PROJECTS = {
    "双十一投放": dict(
        summary="双十一投放上周启动。上周点击率因为素材疲劳从 2.1% 掉到 1.2%，本周先把预算控制在每天 600 元，等新素材。主视觉已选定暖色方向，设计周四前出整套图。",
        stage="等周三新素材上线，再决定要不要加预算",
        risks=["新素材周三上不了，点击率会继续往下掉", "主视觉周四才出，留给投放测试的时间只有一周"],
        next_questions=["小林的三版新素材周三能不能准时上？", "新素材跑两天后，点击率能不能回到 2% 以上？"],
        commitments=[{"who": "小林", "what": "三版新素材", "status": "未完成", "when": "今天例会"},
                     {"who": "我", "what": "把上周投放数据发到群里", "status": "已完成", "when": "今天例会"},
                     {"who": "设计", "what": "双十一整套主视觉", "status": "未完成", "when": "今天下午"}]),
    "华东渠道合作": dict(
        summary="和王总谈华东渠道合作。上周第一次通话聊了合作方式，今天过了合同，定下华东三个城市试点三个月，剩下的分歧只有首付款比例。",
        stage="等我和财务商量首付款，周三前答复王总",
        risks=["首付款降到 30% 会压我们自己的现金流"],
        next_questions=["财务能接受的最低首付款是多少？", "三个试点城市具体是哪几个？"],
        commitments=[{"who": "我", "what": "周三前答复首付款比例", "status": "未完成", "when": "今天上午"},
                     {"who": "王总", "what": "本周发试点门店名单", "status": "未完成", "when": "今天上午"}]),
}

DIGEST = {
    "headline": "上午定了本周投放预算、和王总谈下试点，下午排好新人培训、选定双十一主视觉",
    "blocks": [{"name": "上午", "summary": "例会定下本周每天 600 元的投放预算，等周三新素材再决定加不加；和王总过了合同，华东三个城市试点三个月，首付款比例回去和财务商量。"},
               {"name": "下午", "summary": "和阿杰排好新人两周的培训；让 AI 汇总了上周素材数据；和设计选定双十一主视觉的暖色方向。"}],
    "todos": [{"who": "我", "what": "和财务商量首付款，答复王总", "due": "周三前"}, {"who": "小林", "what": "出三版新素材", "due": "周三前"},
              {"who": "我", "what": "把盖好章的合同寄给王总", "due": "周五前"}],
    "commitments": ["周三前答复王总首付款比例", "今天把上周投放数据发到群里"],
    "risks": ["新素材周三要是还没上，本周投放量会继续往下掉"],
}

INSIGHTS = [
    dict(insight="开会时你习惯先给结论、再问意见，之后很少有人提反对。",
         evidence=["例会上你先说「这周预算先别加」，之后没人再提加预算", "对主视觉时你先说暖色最搭，另外两个方向就没再讨论"],
         suggestion="下次开会先问一句「你们怎么看」，等两三个人说完再给结论。"),
    dict(insight="答应别人的事，常常不说具体时间。",
         evidence=["和王总先说「回去商量一下」，对方追问后才补了「周三前」", "上周四对阿杰说「这个我看一下」，没说什么时候"],
         suggestion="答应一件事时当场把时间说出来，听记会自动把它记成一条承诺。"),
]


def build():
    DEMO.mkdir(parents=True, exist_ok=True)
    if config.DB_PATH.exists():
        for f in DEMO.glob("tingji.db*"):
            f.unlink()
    con = connect()
    now = datetime.now(TZ)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    pid = {}
    for name, p in PROJECTS.items():
        cur = con.execute("INSERT INTO projects(name, summary, stage, risks, next_questions, commitments, created, updated) VALUES(?,?,?,?,?,?,?,?)",
                          (name, p["summary"], p["stage"], J(p["risks"]), J(p["next_questions"]), J(p["commitments"]),
                           at(day - timedelta(days=5), "16:10"), at(day, "16:55")))
        pid[name] = cur.lastrowid

    def add_event(d, e):
        s, t = at(d, e["start"]), at(d, e["end"])
        cur = con.execute(
            "INSERT INTO events(start_ms, end_ms, status, category, title, summary, importance, points, todos, decisions, highlights, people, memories, project_id, speech_ms, created, updated) "
            "VALUES(?,?,'done',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s, t, e["category"], e["title"], e["summary"], e["importance"],
             *(J(e.get(k, [])) for k in ("points", "todos", "decisions", "highlights", "people", "memories")),
             pid.get(e.get("project")), t - s, t, t))
        piece = con.execute("INSERT INTO pieces(source, start_ms, end_ms, speech_ms, audio_path, timemap, status, created, done) "
                            "VALUES('mic',?,?,?,NULL,?,'done',?,?)", (s, t, t - s, J([[0, s, t - s]]), t, t)).lastrowid
        for hms, who, text in e.get("lines", []):
            ms = at(d, hms)
            con.execute("INSERT INTO sentences(piece_id, source, start_ms, end_ms, text, speaker, event_id) VALUES(?,'mic',?,?,?,?,?)",
                        (piece, ms, ms + 4000, text, who, cur.lastrowid))

    for e in EARLIER:
        add_event(day - timedelta(days=e["days"]), e)
    for e in TODAY:
        add_event(day, e)
    for n in NOTES:
        ms = at(day, n["t"])
        con.execute("INSERT INTO notes(start_ms, end_ms, status, text, intent, title, answer, due, created, updated) VALUES(?,?,'done',?,?,?,'',?,?,?)",
                    (ms, ms + 6000, n["text"], n["intent"], n["title"], n.get("due", ""), ms, ms))
    con.execute("INSERT INTO digests(day, content, created) VALUES(?,?,?)", (day.strftime("%Y-%m-%d"), J(DIGEST), at(day, "18:05")))
    week = f"{(day - timedelta(days=6)).strftime('%Y-%m-%d')}～{day.strftime('%Y-%m-%d')}"
    for x in INSIGHTS:
        con.execute("INSERT INTO insights(period, insight, evidence, suggestion, created) VALUES(?,?,?,?,?)",
                    (week, x["insight"], J(x["evidence"]), x["suggestion"], at(day, "18:05")))
    kv_set(con, "processed_until", at(day, "18:05"))
    con.commit()
    (DEMO / "settings.json").write_text(J({"web_port": PORT}))


def keep_status_fresh():
    """网页顶上的「录音中」看的是录音器每 10 秒写一次的 status.json，示例里替它写"""
    while True:
        (DEMO / "status.json").write_text(J({"ts": int(time.time() * 1000), "enabled": True, "capturing": True, "paused_for_media": [],
                                             "mic": {"running": True, "device": "MacBook Pro 麦克风", "error": None}}))
        time.sleep(5)


if __name__ == "__main__":
    build()
    threading.Thread(target=keep_status_fresh, daemon=True).start()
    from tingji import web
    print(f"示例数据在 {DEMO}，打开 http://127.0.0.1:{PORT}/  （Ctrl+C 退出）")
    try:
        web.main()
    except KeyboardInterrupt:
        pass
