# -*- coding: utf-8 -*-
"""库体检（lint）：这个库自己有没有长出毛病。

    python tools/lint.py            # 报告
    python tools/lint.py --json     # 给网页 / 自检用

Karpathy 那套 LLM 维护知识库的三层是 raw / wiki / schema，三个动作是 ingest / query / **lint**。
前两个我们有了，lint 没有 —— 缺了它，库只会越长越脏而没人报账。这里查九件事：
  1 悬空双链      指向不存在的卡（图谱上的幽灵节点）
  2 孤儿卡        没有任何入链，也没被原料引用
  3 单来源概念卡  概念页只长在一个来源上 —— 该合并或该被第二篇印证
  4 太薄的卡      正文不足 90 字：一句口号不算笔记
  5 原料没消化稿  只有原文、从没处理过
  6 长期待吃透    超过 30 天还挂着「待吃透」
  7 原料里两份卡列表  同一个主张被写了两遍（历史上 digest 和迁移脚本各写了一份）
  8 原料里混进处理日志 「我什么时候重跑的、备份在哪个 trash、用的哪个引擎」不属于内容
  9 导航页幽灵链接    index.md / progress.md 里点了没有卡 —— 卡片重做过，导航没跟着改
"""
import io
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import server   # 复用 VAULT / CARD_DIR 的判定，避免两套口径

VAULT = Path(server.VAULT)
CARD_DIRS = ["wiki/concepts", "wiki/connections", "wiki/questions"]
THIN_CHARS = 90
STALE_DAYS = 30
CARD_SEC_RE = re.compile(r"^#{2,4}\s*(?:本次长成的卡|%s)" % server.CARD_SECTION, re.M)
# 只对项目组有意义、对读者没意义的过程话（正文里出现就是污染）
CLIP_LOG_RE = re.compile(r"旧稿已备份到|本节由当前会话的模型|上一条只送了|说明：这条原本是"
                         r"|^\s*>?\s*20\d\d-\d\d-\d\d 改：|没走 DeepSeek|maxOcrImages", re.M)


def read(rel):
    return io.open(VAULT / rel, encoding="utf-8", errors="ignore").read()


def frontmatter(text):
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else ""


def body(text):
    return re.sub(r"\A---.*?\n---\s*", "", text, flags=re.S)


def title_of(rel, text):
    t = re.search(r"^title:\s*(.+)$", frontmatter(text), re.M)
    return (t.group(1).strip().strip('"') if t else Path(rel).stem)


def collect():
    notes = {}          # rel -> {"title","text","body","fm"}
    for sub in CARD_DIRS + ["raw/clips", "raw"]:
        d = VAULT / sub
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.md")):
            rel = str(p.relative_to(VAULT)).replace("\\", "/")
            if "trash" in rel:
                continue
            txt = read(rel)
            notes[rel] = {"title": title_of(rel, txt), "text": txt,
                          "body": body(txt), "fm": frontmatter(txt)}
    return notes


def main():
    as_json = "--json" in sys.argv
    notes = collect()
    # 可解析目标：文件名 ∪ title ∪ aliases（Obsidian 三种都认）
    targets = set()
    for rel, n in notes.items():
        targets.add(Path(rel).stem)
        targets.add(n["title"])
        for a in re.findall(r"^\s+-\s+\"?([^\"\n]+?)\"?\s*$", n["fm"], re.M):
            targets.add(a.strip())
    inbound = {rel: 0 for rel in notes}
    dangling, thin, single_src, stale, no_digest = [], [], [], [], []
    dup_card_sec, clip_logs = [], []

    for rel, n in notes.items():
        for m in re.finditer(r"\[\[([^\]\|#]+)", n["text"]):
            t = m.group(1).strip()
            if t not in targets:
                dangling.append((rel, t))
            else:
                for r2, n2 in notes.items():
                    if r2 != rel and t in (Path(r2).stem, n2["title"]):
                        inbound[r2] += 1
        if rel.startswith("wiki/"):
            chars = sum(len(re.sub(r"^[-*>#\s]+", "", l))
                        for l in n["body"].split("\n")
                        if l.strip() and not l.startswith("#")
                        and "用自己的话重写" not in l and not l.startswith("出处：")
                        and "判定标准" not in l and "不是模型口径" not in l)
            if chars < THIN_CHARS:
                thin.append((rel, chars))
            srcs = set(re.findall(r"来自《([^》]+)》", n["text"]) +
                       re.findall(r"出处：\[\[([^\]]+)\]\]", n["text"]) +
                       re.findall(r'^\s+-\s+"([^"|]+)', n["fm"], re.M))
            if rel.startswith("wiki/concepts") and len(srcs) <= 1:
                single_src.append((rel, len(srcs)))
            m = re.search(r"^captured_at:\s*(\d{4}-\d{2}-\d{2})", n["fm"], re.M)
            if m and "status: 待吃透" in n["fm"]:
                age = (time.time() - time.mktime(time.strptime(m.group(1), "%Y-%m-%d"))) / 86400
                if age > STALE_DAYS:
                    stale.append((rel, int(age)))
        if rel.startswith("raw/clips"):
            if "# 消化稿" not in n["text"]:
                no_digest.append(rel)
            n_sec = len(CARD_SEC_RE.findall(n["text"]))
            if n_sec > 1:
                dup_card_sec.append((rel, n_sec))
            hits = [l.strip()[:60] for l in n["text"].splitlines() if CLIP_LOG_RE.search(l)]
            if hits:
                clip_logs.append((rel, hits))

    orphans = [(r, n["title"]) for r, n in notes.items()
               if r.startswith("wiki/") and inbound[r] == 0]
    # 9 导航页里的悬空双链：index.md / progress.md 是入口，点开没有卡就是幽灵
    nav_ghosts = []
    for f in ("index.md", "progress.md"):
        fp = VAULT / f
        if not fp.exists():
            continue
        links = {x.strip() for x in re.findall(r"\[\[([^\]\|#]+)",
                                               fp.read_text(encoding="utf-8"))}
        nav_ghosts += [(f, t) for t in sorted(links - {""}) if t not in targets]
    report = {
        "vault": str(VAULT), "notes": len(notes),
        "dangling": [{"file": a, "target": b} for a, b in dangling],
        "orphans": [{"file": a, "title": b} for a, b in orphans],
        "thin": [{"file": a, "chars": c} for a, c in thin],
        "single_source_concepts": [{"file": a, "sources": c} for a, c in single_src],
        "stale": [{"file": a, "days": c} for a, c in stale],
        "clips_without_digest": no_digest,
        "duplicate_card_section": [{"file": a, "count": c} for a, c in dup_card_sec],
        "clip_logs": [{"file": a, "lines": h} for a, h in clip_logs],
        "nav_ghosts": [{"file": a, "target": b} for a, b in nav_ghosts],
    }
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    def sect(name, rows, fmt):
        print("\n[%s] %d" % (name, len(rows)))
        for r in rows[:8]:
            print("   " + fmt(r))
        if len(rows) > 8:
            print("   …另有 %d 条" % (len(rows) - 8))

    print("库体检：%s ｜ 笔记 %d 份" % (VAULT, len(notes)))
    sect("1 悬空双链（图谱幽灵节点）", report["dangling"],
         lambda r: "%s → %s" % (r["file"][:44], r["target"][:30]))
    sect("2 孤儿卡（没有任何入链）", report["orphans"],
         lambda r: r["file"][:60])
    sect("3 单来源概念卡（该被第二篇印证或合并）", report["single_source_concepts"],
         lambda r: "%d 个来源  %s" % (r["sources"], r["file"][:52]))
    sect("4 太薄的卡（<%d 字）" % THIN_CHARS, report["thin"],
         lambda r: "%3d 字  %s" % (r["chars"], r["file"][:52]))
    sect("5 原料没有消化稿", [{"file": x} for x in report["clips_without_digest"]],
         lambda r: r["file"][:60])
    sect("6 超过 %d 天仍「待吃透」" % STALE_DAYS, report["stale"],
         lambda r: "%3d 天  %s" % (r["days"], r["file"][:52]))
    sect("7 原料里有两份卡列表", report["duplicate_card_section"],
         lambda r: "%d 份  %s" % (r["count"], r["file"][:52]))
    sect("8 原料正文里混着处理日志", report["clip_logs"],
         lambda r: "%s ｜ %s" % (r["file"][:44], " / ".join(r["lines"][:2])[:70]))
    sect("9 导航页（index/progress）里的悬空双链", report["nav_ghosts"],
         lambda r: "%s → %s" % (r["file"], r["target"][:40]))
    score = sum(len(report[k]) for k in ("dangling", "orphans", "thin", "stale",
                                         "duplicate_card_section", "clip_logs"))
    print("\n结论：需要动手的硬问题 %d 个（悬空 / 孤儿 / 太薄 / 过期 / 重复卡列表 / 处理日志之和）；"
          "单来源概念 %d 张是设计问题，不是错误。"
          % (score, len(report["single_source_concepts"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
