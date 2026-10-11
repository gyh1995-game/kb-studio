#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract.py — 一条链接进来，一份 markdown 原料出去。

单独安装、单独跑，不依赖 KB Studio 主程序：

    python extract.py "https://www.zhihu.com/question/xxx/answer/yyy"
    python extract.py "<整段粘贴的正文>" --title 手边的一段话
    python extract.py "<链接>" --digest          # 再调一次模型，追加「## 凝练总结」
    python extract.py "<链接>" --json            # 给脚本用：stdout 只有一份 JSON

平台：知乎 / 抖音 / 小红书 / B站 / 公众号 / Instagram / Facebook / 任意网页 / 纯文本。
需要登录态的站点走 cookie（cookies/<站点>.txt，从浏览器 F12 复制）。

两条硬规矩，和主程序一致：
  1. 抓不到就说抓不到，并给出下一步动作。**绝不编造正文、作者、数据。**
  2. 「原文」那一节是作者的话，一个字都不改写；凝练总结单独一节，标明是模型写的。
"""
import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import fetchers                      # noqa: E402  本目录自带的那一份
import prompts                       # noqa: E402  从主程序抽出来的提示词

CONFIG = HERE / "extract-config.json"
DEFAULTS = {"base_url": "https://api.deepseek.com", "model": "deepseek-flash",
            "api_key": "", "temperature": 0.3, "maxTokens": 6000}


def resolve_key(cfg):
    """key 的三个来源，和主程序同一套优先级：显式配置 > 环境变量 > ~/.claude/settings.json。
    最后那条只是顺手：装了 Claude Code 的人不必再填一遍。但 sk-ant- 打头的是 Anthropic 的
    token，把它当 DeepSeek key 发出去等于把一套服务的凭据递给另一套 —— 直接不用。"""
    if cfg.get("api_key"):
        return cfg["api_key"]
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    try:
        tok = (json.loads((Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
               .get("env") or {}).get("ANTHROPIC_AUTH_TOKEN", "") or ""
        return "" if tok.startswith("sk-ant-") else tok
    except Exception:
        return ""


def load_cfg():
    cfg = dict(DEFAULTS)
    if CONFIG.is_file():
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except Exception as e:
            print("配置文件读不了（%s），先用默认值" % e, file=sys.stderr)
    cfg["api_key"] = resolve_key(cfg)
    return cfg


def call_model(user, system, cfg):
    """OpenAI 兼容的一次调用。只用标准库，不引 SDK —— 装不上 pip 的机器也要能跑。"""
    import urllib.request
    key = cfg.get("api_key") or ""
    if not key:
        raise RuntimeError("没配 API key：写 extract-config.json 的 api_key，"
                           "或设环境变量 DEEPSEEK_API_KEY。不加 --digest 就不需要。")
    url = (cfg["base_url"].rstrip("/") or "https://api.deepseek.com") + "/chat/completions"
    body = {"model": cfg["model"], "temperature": cfg["temperature"],
            "max_tokens": cfg["maxTokens"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not txt.strip():
        raise RuntimeError("模型回了空内容：" + json.dumps(d, ensure_ascii=False)[:200])
    return txt.strip()


def slug(text, n=28):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", str(text or "")).strip("-")
    return (s[:n] or "untitled")


def render(meta, digest=""):
    """原料 md。frontmatter 只放抓取事实，不放模型自估的东西。"""
    fm = ["---",
          'title: %s' % json.dumps(meta["title"], ensure_ascii=False),
          "source: %s" % json.dumps(meta["url"] or "本地粘贴", ensure_ascii=False),
          "platform: %s" % json.dumps(meta["platform"], ensure_ascii=False),
          "author: %s" % json.dumps(meta.get("author") or "未知", ensure_ascii=False),
          "captured_at: %s" % time.strftime("%Y-%m-%d"),
          "method: %s" % json.dumps(meta.get("method") or "", ensure_ascii=False),
          "chars: %d" % meta["chars"],
          "tags: []",
          "---", ""]
    body = ["# %s" % meta["title"], "",
            "> 来源：%s ｜ 平台：%s ｜ 作者：%s" % (meta["url"] or "本地粘贴",
                                                 meta["platform"],
                                                 meta.get("author") or "未知"), ""]
    if meta.get("note"):
        body += ["> %s" % meta["note"], ""]
    body += ["## 原文（%s）" % ("抓取所得，未加工" if meta["url"] else "本人粘贴，未加工"),
             "", meta["text"].strip(), ""]
    if digest:
        body += ["---", "", "## 凝练总结（模型写的，不是作者原话）", "", digest, ""]
    return "\n".join(fm + body)


def main():
    ap = argparse.ArgumentParser(description="多平台内容 → markdown 原料")
    ap.add_argument("src", help="链接，或者一整段直接粘进来的正文")
    ap.add_argument("--title", default="", help="粘正文时给它起个名")
    ap.add_argument("--out", default="out", help="输出目录（默认 ./out）")
    ap.add_argument("--digest", action="store_true", help="再调一次模型，追加凝练总结")
    ap.add_argument("--json", action="store_true", help="stdout 只输出一份 JSON")
    ap.add_argument("--no-images", action="store_true", help="不把图片下载到本地")
    a = ap.parse_args()

    cfg = load_cfg()
    pasted = not str(a.src).strip().startswith("http")
    if pasted:
        got, _ = fetchers.extract_url(a.src)
        pasted = not got
    if pasted:
        text = fetchers.reflow_text(a.src.strip())
        meta = {"ok": True, "platform": "本地粘贴", "url": "",
                "title": (a.title or re.split(r"[\n。]", a.src.strip())[0][:24]).strip(),
                "author": "未知", "text": text, "method": "直接粘贴", "note":
                "这一份是手工粘进来的，不是抓的：排版可能和原页不一致。"}
    else:
        r = fetchers.fetch(a.src)
        if not r.get("ok"):
            fail = {"ok": False, "error": r.get("error", "未知原因"),
                    "hint": r.get("hint", ""), "platform": r.get("platform", "")}
            if a.json:
                print(json.dumps(fail, ensure_ascii=False))
            else:
                print("抓不到：%s" % fail["error"], file=sys.stderr)
                print("下一步：%s" % (fail["hint"] or "把正文直接粘进来再跑一次"), file=sys.stderr)
            return 1
        meta = {"ok": True, "platform": r.get("platform") or "网页", "url": r.get("url", ""),
                "title": (r.get("title") or "").strip()[:80] or slug(r.get("url", "")),
                "author": (r.get("author") or "未知"), "text": r.get("text") or "",
                "method": r.get("method") or "", "note": r.get("note") or ""}
        if not a.no_images and meta["url"]:
            outdir = (HERE / a.out).resolve()
            assets = outdir / "assets"
            md, n = fetchers.download_images(meta["text"], meta["url"], assets,
                                             cookie=fetchers.load_cookie(meta["url"]) or "",
                                             limit=30, rel_prefix="assets/")
            if n:
                meta["text"] = md
                meta["images"] = n

    meta["chars"] = len(meta["text"])
    meta.setdefault("images", 0)

    digest, dig_err = "", ""
    if a.digest:
        try:
            user = prompts.DIGEST_PROMPT.format(
                title=meta["title"], platform=meta["platform"], url=meta["url"] or "本地粘贴",
                author=meta["author"], content=meta["text"][:12000],
                existing="（这份工具不带库，没有可连接的旧卡）")
            digest = call_model(user, prompts.DIGEST_SYSTEM, cfg)
        except Exception as e:
            dig_err = str(e)[:200]
            print("凝练总结没生成：%s（原文已经存好了）" % dig_err, file=sys.stderr)

    outdir = (HERE / a.out).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    dst = outdir / ("%s-%s.md" % (time.strftime("%Y%m%d"), slug(meta["title"])))
    dst.write_text(render(meta, digest), encoding="utf-8")

    result = {"ok": True, "path": str(dst), "platform": meta["platform"],
              "title": meta["title"], "author": meta["author"], "url": meta["url"],
              "chars": meta["chars"], "images": meta.get("images", 0),
              "method": meta["method"], "digest": bool(digest), "digest_error": dig_err}
    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("已写 %s" % dst)
        print("  平台 %s ｜ 作者 %s ｜ %d 字 ｜ 图片 %d ｜ 通道 %s%s"
              % (meta["platform"], meta["author"], meta["chars"], meta.get("images", 0),
                 meta["method"], "｜ 已凝练" if digest else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
