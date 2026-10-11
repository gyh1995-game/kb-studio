# -*- coding: utf-8 -*-
"""把散在 server.py 里的每一份提示词抽成一份可查阅的文档。

    python tools/dump_prompts.py            # 写 docs/提示词全集.md
    python tools/dump_prompts.py --check    # 只比对，不落盘；漂移了退出码 1

为什么不手抄一份：手抄就是同一份信息的第二个出口，改天代码动了文档还停在昨天，
比没有文档更坏。这份文件是**从 server.py 现读现生成**的，永远和跑着的那份一致。
"""
import argparse
import io
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import server  # noqa: E402  唯一真相就是它

# 每一处会真的发给模型的文本：常量名 → 给人看的说明
SITES = [
    ("DIGEST_SYSTEM", "消化 · 系统段",
     "丢一条链接进来，抓成正文后第一次调模型时挂的 system。定的是「只准输出这份结构、"
     "不许编、不许写处理过程、不许自估时长和类型」。"),
    ("DIGEST_PROMPT", "消化 · 用户段（模板）",
     "八段骨架 + 概念准入判据 + 排版硬规则 + 反面例子。`{title}` `{platform}` `{url}` "
     "`{author}` `{content}` `{existing}` 六个槽由代码填，`{existing}` 是库里已有卡名，"
     "连接候选只能从里面挑。"),
    ("CARD_ASK", "补一张卡 · 三类卡各自的任务说明",
     "「让模型按一句话生成」那一栏，按 concept / connection / question 各换一句任务描述，"
     "拼在下面那段系统提示词的最前面。"),
    ("LINK_SYSTEM", "新卡回接库里 · 系统段",
     "一张卡刚入库时，拿它和库里已有卡比一遍，接得上的写进正文的「接上的线」。"
     "消化长出来的、一句话生成的、他自己手写的，三类都走这一份。"
     "模型给的卡名会回到索引里核一遍，对不上就丢 —— 宁可少接也不造边。"),
    ("OCR_PROMPT", "图文笔记 · 图片识字",
     "小红书 / 知乎那种正文长在图上的笔记：把本地下来的图直接交给模型读。"
     "配的 system 是一句「你是文字识别工具：只抄见，不写不见」。"),
    ("FRAME_OCR_PROMPT", "视频 · 抽帧识字",
     "短视频没有字幕时，按时间顺序抽几帧，把帧上的字幕、标题、贴纸读回来。"
     "和转写（faster-whisper / FunASR，本地跑，不调模型）互补。"),
    ("SUMMARY_SYSTEM", "学习总结 · 系统段",
     "小助手头上「今日总结 / 本周总结」那两个按钮。证据 = 本机 localStorage 记的阅读停留 "
     "+ 后端现读的卡片正文，模型只许依据这些写，没读过就写没读过。"),
    ("SUMMARY_PROMPT", "学习总结 · 用户段（模板）",
     "四段固定输出：这一天学了什么 / 反复回到哪几处 / 还没接上的线 / 下一天该做什么。"
     "`{label}` `{unit}` `{span}` `{made_n}` `{made}` `{done_n}` `{done}` `{read_n}` `{read}` 由代码填。"),
]

# 不走模型、但同样是「口径」的几处，一并列出来，免得他以为漏了
NO_MODEL = [
    ("状态五值", "`STATUSES` / `STATUS_ALIAS` / `norm_status()`",
     "未读 / 已读 / 待吃透 / 待实践 / 已内化。同义词折叠进 STATUS_ALIAS，"
     "整句状态直接拒绝并报错，不新增第六个值。"),
    ("内容形态", "`MEDIA_TYPES` / `media_type_of()` / `guess_media_type()`",
     "文本 / 图文 / 视频 / 音频 / 其他。**由抓取结果判，模型不许自填。**"),
    ("时长常数", "`READING_CPM` / `READING_WPM` / `CARD_CPM` / `CARD_WPM` / `image_seconds()`",
     "中文 200 字/分、英文 200 词/分（出处：ngryman/reading-time 源码）；"
     "卡片 150（产品自定，无一手来源）；图片首图 12 秒逐张 −1 秒下限 3 秒（出处：Ghost）；"
     "视频音频直接取原片时长。"),
    ("字数口径", "`count_words()` / `read_units()`",
     "只算正文净字：剥 frontmatter、围栏代码、行内 code、裸 URL、Markdown 语法字符，"
     "中文字按字、拉丁词按词。界面显示的「N 字」和「约 N 分钟」都出自这一把尺。"),
    ("标签卫生", "`TAG_STOPWORDS` / `TAG_BAD_CHARS` / `tag_ok()` / `clean_tags()`",
     "维度词、状态词、平台名、处理日志词、色值、CSS 选择器、camelCase、"
     "含 # 空格逗号冒号、超 12 字，全部拦下；只差大小写的归并成一格。"),
    ("复习队列入队", "`append_review_tasks()`",
     "两个入口：每份原料一条「重读原料」（+7 天），每条疑问一条「回看原文」（+3 天）。"
     "类型 / 预估 / 来源在入队那一刻写死。"),
    ("卡片准入闸门", "`compile_cards()`",
     "一次消化最多 3 概念 / 2 连接 / 3 问题；概念名少于 8 字判为从句不是主张，不建卡；"
     "四段缺一段不建卡；互动数据 / 项目身份 / 技术规格一律不建卡。"),
    ("抓取通道", "`fetchers.py`",
     "知乎 / 抖音 / 小红书 / B站 / 公众号 / Instagram / Facebook / 网页 / 本地文件。"
     "不走模型，但 `unbreak_urls()`（拼回被硬断行的链接）和 `reflow_text()`（恢复段落）"
     "是它的数据层职责，别指望 CSS 兜。"),
]


def render():
    out = ["# KB Studio · 提示词全集（自动生成，别手改）",
           "",
           "> 这份文件由 `python tools/dump_prompts.py` 从 `server.py` 现读现生成。",
           "> **改提示词只改 `server.py`，改完重跑这条命令**；直接编辑这份文件会被下一次生成覆盖。",
           "> 放在 `KB-Studio/docs/` 而不是笔记库里：它是施工说明，不是你的知识内容。",
           "> 口径层面的规则（为什么这么定）在 `KB-Studio/skill/PROMPTS.md`；"
           "协作约束在 `KB-Studio/AGENTS.md`。",
           "",
           "## 目录",
           ""]
    for key, title, _note in SITES:
        out.append("- [%s](#%s) —— `%s`" % (title, title.replace(" ", "").lower(), key))
    out.append("- [不调模型但同样是口径的地方](#不调模型但同样是口径的地方)")
    out.append("")
    out.append("---")
    out.append("")

    for key, title, note in SITES:
        # CARD_ASK 挂在 Handler 上（它是接口方法用的类属性），不在模块顶层
        val = getattr(server, key, None)
        if val is None:
            val = getattr(server.Handler, key)
        out.append("## %s" % title)
        out.append("")
        out.append("常量：`server.py` 里的 **`%s`**。" % key)
        out.append("")
        out.append(note)
        out.append("")
        if isinstance(val, dict):
            for k, v in val.items():
                out.append("**`%s`**\n\n```\n%s\n```\n" % (k, v))
        else:
            out.append("```\n%s\n```\n" % str(val).rstrip())
        out.append("---")
        out.append("")

    out.append("## 不调模型但同样是口径的地方")
    out.append("")
    out.append("这些不发给模型，但决定产出长什么样，审计时一样要看：")
    out.append("")
    for title, where, note in NO_MODEL:
        out.append("- **%s** —— %s\n  %s\n" % (title, where, note))
    out.append("")
    out.append("## 怎么验")
    out.append("")
    out.append("```bash")
    out.append("python tools/spec_check.py     # 50 项离线断言，不打网络不调模型")
    out.append("python tools/dump_prompts.py --check   # 这份文档和 server.py 有没有漂移")
    out.append("```")
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对，不写文件")
    a = ap.parse_args()
    text = render()
    dst = HERE / "docs" / "提示词全集.md"
    old = dst.read_text(encoding="utf-8") if dst.is_file() else ""
    if a.check:
        if old != text:
            print("漂移了：server.py 改过但文档没重生成。跑 python tools/dump_prompts.py")
            return 1
        print("一致：%s 与 server.py 同步" % dst.name)
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    print("已写 %s（%d 字，%d 处提示词）" % (dst, len(text), len(SITES) + len(NO_MODEL)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
