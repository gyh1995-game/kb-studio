# -*- coding: utf-8 -*-
"""内容输出规范回归检查（第三轮）。

    python tools/spec_check.py

不打网络、不调模型：call_deepseek 用一份写死的消化稿顶替，
库指到 %TEMP%/kb-spec-vault 的一份空库，跑完直接删。
验的是「规范有没有真的落到文件和队列里」，不是模型答得好不好。
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

VAULT = Path(os.environ.get("KB_SPEC_VAULT") or (Path(tempfile.gettempdir()) / "kb-spec-vault"))
if VAULT.exists():
    shutil.rmtree(VAULT, ignore_errors=True)
for d in ("raw/clips", "raw/assets", "wiki/concepts", "wiki/connections",
          "wiki/questions", "inbox", "inbox/trash"):
    (VAULT / d).mkdir(parents=True, exist_ok=True)
os.environ["KB_VAULT"] = str(VAULT)

import server          # noqa: E402  必须在改完 KB_VAULT 之后才 import
import fetchers        # noqa: E402

TITLE = "知识库的单位是判断不是文件"
DIGEST = """## 核心观点
- 收藏不等于拥有，打开过才算
- 分层是为了让下一次能复用，不是为了整齐

## 可执行清单
- 每次入库强制产出三条疑问

## 概念候选
- 知识库的价值在编译层不在存储层 —— 存进来只是把别人的话搬一遍，只有重写成果点和疑问，下一次决策才用得上｜为什么成立：作者把自己十年前的笔记重编成四十张卡之后，三个月内主动引用了十一次，而同一批原始收藏再没打开过｜边界：需要快速留证的场景（会议纪要、合同条款）反而要保全文，不该压缩｜用在哪：决定一条内容要不要建卡时先问它能不能改我某个动作
- 强制产出疑问比摘要更能防止假读 —— 摘要可以照着原文抄，疑问必须指出自己哪里还没想通｜为什么成立：那篇讲四层结构的原文里写了他自己做过对照，只写摘要的三个月后完全想不起读过什么｜边界：纯工具类操作手册没有可质疑的主张，硬凑疑问会生成废话｜用在哪：读完一段内容准备关掉之前

## 连接候选
- 编译层优先 ↔ 强制产出疑问 —— 前置｜两条说的是同一件事的前后步：先有疑问才有要编译的东西

## 我的疑问
- 边界：这套分层在只看不写的资料库上还成立吗 —— 原文那四十张卡全是他自己写的 —— 无 —— 不确定纯收藏库有没有复用需求
- 证据：作者说十一次引用是他自己数的还是有记录 —— 他把引用次数写在第三段里 —— 只有一句「主动引用了十一次」，没有日志 —— 分不清是估的还是查的
- 与我：我现在的收藏有多少真的变成了下一次的判断 —— 我库里三百条收藏从没回看过 —— 无 —— 卡在不知道该按什么口径统计

## 值得记住的原话
> 工具不重要，重要的是每次入库都产生下一次要用的判断。

## 下一步
- 把最近三十条收藏拿五条出来重写

## 主题标签
- 知识管理、提示词工程、#c4734a、已读、边界、readBody、抖音
"""

RESULT = []


def check(name, ok, detail=""):
    RESULT.append((name, bool(ok), detail))
    print("  %s %-34s %s" % ("✓" if ok else "✗", name, str(detail)[:130]))


def fm_of(rel):
    return server.parse_frontmatter((VAULT / rel).read_text(encoding="utf-8"))[0]


def body_of(rel):
    return (VAULT / rel).read_text(encoding="utf-8")


class FakeHandler(server.Handler):
    """绕开 HTTP，直接调 handler 上的方法：这些方法都只依赖 self 的类作用域。"""
    def __init__(self):            # noqa: D107 - 故意不调用 BaseHTTPRequestHandler.__init__
        pass


H = FakeHandler()
server.call_deepseek = lambda *a, **k: DIGEST

# ---------------------------------------------------------------- 1. 消化写盘
r = H.digest({"text": "知识库的单位是判断，不是文件。" * 60, "title": TITLE,
              "platform": "网页", "url": "https://example.com/a", "author": "某人",
              "compile": True, "images": False, "ocr": False})
check("digest 成功", r.get("ok"), r.get("error", ""))
clip = r.get("clip", "")
fm = fm_of(clip)
check("原料有 media_type/est_min", fm.get("media_type") == "文本" and int(fm.get("est_min", 0)) > 0,
      "%s / %s" % (fm.get("media_type"), fm.get("est_min")))
check("原料 status 是五个值之一", server.norm_status(fm.get("status")) == fm.get("status"),
      fm.get("status"))
check("原料不写 L 等级", "level" not in fm, fm.get("level", "（无）"))
check("正文里没有卡列表小节", server.CARD_SECTION not in body_of(clip))
check("digest 返回里带 media_type/est_min", bool(r.get("media_type")) and int(r.get("est_min", 0)) > 0,
      "%s %s" % (r.get("media_type"), r.get("est_min")))

cards = r.get("cards", [])
check("长出 2 概念 / 1 连接 / 3 问题", len(cards) == 6, "、".join(c["name"][:12] for c in cards))
concepts = [c for c in cards if c["kind"] == "concept"]
cfm = fm_of(concepts[0]["path"])
check("卡片带 from_raw", cfm.get("from_raw") == clip, cfm.get("from_raw", "（缺）"))
qcards2 = [c for c in cards if c["kind"] == "question"]
qb = body_of(qcards2[0]["path"])
for seg in ("是什么让我问出这句", "我已经知道什么", "目前的猜测", "什么算想通了", "下一步要什么材料"):
    check("问题卡带「%s」这一段" % seg, seg in qb, qb[:80])
qbody = server.parse_frontmatter((VAULT / qcards2[0]["path"]).read_text(encoding="utf-8"))[1]
qlines = [l for l in qbody.splitlines() if l.startswith("> ")]
check("问题卡正文里那句问句不再拖着整行补充",
      qlines and "——" not in qlines[0], qlines[0][:60] if qlines else "（没有问句行）")
check("dim 认得出维度，不再兜成追问",
      fm_of(qcards2[0]["path"]).get("dim") in ("边界", "证据", "与我", "待验证", "反例", "前提"),
      fm_of(qcards2[0]["path"]).get("dim"))
check("卡片带 media_type + est_min", cfm.get("media_type") == "文本" and int(cfm.get("est_min", 0)) > 0,
      "%s / %s" % (cfm.get("media_type"), cfm.get("est_min")))
cb = body_of(concepts[0]["path"])
check("卡正文里出处只有一个标准写法", cb.count("出处：[[") == 1 and "（知乎）（" not in cb,
      repr([l for l in cb.split("\n") if l.startswith("出处")]))
check("维度行不被出处规则误删",
      server.strip_meta_echo("边界：留着\n出处：删掉\n用在哪：留着") == "边界：留着\n用在哪：留着",
      repr(server.strip_meta_echo("边界：留着\n出处：删掉\n用在哪：留着")))
check("卡片 status 归一", cfm.get("status") in server.STATUSES, cfm.get("status"))
tags = cfm.get("tags") or []
check("卡片标签只剩主题", set(tags) <= {"知识管理", "提示词工程"}, tags)
bad = [t for t in tags if not server.tag_ok(t)]
check("没有色值 / 标识符 / 状态词标签", not bad, bad)

qcards = [c for c in cards if c["kind"] == "question"]
qfm = fm_of(qcards[0]["path"])
check("维度进 dim 字段而不是 tags", qfm.get("dim") in ("边界", "证据", "与我", "待验证") and
      not any(t in ("边界", "证据", "与我", "待验证") for t in (qfm.get("tags") or [])),
      "dim=%s tags=%s" % (qfm.get("dim"), qfm.get("tags")))

tasks = json.loads((VAULT / "inbox" / "待复习任务.json").read_text(encoding="utf-8"))["tasks"]
qs_tasks = [t for t in tasks if t.get("category") == "消化提问"]
raw_tasks = [t for t in tasks if t.get("category") == "原料重读"]
check("队列两个入口：3 条疑问 + 1 条原料重读", len(qs_tasks) == 3 and len(raw_tasks) == 1,
      "疑问 %d / 原料 %d" % (len(qs_tasks), len(raw_tasks)))
check("原料本身就在那份「重读原料」条目里", raw_tasks and raw_tasks[0]["card"] == clip
      and raw_tasks[0]["title"].startswith("重读原料："),
      raw_tasks[0]["title"] if raw_tasks else "（没有）")
check("到期日：疑问 +3 天、原料 +7 天",
      raw_tasks[0]["due"] > qs_tasks[0]["due"],
      "%s vs %s" % (qs_tasks[0]["due"], raw_tasks[0]["due"]))
t0 = qs_tasks[0]
check("条目带 card + raw 两条路径", t0.get("card", "").startswith("wiki/questions") and t0.get("raw") == clip,
      "%s / %s" % (t0.get("card"), t0.get("raw")))
check("条目带 类型 / 预估 / 来源", t0.get("media_type") == "文本" and int(t0.get("est_min", 0)) > 0
      and t0.get("platform") == "网页" and t0.get("origin") == TITLE,
      " / ".join(str(t0.get(k)) for k in ("media_type", "est_min", "platform", "origin")))

# ---------------------------------------------------------------- 2. 扫库口径
cs = server.scan_vault()
alltags = set()
for c in cs:
    alltags |= set(c["tags"])
check("扫库结果里没有脏标签", not any(t in ("消化生成", "手写", "已读", "待实践", "c4734a", "readBody")
                              for t in alltags), sorted(alltags)[:8])
check("每张卡都有 media_type 和 est_min",
      all(c["media_type"] for c in cs if c["kind"] != "system") and
      all(c["est_min"] > 0 for c in cs if c["kind"] != "system"))
grew = [c for c in cs if c.get("from_raw") == clip]
check("原料按 from_raw 能找到长成的卡", len(grew) == len(cards), "%d / %d" % (len(grew), len(cards)))

# ---------------------------------------------------------------- 3. 状态与标签写回
r = H.set_status({"path": clip, "status": "已消化"})
check("状态写回并归一（已消化 → 已读）", r.get("ok") and r.get("status") == "已读", r)
r = H.set_status({"path": clip, "status": "今天心情好所以看完了"})
check("整句话的状态被拒", not r.get("ok"), r.get("error", ""))
r = H.set_tags({"path": clip, "tags": ["知识管理", "#c4734a", "readBody", "已读"]})
check("脏标签存不进去", r.get("ok") and r.get("tags") == ["知识管理"], r)
r = H.set_tags({"path": clip, "tags": ["#c4734a", "readBody"]})
check("全是脏标签时明确报错", not r.get("ok"), r.get("error", ""))

# ---------------------------------------------------------------- 4. 重消化 / 重编译 / 补卡
r = H.redigest({"path": clip, "compile": True})
check("redigest 成功", r.get("ok"), r.get("error", ""))
check("redigest 后正文仍无卡列表", server.CARD_SECTION not in body_of(clip))
check("redigest 刷新了 est_min", int(fm_of(clip).get("est_min", 0)) > 0, fm_of(clip).get("est_min"))
r = H.recompile({"path": clip})
check("recompile 不调模型也能对齐卡片", r.get("ok"), r.get("error", ""))
check("recompile 没往正文塞卡列表", server.CARD_SECTION not in body_of(clip))

r = H.gen_card({"kind": "concept", "prompt": "把编译层讲清楚", "source": clip})
check("一句话生成的卡带 from_raw", r.get("ok") and fm_of(r["path"]).get("from_raw") == clip, r.get("path", r))
check("一句话生成的卡没有脏标签",
      all(server.tag_ok(t) for t in (fm_of(r["path"]).get("tags") or [])),
      fm_of(r["path"]).get("tags"))
grew2 = [c for c in server.scan_vault() if c.get("from_raw") == clip]
check("新生成的卡出现在原料的卡列表里", any(c["path"] == r["path"] for c in grew2), len(grew2))

r = H.add_card({"kind": "question", "name": "我多久真的再遇见一条收藏", "text": "收藏之后再没打开过。",
                "source": clip})
check("手写卡不再打「手写」标签", r.get("ok") and "手写" not in (fm_of(r["path"]).get("tags") or []),
      fm_of(r["path"]).get("tags"))

# ---------------------------------------------------------------- 5. 删除只留一处来源
r = H.drop_card({"path": qcards[0]["path"]})
check("删卡进回收站", r.get("ok"), r.get("trash", r))

# ---------------------------------------------------------------- 6. 断行链接拼回
broken = "\n".join(["看这个", "https://", "", "huggingface.co/ukisai/S", "", "wift-1.5-Qwen3.8-27B-GGUF",
                    "", "下一句中文 https://a.co/x 结尾"])
fixed = fetchers.unbreak_urls(broken)
check("被断开的链接拼回一条", "https://huggingface.co/ukisai/Swift-1.5-Qwen3.8-27B-GGUF" in fixed,
      repr(fixed.split("\n")[0:4]))
check("拼链接不吞字", len(broken.replace("\n", "")) == len(fixed.replace("\n", "")))
check("正常句子里的链接不被乱并", "下一句中文 https://a.co/x 结尾" in fixed)
check("段落恢复会先拼链接", "https://huggingface.co/ukisai/Swift-1.5" in fetchers.reflow_text(broken))

# ---------------------------------------------------------------- 7. 口径常数与 YAML 合法性
check("中文按 200 字/分（reading-time 的 1 汉字=1 词）", server.est_minutes(text="知" * 400) == 2,
      server.est_minutes(text="知" * 400))
check("中英混排按词折拉丁文", server.est_minutes(text=("DeepSeek flash " * 40)) == 1,
      server.est_minutes(text=("DeepSeek flash " * 40)))
check("图片按递减秒数计入", server.image_seconds(3) == 33 and server.image_seconds(0) == 0,
      server.image_seconds(3))
check("卡片比初读慢一档", server.CARD_CPM < server.READING_CPM,
      "%s < %s" % (server.CARD_CPM, server.READING_CPM))
nasty = '标题里有: 冒号 和 # 井号'
rel_n, _st = server.write_card("concept", nasty, ["主张：X"], "原料：带冒号", "https://a/b?x=1#y",
                               "知乎", "某人", {"media_type": "文本", "tags": ["AI工具"]})
fm_n = fm_of(rel_n)
check("标题里的冒号和井号不撕 YAML", fm_n.get("title") == nasty and fm_n.get("source") == "https://a/b?x=1#y",
      repr([fm_n.get("title"), fm_n.get("source")]))
check("标签折掉空白且不含逗号冒号",
      server.clean_tags(["AI 工具", "a,b", "x:y", "#c4734a", "知识管理"]) == ["AI工具", "知识管理"],
      server.clean_tags(["AI 工具", "a,b", "x:y", "#c4734a", "知识管理"]))
check("只差大小写的标签归并成一格",
      server.clean_tags(["AI工具", "ai工具", "AI工具"]) == ["AI工具"],
      server.clean_tags(["AI工具", "ai工具", "AI工具"]))
# 字数只算正文净字：frontmatter / 标记 / 裸链接都不许算进去
_md = ("---\ntitle: X\nsource: https://a.b/c\n---\n\n"
       "# X\n\n这是一段二十个字的正文，够数了。\n\n"
       "```python\nprint(1)\n```\n\n看 https://example.com/very/long/path 和 [锚文字](https://b.c/d)\n")
_fm_x, _body_x = server.parse_frontmatter(_md)
check("字数只算正文净字", server.count_words(_body_x) == 20,
      "%s（代码块和裸 URL 若算进去会到 %d）"
      % (server.count_words(_body_x), server.count_words(_body_x + " print(1) example")))
check("双链的锚文字算字、括号标记不算",
      server.count_words("接 [[概念甲]] 与 [[概念乙|别名]]") == 8,
      server.count_words("接 [[概念甲]] 与 [[概念乙|别名]]"))

print("\n%d 项检查，%d 项通过" % (len(RESULT), sum(1 for _n, o, _d in RESULT if o)))
shutil.rmtree(VAULT, ignore_errors=True)
print("临时库已删：%s 存在=%s" % (VAULT, VAULT.exists()))
sys.exit(0 if all(o for _n, o, _d in RESULT) else 1)
