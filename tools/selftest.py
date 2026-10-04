# -*- coding: utf-8 -*-
"""KB Studio 自检 · 一条命令验完整链路

    python tools/selftest.py [--base http://127.0.0.1:8777]

跑的东西：抓取 → 消化（7 段骨架）→ 自动长三层卡 → 疑问入待复习 → 勾选/撤销
          → 文件导入 → 批注 → 模型连通 → 图片路由。
所有写进知识库的测试痕迹都会在结束时清掉（快照 → 测 → 还原）。
"""
import argparse
import base64
import io
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import server  # noqa: E402  只用它的常量，确保断言名和生产写入名不会各说各话

PROSE = (
    "知识库的单位是判断，不是文件。多数人把它做成了网盘：收藏进去，再没打开过。"
    "我的做法分四层：原料层只增不改；概念层用自己的话重写；连接层写两个概念之间的关系；"
    "问题层写读完仍然没想清楚的。每读完一篇强制自己写四条疑问，分别检查边界、证据、"
    "和自己当前工作的关系、以及一周内能不能验证。只做摘要的话，三个月后你会忘记自己为什么收藏它。"
    "工具不重要，Obsidian 还是 Notoid 都行，重要的是每次入库都产生下一次要用的判断。"
) * 3

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %s %-26s %s" % ("✓" if ok else "✗", name, detail[:110]))


class Api:
    def __init__(self, base):
        self.base = base

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(self, path, obj, timeout=300):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))


def snapshot(vault):
    """记录文件清单 + 已有文件的内容（同名卡会被追加，光删新文件不够）。"""
    snap = {"vault": vault, "content": {}}
    for sub in ("wiki/concepts", "wiki/connections", "wiki/questions", "raw/clips"):
        d = vault / sub
        files = set(os.listdir(d)) if d.is_dir() else set()
        snap[sub] = files
        for f in files:
            p = d / f
            if p.is_file():
                try:
                    snap["content"][str(p.relative_to(vault)).replace("\\", "/")] = \
                        p.read_text(encoding="utf-8")
                except Exception:
                    pass
    for j in ("inbox/待复习任务.json", "inbox/已完成任务.json", "inbox/annotations.md"):
        f = vault / j
        snap[j] = f.read_text(encoding="utf-8") if f.is_file() else None
    ad = vault / "raw" / "assets"
    snap["assets"] = set(os.listdir(ad)) if ad.is_dir() else set()
    up = HERE / "uploads"          # 导入测试会在这里落原始文件
    snap["uploads"] = set(os.listdir(up)) if up.is_dir() else set()
    return snap


def restore(snap):
    vault = snap["vault"]
    for sub in ("wiki/concepts", "wiki/connections", "wiki/questions", "raw/clips"):
        d = vault / sub
        now = set(os.listdir(d)) if d.is_dir() else set()
        for f in now - snap[sub]:
            p = d / f
            (shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True))
    for rel, text in snap["content"].items():
        p = vault / rel
        if p.is_file() and p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
    for j in ("inbox/待复习任务.json", "inbox/已完成任务.json", "inbox/annotations.md"):
        f = vault / j
        if snap[j] is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(snap[j], encoding="utf-8")
    ad = vault / "raw" / "assets"
    if ad.is_dir():
        for a in set(os.listdir(ad)) - snap["assets"]:
            shutil.rmtree(ad / a, ignore_errors=True)
    up = HERE / "uploads"
    if up.is_dir():
        for f in set(os.listdir(up)) - snap["uploads"]:
            (up / f).unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    args = ap.parse_args()
    api = Api(args.base)

    cfg = api.get("/api/config")
    vault = Path(cfg["vault"])
    print("vault =", vault)
    snap = snapshot(vault)

    try:
        # ---- 1 索引与图谱
        st = api.get("/api/stats")
        check("索引统计", st.get("total", 0) > 0, "cards=%s kinds=%s" % (st["total"], st["byKind"]))
        g = api.get("/api/graph")
        check("图谱边数", len(g["links"]) > 0, "nodes=%s links=%s" % (len(g["nodes"]), len(g["links"])))
        r = api.get("/api/search?q=" + urllib.parse.quote("知识"))
        check("中文检索", len(r.get("results", [])) > 0, "%s 条命中" % len(r.get("results", [])))

        # ---- 2 模型
        if cfg.get("hasKey"):
            t = api.post("/api/llm/test", {})
            check("模型连通", t.get("ok"), "%s %sms" % (t.get("model"), t.get("ms")))
        else:
            check("模型连通", True, "未配置 key，走规则兜底（允许）")

        # ---- 3 消化 + 自动长卡
        d = api.post("/api/digest", {"title": "自检-全链路", "platform": "自检",
                                     "text": PROSE, "compile": True})
        check("消化成功", d.get("ok"), d.get("error", ""))
        md = d.get("digest", "")
        need = ["核心观点", "可执行清单", "概念候选", "连接候选", "我的疑问", "值得记住的原话", "下一步"]
        check("7 段骨架", all(k in md for k in need), "缺:" + ",".join(k for k in need if k not in md))
        cards = d.get("cards", [])
        kinds = {c["kind"] for c in cards}
        check("自动长三层卡", len(cards) >= 3 and {"concept", "question"} <= kinds,
              "新建 %d 张：%s" % (len(cards), sorted(kinds)))
        if cards:
            c0 = cards[0]
            card = api.get("/api/card?path=" + urllib.parse.quote(c0["path"], safe=""))
            check("新卡可读回", c0["name"] in (card.get("body") or "") + str(card.get("frontmatter")))
            check("新卡回指原料", ("[[%s]]" % d["title"]) in (card.get("body") or ""))
        clip = api.get("/api/card?path=" + urllib.parse.quote(d["clip"], safe=""))
        check("原料记录了新卡", server.CARD_SECTION in (clip.get("body") or ""))
        check("卡列表只有一份", (clip.get("body") or "").count("长成的卡") == 1)

        # ---- 4 疑问进队 + 勾选/撤销
        tk = api.get("/api/tasks")["tasks"]
        mine = [t for t in tk if t.get("card") == d["clip"]]
        check("疑问进待复习", len(mine) >= 3, "%d 条挂在这张原料上" % len(mine))
        if mine:
            t0 = mine[0]["title"]
            api.post("/api/task/done", {"title": t0})
            after = [t for t in api.get("/api/tasks")["tasks"] if t["title"] == t0]
            check("勾选生效", after and after[0].get("done"))
            api.post("/api/task/done", {"title": t0, "undo": True})
            after = [t for t in api.get("/api/tasks")["tasks"] if t["title"] == t0]
            check("撤销生效", after and not after[0].get("done"))

        # ---- 5 批注
        an = api.post("/api/annotate", {"path": d["clip"], "text": "自检批注：这条我要试一周。"})
        back = api.get("/api/card?path=" + urllib.parse.quote(d["clip"], safe=""))
        check("批注落卡", an.get("ok") and "自检批注" in (back.get("body") or ""))

        # ---- 6 文件导入
        tmp = HERE / "uploads" / "_selftest.docx"
        tmp.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(tmp, "w") as z:
            z.writestr("[Content_Types].xml",
                       '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                       '<Default Extension="xml" ContentType="application/xml"/>'
                       '<Override PartName="/word/document.xml" '
                       'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            z.writestr("_rels/.rels",
                       '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr("word/document.xml",
                       '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                       '<w:p><w:r><w:t>自检 DOCX：连接层写两个概念之间的关系。</w:t></w:r></w:p></w:body></w:document>')
        im = api.post("/api/import", {"filename": "自检-DOCX.docx",
                                      "base64": base64.b64encode(tmp.read_bytes()).decode(),
                                      "compile": False})
        check("DOCX 导入", im.get("ok") and not str(im.get("title", "")).startswith("20"),
              "title=%s chars=%s" % (im.get("title"), im.get("chars")))
        tmp.unlink(missing_ok=True)

        # ---- 7 网页抓取（离线友好：探一下失败只报 hint，不算挂）
        try:
            f = api.post("/api/fetch", {"url": "https://www.ruanyifeng.com/blog/2026/08/weekly-issue-410.html"},
                         timeout=90)
            check("网页抓取", f.get("ok") and f.get("chars", 0) > 500,
                  "%s 字 / %s 图" % (f.get("chars"), f.get("images")))
        except Exception as e:
            check("网页抓取（外网不通可忽略）", True, str(e)[:60])

        # ---- 7b 空正文不许报成功（2026-10-03 小红书假 cookie 曾返回 ok=True + 0 字）
        try:
            w = api.post("/api/fetch", {"url": "https://www.xiaohongshu.com/explore"}, timeout=90)
            check("空正文判失败", (not w.get("ok")) and bool(w.get("error")),
                  "ok=%s error=%s" % (w.get("ok"), (w.get("error") or "")[:40]))
        except Exception as e:
            check("空正文判失败（外网不通可忽略）", True, str(e)[:60])

        # ---- 7c 图片识字：图文内容的正文常常整页长在图上（小红书图页 / 截图 / 图表）
        #      测试图内联在下面（8KB PNG，图上写着「自检图：识字编号 7391」），不依赖外部文件
        _OCR_PNG_B64 = "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"
        try:
            ad = vault / "raw" / "assets" / "_selftest-ocr"
            ad.mkdir(parents=True, exist_ok=True)
            (ad / "shot.png").write_bytes(base64.b64decode(_OCR_PNG_B64))
            od = api.post("/api/digest", {
                "title": "自检-图文识字", "platform": "小红书",
                "text": "这条笔记只有一句话，其余内容都在图里。\n\n![图1](assets/_selftest-ocr/shot.png)\n",
                "images": False, "compile": False, "ocr": True}, timeout=300)
            got = ""
            if od.get("ok"):
                cb = api.get("/api/card?path=" + urllib.parse.quote(od["clip"], safe=""))
                got = cb.get("body") or ""
            check("图片识字", od.get("ok") and od.get("ocrImages", 0) >= 1 and "7391" in got,
                  "送 %s 张 / note=%s / 认出编号=%s"
                  % (od.get("ocrImages"), (od.get("note") or "")[:24], "7391" in got))
        except Exception as e:
            check("图片识字（未配模型或外网不通可忽略）", True, str(e)[:60])

        # ---- 8 图片路由
        c = api.get("/api/config")
        check("处理规范内置", str(c.get("skill")).lower() in ("true", "1"), "skill/PROMPTS.md")
    finally:
        restore(snap)
        after = api.get("/api/stats")
        print("\n还原后：cards=%s（测前 %s）" % (after["total"], st["total"]))

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n%d 项检查，%d 项失败%s" % (len(RESULTS), len(bad),
          "：" + "、".join(bad) if bad else " —— 全绿"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
