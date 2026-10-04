# -*- coding: utf-8 -*-
"""原料清污：卡列表只留一份、把我写的处理日志从正文里摘掉。

原则：留下「这段文字可能有错、不是作者原话」这种读者需要的说明；
删掉「我什么时候重跑的、备份在哪个 trash 文件、用了哪个引擎、有没有调 DeepSeek」这种只对项目组有意义的过程日志。

用法:
    python tools/clean_clips.py                # 全部演练（只打 diff，不写盘）
    python tools/clean_clips.py --one 关键词    # 只看文件名含关键词的那份
    python tools/clean_clips.py --apply         # 真写；写前每份备份到 inbox/trash/*.pre-clean-*.md
"""
import argparse
import difflib
import io
import re
import sys
import time
from pathlib import Path, PurePosixPath

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import server  # noqa: E402  用生产同一套 render_card_section / strip_card_sections

VAULT = Path(server.VAULT)

# 整行都是过程日志 → 删
LOG_LINE = re.compile(
    r"^>\s*(?:20\d\d-\d\d-\d\d 改：|说明：这条原本是|本节由当前会话的模型撰写"
    r"|上一条只送了|旧稿已备份到)")
# 小节标题里的引擎名 / 日期 → 换成中性说法（保留「图 7–图 12」这种范围）
IMG_HEAD = re.compile(r"^##+\s*(图片里的文字|画面里的文字)（([^）]*)）\s*$", re.M)


def _neutral_img_head(m):
    rng = re.search(r"图\s*\d+\s*[–—-]\s*图\s*\d+", m.group(2) or "")
    return "## %s（%s非原文，可能识别有误）" % (
        m.group(1), rng.group(0) + "，" if rng else "")


HEAD_FIX = [
    (IMG_HEAD, _neutral_img_head),
    (re.compile(r"^##+\s*逐字稿（faster-whisper 本地转写，(.+?)）\s*$", re.M),
     r"## 逐字稿（机器转写，\1，可能有错字）"),
    (re.compile(r"^##+\s*逐字稿（faster-whisper 本地转写）\s*$", re.M),
     "## 逐字稿（机器转写，可能有错字）"),
]
LINE_FIX = [
    # 小节标题里出现引擎名（whisper small / faster-whisper / FunASR）→ 换成「机器转写」：
    # 换引擎不该把旧原料的标题一起带跑
    (re.compile(r"^(#{1,6}[^\n]*)", re.M),
     lambda m: re.sub(r"机器转写(?:\s*转写)+", "机器转写",
                     re.sub(r"机器\s*转写", "机器转写",
                            re.sub(r"whisper small|faster-whisper|FunASR paraformer-zh|FunASR|paraformer-zh",
                                   "机器转写", m.group(0))))),
    # 没有字幕文件是事实，但「payload 里只有 subtitleDefaultOpen」是我的实现细节
    (re.compile(r"^>\s*视频没有平台字幕文件[^\n]*\n>[^\n]*$", re.M),
     "> 这条视频没有平台字幕文件，以下是抽帧认出的画面文字；语音转写容易把专有名词听错，以这一节为准核对。"),
    (re.compile(r"^>\s*转写误差说明：whisper small 对专有名词识别有偏差，", re.M),
     "> 转写误差说明：机器转写对专有名词识别有偏差，"),
    (re.compile(r"^>\s*本节由当前会话的模型撰写（未调用 DeepSeek API），(最近一次重生成：[0-9: -]+)。*$", re.M),
     r"> \1"),
    (re.compile(r"^>\s*最近一次重生成：([0-9: -]+)（旧稿已备份到[^）]*）\s*$", re.M),
     r"> 最近一次重生成：\1"),
]

LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
KIND_RE = re.compile(r"^-\s*(概念|连接|问题|并入已有卡[^：]*|已存在同名卡[^：]*)：(.*)$")
DIR_KIND = {"concepts": "concept", "connections": "connection", "questions": "question"}
LABEL_KIND = {"概念": "concept", "并入已有卡": "concept", "已存在同名卡": "concept",
              "连接": "connection", "问题": "question"}


def collect_card_links(s):
    """把新旧两种卡列表里的链接并起来（按首次出现顺序），并标出没落盘成卡的。"""
    seen, ghosts = [], []
    for m in re.finditer(r"^#{2,4}\s*(?:本次长成的卡|这张原料长成的卡)[^\n]*\n"
                         r"((?:(?!^#)[^\n]*\n?)*)", s, re.M):
        for line in m.group(1).splitlines():
            mm = KIND_RE.match(line.strip())
            label = (mm.group(1).split("（")[0] if mm else "概念")
            body = mm.group(2) if mm else line
            for lk in LINK_RE.findall(body):
                lk = lk.strip()
                if lk and lk not in [x[0] for x in seen]:
                    seen.append((lk, label))
    out = []
    for lk, label in seen:
        rel = server.find_card_file(lk)
        if rel:
            kind = DIR_KIND.get(PurePosixPath(str(rel).replace("\\", "/")).parent.name, "concept")
        else:
            kind = LABEL_KIND.get(label, "concept")
            ghosts.append(lk)
        out.append((kind, lk))
    return out, ghosts


def clean_one(path):
    s = old = path.read_text(encoding="utf-8")
    pairs, ghosts = collect_card_links(s)
    s = server.strip_card_sections(s)            # 1) 两份都删，稍后只写一份
    for pat, rep in HEAD_FIX + LINE_FIX:          # 2) 中性化 / 删过程日志
        s = pat.sub(rep, s)
    s = "\n".join(l for l in s.splitlines() if not LOG_LINE.match(l))
    made = [(k, n, "", "new") for k, n in pairs]
    if made:                                     # 3) 文末唯一一份
        s = s.rstrip("\n") + "\n\n" + server.render_card_section(made)
    s = re.sub(r"\n{3,}", "\n\n", s).rstrip("\n") + "\n"
    return old, s, ghosts, pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--one", default="")
    args = ap.parse_args()
    files = sorted((VAULT / "raw" / "clips").glob("*.md"))
    if args.one:
        files = [f for f in files if args.one in f.name]
    if not files:
        print("没有匹配到原料")
        return 1
    trash = VAULT / "inbox" / "trash"
    ts = time.strftime("%Y%m%d%H%M%S")
    changed = 0
    for p in files:
        old, new, ghosts, pairs = clean_one(p)
        cnt = {}
        for k, _n in pairs:
            cnt[server.CARD_LABEL[k]] = cnt.get(server.CARD_LABEL[k], 0) + 1
        print("== %s（卡 %d 张：%s）" % (
            p.name, len(pairs),
            "、".join("%s%d" % (v, cnt[v]) for v in ("概念", "连接", "问题") if v in cnt)))
        if ghosts:
            print("   ！这些链接没有对应卡文件：%s" % "、".join(ghosts))
        if old == new:
            print("   不用动")
            continue
        changed += 1
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(),
                                         "旧", "新", lineterm="", n=1):
            print("   " + line[:200])
        if args.apply:
            trash.mkdir(parents=True, exist_ok=True)
            (trash / ("%s.pre-clean-%s.md" % (p.stem, ts))).write_text(old, encoding="utf-8")
            p.write_text(new, encoding="utf-8")
            print("   [已写，旧版在 inbox/trash/%s.pre-clean-%s.md]" % (p.stem, ts))
    print("\n%d 份要动 / 共 %d 份%s" % (changed, len(files),
                                  "" if args.apply else "（演练，没写盘）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
