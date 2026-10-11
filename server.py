#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KB Studio — 本地个人知识库网站（零依赖，仅用 Python 标准库）

启动:  python server.py           默认 http://127.0.0.1:8777
       python server.py --port 9000
       python server.py --vault "D:/Notes/我的知识库"      # 或 ~/Documents/MyKB

数据本体就是 Obsidian vault 里的 .md 文件，本服务只做读取、索引和写入，
不额外维护数据库——Obsidian 里改了，网站刷新即可见。
"""
import os
import re
import io
import sys
import json
import math
import time
import base64
import shutil
import argparse
import importlib.util
import threading
import webbrowser
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"


def guess_vault():
    """知识库目录 = 环境变量 > 程序被放在某个库里面（父目录有 .obsidian 或 system.md）> 家目录下的 KB-Vault。
    这样把 KB-Studio 整个文件夹拷给别人 / 部署到别处，不用再改代码。
    兜底不能再写死任何人的桌面路径 —— 那台机器上没有这个目录，别人第一次跑就会撞墙。"""
    env = os.environ.get("KB_VAULT")
    if env:
        return Path(os.path.expanduser(env))
    for cand in (HERE.parent, HERE.parent.parent):
        if (cand / ".obsidian").is_dir() or (cand / "system.md").is_file():
            return cand
    return Path.home() / "KB-Vault"


DEFAULT_VAULT = guess_vault()

# 一个空库该有的骨架。缺了它，第一次跑的人连「原料写到哪」都要自己猜。
VAULT_DIRS = ("raw/clips", "raw/assets", "wiki/concepts", "wiki/connections",
              "wiki/questions", "inbox/trash")
VAULT_FILES = {
    "index.md": "---\ntitle: 知识库索引\ntype: index\n---\n\n# 知识库索引\n\n"
                "> 每次消化新内容后由 AI 同步更新。卡片在 `wiki/`，原料在 `raw/clips/`。\n",
    "log.md": "---\ntitle: 消化日志\ntype: log\n---\n\n# 消化日志\n\n"
              "> 处理过程记在这儿，不记进笔记正文。\n",
    "progress.md": "---\ntitle: 进度\ntype: progress\n---\n\n# 进度\n",
    "revision_notes.md": "---\ntitle: 复习批注\ntype: notes\n---\n\n# 复习批注\n\n"
                         "> 你重读卡片时写下的疑问、推翻、补充 —— 这一份只有你自己写。\n",
    "inbox/links.txt": "",
    "inbox/annotations.md": "---\ntitle: 批注汇总\ntype: annotations\n---\n\n# 批注汇总\n",
    "inbox/待复习任务.json": '{\n  "tasks": []\n}\n',
    "inbox/已完成任务.json": '{\n  "done": []\n}\n',
}
# system.md / learner_profile.md 优先用 templates/ 里那份（用户能自己改宪法），没有才退到内置最小版
VAULT_FALLBACK = {
    "system.md": "---\ntitle: 个人知识库运转手册\ntype: system\n---\n\n"
                 "# 个人知识库运转手册（system.md）\n\n"
                 "> 这份文件是这套库的「宪法」，AI 消化内容时按它办事。\n"
                 "> 完整口径见 `KB-Studio/skill/PROMPTS.md`；把这一行以上换成你自己的规则即可。\n\n"
                 "人只负责两件事：扔原料、提问。AI 负责维护结构。\n",
    "learner_profile.md": "---\ntitle: 学习者画像\ntype: profile\n---\n\n"
                          "# 学习者画像（只由本人修改，AI 不得改动）\n\n"
                          "> 这份文件决定 AI 消化时的取舍：与你方向无关的降级到 L0，相关的才深挖。\n\n"
                          "## 关注方向（不超过 5 个）\n\n- （写你自己的）\n\n"
                          "## 我现在正在做的事\n\n- （写你自己的）\n",
}


def scaffold_vault(vault=None, quiet=False):
    """空目录 → 一个能直接开始丢链接的知识库。只补缺的，已有文件一个字不动。"""
    v = Path(vault or VAULT)
    made = []
    for d in VAULT_DIRS:
        p = v / d
        if not p.is_dir():
            p.mkdir(parents=True, exist_ok=True)
            made.append(d + "/")
    tpl = HERE / "templates"
    for rel, text in list(VAULT_FALLBACK.items()) + list(VAULT_FILES.items()):
        p = v / rel
        if p.exists():
            continue
        src = tpl / rel
        body = src.read_text(encoding="utf-8") if src.is_file() else text
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        made.append(rel)
    if not quiet:
        print("🧱 空库已初始化：%s（补了 %d 项）" % (v, len(made)))
    return made
SCRIPTS_DIR = Path.home() / ".workbuddy" / "skills" / "personal-kb" / "scripts"

VAULT = Path(os.environ.get("KB_VAULT", DEFAULT_VAULT))
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# ---------------------------------------------------------------- 工具函数

def read_deepseek_key():
    """从环境变量或 ~/.claude/settings.json 里取 DeepSeek key"""
    if DEEPSEEK_KEY:
        return DEEPSEEK_KEY
    try:
        cfg = json.loads((Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
        tok = (cfg.get("env") or {}).get("ANTHROPIC_AUTH_TOKEN", "") or ""
        # 那是别人机器的凭据来源：Claude Code 的 token 长得像 sk-ant-…，
        # 把它当 DeepSeek key 发给 base_url，等于把一套服务的凭据递给另一套。
        if tok.startswith("sk-ant-"):
            return ""
        return tok
    except Exception:
        return ""


def parse_frontmatter(text):
    """解析 YAML frontmatter 的常用子集：key: value / key: [a, b] / key:\\n  - item"""
    fm, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return fm, text
    raw, body = m.group(1), text[m.end():]
    key = None
    for line in raw.split("\n"):
        if not line.strip() or line.strip().startswith("#"):
            continue
        indented = line.startswith((" ", "\t"))
        s = line.strip()
        if not indented and ":" in s:
            k, _, v = s.partition(":")
            key = k.strip()
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                fm[key] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            elif v == "":
                fm[key] = []
            else:
                fm[key] = v.strip("'\"")
        elif indented and s.startswith("- ") and key:
            if not isinstance(fm.get(key), list):
                fm[key] = []
            fm[key].append(s[2:].strip().strip("'\""))
    return fm, body


# 学习内容只认这两个目录。根目录那批运行文件（system.md / index.md / log.md / 进度 /
# 复习批注 / 学习者画像 / 首页）是这套工具的说明书和台账，不是他学过的东西 ——
# 之前把它们当卡片读进列表，空库第一眼就是 6 张「系统」卡，图谱里也多出 6 个孤立点。
# 文件照旧存在、照旧被 digest 提示词读取，只是不进卡片列表和图谱。
INDEX_DIRS = {"wiki", "raw"}
INDEX_ROOT_FILES = frozenset()   # 保留这个名字给旧脚本用；根目录一律不算卡片
RUNTIME_FILES = {"system.md", "index.md", "log.md", "learner_profile.md",
                 "progress.md", "revision_notes.md", "首页.md"}

_FM_KINDS = {"concept", "connection", "question", "raw", "system"}


def card_kind(rel_path):
    if rel_path.startswith("wiki/concepts"):
        return "concept"
    if rel_path.startswith("wiki/connections"):
        return "connection"
    if rel_path.startswith("wiki/questions"):
        return "question"
    if rel_path.startswith("raw/"):
        return "raw"
    if "/" not in rel_path and rel_path in RUNTIME_FILES:
        return "system"
    return "page"


def kind_of(rel_path, fm):
    """frontmatter 里作者自己标的 type 优先，路径只是兜底。"""
    t = str(fm.get("type", "")).strip().lower()
    if t in _FM_KINDS:
        return t
    return card_kind(rel_path)


KIND_LABEL = {"concept": "概念", "connection": "连接", "question": "问题",
              "raw": "原料", "system": "系统", "page": "页面"}


def scan_vault():
    """扫描 vault，返回卡片列表（含 frontmatter、双链、标签、摘要）"""
    cards = []
    if not VAULT.is_dir():
        return cards
    for p in sorted(VAULT.rglob("*.md")):
        if any(part.startswith(".") for part in p.parts):
            continue
        rel = str(p.relative_to(VAULT)).replace("\\", "/")
        # 白名单口径：只有知识库内容才进仪表盘（根目录其余散件、部署包、工具文件都不算卡片）
        top = rel.split("/")[0] if "/" in rel else rel
        if "/" in rel:
            if top not in INDEX_DIRS:
                continue
        elif rel not in INDEX_ROOT_FILES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        fm, body = parse_frontmatter(text)
        kind = kind_of(rel, fm)
        links = re.findall(r"\[\[([^\]\|#]+)", body)
        tags = []
        if isinstance(fm.get("tags"), list):
            tags = [str(t).lstrip("#") for t in fm["tags"]]
        elif fm.get("tags"):
            tags = [str(fm["tags"]).lstrip("#")]
        # 正文里的 #标签：先去掉代码块和行内 code（CSS 色值、JS 标识符都在那儿），
        # 再要求 # 前面是行首或空白 —— Obsidian 自己就是这么认的。
        plain = strip_code(body)
        tags += re.findall(r"(?:(?<=^)|(?<=[\s（(「『]))#([\w\u4e00-\u9fa5]{1,%d})" % TAG_MAX_LEN, plain)
        tags = clean_tags(tags)
        # 摘要：取正文里第一段有意义的话（跳过标题、引用、表格这种结构性行）
        summary = ""
        for ln in body.split("\n"):
            s = ln.strip().lstrip("#").strip()
            if not s or s.startswith("---") or s.startswith(("|", ">", "!", "-", "*", "1.")):
                continue
            if len(s) > 12:
                summary = s[:120]
                break
        title = fm.get("title") or p.stem
        # aliases 也要进列表：[[双链]] 指向的常常是别名或标题，不是文件名
        al = fm.get("aliases") or fm.get("alias") or []
        if isinstance(al, str):
            al = [al]
        status_raw = str(fm.get("status", "")).strip()
        mt = str(fm.get("media_type", "")).strip()
        if mt not in MEDIA_TYPES:
            mt = guess_media_type(fm, kind)
        est = as_int(fm.get("est_min"))
        # 字数和时长都只按正文算：frontmatter、Markdown 语法、裸 URL 不算字，也不该占阅读时间
        chars = count_words(body)
        if not est and kind in ("raw", "concept", "connection", "question"):
            est = est_minutes(chars, mt or "文本", as_int(fm.get("duration_ms")),
                              is_card=kind != "raw", images=as_int(fm.get("images")),
                              text=body)
        cards.append({
            "path": rel,
            "name": p.stem,
            "title": str(title),
            "aliases": [str(a).strip() for a in al if str(a).strip()],
            "kind": kind,
            "kindLabel": KIND_LABEL[kind],
            "level": str(fm.get("level", "")),
            "status": norm_status(status_raw),
            "status_raw": status_raw,
            "media_type": mt,
            "est_min": est,
            "from_raw": str(fm.get("from_raw", "")).replace("\\", "/"),
            "platform": str(fm.get("platform", "")),
            "author": str(fm.get("author", "")),
            "source": str(fm.get("source", "")),
            "captured_at": str(fm.get("captured_at", "")),
            "tags": tags,
            "links": sorted(set(l.strip() for l in links if l.strip())),
            "summary": summary,
            "updated": str(fm.get("updated", "")),
            "mtime": int(p.stat().st_mtime),
            "size": len(text),
            "chars": chars,
        })
    return cards


def build_graph(cards):
    """根据 [[双链]] 构建图谱；链接按标题匹配卡片"""
    by_name = {}
    for c in cards:
        by_name.setdefault(c["title"], c["path"])
        by_name.setdefault(c["name"], c["path"])
    nodes = [{"id": c["path"], "label": c["title"],
              "kind": c["kind"], "level": c["level"]} for c in cards]
    links, seen = [], set()
    for c in cards:
        for target in c["links"]:
            dst = by_name.get(target.strip())
            if not dst or dst == c["path"]:
                continue
            key = tuple(sorted([c["path"], dst]))
            if key in seen:
                continue
            seen.add(key)
            links.append({"source": c["path"], "target": dst})
    return {"nodes": nodes, "links": links}


# ---------------------------------------------------------------- AI 消化

DIGEST_SYSTEM = """你是一个个人知识库的「收藏夹消化器」。你的输出会直接存成 Obsidian 笔记里的「消化稿」一节。

规则：
1. 只输出 Markdown，不要任何前后缀说明，也不要把整篇包在代码块里。
2. 章节标题按给定顺序一字不改、不增不减；每节的条目用 `- ` 开头，一行一条，条目内部不再换行。
3. 概念、连接、引文必须能在原文里指认出来，禁止编造；原文没给的写「原文未给出」。
4.「我的疑问」是这份产出里最值钱的部分，必须正好 3 条，少一条就是不合格。
5. 正文里不写处理过程（用了多少帧、生成了几张卡、备份到哪里），也不重复系统自己记录的东西：
   类型、预估时长、来源 URL、卡列表、反向链接都由字段和界面给出，不要抄进正文。
6. 字段里不写元话语：不许出现「（隐含…）」「我认为」「不过…」「可能需要」这类推理或犹豫，
   也不许在一个字段里并列多个候选（「A，或者 B，或者 C」）—— 一个位置只放一个值。
7. 每个位置只有三种合法取值：原文里能指认并归一化的值 / 保留已有的旧值 / 写「无」。
   不许留空、不许猜、不许用「大家都这样」当依据。
8. 消化稿是**转述不是摘抄，也不是泛化**：原文里的具体名词、数字、型号、量级、地名必须活下来；
   句子结构可以重写，但不许把「他测了 3 个账号」写成「多数人」。主语用实体名，不用「它 / 这 / 他们」。
9. 一次出现不是一条规律：只发生过一件事的，写进「我的疑问」当待验证，不要写成普适主张。
10. 语言：中文。
"""

DIGEST_PROMPT = """把下面的内容消化成知识库卡片。严格按模板输出，只输出 Markdown。
处理规范的完整版本见 skill/PROMPTS.md（概念/连接/疑问的判定口径、标签白名单和排版规则以它为准）。

## 核心观点
3-5 条，每条一句话（≤40 字），只写这篇真正的主张；不要复述背景，不要写「本文介绍了」。

## 可执行清单
能照做的步骤 / 清单 / 参数；纯观点类就列支撑论据。没有写「无」。≤5 条。

## 概念候选
**最多 3 个。** 概念是知识块，不是这条内容的履历。三条全过才算：
1. 换个平台、换个人、把这条内容删掉，这句话照样成立 —— 讲的是机制 / 因果 / 方法 / 判断标准 / 取舍；
2. 是一个能被同意或被反对的说法，不是名词。写「量产变体不等于流量」，不写「100 个变体」；
3. 说得出它用在你的哪个场景（这栏写不出来的，就别建卡）。

**下面这些一律不许当概念**（它们记录的是"这一条"，不是知识，留在原文里就够）：
产品名 / 项目名 / 仓库名 / 人名 / 账号名；许可证、版本号、运行环境、安装命令；
点赞、收藏、评论、转发、播放量、star 数、藏赞比这类互动数据；作者 IP 归属、发布时间；
被当成卖点写出来的数字口号（「1 亿播放量」那种）。

每张概念卡是一张**会不断长大的页**，不是一句摘要。每行格式（五段都要有，缺一段就别提这条）：
- 概念名 —— 主张一句话｜为什么成立：这篇里能支撑它的依据（例子 / 数据 / 推演，1-2 句）｜边界：什么条件下不成立、别在哪些场景用它｜用在哪：什么场景会用到它｜标签：这条主张自己的主题词，2-3 个，顿号分隔

**概念名本身必须是一句主谓句**（8–25 字），读一遍就能同意或反对：
✓「量产变体不等于流量」「知识库的价值在编译层不在存储层」
✗「知识库」（光杆名词）、「关于流量的一些思考」（主题不是主张）、「AI 该持续维护结构」（从句被截断）。

「为什么成立」里的依据必须能在原文里指认出来（一个数字、一个例子、一句原话、一个步骤都行）；
只有断言没有论证的，就把它写进「我的疑问」而不是概念卡。「边界」写不出来的，这条也别提 ——
放到哪都对的句子是废话，不是知识。四段加起来要够 90 字，一两句话的口号会被库体检判成薄卡。

**先查文末的「库里已有的卡」**：如果这篇其实是在印证一张已有的卡，不要另起一个近义名字，
写成 `- 并入：<已有卡名原文> —— …`（同样带依据 / 边界 / 用在哪），系统会把这篇追加到那张卡上。
一个主张只留一张卡，重复起名字等于把这个库搞脏。

## 连接候选
**最多 2 条，宁缺毋滥。** 连接的价值是把新东西接到已有认知上。另一头可以是：
① 文末「库里已有的卡」清单里的卡名；② **你自己在本次「概念候选」里刚提出的那几条**。
两者都挑不出来就写「无」，不要为了凑数现编一个假关系。一条连接里最多两个 [[双链]]。
格式：- 本篇的主张 ↔ 另一头的卡名 —— 关系（前置 / 矛盾 / 同结论不同路径 / 上下位 / 互补）｜为什么真的相关（一句机制，不许写「都在讲 X」这种话题重合）
**跨来源优先**：接上你以前那篇的东西，比把同一篇里的两个主张互连更有价值。

## 我的疑问
**正好 3 条**，覆盖下面四类中至少两类，这是本库最值钱的产出。
每行**必须**是这个格式，四段一段都不能少（第一段直接当卡片标题和复习条目）：

`- 维度：能在 20 字内问完的短问题 —— 是什么让我问出这句 —— 我已经知道什么 —— 我还卡在哪`

维度只能从这四个里选：**边界 / 证据 / 与我 / 待验证**（不写前缀系统就认不出这道题属于哪类）。
不要把整句原文抄成问题，也不要写「是不是」「如何做」这种没有指向的问句；
问题本身要**合上这篇也答得出**，别写成「第 3 段说的那个东西怎么改」。
「我已经知道什么」没有就写「无」，不许留空、不许编。
这一节决定复习队列里有没有东西，空着等于这份素材白处理了。

## 值得记住的原话
1-3 句，用 > 引用格式，只挑信息密度高的；不为凑数而摘。

## 下一步
1-3 条具体动作（要查什么、要试什么、还要丢哪些链接进来）。

## 主题标签
一行写完，2-4 个主题词，顿号分隔。标签要能把一批卡筛出来。
只写主题（例：提示词工程、知识管理、视频转写、竞价投放）。
**不许写**：平台名（抖音 / 知乎）、人名和账号名、产品名、版本号、纯数字、互动数据；
状态词（未读 / 已读 / 已消化 / 待吃透 / 待实践 / 已内化）；维度词（边界 / 证据 / 与我 / 待验证 / 追问）；
代码里的词和色值（readBody、#c4734a 那种）；每个词不超过 12 个字。

排版硬规则（这几条会被机器检查，违反会被判成格式不合格）：
- 一行只写一条，条目中间不要手动换行；
- 链接写成 [短锚文本](URL)，不要把裸 URL 夹在句子中间；确实要贴 URL 就让它独占一行；
- 不写表格，除非每行列数完全一致，且单元格里不放 URL；
- 只用 ## 一级标题，且只用上面给的那几个段名；不要写 `# 一级`（文件名就是标题），标题行里不放链接；
- 正文里出现的 * _ | 若不是 Markdown 语法，写成 \\* \\_ \\|；中文与英文/数字之间加一个半角空格；
- 一张概念卡只装一条主张，别把三条不相干的观点塞进同一条；
- 「我的疑问」每条要**合上原文也答得了**：把问题单独贴出来，没读过原文的人能作答；
- 不要输出「这张原料长成的卡」「本次长成的卡」这类小节，也不要输出 frontmatter 和 `---`；
- 不要写「出处：」「来源：」这类行，出处由系统统一补；也不要写「约 X 分钟」「类型：视频」，
  时长和类型由抓取结果算，不许自己估。

反面例子（每一条都是这里真出过的事故）：
✗ - 这个工具很好用 —— 它能提高效率｜边界：无｜用在哪：日常      ← 是评价不是主张，边界空着就别提
✗ ## 概念候选：hypit                                            ← 产品名当概念
✗ 出处：https://www.zhihu.com/question/2076689931695546845      ← 裸 URL 夹进正文，窄屏撕成三截
✗ tags: 边界、已读、消化生成、#c4734a、抖音                      ← 五个里没有一个能当标签
✗ 状态：已消化                                                   ← 第六个状态值，不在五值之内
✗ ## 出处 / ### 为什么成立                                       ← 维度升格成标题，大纲被撑乱

================== 以上是固定规范，以下是本条素材 ==================

【标题】{title}
【来源平台】{platform}
【原文链接】{url}
【作者】{author}

【内容】
{content}

【库里已有的卡】（连接候选的"另一头"只能从这份清单里挑，挑不出就写「无」）
{existing}
"""


# ---------------------------------------------------------------- 学习总结提示词
#
# 小助手头上那两个按钮（今日 / 本周）走这一份。证据由前端从本机 localStorage 带来
# （读了哪几张卡、停留多久、勾掉了哪条复习），正文由这里现读 —— 后端不存阅读行为，
# 所以「你学了什么」这件事只有客户端知道，模型只负责把它写成一段人话。

SUMMARY_SYSTEM = """你在给一个人写他自己的学习总结，不是写工作汇报。

读的人就是本人，三个月后回来看这一篇。他要能一眼想起那几天自己在想什么。

规则：
1. 只写证据里出现过的事。没读过就是没读过，不许编「深入研读了」，不许补材料里没有的数字或结论。
2. 写**主张和判断**，不写动作流水账。「看了 5 张卡」没价值，「他反复回到『编译层 vs 存储层』这条主张上」才有价值。
3. 把散在几张卡里的东西连起来说：同一话题下出现过的重复、矛盾、还没接上的线，这些他自己看不出来。
4. 允许指出问题：只收藏没重读、某条主张一直没接双链、复习拖过了账期 —— 但要说依据，不许说教。
5. 不夸人，不用「棒」「加油」「继续保持」这类话，不写 emoji。
6. 中文，Markdown，只用 `- ` 列表和 `**粗**`，不写 `#` 标题，不写表格，不放裸链接。
7. 每个字段只有三种合法取值：证据里有的 / 证据里明确没有（就写「没有」）/ 不适用。不许猜。
"""

SUMMARY_PROMPT = """把下面这份证据写成一段{label}学习总结。严格按四段输出，段名一字不改：

## 这一{unit}学了什么
3-6 条，每条一句话，说**主张**不说动作。同一话题的几张卡合并成一条。

## 反复回到哪几处
1-3 条。证据里被读了两遍以上、或者停留明显更久的那几张卡，说明它还没被你想清楚。

## 还没接上的线
1-3 条。哪些主张之间显然有关系但库里没建连接卡、哪些卡一条双链都没有。
没有就写「没有」。

## 下一{unit}该做什么
最多 3 条，每条要能直接动手（重读哪张、补哪条连接、丢哪类链接进来），不许写「多学习」这种。

================== 以下是证据，只依据它，不许外推 ==================

【统计口径】{label}：{span}
【新入库的卡】共 {made_n} 张：{made}
【勾掉的复习条目】共 {done_n} 条：{done}
【打开过的卡】共 {read_n} 张（停留秒数是本机记的，只到「打开阅读页」这一层）：
{read}
"""


# ---------------------------------------------------------------- AI 提供方（OpenAI 兼容）
#
# 开源给别人用时，这里就是「接自己的模型」的唯一开关：
#   config.json 里填 base_url / api_key / model，DeepSeek、通义千问、小米 MiMo、
#   Kimi、智谱、OpenAI、本地 Ollama / LM Studio 都能用（都是 /chat/completions）。
# 没配 key 也能跑：AI 提炼退化为规则抽取，不报错、不卡住。

CONFIG_FILE = HERE / "config.json"

PROVIDER_DEFAULTS = {
    "name": "deepseek",
    "base_url": "https://api.deepseek.com",
    # 2026-10-03 实测：DeepSeek 现在 /v1/models 只列 deepseek-flash 和 deepseek-v4-pro。
    # 老名字 deepseek-chat 还能调（服务端回显已解析到 deepseek-flash），但官方文档里已经没有它了，
    # 所以默认值跟官方对齐。**看图只能靠 flash**：同一个请求发给 v4-pro 会明确回答「无法查看图片」。
    "model": "deepseek-flash",
    "api_key": "",
    "temperature": 0.3,
    # deepseek-flash 是推理模型：实测 633 字的素材它会先写 3311 字思维链，
    # 2000 的预算会把消化稿从中间砍断（缺的正是最后两段）。上限只是天花板，不是计费量。
    "maxTokens": 6000,
    "visionOcr": True,      # 图文笔记的正文常常长在图上：把本地化的图交给模型识字
    "maxOcrImages": 6,      # 一条最多认几张图（控制耗时和 token）
    # 抽帧张数以前只在 digest 里现读（load_config().get("maxFrames")），却不在白名单里，
    # 所以「模型配置」这一栏存了也会被 save_config 丢掉。补进默认值才能真的可配。
    "maxFrames": 6,
}


def load_config():
    cfg = dict(PROVIDER_DEFAULTS)
    if CONFIG_FILE.is_file():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(part):
    cfg = load_config()
    for k, v in (part or {}).items():
        if k in PROVIDER_DEFAULTS and v not in (None, ""):
            cfg[k] = v
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    return cfg


def masked_config():
    cfg = load_config()
    key = cfg.get("api_key") or ""
    eff = bool(key or os.environ.get("DEEPSEEK_API_KEY") or read_deepseek_key())
    cfg["api_key"] = (key[:4] + "…" + key[-4:]) if len(key) > 10 else ("已设置" if key else "")
    cfg["hasKey"] = eff
    cfg["keySource"] = ("config.json" if key else
                        ("环境变量 DEEPSEEK_API_KEY" if os.environ.get("DEEPSEEK_API_KEY")
                         else ("~/.claude/settings.json" if read_deepseek_key() else "无")))
    cfg.pop("api_key_full", None)
    cfg["vault"] = str(VAULT)
    return cfg


def fit_content(text, limit=24000):
    """超长内容不能默默只喂前半段：模型没看到后半截，就会把「没看到」写成「不存在」。
    头 60% + 尾 40%（结论和文末的例子往往在后半段），中间明说跳过了多少字。"""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    head = int(limit * 0.6)
    tail = limit - head
    return (t[:head] +
            "\n\n【中间跳过了 %d 字没送进模型 —— 别把没看到的部分当作不存在，"
            "需要引用中段时写「原文未给出」】\n\n" % (len(t) - head - tail) +
            t[-tail:])


def call_deepseek(prompt, system=DIGEST_SYSTEM, max_tokens=2000, images=None):
    cfg = load_config()
    key = cfg.get("api_key") or os.environ.get("DEEPSEEK_API_KEY") or read_deepseek_key()
    if not key:
        raise RuntimeError("未配置模型 API Key（本页「模型配置」里填，或设环境变量 DEEPSEEK_API_KEY）")
    base = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    user = prompt
    if images:
        # OpenAI 兼容的多模态写法：content 变数组，图用 data URL 内联（本地文件不外传链接）
        parts = [{"type": "text", "text": prompt}]
        for p in images:
            try:
                raw = Path(p).read_bytes()
            except Exception:
                continue
            b64 = base64.b64encode(raw).decode()
            parts.append({"type": "image_url",
                          "image_url": {"url": "data:image/jpeg;base64," + b64}})
        user = parts
    want = int(cfg.get("maxTokens") or max_tokens)
    reason, out, budget = "", "", want
    for attempt in range(3):
        budget = min(want * (2 ** attempt), 20000)
        payload = {
            "model": cfg.get("model") or os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": float(cfg.get("temperature", 0.3)),
            "max_tokens": budget,
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode("utf-8"))
        ch = data["choices"][0]
        msg = ch.get("message") or {}
        out = (msg.get("content") or "").strip()
        reason = ch.get("finish_reason") or ""
        if reason != "length":
            break
        # 推理模型常把预算花在思维链上：先自己加倍重试，三次还截断才认失败
    if reason == "length":
        raise RuntimeError("模型输出被 max_tokens 截断（加到 %d token 仍不够）"
                           "→ 素材太长，把正文缩短或在「模型配置」调大 maxTokens 再消化" % (budget,))
    if not out and msg.get("reasoning_content"):
        # 推理型模型有时把话说一半、content 留空，别当成「没认出字」
        out = msg["reasoning_content"].strip()
    return out


OCR_PROMPT = """下面是从同一篇文章里下载下来的图片（可能是截图、图表、小红书图文的图页）。
把每张图片里**肉眼可见的文字**逐张抄出来：保持原顺序，不改写、不补全、不总结、不翻译。
看不清或没有文字就写「（无可辨认文字）」。每张图一段，开头用「图1」「图2」标号。"""

IMG_REF_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+)')


def local_images_in(text, limit=6):
    """从（已改写成站内路径的）正文里取出落在 raw/assets 下的本地图片，按出现顺序去重。"""
    out, seen = [], set()
    for m in IMG_REF_RE.finditer(text or ""):
        ref = m.group(1).strip("<>\"' ")
        if ref.startswith("/assets/"):
            p = (VAULT / "raw" / ref[len("/assets/"):])
        elif ref.startswith("assets/"):
            p = VAULT / "raw" / ref
        else:
            continue          # 远程图 / data URI 都不送
        p = Path(os.path.normpath(str(p)))
        if not p.is_file() or str(p) in seen:
            continue
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
            continue
        if p.stat().st_size > 4_000_000:      # 单图 4MB 以上不送，base64 会顶爆请求
            continue
        seen.add(str(p))
        out.append(p)
        if len(out) >= max(0, int(limit or 0)):
            break
    return out


def ocr_local_images(text, limit=None):
    """图文内容常常整页长在图上：把本地化的图片交给模型识字，返回「## 图片里的文字」一段。
    认不出、没配 key、模型不看图 —— 全部安静跳过，绝不用猜测填正文。"""
    cfg = load_config()
    if not cfg.get("visionOcr", True):
        return "", 0, "配置里关了图片识字"
    limit = max(0, min(int(limit or cfg.get("maxOcrImages") or 6), 24))
    imgs = local_images_in(text, limit)
    if not imgs:
        return "", 0, ""
    try:
        got = call_deepseek(OCR_PROMPT, system="你是文字识别工具：只抄见，不写不见。",
                            max_tokens=2000, images=imgs)
    except Exception as e:
        return "", len(imgs), "图片识字失败：%s" % str(e)[:80]
    got = (got or "").strip()
    if len(got) < 8 or "无可辨认文字" in got and len(got) < 40:
        return "", len(imgs), "%d 张图里没认出文字" % len(imgs)
    block = ["", "## 图片里的文字（非原文，可能识别有误）", "",
             "> 这一节是把正文里的 %d 张图交给模型认字得到的，错字漏字都算它的，别当成作者原话。" % len(imgs), ""]
    return "\n".join(block) + got + "\n", len(imgs), ""


FRAME_OCR_PROMPT = """下面这段短视频按时间顺序抽出来的若干帧（每帧文件名里的数字是它在视频里的秒数附近）。
把每帧画面里**肉眼可见的文字**逐帧抄出来：烧录字幕、标题、界面里的专有名词、产品名都算。
保持原顺序，不改写、不补全、不总结、不翻译；同一句字幕在相邻帧重复出现就各抄各的；没有文字写「（无文字）」。
每帧一段，开头用「帧1」「帧2」标号。"""


def ocr_video_frames(media_name, assets_dir, max_frames=8):
    """视频没有字幕文件时，从固定帧里把烧上去的字认出来 —— 专有名词主要靠这条补齐。
    认不出/没 ffmpeg/没配 key 都安静返回错误串，不影响逐字稿那条主线。"""
    cfg = load_config()
    if not cfg.get("visionOcr", True):
        return "", 0, "配置里关了图片识字"
    if not media_name:
        return "", 0, "没拿到视频文件，跳过抽帧"
    src = (HERE / "media" / Path(str(media_name)).name)
    if not src.is_file():
        return "", 0, "本地找不到视频文件 %s（可能没下载成功）" % Path(str(media_name)).name
    import fetchers
    frames, err = fetchers.sample_frames(str(src), assets_dir, max_frames=max_frames)
    if err:
        return "", 0, err
    try:
        got = call_deepseek(FRAME_OCR_PROMPT, system="你是文字识别工具：只抄见，不写不见。",
                            max_tokens=2000, images=frames)
    except Exception as e:
        return "", len(frames), "抽帧识字失败：%s" % str(e)[:80]
    got = (got or "").strip()
    if len(got) < 8:
        return "", len(frames), "%d 帧里没认出文字" % len(frames)
    block = ["", "## 画面里的文字（非原文，可能识别有误）", "",
             "> 从 %d 个时间点抽帧认出来的烧录字幕/界面文字；语音转写容易把专有名词听错，以这一节为准来核对。" % len(frames),
             ""]
    return "\n".join(block) + got + "\n", len(frames), ""


def rule_digest(title, text):
    """没配模型时的兜底：结构抽取，不编造。"""
    lines = [l.strip() for l in (text or "").splitlines()]
    pts = [re.sub(r"^[-*\d.、\s]+", "", l) for l in lines
           if len(l) > 14 and not l.startswith("#")]
    heads = [l for l in lines if l.startswith("#")]
    out = ["## 核心观点"]
    out += ["- " + p[:120] for p in pts[:5]] or ["- （正文太短，未能抽取要点）"]
    if heads:
        out += ["", "## 原文结构",] + ["- " + re.sub(r"^#+\s*", "", h) for h in heads[:10]]
    out += ["", "## 概念候选"] + (["- " + re.sub(r"^#+\s*", "", h) for h in heads[:4]]
            or ["- （规则抽取未能确定概念）"])
    out += ["", "## 我的疑问",
            "- 边界：这套做法换到我的场景还成立吗？",
            "- 证据：正文里的结论有没有给数据或例子？（规则抽取无法判断，需要 AI 提炼）",
            "- 与我：这条能改我本周哪一个动作？",
            "- 待验证：哪一条我能一周内自己试一遍？",
            "", "## 下一步",
            "- 在「模型配置」里填 API 后重新消化这一条，补全概念与连接。（当前为规则抽取兜底）"]
    return "\n".join(out)


def split_question(line):
    """模型给的疑问是一整行：「维度：问题 —— 触发 —— 已经知道 —— 还卡在哪」。
    以前整行当卡名，22 字一刀砍下去标题就断了，正文里也只剩一句没头没尾的话。
    现在拆开各归各位：问题当标题和队列条目，其余三段进正文当上下文。

    dim 不给默认值：三张卡全都兜成「追问」，等于这个字段白存了 ——
    认不出维度就明写「未标」，界面上看得出来，才知道要回去改提示词。"""
    m = re.match(r"^(边界|证据|与我|待验证|反例|前提)\s*[：:]\s*(.+)$", line)
    dim, rest = (m.group(1), m.group(2)) if m else ("未标", line)
    parts = [p.strip() for p in re.split(r"\s*(?:——|—|｜|\|)\s*", rest) if p.strip()]
    q = parts[0] if parts else rest.strip()
    got = parts[1:]
    # 只给了一段补充时，宁可当「触发」，也别硬凑成三段假上下文
    trigger = got[0] if got else ""
    known = got[1] if len(got) > 1 else ""
    stuck = got[2] if len(got) > 2 else ""
    return dim, q[:60], trigger[:160], known[:160], stuck[:160]


def questions_from_digest(md):
    """从消化稿的「## 我的疑问」里取出条目，变成待复习任务。

    队列里那条只留问题本身（+3 天到期时一眼读得完），上下文留在卡正文里。"""
    m = re.search(r"^##+\s*我的疑问\s*$\n(.*?)(?=^##+\s|\Z)", md or "", re.M | re.S)
    if not m:
        return []
    items = []
    for ln in m.group(1).splitlines():
        q = re.sub(r"^[-*\d.、\s]+", "", ln).strip()
        _dim, question, *_rest = split_question(q)
        if len(question) >= 6 and not question.startswith("（"):
            items.append(question[:80])
    return items


def tags_from_digest(md):
    """「## 主题标签」里的词才是这张库的标签；每行可以是 - 标签 或 顿号分隔。
    模型写飞了（色值、标识符、状态词）由 clean_tags 拦掉，不靠提示词自觉。"""
    m = re.search(r"^##+\s*主题标签\s*$\n(.*?)(?=^##+\s|\Z)", md or "", re.M | re.S)
    if not m:
        return []
    raw = []
    for ln in m.group(1).splitlines():
        s = re.sub(r"^[-*\d.、\s]+", "", ln).strip()
        if not s or s.startswith("（"):
            continue
        raw += re.split(r"[、,，;；|｜\s]+", s)
    return clean_tags([t.strip("#:： ") for t in raw], limit=6)


def append_review_tasks(questions, raw_rel, src=None, raw_chars=0):
    """复习队列的两个入口（口径也写在首页「今天该复习」那一栏的说明里）：

    1. **原料本身** —— 每消化一份原料排一条「重读这份原料」，到期 = 入库 +7 天。
       你说「原料至少要在里面」，之前只有疑问进队列，原料页靠疑问间接出现，
       一份没长出疑问的原料就等于永远不会再遇见。
    2. **每条疑问** —— 一条疑问一个条目，到期 = 入库 +3 天。

    `card` 指向那张问题卡（对不上就退回原料路径，条目一定要能打开），
    `raw` 永远指向这份原料 —— 阅读页两路都算，所以原料页和问题卡页都不会看到空栏。
    src 给平台 / 类型 / 出处，est_min 在入队那一刻算好，前端不再猜。"""
    src = src or {}
    f = VAULT / "inbox" / "待复习任务.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except Exception:
        data = {}
    tasks = data.get("tasks", [])
    have = {t.get("title") for t in tasks}
    now = time.time()
    day = lambda n: time.strftime("%Y-%m-%d", time.localtime(now + n * 86400))
    mt = src.get("media_type") or "文本"
    origin = src.get("title") or raw_rel
    added = 0

    def push(item):
        nonlocal added
        if item["title"] in have:
            return
        tasks.append(item)
        have.add(item["title"])
        added += 1

    raw_title = "重读原料：" + origin
    # 原料这一条的耗时直接沿用这份原料自己的 est_min（调用方刚算过），不在这里第二次折算
    raw_est = as_int(src.get("est_min")) or est_minutes(raw_chars, mt, src.get("duration_ms"),
                                                        images=src.get("images"))
    push({"title": raw_title, "category": "原料重读", "priority": 2,
          "due": day(7), "card": raw_rel, "raw": raw_rel,
          "media_type": mt, "est_min": raw_est,
          "platform": src.get("platform") or "本地", "origin": origin,
          "url": src.get("url") or ""})

    questions = [q for q in (questions or []) if str(q).strip()]
    per = est_review_minutes(raw_chars, len(questions) or 1, mt)
    for q in questions:
        cp = find_card_file(card_name(q, maxlen=22)) or raw_rel
        push({"title": "回看原文：" + q, "category": "消化提问", "priority": 3,
              "due": day(3), "card": cp, "raw": raw_rel,
              "media_type": mt, "est_min": per,
              "platform": src.get("platform") or "本地", "origin": origin,
              "url": src.get("url") or ""})

    if added:
        data["tasks"] = tasks
        data["generatedAt"] = time.strftime("%Y-%m-%d %H:%M")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return added


def slugify(text, maxlen=24):
    s = re.sub(r"[\\/:*?\"<>|\n\r\t]", "", str(text)).strip()
    s = re.sub(r"\s+", "-", s)
    return (s[:maxlen] or "untitled").strip("-")


# ------------------------------------------------- 消化稿 → 三层卡（编译）
#
# 用户的核心链路是「丢链接 → md → 长出概念/连接/问题」。
# 这一步只做确定性的拆解：内容全部来自消化稿本身，不二次调用模型、不编造，
# 并且遵守三条硬规矩：已有同名卡绝不覆盖（只追加）、从不删除、每句话都能回指原料。

CARD_DIR = {"concept": "wiki/concepts", "connection": "wiki/connections",
            "question": "wiki/questions"}
CARD_LABEL = {"concept": "概念", "connection": "连接", "question": "问题"}

# ---------------------------------------------------------------- 字段口径
# 这一节是「内容输出规范」的代码化：状态、类型、预估时间、标签四个字段，
# 每个只有一个合法值集合和一个归一函数，读的人（scan_vault）和写的人（write_card /
# set_status / digest）都走同一个函数，所以页面、筛选、统计不会各说各话。

STATUSES = ("未读", "已读", "待吃透", "待实践", "已内化")

# 库里出现过的历史写法。同义的一律并到一个值上，「意思相近的两个状态」就是这么来的。
STATUS_ALIAS = {
    "已消化": "已读", "消化过": "已读", "读完": "已读", "看过": "已读", "读毕": "已读",
    "未消化": "未读", "没读": "未读", "待看": "未读", "待处理": "未读",
    "待消化": "未读", "在读": "已读", "正在读": "已读",
    "吃透": "待吃透", "待理解": "待吃透", "要吃透": "待吃透",
    "待落地": "待实践", "要去用": "待实践", "待使用": "待实践",
    "内化": "已内化", "已吸收": "已内化", "掌握了": "已内化",
}

# 复习流转只有原料和卡片参与
STATUS_ALLOWED_KINDS = ("wiki", "raw")


def norm_status(v):
    """状态归一：只认 STATUSES 这五个值。
    认不出来的（例如一整句话被写进了 status）返回空串 = 未填写，
    原值另存在 status_raw，阅读页会提示这一条需要归一 —— 不猜、不编。"""
    s = str(v or "").strip().strip("'\"")
    if not s:
        return ""
    if s in STATUSES:
        return s
    if s in STATUS_ALIAS:
        return STATUS_ALIAS[s]
    for st in STATUSES:                       # 「已内化（能脱稿复述）」这种带尾巴的
        if s.startswith(st):
            return st
    return ""


MEDIA_TYPES = ("文本", "图文", "视频", "音频", "其他")

# 阅读时长的常数。有出处的标明出处，没出处的直说是产品自定 —— 别拿 folklore 当依据：
#   200 —— ngryman/reading-time 源码 wordsPerMinute=200，且注释写死「A CJK character is always
#          a word」，即 1 汉字 = 1 词 ⇒ 中文 200 字/分。这是被用得最广的一份实现。
#          （流传的「Medium 用 265 wpm」查不到一手来源，不用。）
#   150 —— 卡片要能复述，比初读慢一档。**没有一手来源，是产品自定常数。**
#   图片 —— @tryghost/helpers 的 reading-minutes：首图 12 秒，逐张 −1 秒，下限 3 秒。
#   视频 / 音频 —— 直接取平台时长，逐字稿再按字数算一遍等于计两次。
READING_CPM, READING_WPM = 200, 200
CARD_CPM, CARD_WPM = 150, 150


def image_seconds(n):
    """首图 12 秒，之后每张递减 1 秒，下限 3 秒（Ghost 的实现）。"""
    return sum(max(12 - i, 3) for i in range(max(0, int(n or 0))))


def media_type_of(note_kind="", text="", has_media=False, method=""):
    """内容形态：只由抓取结果决定，人不填。"""
    nk = str(note_kind or "").strip().lower()
    if nk == "video" or has_media:
        return "视频"
    if nk == "audio":
        return "音频"
    if nk == "image":
        return "图文"
    if re.search(r"!\[[^\]]*\]\(", text or ""):
        return "图文"
    if nk == "text":
        return "文本"
    if str(method or "") == "本地文件导入":
        return "其他"
    return "文本" if (text or "").strip() else "其他"


def count_words(md):
    """正文的「字数」：中文字符按字、拉丁词按词，和 est_minutes 用同一把尺。
    先剥掉围栏代码 / 行内 code / 裸 URL / Markdown 语法字符 / 空白 —— 之前全站把
    「整份 md 的字节数」当字数显示，frontmatter 和 `##` `|` `[[ ]]` 全算进去了，
    一张两百字的薄卡能显示成四百多字，「还没长实」的判据因此从来没真的生效过。"""
    t = strip_code(md)
    t = re.sub(r"https?://\S+", " ", t)                          # 裸 URL 不是要读的字
    t = re.sub(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]", r"\1", t)      # 双链只算锚文字
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                   # 图片标记不算字
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)                # 链接只算锚文字
    t = re.sub(r"[#>*_~`|=\-]{1,}", " ", t)                       # 语法字符
    cjk, latin = read_units(t)
    return cjk + latin


def read_units(text):
    """把一份文本拆成「按字读的中文」和「按词读的拉丁文」。
    中英混排只按单一 cpm 折算会明显偏快：一个英文单词平均 5 个字符，按字符数等于把它当 5 个字读。"""
    t = text or ""
    latin = len(re.findall(r"[A-Za-z0-9_]+", t))
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]", t))
    return cjk, latin


def est_minutes(chars=0, media_type="文本", duration_ms=0, is_card=False, images=0, text=""):
    """预估多少分钟能读完这一份（向上取整，最少 1 分钟）。

    视频 / 音频只取平台给的时长：逐字稿就是那段视频，字数再算一遍等于把同一内容计两次。
    图片按 Ghost 的递减权重补秒数。常数出处见 skill/PROMPTS.md §6。"""
    if media_type in ("视频", "音频") and duration_ms:
        return max(1, int(round(duration_ms / 60000.0)))
    slow = 0.8 if media_type == "图文" else 1.0
    cpm = (CARD_CPM if is_card else READING_CPM) * slow
    wpm = (CARD_WPM if is_card else READING_WPM) * slow
    if text:
        cjk, latin = read_units(text)
        secs = cjk / float(cpm) * 60 + latin / float(wpm) * 60
    else:
        secs = max(0, int(chars or 0)) / float(cpm) * 60
    secs += image_seconds(images)
    return max(1, int(math.ceil(secs / 60.0)))


def est_review_minutes(raw_chars, n_questions, media_type="文本"):
    """一条复习条目几分钟：想起这道题 40 秒，再把原文里它那一段重读一遍。
    「那一段」按原料长度平摊到各条疑问上 —— 只用手上有的字数，不猜内容。"""
    n = max(1, int(n_questions or 1))
    sec = 40 + (float(raw_chars or 0) / n) / READING_CPM * 60
    if media_type in ("视频", "音频"):
        sec += 90                     # 回看视频要拖进度条、看画面
    return max(1, int(math.ceil(sec / 60.0)))


def as_int(v, default=0):
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default


def guess_media_type(fm, kind):
    """老原料没写 media_type 时按抓取留下的痕迹推：有本地视频或抽帧就是视频，
    有图片或识字就是图文；卡片一律算文本（读的是卡片本身）。"""
    if kind in ("concept", "connection", "question"):
        return "文本"
    if kind != "raw":
        return ""
    if str(fm.get("media", "")).strip() or as_int(fm.get("frames")) > 0:
        return "视频"
    if as_int(fm.get("ocr_images")) > 0 or as_int(fm.get("images")) > 0:
        return "图文"
    if str(fm.get("platform", "")) in ("抖音", "B站", "bilibili"):
        return "视频"
    return "文本"


# 标签合法形态：短、能当筛选用。这些噪声是实测抓出来的：
# 正文代码块里的 CSS 色值（#c4734a）、JS 标识符（#readBody）、
# 状态词和维度词被当标签（#已读 #待实践 #边界 #追问）、处理日志（#消化生成 #手写）。
TAG_MAX_LEN = 12
TAG_STOPWORDS = {
    "消化生成", "手写", "AI 代写", "追问", "并入", "原文未给出", "未命名", "待归位",
    "批注", "高亮", "示例", "待补", "无",
    "边界", "证据", "与我", "待验证", "反例", "前提",
}
TAG_STOPWORDS |= set(STATUSES)                       # 状态是状态，不是标签
TAG_STOPWORDS |= {s for s in STATUS_ALIAS}
TAG_HEX = re.compile(r"^[0-9a-fA-F]{3,8}$")
# 逗号 / 冒号 / 引号会把 frontmatter 的 tags: [a, b] 流式列表撕开；# 是标签前缀本身
TAG_BAD_CHARS = re.compile(r"[=/\\<>#&%$@`{}\[\]()|*~?,:;\"']")


def norm_tag(t):
    """Obsidian 的 tag 里不许有空白：中英混排常见的「AI 工具」折成「AI工具」，
    而不是直接丢掉 —— 折一下还能用，丢掉等于把这个维度删了。"""
    return re.sub(r"\s+", "", str(t or "").strip().lstrip("#").strip())
# 平台名 / 来源字段值不是标签：它们已经活在 platform / source 字段里，写进标签就是同一件事说两遍
TAG_STOPWORDS |= {"抖音", "知乎", "B站", "bilibili", "小红书", "公众号", "微信", "微博", "YouTube",
                  "网页", "本地文件", "手动", "Instagram", "Facebook", "Twitter", "X"}


def strip_code(text):
    """去掉围栏代码块和行内 code：那里面出现的 #xxx 是代码，不是标签。"""
    t = re.sub(r"```[^\n]*\n[\s\S]*?(```|$)", " ", str(text or ""))
    return re.sub(r"`[^`\n]+`", " ", t)


def tag_ok(t):
    s = norm_tag(t)
    if not s or len(s) > TAG_MAX_LEN:
        return False
    if TAG_HEX.match(s) and not re.search(r"[g-zG-Z]", s[1:]):   # 240 / abcdef 这种色值
        return False
    if re.fullmatch(r"[\d\W_]+", s):
        return False
    if TAG_BAD_CHARS.search(s):
        return False
    stop = {norm_tag(x) for x in TAG_STOPWORDS}
    if s in stop or s.lower() in {x.lower() for x in stop}:
        return False
    if re.search(r"[a-z][A-Z]", s):            # camelCase 标识符
        return False
    return True


def clean_tags(seq, limit=12):
    """去重 + 归一 + 拦噪声；顺序按出现频次再按字母，保证筛选面板每次排一样。
    Obsidian 的 tag 不区分大小写，所以「AI工具」和「ai工具」是同一个标签 —— 按小写归并，
    拼写取出现次数最多的那一种，不然筛选栏里会并排出现两个只差大小写的芯片。"""
    counts, spell = {}, {}
    for t in seq or []:
        s = norm_tag(t)
        if not tag_ok(s):
            continue
        k = s.lower()
        counts[k] = counts.get(k, 0) + 1
        spell.setdefault(k, {})
        spell[k][s] = spell[k].get(s, 0) + 1
    picked = [(max(spell[k], key=lambda s: (spell[k][s], s)), counts[k]) for k in counts]
    picked.sort(key=lambda x: (-x[1], x[0].lower()))
    return [w for w, _n in picked][:limit]


# 「这份原料长成了哪些卡」只由卡片的 from_raw 字段决定，正文里不再写第二份。
# 老原料和模型自己 echo 出来的这两种小节标题，一律由 strip_card_sections() 剥掉。
CARD_SECTION = "这张原料长成的卡"
CARD_HEADING_RE = re.compile(r"^\s*#{2,4}\s*(?:本次长成的卡|%s)\b" % CARD_SECTION)


def strip_card_sections(md):
    """删掉文本里所有卡列表小节（含模型自己 echo 出来的那种）。
    连它上面那条 `---` 一起删，否则每重写一次就多一条分隔线。"""
    out, skipping = [], False
    for ln in (md or "").splitlines():
        if CARD_HEADING_RE.match(ln):
            skipping = True
            while out and (not out[-1].strip() or re.fullmatch(r"-{3,}", out[-1].strip())):
                out.pop()
            continue
        if skipping:
            if re.match(r"^#{1,6}\s", ln):
                skipping = False
                out.append(ln)
            continue
        out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _fm_set(text, kv):
    """改 frontmatter 里的标量字段（有就替换，没有就插到结束线前面）。
    frontmatter 的字段写入方只有这一个函数 + set_status/set_tags，避免各处各写一套正则。"""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    head = text[:end]
    lines = head.split("\n")
    for k, v in kv.items():
        pat = re.compile(r"^%s:" % re.escape(k))
        line = "%s: %s" % (k, v)
        for i, ln in enumerate(lines[1:], 1):
            if pat.match(ln):
                lines[i] = line
                break
        else:
            lines.insert(len(lines), line)
    return "\n".join(lines) + text[end:]


def section_of(md, name):
    m = re.search(r"^##+\s*%s\s*$\n(.*?)(?=^##+\s|\Z)" % re.escape(name),
                  md or "", re.M | re.S)
    return m.group(1) if m else ""


def bullets(text):
    out = []
    for ln in (text or "").splitlines():
        # 只剥列表标记（- / * / 1. / 2、），不能连内容开头的数字也吃掉（「100 个变体」曾被当序号）
        b = re.sub(r"^\s*(?:[-*+•]|\d+[.)、])\s+", "", ln).strip()
        if len(b) >= 4 and not b.startswith("（"):
            out.append(b)
    return out


def card_name(raw, maxlen=28):
    s = re.sub(r"[\\/:*?\"<>|#\[\]^]", "", str(raw)).strip()
    s = re.sub(r"\s+", " ", s)
    # 维度（边界/证据/与我/待验证）进标签，不进标题 —— 否则每张问题卡都读不通
    s = re.sub(r"^\s*(边界|证据|与我|待验证|反例|前提)\s*[：:]\s*", "", s)
    s = re.sub(r"\s*[（(](边界|证据|与我|待验证|反例|前提)[)）]", "", s)   # 也接受写在尾巴上的维度
    s = re.sub(r"\s*[，,]?\s*原文[^。！？]{0,12}[。！？]?\s*$", "", s)  # 「……？原文未给出」不进标题
    s = re.sub(r"\s*(原文未给出)\s*$", "", s)
    m = re.search(r"\s*[—–]{1,2}\s+", s)           # 「正文——解释」的尾巴：标题只留破折号前面
    if m and m.start() >= 8:
        s = s[:m.start()]
    if len(s) > maxlen:
        stop = "？。！；，、"
        cut = max([i for i, ch in enumerate(s[:maxlen + 1], 1)
                   if ch in stop and i <= maxlen] or [0])
        if cut >= 6:
            s = s[:cut]
        else:                                     # 没有停顿可用：退到空格/括号前，别把词砍一半
            for sep in (" ", "「", "（", "《"):
                j = s[:maxlen].rfind(sep)
                if j >= 6:
                    s = s[:j]
                    break
            else:
                s = s[:maxlen]
    s = s.strip()
    s = _close_brackets(s)                       # 先配平括号，再动首尾字符
    s = re.sub(r"[的地和与是在把对给]\s*$", "", s)   # 截断后留下的虚词让标题读不通
    return (s.strip(" -·—。；，、") or "未命名")


def split_gloss(line):
    """「概念名 —— 一句话解释」；没有分隔符时整条当名字。"""
    for sep in ("——", " — ", "―", " -- ", "：", " —", "—"):
        if sep in line:
            a, b = line.split(sep, 1)
            return a.strip(), b.strip(" —-：")
    return line, ""


def _close_brackets(s):
    """截断后留着半个「（《 或孤零零一个 」） 都很难看：成对不齐的那一头直接去掉。"""
    for o, c in (("「", "」"), ("（", "）"), ("《", "》"), ("【", "】"), ("“", "”")):
        while s.count(o) > s.count(c):
            i = s.rfind(o)
            if i < 0:
                break
            s = s[:i].rstrip()
        while s.count(c) > s.count(o):
            i = s.find(c)
            if i < 0:
                break
            s = (s[:i] + s[i + 1:]).strip()
    return s


def existing_card_names(limit=200):
    try:
        cards = scan_vault()
    except Exception:
        return []
    return [c["title"].strip() for c in cards
            if c.get("kind") in ("concept", "connection") and (c.get("title") or "").strip()][:limit]


def existing_card_titles(limit=200):
    """喂给模型的「库里已有的卡」：连接卡的另一头只能从这里挑，顺带让新卡真的接到旧卡上。"""
    names = existing_card_names(limit)
    return "、".join(names) if names else "（库里还没有卡）"


# 互动数据 / 项目身份 / 技术规格：它们记录的是「这一条」，不是知识。模型不听话时代码兜住
# 指标类只拦「数字/高低比」这种说法，别把「评论区运营」这种正经选题词也毙了
META_NAME = re.compile(
    r"(藏赞|赞藏|收藏高|收藏低|收藏数|收藏倒挂|倒挂|评论低|评论高|评论数|点赞高|点赞低|点赞数|转发数|"
    r"播放量|阅读量|星数|涨粉|掉粉|粉丝数|IP归属|发布时间|"
    r"Apache|MIT许可|GPL|License|许可证|版本号|Node\.js|pnpm|npm|TypeScript|"
    r"安装命令|Repository|仓库名)")
# 有判断词的句子里出现指标名是正常的（「变体量产 ≠ 播放量」），那不是在描述这条笔记的数据
CLAIM_OPS = re.compile(r"(≠|＝|不是|不等于|不算|不能|不可|才是|才会|才能|取决于|前提|优先|一律|别再|才会)")


def find_card_file(name):
    """按卡名（或 frontmatter title）找到那张卡的相对路径；找不到返回 None。"""
    want = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "", str(name or "")).lower()
    if len(want) < 2:
        return None
    for sub in CARD_DIR.values():
        d = VAULT / sub
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            if re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "", p.stem).lower() == want:
                return "%s/%s.md" % (sub, p.stem)
            s = p.read_text(encoding="utf-8", errors="ignore")[:400]
            t = re.search(r"^title:\s*(.+)$", s, re.M)
            if t and re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "", t.group(1)).lower() == want:
                return "%s/%s.md" % (sub, p.stem)
    return None


LINK_SYSTEM = """你在给刚入库的知识卡找它们该接上的旧卡。

判据（三条全过才提，过不了就不提）：
1. 两边讲的是**同一件事的因果、前后、矛盾、上下位或同结论不同路径** ——
   不是「都提到 AI」「都在讲效率」这种话题相似；
2. 关系用一句话说得清，这句话离开两张卡的正文也成立；
3. 另一头的名字**必须**逐字来自我给的那份清单，不许自己造、不许改写、不许挑近义词凑。

输出格式，不许有多余解释、不许标题、不许代码块：
### 新卡的名称原文
[[另一头的名称原文]] —— 关系词 —— 一句话说清为什么真的相关
### 另一张新卡的名称原文
无

关系词只能用：前置 / 矛盾 / 同结论不同路径 / 上下位 / 互补 / 反例。
一张新卡最多 3 条；接不上就在它下面写「无」。宁少勿滥：接错一条，全库图谱就多一条假边。
"""


PLACEHOLDER_HEAD = re.compile(r"^(?:本篇|本文|这一篇|这篇|这个|那条|该|这一|新)?[的以是]?"
                              r"(?:主张|观点|看法|结论|说法|论点)[的以是]?[：:，,、]?\s*")


def norm_head(s):
    """比对卡名时把标点和空白全抹掉：模型爱在 ### 那行加《》或句号，逐字比就会漏掉一整段。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(s or "")).lower()


def side_name(a):
    """连接卡左半边：模型常写「本文主张 ↔ 旧卡」，得把那层指示词剥干净。
    一次剥不净就循环剥 —— 「本文主张」先命中「本文」，剩下「主张」还得再削一刀，
    否则卡名会长成「主张 与 某某」这种没有主语的东西。"""
    s = str(a or "").strip()
    for _ in range(3):
        t = PLACEHOLDER_HEAD.sub("", s).strip(" ：:，,、《》\"'")
        if t == s:
            break
        s = t
    return s


def link_cards(rels, max_per_card=3):
    """新卡入库后回接库里：一次模型调用覆盖这一批，逐张挑真有关系的那几张，写进「接上的线」。

    消化长出来的、一句话生成的、他自己手写的，都走这一条 —— 库里不该有孤立卡。
    模型给的名字一律回到索引里核一遍，对不上的直接丢：宁可少接，也不造边。
    返回 {新卡路径: [接上的卡名]}。"""
    rels = [r for r in (rels or []) if r]
    if not rels:
        return {}
    cards = scan_vault()
    by_rel = {c["path"]: c for c in cards}
    news = [by_rel[r] for r in rels if r in by_rel]
    cands = [c for c in cards if c["path"] not in rels
             and c["kind"] in ("concept", "connection", "question")]
    if not news or not cands:
        return {}
    listing = "\n".join("- %s" % c["title"] for c in cands[:160])
    brief = "\n\n".join("### %s\n类型：%s\n主张：%s" % (
        c["title"], c["kind"],
        re.sub(r"\s+", " ", c.get("summary") or "")[:180] or "（无摘要）") for c in news)
    user = ("刚入库的新卡：\n\n%s\n\n可以接的已有卡（只能逐字从这份清单里挑）：\n%s"
            % (brief, listing))
    try:
        out = call_deepseek(user, system=LINK_SYSTEM, max_tokens=1600)
    except Exception:
        return {}
    by_title = {c["title"]: c for c in cands}
    head_map = {norm_head(c["title"]): c for c in news}
    result, current = {}, None
    for ln in out.splitlines():
        head = re.match(r"^#{1,4}\s*(.+?)\s*$", ln)
        if head:
            current = head_map.get(norm_head(head.group(1)))
            continue
        if not current or "接上的线" in ln:
            continue
        m = re.match(r"^\s*[-*]?\s*\[\[([^\]]+)\]\]\s*[—\-–]+\s*([^—\-–]+)\s*[—\-–]+\s*(.+)$", ln)
        if not m:
            continue
        target, word, why = (x.strip() for x in m.groups())
        hit = by_title.get(target) or next(
            (c for c in cands if norm_head(c["title"]) == norm_head(target)), None)
        if not hit or hit["path"] == current["path"]:
            continue
        bucket = result.setdefault(current["path"], [])
        if any(b.startswith("[[%s]]" % hit["title"]) for b in bucket) or len(bucket) >= max_per_card:
            continue
        bucket.append("[[%s]] —— %s —— %s" % (hit["title"], word[:12], why[:120]))
    for path, lines in list(result.items()):
        if not lines:
            del result[path]
            continue
        p = VAULT / path
        if not p.is_file():
            continue
        head, body = _split_fm(p.read_text(encoding="utf-8"))
        body = re.sub(r"\n*## 接上的线\n[\s\S]*$", "", body.rstrip("\n"))
        body += "\n\n## 接上的线\n" + "".join("- %s\n" % l for l in lines)
        p.write_text(head + body, encoding="utf-8")
    return {k: [l.split(" —— ")[0].strip("[]") for l in v] for k, v in result.items()}


def link_new_card(rel, max_links=3):
    """单张卡（手写 / 一句话生成）的接线路径，复用上面那份批次实现。"""
    return (link_cards([rel], max_per_card=max_links) or {}).get(rel, [])


def _split_fm(text):
    m = re.match(r"^(---\s*\n.*?\n---\s*\n?)", text, re.S)
    return (m.group(1), text[m.end():]) if m else ("", text)


def append_to_card(name, blocks, src_title, url, platform, author, meta=None):
    """把这篇追加进一张已有的概念页（跨来源累积），而不是另起一张近义卡。
    同一篇来源已经引过就不重复写；frontmatter 的 sources 会一路长下去。"""
    rel = find_card_file(name)
    if not rel:
        return write_card("concept", name, blocks, src_title, url, platform, author, meta)
    p = VAULT / rel
    old = p.read_text(encoding="utf-8")
    if ("[[%s]]" % src_title) in old or ("来自《%s》" % src_title) in old:
        return rel, "exists"
    n_src = len(set(re.findall(r"来自《([^》]+)》", old)))
    new = _fm_fill_if_absent(old, {"media_type": (meta or {}).get("media_type") or "",
                                   "from_raw": str((meta or {}).get("from_raw") or "").replace("\\", "/")})
    new = new.rstrip("\n") + "\n\n---\n\n## 补：来自《%s》（%s）\n\n%s\n" % (src_title, platform,
                                                                            strip_meta_echo("\n".join(blocks)))
    new = _add_source_to_frontmatter(new, "%s|%s" % (src_title, url or "本地"))
    p.write_text(new, encoding="utf-8")
    return rel, "merged"


def _add_source_to_frontmatter(text, entry):
    """把来源追加进 frontmatter 的 sources 列表（YAML 块状列表，Obsidian 属性面板能展开看）。"""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    lines, out, i = text[:end].split("\n"), [], 0
    esc = entry.replace('"', "'")
    while i < len(lines):
        if re.match(r"^sources:\s*$", lines[i]):
            i += 1
            vals = []
            while i < len(lines) and re.match(r"^\s+-\s+", lines[i]):
                vals.append(re.sub(r"^\s+-\s+", "", lines[i]).strip().strip('"'))
                i += 1
            if esc not in vals:
                vals.append(esc)
            out += ["sources:"] + ['  - "%s"' % v for v in vals]
            continue
        out.append(lines[i])
        i += 1
    if not any(l.startswith("sources:") for l in out):
        out += ['sources:', '  - "%s"' % esc]
    return "\n".join(out) + text[end:]


# 卡片正文里不许让模型自己复述 frontmatter 已经记着的事实。它很爱在末尾补一行
# 「出处：xxx（知乎）」，格式每轮都不一样，还常和 platform 拼出「（知乎）（知乎）」。
# 剥掉之后由 write_card 统一补一行标准双链 —— 同一件事只有一种写法。
META_ECHO_RE = re.compile(r"^\s*(?:出处|来源|原文链接|原始来源|出自)\s*[：:]")


def strip_meta_echo(body):
    """剥掉模型自己写的「出处：」「来源：」行 —— 出处只有一个写法（见 write_card 末尾）。
    只认行首这几个词，正文里「用在哪：」「边界：」是卡片的维度，不能碰。"""
    lines = [ln for ln in body.split("\n") if not META_ECHO_RE.match(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def q(v):
    """frontmatter 的标量一律走这里。标题里出现「: 」、「#」、开头是 [ 或 { 都会把 YAML 撕开
    （Obsidian 的实时校验会直接标红），而卡片标题就是自由文本。
    YAML 1.2 的双引号转义和 JSON 基本同构，所以借用 json.dumps，不自己拼转义表。"""
    return json.dumps(str("" if v is None else v), ensure_ascii=False)


def tags_flow(tags):
    return "[%s]" % ", ".join(q(t) for t in tags)


def write_card(kind, name, blocks, src_title, url, platform, author, meta=None):
    """建一张卡；同名已存在就追加一段，绝不覆盖原内容。

    meta 是这篇原料的口径（内容输出规范 §2）：media_type / est_min / from_raw / duration_ms /
    tags / dim。字段只在这里写一次，别处不再重复生成 —— 页面显示的「类型 · 预估 · 来源」
    全部来自这几个字段。"""
    meta = meta or {}
    rel = "%s/%s.md" % (CARD_DIR[kind], card_name(name))
    p = VAULT / rel
    tags = clean_tags(meta.get("tags") or [])
    mt = meta.get("media_type") or "文本"
    card_text = "\n".join(blocks)
    est = as_int(meta.get("est_min")) or est_minutes(count_words(card_text),
                                                     mt, meta.get("duration_ms"),
                                                     is_card=True, text=card_text)
    extra = "dim: %s\n" % meta["dim"] if meta.get("dim") else ""
    from_raw = str(meta.get("from_raw") or "").replace("\\", "/")
    head = "---\ntitle: %s\ntype: %s\nsource: %s\nplatform: %s\nauthor: %s\n" \
           "captured_at: %s\nfrom: %s\n%smedia_type: %s\nest_min: %d\n%ssources:\n  - %s\n" \
           "status: %s\ntags: %s\n---\n\n" \
           % (q(name), kind, q(url or "本地文件"), q(platform), q(author),
              time.strftime("%Y-%m-%d"), q(src_title), extra, mt, est,
              ("from_raw: %s\n" % from_raw if from_raw else ""),
              q("%s|%s" % (src_title, url or "本地")),
              meta.get("status") or "待吃透", tags_flow(tags))
    body = strip_meta_echo("\n".join(blocks))
    fill = {"from_raw": from_raw, "media_type": mt, "est_min": est}
    if p.exists():
        old = p.read_text(encoding="utf-8")
        # 命中已有卡时也要补口径字段：老卡可能是上一版规范写的，没有 from_raw / media_type，
        # 不补的话原料页的「长成的卡」会漏掉它，卡片行的「类型 · 预估」会是空的。
        new = _fm_fill_if_absent(old, fill)
        if ("[[%s]]" % src_title) in old or ("来自《%s》" % src_title) in old:
            if new != old:
                p.write_text(new, encoding="utf-8")
            return rel, "exists"
        p.write_text(new.rstrip("\n") + "\n\n---\n\n## 补：来自《%s》\n\n%s\n"
                     % (src_title, body), encoding="utf-8")
        return rel, "appended"
    # 出处由我们写、只写一次：模型爱在末尾补一行「出处：…（知乎）」，格式每轮不一样，
    # 还常和 platform 拼出重复的「（知乎）（知乎）」。统一成一行标准双链，Obsidian 的图谱边也靠它。
    if from_raw and src_title:
        body += "\n\n出处：[[%s]]" % src_title
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(head + "# %s\n\n%s\n" % (name, body), encoding="utf-8")
    return rel, "created"


def _fm_fill_if_absent(text, kv):
    """只补 frontmatter 里根本没有的键，已有的值一个都不覆盖。
    from_raw 是单值：一张卡可以来自多份原料，第一份占位，其余靠正文里的「出处」双链找回。"""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    head, rest = text[:end], text[end:]
    have = set(re.findall(r"^([A-Za-z_][\w-]*)\s*:", head, flags=re.M))
    add = dict((k, v) for k, v in kv.items() if k not in have and str(v or "").strip())
    return (_fm_set(head, add) + rest) if add else text


def compile_cards(md, src_title, url, platform, author, meta=None):
    """把消化稿的三段候选编译成卡，返回 ([(kind, 卡名, 路径, 状态)], [被拦下的说明])。

    meta 是这篇原料的口径（media_type / est_min / from_raw / tags / duration_ms），
    会原样落到每张卡片的 frontmatter —— 「这张卡是什么类型、大概读几分钟、从哪份原料来」
    在生成那一刻就定下来，之后前端只读不猜。

    数量和准入是硬闸门：一次消化最多 3 概念 / 2 连接 / 3 问题，互动数据一律不成卡，
    连接必须接到库里已有的卡上。「每条内容都长出十几张卡」正是这个库要治的病（见 [[双链滥用症]]）。
    """
    meta = dict(meta or {})
    made, dropped = [], []
    src_link = "[[%s]]" % src_title
    known = existing_card_names()
    def match_known(text):
        """把模型给的一侧名字对到库里真实存在的卡名上（模糊包含，去标点后比）。"""
        def norm(x):
            return re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "", x).lower()
        x = norm(text)
        if len(x) < 2:
            return None
        for k in known:
            n = norm(k)
            if n and (n in x or x in n):
                return k
        return None

    def is_meta(name):
        """卡名是互动数据/项目身份就拦；但带判断的（≠ / 不是 / 才是）是真主张，放行。"""
        return bool(META_NAME.search(name)) and not CLAIM_OPS.search(name)

    def cut(line, key):
        """把「｜为什么成立：xxx｜边界：xxx」这类字段拆出来，剩下还给主串。"""
        pat = r"[|｜]\s*" + key + r"[：:]\s*"
        m = re.search(pat, line)
        if not m:
            return line, ""
        head, rest = line[:m.start()].rstrip(" ｜|"), line[m.end():]
        nxt = re.search(r"[|｜]\s*(?:为什么成立|边界|用在哪|并入)[：:]", rest)
        return head + ("｜" + rest[nxt.start():].strip("｜| ") if nxt else ""), \
            (rest[:nxt.start()] if nxt else rest).strip("｜| ")

    def match_concept(text):
        """概念卡回填用的严匹配：连接卡可以靠包含关系找锚点，
        概念卡认错会把两回事写进同一张页，所以要求重叠足够长才算同一张。"""
        def norm(x):
            return re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "", str(x or "")).lower()
        x = norm(text)
        if len(x) < 8:
            return None
        best, score = None, 0
        for k in known:
            n = norm(k)
            if len(n) < 8:
                continue
            if n == x:
                return k
            if n in x or x in n:
                ov = min(len(n), len(x))
            else:                       # 没有包含关系时，看最长公共片段够不够长
                ov = 0
                for i in range(len(x) - 7):
                    if x[i:i + 8] in n:
                        ov = 8
                        break
            if ov > score and ov >= max(8, 0.6 * min(len(n), len(x))):
                best, score = k, ov
        return best

    for line in bullets(section_of(md, "概念候选"))[:3]:
        # 「并入：某张已有卡」= 这篇在印证一张已有的页，不另起近义名（Karpathy：一条素材应该
        # 回填 10-15 张已有页，而不是每条素材都发新卡 —— 见 agent.md §18）
        merge_into = ""
        mm = re.match(r"^\s*并入\s*[：:]\s*[《【\"「]?(.+?)[》】\"」]?\s*(?=——|—|[|｜]|$)", line)
        if mm:
            merge_into = mm.group(1).strip()
            line = line[mm.end():].lstrip(" ——｜|")
        line, tags_txt = cut(line, "标签")
        line, use = cut(line, "用在哪")
        line, why = cut(line, "为什么成立")
        line, edge = cut(line, "边界")
        # 四段没写齐就不建卡。老格式（`- 五组件骨架 —— 一句话`）在这种原料里还有 5 份，
        # 放开不管就会长出一堆名词卡 —— 那正是这个库判为无效记录的那一类。
        if not (why and edge and use):
            miss = "、".join(x for x, v in (("为什么成立", why), ("边界", edge), ("用在哪", use)) if not v)
            dropped.append("概念「%s」没按四段格式写（缺 %s）→ 按标准不算有效记录，没建卡"
                           % (re.split(r"\s*[—|｜]", line, 1)[0].strip()[:20], miss))
            continue
        name, gloss = split_gloss(line)
        name = card_name(name, maxlen=30)
        # 卡名是一句主张，不是一个词：少于 8 字基本是被截断的从句（真出过「摘要可以照抄」）
        if len(name) < 8:
            dropped.append("概念「%s」只有 %d 字，不像一句能独立成立的主张 → 没建卡，"
                           "写成「什么在什么条件下不成立」那样的完整句子再来" % (name, len(name)))
            continue
        if is_meta(name):     # 只看名字：正解释里引用「1 亿播放量」是允许的，那不代表这张卡是数据卡
            dropped.append("概念「%s」是互动数据 / 项目身份 / 技术规格，不是知识 → 没建卡" % name)
            continue
        # 标签按这张卡自己的主题给；模型没给才退回整份原料那一批 ——
        # 之前是一份原料一套标签发到所有卡上，等于「这批货哪来的」而不是「这条主张讲什么」
        cmeta = dict(meta)
        own = clean_tags(re.split(r"[、,，;；\s]+", tags_txt or ""), limit=4)
        if own:
            cmeta["tags"] = own
        target = (match_concept(merge_into) if merge_into else None) or None
        if not target and not merge_into:
            target = match_concept(name)   # 模型没写「并入」但起了个近义名，也当回填处理
        blocks = ["> %s" % (gloss or line), ""]
        blocks += ["**为什么成立**：%s" % (why or "（这篇没给依据 —— 要么你补上，要么这张卡该删）"), ""]
        blocks += ["**边界**：%s" % (edge or "（说不清什么条件下不成立 —— 那它还不是知识，先当你自己的疑问处理）"), ""]
        blocks += ["用在哪：%s" % (card_name(use, maxlen=40) if use else "（说不出来就别留这张卡）"),
                   "", "这是模型的口径，**用自己的话重写它，才算进过你的库**。"]
        if target:
            rel, state = append_to_card(target, blocks, src_title, url, platform, author, cmeta)
            made.append(("concept", target) + (rel, state))
            continue
        made.append(("concept", name) + write_card(
            "concept", name, blocks, src_title, url, platform, author, cmeta))
        known.append(name)          # 空库第一条也得能连起来：本次新建的卡同样是锚点

    for line in bullets(section_of(md, "连接候选"))[:2]:
        m = re.match(r"^(.+?)\s*(?:↔|⇄|<->|<=>|→)\s*(.+?)\s*(?:——|—|:|：)\s*(.+)$", line)
        if not m:
            continue
        a, b, rel_desc = (x.strip() for x in m.groups())
        anchor = match_known(b) or match_known(a)
        if not anchor:
            dropped.append("连接「%s ↔ %s」另一头对不上库里已有的卡 → 没建卡"
                           % (card_name(a, maxlen=14), card_name(b, maxlen=14)))
            continue
        # 模型有时把左边写成「本篇 / 本文 / 这个主张」这种占位词 —— 换成真的说法
        side = card_name(side_name(a), maxlen=16)
        if len(side) < 4 or side == "未命名":
            own = [n for k, n, _r, _s in made if k == "concept"]
            side = own[0] if own else card_name(src_title, maxlen=16)
        # 两边各自留预算，别让 card_name 从右往左砍掉 —— 曾经砍到只剩左半边，
        # 结果连接卡和概念卡同名（先质疑问题本身再回答它.md），图谱上就是一个幽灵
        name = card_name("%s 与 %s" % (card_name(side, maxlen=14),
                                       card_name(anchor, maxlen=20)), maxlen=38)
        blocks = ["%s ↔ [[%s]]：%s" % (side, anchor, rel_desc),
                  "", "一侧是这篇的新主张，另一头接的是库里已经想明白的东西 —— 接不上就不该是张卡。"]
        made.append(("connection", name) + write_card(
            "connection", name, blocks, src_title, url, platform, author, meta))

    for line in bullets(section_of(md, "我的疑问"))[:3]:
        dim, q, trigger, known, stuck = split_question(line)
        name = card_name(q, maxlen=22)
        if len(name) < 4:
            continue
        # 问题卡的价值全在上下文：光一句问句，三个月后回来根本想不起在问什么。
        # 六段固定 —— 问题 / 触发 / 已经知道 / 目前的猜测 / 判定标准 / 下一步要什么材料。
        blocks = ["> %s" % q,
                  "", "**是什么让我问出这句**：%s" % (trigger or "（模型没给，等你自己补一句）"),
                  "", "**我已经知道什么**：%s" % (known or "无"),
                  "", "**目前的猜测**：%s" % (stuck or "还没有"),
                  "", "**什么算想通了**（勾得动这条，这道题才算答过）：", "", "- [ ] 判定标准：",
                  "", "**下一步要什么材料**：（要查什么 / 再丢哪条链接进来）"]
        qmeta = dict(meta)
        qmeta["dim"] = dim          # 维度进 dim 字段，不进 tags：标签是主题，不是处理步骤
        made.append(("question", name) + write_card(
            "question", name, blocks, src_title, url, platform, author, qmeta))

    return made, dropped


# ---------------------------------------------------------------- HTTP 处理

class Handler(BaseHTTPRequestHandler):
    server_version = "KBStudio/1.0"

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stderr.write("  %s %s\n" % (self.command, self.path))

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def send_media(self, rel):
        """把本地视频流回去，支持单段 Range —— 浏览器拖进度条、逐字稿跳时间点全靠它。"""
        root = (HERE / "media").resolve()
        f = (root / rel).resolve()
        if not str(f).startswith(str(root)) or not f.is_file():
            return self._send(404, b"404 Not Found", "text/plain; charset=utf-8")
        size = f.stat().st_size
        ctype = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
                 ".m4a": "audio/mp4", ".mp3": "audio/mpeg"}.get(f.suffix.lower(),
                                                               "application/octet-stream")
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        start, end, code = 0, size - 1, 200
        if m and (m.group(1) or m.group(2)):
            start = int(m.group(1) or 0)
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            code = 206
        start = max(0, min(start, size - 1))
        with open(f, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(max(0, end - start + 1))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(chunk)

    # ------------------------------------------------------------ GET
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path.startswith("/api/"):
            return self.api(path, qs)

        # 库内文件（正文图片等）：只允许读 vault 里 raw/assets 下的资源，做了穿越防护
        if path.startswith("/assets/"):
            rel = urllib.parse.unquote(path[len("/assets/"):])
            root = (VAULT / "raw" / "assets").resolve()
            f = (root / rel).resolve()
            if not str(f).startswith(str(root)) or not f.is_file():
                return self._send(404, b"404", "text/plain; charset=utf-8")
            ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                     ".svg": "image/svg+xml"}.get(f.suffix.lower(), "application/octet-stream")
            return self._send(200, f.read_bytes(), ctype)

        # 本地视频（抖音/小红书下载下来的 mp4）：逐字稿点时间戳要靠它，支持 Range 才能跳转
        if path.startswith("/media/"):
            return self.send_media(urllib.parse.unquote(path[len("/media/"):]))

        # 静态文件
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if not str(f).startswith(str(STATIC.resolve())) or not f.is_file():
            return self._send(404, b"404 Not Found", "text/plain; charset=utf-8")
        ctype = {".html": "text/html; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".svg": "image/svg+xml",
                 # 皮肤里的 IP 姿态图在 static/ip/ 下，之前一律回 octet-stream
                 ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp", ".gif": "image/gif"}.get(f.suffix, "application/octet-stream")
        self._send(200, f.read_bytes(), ctype)

    def api(self, path, qs):
        try:
            if path == "/api/stats":
                return self._json(self.stats())
            if path == "/api/cards":
                return self._json({"cards": scan_vault()})
            if path == "/api/graph":
                return self._json(build_graph(scan_vault()))
            if path == "/api/card":
                return self._json(self.card(qs.get("path", [""])[0]))
            if path == "/api/search":
                return self._json({"results": self.search(qs.get("q", [""])[0])})
            if path == "/api/tasks":
                return self._json({"tasks": self.tasks(), "log": self.done_log()})
            if path == "/api/config":
                import fetchers
                return self._json(dict(masked_config(),
                                       cookies=fetchers.cookie_status(),
                                       skill=str((HERE / "skill" / "PROMPTS.md").is_file())))
            return self._json({"error": "未知接口 " + path}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def stats(self):
        cards = scan_vault()
        by_kind = {}
        for c in cards:
            by_kind.setdefault(c["kind"], 0)
            by_kind[c["kind"]] += 1
        tasks = self.tasks()
        today = time.strftime("%Y-%m-%d")
        due = [t for t in tasks if not t.get("done") and t.get("due", "") <= today]
        tags, spell = {}, {}
        for c in cards:
            for t in c["tags"]:
                k = str(t).lower()          # Obsidian 的标签不分大小写，归并成一格
                tags[k] = tags.get(k, 0) + 1
                spell.setdefault(k, {})
                spell[k][t] = spell[k].get(t, 0) + 1
        named = [(max(spell[k], key=lambda s: (spell[k][s], s)), n) for k, n in tags.items()]
        return {"vault": str(VAULT), "total": len(cards), "byKind": by_kind,
                "tasksTotal": len(tasks), "tasksDue": len(due),
                "topTags": sorted(named, key=lambda x: -x[1])[:12]}

    def card(self, rel):
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())) or not p.is_file():
            return {"error": "文件不存在"}
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(text)
        cards = scan_vault()
        # 反链要认全：[[双链]] 里写的多半是标题或别名，不是文件名
        # （只按 stem 匹配的话，卡片里那句「出处：[[原料标题]]」永远数不到原料头上）
        names = {p.stem, p.name[:-3], str(fm.get("title") or "")}
        al = fm.get("aliases") or fm.get("alias") or []
        if isinstance(al, str):
            al = [al]
        names |= {str(a).strip() for a in al if str(a).strip()}
        names.discard("")
        backlinks = [{"path": c["path"], "title": c["title"]}
                     for c in cards if names & set(c.get("links") or [])]
        return {"path": rel, "frontmatter": fm, "body": body, "raw": text,
                "linkNames": sorted(names), "backlinks": backlinks}

    def search(self, q):
        q = (q or "").strip()
        if not q:
            return []
        cards = scan_vault()
        out = []
        for c in cards:
            try:
                text = (VAULT / c["path"]).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if q.lower() not in text.lower() and q.lower() not in c["title"].lower():
                continue
            idx = text.lower().find(q.lower())
            snippet = ""
            if idx >= 0:
                snippet = text[max(0, idx - 60):idx + 120].replace("\n", " ")
            out.append({"path": c["path"], "title": c["title"], "kind": c["kind"],
                        "kindLabel": c["kindLabel"], "snippet": snippet})
        return out[:50]

    def tasks(self):
        f = VAULT / "inbox" / "待复习任务.json"
        if not f.is_file():
            return []
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
        tasks = data.get("tasks", [])
        done_file = VAULT / "inbox" / "已完成任务.json"
        done = set()
        if done_file.is_file():
            try:
                done = set(json.loads(done_file.read_text(encoding="utf-8")).get("done", []))
            except Exception:
                done = set()
        for t in tasks:
            t["done"] = t.get("title") in done
        return tasks

    def done_log(self):
        """「什么时候勾掉的」只有完成记录里才有，仪表盘「近期已完成」要靠它。
        done 仍是纯标题列表（别的脚本在读），日期另开一个 log 数组，两边一起写。"""
        f = VAULT / "inbox" / "已完成任务.json"
        if not f.is_file():
            return []
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
        log = data.get("log") or []
        out = []
        for it in log:
            if isinstance(it, dict) and it.get("title"):
                out.append({"title": it["title"], "at": it.get("at", "")})
        titles = {t["title"] for t in out}
        for title in (data.get("done") or []):
            if isinstance(title, str) and title and title not in titles:
                out.append({"title": title, "at": ""})
        out.sort(key=lambda x: x["at"], reverse=True)
        return out[:60]

    # ------------------------------------------------------------ POST
    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            return self._json({"error": "请求体不是合法 JSON"}, 400)
        try:
            if u.path == "/api/digest":
                return self._json(self.digest(body))
            if u.path == "/api/redigest":
                return self._json(self.redigest(body))
            if u.path == "/api/recompile":
                return self._json(self.recompile(body))
            if u.path == "/api/status":
                return self._json(self.set_status(body))
            if u.path == "/api/card/add":
                return self._json(self.add_card(body))
            if u.path == "/api/card/generate":
                return self._json(self.gen_card(body))
            if u.path == "/api/card/tags":
                return self._json(self.set_tags(body))
            if u.path == "/api/task/done":
                return self._json(self.task_done(body))
            if u.path == "/api/save":
                return self._json(self.save(body))
            if u.path == "/api/card/drop":
                return self._json(self.drop_card(body))
            if u.path == "/api/config":
                if not body:
                    return self._json(load_config())
                # 这一行以前是漏的：网页点「保存」只回了一份掩码配置，
                # 界面显示「已保存」而磁盘上连 config.json 都没生成 —— 新用户第一次填 key 就卡在这。
                save_config(body)
                return self._json(masked_config())
            if u.path == "/api/llm/test":
                return self._json(self.llm_test())
            if u.path == "/api/summary":
                return self._json(self.study_summary(body))
            if u.path == "/api/fetch":
                return self._json(self.preview_fetch(body))
            if u.path == "/api/cookie":
                return self._json(self.save_cookie(body))
            if u.path == "/api/cookie/check":
                return self._json(self.check_cookie(body))
            if u.path == "/api/cookie/drop":
                return self._json(self.drop_cookie(body))
            if u.path == "/api/annotate":
                return self._json(self.annotate(body))
            if u.path == "/api/import":
                return self._json(self.import_file(body))
            return self._json({"error": "未知接口 " + u.path}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def digest(self, body):
        """完整消化：抓取 → 图片入资产 → AI 提炼（没配模型则规则兜底）→ 写入 vault"""
        import fetchers

        url = (body.get("url") or "").strip()
        raw_text = (body.get("text") or "").strip()
        # 他从 App 复制的经常是「口令 + 中文 + 链接 + 尾巴」，先把链接抠出来
        if url and not re.match(r"^https?://\S+$", url):
            import fetchers
            got, _junk = fetchers.extract_url(url)
            if got:
                url = got
        if not url and not raw_text:
            return {"ok": False, "error": "给我一条链接，或者直接把正文粘进来"}

        meta = {"title": (body.get("title") or "").strip(),
                "author": (body.get("author") or "").strip(),
                "platform": (body.get("platform") or "网页").strip(),
                "url": url, "text": raw_text,
                "method": (body.get("method") or "手动粘贴").strip()}
        note = ""

        # 1) 抓取（读不到就把平台的 hint 原样告诉用户：怎么给 cookie、或改为粘贴）
        if url and not raw_text:
            info = fetchers.fetch(url, block=int(body.get("block") or 45))
            if not info.get("ok"):
                return {"ok": False, "platform": info.get("platform", ""),
                        "error": info.get("error", "抓取失败"),
                        "hint": info.get("hint", ""), "url": url}
            meta.update({"title": info.get("title", ""), "author": info.get("author", ""),
                         "platform": info.get("platform", "网页"), "url": info.get("url", url),
                         "text": info.get("text", ""), "method": info.get("method", ""),
                         "media": info.get("media", ""),
                         "duration_ms": info.get("duration_ms", 0),
                         "note_kind": info.get("note_kind", "")})

        if not (meta["text"] or "").strip():
            return {"ok": False, "error": "取到了页面但没有正文",
                    "hint": "多半是正文在图片里或被前端渲染；把文字粘给我最快"}

        # 1b) 恢复段落：从知乎/小红书/微信粘过来的正文经常整个回答挤在一行，
        #     这种文本喂给模型会丢结构、手机上也读不动。只插换行，不改一个字。
        before = meta["text"]
        meta["text"] = fetchers.reflow_text(before)
        if meta["text"].count("\n") > before.count("\n") + 1:
            note = "已恢复段落（原文整段被压成一行的部分重新分段）"

        title = re.sub(r"\s+", " ", meta["title"] or (url[:60] if url else "未命名")).strip()[:80]
        today = time.strftime("%Y%m%d")
        base = slugify(title)
        assets = VAULT / "raw" / "assets" / ("%s-%s" % (today, base))

        # 2) 正文图片落到 raw/assets/，引用改成本地路径
        imgs = 0
        if body.get("images", True):
            try:
                meta["text"], imgs = fetchers.download_images(
                    meta["text"], meta["url"], assets,
                    rel_prefix="assets/%s-%s" % (today, base))
            except Exception as e:
                note = "图片下载失败（不影响正文）：%s" % e

        # 3) 图片识字：图文类内容（小红书图页、截图、图表）的正文常常整页长在图上
        ocr_blk, ocr_n, ocr_err = "", 0, ""
        frame_blk, frame_n, frame_err = "", 0, ""
        if meta.get("note_kind") == "video":
            # 视频不拿封面识字（只会认出水印和大字），但要做抽帧识字：烧录字幕能把专有名词纠对
            if body.get("ocr", True):
                frame_blk, frame_n, frame_err = ocr_video_frames(
                    meta.get("media"), assets,
                    int(body.get("maxFrames") or load_config().get("maxFrames") or 6))
            else:
                frame_err = "视频笔记：按设置跳过了识字"
        elif body.get("ocr", True):
            ocr_blk, ocr_n, ocr_err = ocr_local_images(meta["text"], body.get("ocrLimit"))
        feed = meta["text"]
        for extra in (ocr_blk, frame_blk):
            if extra:
                feed += "\n" + extra
        if ocr_blk:
            note = (note + " " if note else "") + "图片识字：%d 张图认出 %d 字" % (ocr_n, len(ocr_blk.strip()))
        elif ocr_err:
            note = (note + " " if note else "") + ocr_err
        if frame_blk:
            note = (note + " " if note else "") + "抽帧识字：%d 帧认出 %d 字" % (frame_n, len(frame_blk.strip()))
        elif frame_err:
            note = (note + " " if note else "") + frame_err

        # 4) AI 提炼；没配 key 就走规则抽取并明说
        ai_used = True
        try:
            md = call_deepseek(DIGEST_PROMPT.format(
                title=title, platform=meta["platform"], url=meta["url"],
                author=meta["author"] or "未知", content=fit_content(feed),
                existing=existing_card_titles()))
        except Exception as e:
            ai_used = False
            md = rule_digest(title, feed)
            note = (note + " " if note else "") + "未做 AI 提炼（%s）→ 已用规则抽取兜底" % e

        # 5) 编译成三层卡（只有 AI 提炼成功才建；规则兜底那四条固定疑问不该变成卡）
        media_type = media_type_of(meta.get("note_kind"), meta["text"],
                                   bool(meta.get("media")), meta["method"])
        dur_ms = as_int(meta.get("duration_ms"))
        # 原料的目标路径先定下来：卡片的 from_raw 要指它。
        # 「这份原料长成了哪些卡」从此只有这一个来源 —— 前端按 from_raw 算，正文里不写第二份。
        clip = VAULT / "raw" / "clips" / ("%s-%s.md" % (today, base))
        n = 1
        while clip.exists():
            clip = VAULT / "raw" / "clips" / ("%s-%s-%d.md" % (today, base, n))
            n += 1
        rel = str(clip.relative_to(VAULT)).replace("\\", "/")

        md = strip_card_sections(md)
        card_meta = {"media_type": media_type, "duration_ms": dur_ms,
                    "from_raw": rel, "tags": tags_from_digest(md)}
        made, dropped = [], []
        if body.get("compile", True) and ai_used:
            made, dropped = compile_cards(md, title, meta["url"], meta["platform"],
                                          meta["author"] or "未知", card_meta)
            if not made:
                note = (note + " " if note else "") + "消化稿里没解析出可用的候选，未建卡"
            if dropped:
                note = (note + " " if note else "") + "拦下 %d 条不该成卡的" % len(dropped)

        # 6) 写原料（只增不改）
        clip.parent.mkdir(parents=True, exist_ok=True)
        original = meta["text"][:60000]
        for extra in (ocr_blk, frame_blk):
            if extra:
                original += "\n\n" + extra
        media_rel = "media: %s\n" % meta.get("media") if meta.get("media") else ""
        dur_rel = "duration_ms: %d\n" % dur_ms if dur_ms else ""
        est_src = est_minutes(count_words(original + "\n" + md), media_type, dur_ms,
                              images=imgs, text=original + "\n" + md)
        card_meta["est_min"] = est_src
        clip.write_text(
            "---\ntitle: %s\naliases:\n  - \"%s\"\ntype: raw\nsource: %s\nplatform: %s\nauthor: %s\n"
            "captured_at: %s\nmethod: %s\nimages: %d\nocr_images: %d\nframes: %d\n%s%s"
            "media_type: %s\nest_min: %d\nstatus: 未读\n---\n\n"
            "# %s\n\n> 来源：%s ｜ 平台：%s ｜ 作者：%s ｜ 类型：%s ｜ 预估 %d 分钟\n\n"
            "# 原文（抓取所得，未加工）\n\n%s\n\n---\n\n"
            "# 消化稿（模型提炼，属于衍生内容）\n\n"
            "> 下面这一节不是作者原话，是处理结果；要引用请以「原文」一节为准。\n\n%s\n"
            % (title, title.replace('"', "'"), meta["url"], meta["platform"], meta["author"] or "未知",
               time.strftime("%Y-%m-%d"), meta["method"], imgs, ocr_n, frame_n, media_rel, dur_rel,
               media_type, est_src,
               title, meta["url"], meta["platform"], meta["author"] or "未知",
               media_type, est_src, original, md),
            encoding="utf-8")

        # 7) 消化出来的疑问自动进「待复习」队列（勾选仍需先打开原文）
        qs = questions_from_digest(md)
        asked = append_review_tasks(qs, rel,
                                    {"media_type": media_type, "platform": meta["platform"],
                                     "url": meta["url"], "title": title,
                                     "est_min": est_src, "duration_ms": dur_ms, "images": imgs},
                                    raw_chars=len(original))
        if ai_used and not qs:
            note = (note + " " if note else "") + "这份消化稿没写「我的疑问」，复习队列里不会有条目"

        # 8) 回接库里：一次调用覆盖这一批新卡，把接得上的写进各自的「接上的线」。
        #    连接候选要另一头已经在库里才建得出卡，空库前几条永远连不上 —— 这一步补的就是那个洞。
        linked = link_cards([r for _k, _n, r, s in made if s == "created"])

        return {"ok": True, "clip": rel,
                "title": title, "platform": meta["platform"],
                "kind": meta.get("note_kind", ""),
                "media_type": media_type, "est_min": est_src,
                "method": meta["method"], "digest": md, "images": imgs,
                "ocrImages": ocr_n if ocr_blk else 0,
                "frames": frame_n if frame_blk else 0,
                "questions": asked, "chars": len(meta["text"]), "note": note,
                "dropped": dropped, "linked": linked,
                "cards": [{"kind": k, "label": CARD_LABEL[k], "name": n,
                           "path": r, "state": s} for k, n, r, s in made]}

    # ------------------------------------------------------------ 原料改写后重消化 / 手动加卡
    def _clip_parts(self, s):
        """把原料文件切成「原文部分」和「消化稿部分」；没有消化稿时 digest 为空串。"""
        m = re.search(r"^#\s*消化稿[^\n]*$", s, re.M)
        if not m:
            return s, ""
        head = re.sub(r"\n-{3,}\s*$", "", s[:m.start()].rstrip()) + "\n"
        return head, s[m.start():]

    def _raw_body(self, head):
        """取「# 原文」那一节的正文；老原料没有这个标题就退回整篇去掉 frontmatter 的正文。"""
        m = re.search(r"^#\s*原文[^\n]*$", head, re.M)
        body = head[m.end():] if m else re.sub(r"\A---.*?---\s*", "", head, flags=re.S)
        body = body.strip()
        # 去掉尾部那条 "# 原文 … # 消化稿" 之间的分隔线
        while body.endswith("-"):
            body = body[:-1]
        return body.strip()

    def redigest(self, body):
        """原料可以改了：改完点这个按钮，用当前原文重新生成消化稿 + 三层卡。
        只替换「# 消化稿」那一节，原文一字不动；同名卡仍是只补不覆盖。"""
        rel = (body.get("path") or "").strip().replace("\\", "/")
        if not rel.lower().endswith(".md"):
            return {"ok": False, "error": "只能重消化 md 原料"}
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())) or not p.is_file():
            return {"ok": False, "error": "找不到这份原料：%s" % rel}

        s = p.read_text(encoding="utf-8")
        new_content = body.get("content")
        if isinstance(new_content, str) and new_content.strip():
            s = new_content if new_content.endswith("\n") else new_content + "\n"
            p.write_text(s, encoding="utf-8")        # 先把他改的存下来，再生成

        fm, _rest = parse_frontmatter(s)
        head, old_digest = self._clip_parts(s)
        raw = self._raw_body(head)
        if len(raw.strip()) < 30:
            return {"ok": False, "error": "这一份的原文太短（不足 30 字），没法重新消化",
                    "hint": "先把原文补进「# 原文」那一节再点重新生成"}

        title = (fm.get("title") or p.stem).strip()
        url = fm.get("source") or fm.get("url") or ""
        platform = fm.get("platform") or "网页"
        author = fm.get("author") or "未知"
        media_type = str(fm.get("media_type") or "").strip()
        if media_type not in MEDIA_TYPES:
            media_type = guess_media_type(fm, "raw")
        dur_ms = as_int(fm.get("duration_ms"))

        note = ""
        try:
            md = call_deepseek(DIGEST_PROMPT.format(
                title=title, platform=platform, url=url, author=author,
                content=fit_content(raw), existing=existing_card_titles()))
        except Exception as e:
            return {"ok": False,
                    "error": "重新消化失败：%s" % str(e)[:160],
                    "hint": "原文已保存，没有动过；改一下「模型配置」或把过长的图片说明删掉再试"}

        md = strip_card_sections(md)
        est_src = est_minutes(count_words(raw + "\n" + md), media_type, dur_ms,
                              text=raw + "\n" + md)
        card_meta = {"media_type": media_type, "duration_ms": dur_ms, "est_min": est_src,
                     "from_raw": rel, "tags": tags_from_digest(md)}
        made, dropped = [], []
        if body.get("compile", True):
            made, dropped = compile_cards(md, title, url, platform, author, card_meta)
            note = ("重新长出 %d 张卡" % len(made)) if made \
                else "消化稿重写了，但没解析出可建卡的候选"
            if dropped:
                note += "，拦下 %d 条" % len(dropped)
        if not questions_from_digest(md):
            note = (note + " " if note else "") + "这份消化稿没写「我的疑问」，复习队列里不会多出条目"

        stamp = time.strftime("%Y-%m-%d %H:%M")
        backup = ""
        if old_digest.strip():
            trash = VAULT / "inbox" / "trash"
            trash.mkdir(parents=True, exist_ok=True)
            bak = trash / ("%s.digest-%s.md" % (p.stem, time.strftime("%Y%m%d%H%M%S")))
            bak.write_text(old_digest, encoding="utf-8")
            backup = str(bak.relative_to(VAULT)).replace("\\", "/")
        digest_section = ("# 消化稿（模型提炼，属于衍生内容）\n\n"
                          "> 下面这一节不是作者原话，是处理结果；要引用请以「原文」一节为准。\n"
                          "> 最近一次重生成：%s\n\n%s\n"
                          % (stamp, md))
        p.write_text(_fm_set(strip_card_sections(head),
                             {"media_type": media_type, "est_min": est_src}).rstrip("\n")
                     + "\n\n---\n\n" + digest_section, encoding="utf-8")

        asked = append_review_tasks(questions_from_digest(md), rel,
                                    {"media_type": media_type, "platform": platform,
                                     "url": url, "title": title, "est_min": est_src,
                                     "duration_ms": dur_ms}, raw_chars=len(raw))
        linked = link_cards([r for _k, _n, r, s in made if s == "created"])
        return {"ok": True, "clip": rel, "title": title, "digest": md,
                "media_type": media_type, "est_min": est_src,
                "cards": [{"kind": k, "label": CARD_LABEL[k], "name": n,
                           "path": r, "state": s} for k, n, r, s in made],
                "dropped": dropped, "questions": asked, "note": note,
                "linked": linked,
                "backup": backup, "chars": len(raw)}

    def recompile(self, body):
        """只从现有消化稿重新长卡，**不调模型** —— 消化稿被人工改过（或模板换了）之后
        想同步卡片，不必再花一次 API。同名卡仍是只补不覆盖。"""
        rel = (body.get("path") or "").strip().replace("\\", "/")
        if not rel.lower().endswith(".md"):
            return {"ok": False, "error": "只能重编译 md 原料"}
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())) or not p.is_file():
            return {"ok": False, "error": "找不到这份原料：%s" % rel}
        s = p.read_text(encoding="utf-8")
        head, digest = self._clip_parts(s)
        if not digest.strip():
            return {"ok": False, "error": "这一份还没有消化稿，只能先「按原文重新生成」（那一步要调模型）"}
        fm, _rest = parse_frontmatter(s)
        title = (fm.get("title") or p.stem).strip()
        media_type = str(fm.get("media_type") or "").strip()
        if media_type not in MEDIA_TYPES:
            media_type = guess_media_type(fm, "raw")
        est_src = as_int(fm.get("est_min")) or est_minutes(count_words(s), media_type,
                                                           as_int(fm.get("duration_ms")), text=s)
        digest = strip_card_sections(digest)
        made, dropped = compile_cards(digest, title, fm.get("source") or "",
                                      fm.get("platform") or "网页", fm.get("author") or "未知",
                                      {"media_type": media_type, "est_min": est_src,
                                       "duration_ms": as_int(fm.get("duration_ms")),
                                       "from_raw": rel, "tags": tags_from_digest(digest)})
        if not made and not dropped:
            # 消化稿里没有可编译的候选（比如只有核心观点）—— 那就一个字都不动
            return {"ok": True, "clip": rel, "title": title, "cards": [], "dropped": [],
                    "questions": 0, "note": "这份消化稿里没有可编译的候选，卡片没动"}
        p.write_text(head.rstrip("\n") + "\n\n---\n\n" + digest.rstrip("\n") + "\n",
                     encoding="utf-8")
        asked = append_review_tasks(questions_from_digest(digest), rel,
                                    {"media_type": media_type, "platform": fm.get("platform") or "网页",
                                     "url": fm.get("source") or "", "title": title,
                                     "est_min": est_src, "duration_ms": as_int(fm.get("duration_ms"))},
                                    raw_chars=len(self._raw_body(head)))
        return {"ok": True, "clip": rel, "title": title,
                "cards": [{"kind": k, "label": CARD_LABEL[k], "name": n,
                           "path": r, "state": st} for k, n, r, st in made],
                "dropped": dropped, "questions": asked,
                "note": "没调模型：%d 张卡（新建/并入/已存在），拦下 %d 条" % (len(made), len(dropped))}

    def add_card(self, body):
        """他自己照着原料手写的卡：走同一套命名和落盘规则，所以后面 AI 重生成也只补不覆盖。"""
        kind = (body.get("kind") or "").strip()
        if kind not in CARD_LABEL:
            return {"ok": False, "error": "kind 只能是 concept / connection / question"}
        name = card_name((body.get("name") or "").strip(), maxlen=30)
        if len(name) < 4:
            return {"ok": False, "error": "卡片名太短，写成一句能独立成立的话（≥4 字）"}
        src_rel = (body.get("source") or "").strip().replace("\\", "/")
        sp = (VAULT / src_rel).resolve() if src_rel else None
        if sp is not None and not str(sp).startswith(str(VAULT.resolve())):
            sp = None
        if sp is not None and sp.is_file():
            fm, _rest = parse_frontmatter(sp.read_text(encoding="utf-8"))
            src_title, url = fm.get("title") or sp.stem, fm.get("source") or ""
            platform, author = fm.get("platform") or "本地", fm.get("author") or "未知"
            src_link = "[[%s]]" % src_title
            mt = str(fm.get("media_type") or "").strip()
            if mt not in MEDIA_TYPES:
                mt = guess_media_type(fm, kind_of(src_rel, fm))
            card_meta = {"media_type": mt, "est_min": as_int(fm.get("est_min")),
                         "duration_ms": as_int(fm.get("duration_ms")),
                         # 只有原料才写 from_raw：挂在概念卡下面的新卡靠那句「出处」双链，
                         # 不然侧栏「这张原料长成的卡」里会混进非原料的东西
                         "from_raw": src_rel if src_rel.startswith("raw/") else "",
                         "tags": clean_tags(body.get("tags") or [], limit=6)}
        else:
            src_title, url = (body.get("from") or "手动添加").strip(), ""
            platform, author, src_link = "手动", "本人", ""
            card_meta = {"media_type": "文本", "from_raw": "",
                         "tags": clean_tags(body.get("tags") or [], limit=6)}

        text = (body.get("text") or "").strip()
        lines = [l for l in text.splitlines() if l.strip()] or ["（先写一句话，回头再补）"]
        if kind == "connection":
            # 连接必须真的接到一张已有卡上，否则就退化成第二条概念卡
            anchors = [n for n in existing_card_names() if n and n != name]
            linked = re.findall(r"\[\[([^\]]+)\]\]", text)
            blocks = [lines[0]] + ([""] + lines[1:] if len(lines) > 1 else [])
            if not linked:
                near = next((a for a in anchors
                             if a and (a in text or text[:6] in a)), "")
                if near:
                    blocks.insert(0, "%s ↔ [[%s]]" % (name, near))
                else:
                    return {"ok": False,
                            "error": "连接卡要写明接的是库里哪张卡：输入 [[已有卡名]] 或用「A ↔ B」",
                            "hint": "在『接的是』一栏填另一头的卡名（可先搜库）"}
        elif kind == "question":
            blocks = ["> %s" % lines[0]] + ([""] + lines[1:] if len(lines) > 1 else [])
            blocks += ["", "现在还没答案。写得出「什么算想通了」，这道题才算你的：", "", "- [ ] 判定标准："]
        else:
            blocks = ["> %s" % lines[0]] + ([""] + lines[1:] if len(lines) > 1 else [])
            blocks += ["", "用在哪：%s" % ((body.get("use") or "").strip() or "（说不出来就先别留这张卡）")]
        # 出处只留一行：write_card 在 from_raw 存在时已经会补标准双链，这里再补就是同一件事两遍
        if src_link and not card_meta.get("from_raw"):
            blocks += ["", "出处：%s" % src_link]
        blocks += ["", "（这张是我自己写的，不是模型口径。）" if not body.get("ai")
                   else "（这张是按我的一句话让模型补的，口径要我自己核。）"]

        rel, state = write_card(kind, name, blocks, src_title, url, platform, author, card_meta)
        linked = []
        if body.get("link", True):
            # 手写卡和 AI 生成的卡走同一条接线路：库里不该躺着孤立卡
            linked = link_new_card(rel)
        return {"ok": True, "path": rel, "name": name, "state": state,
                "kind": kind, "label": CARD_LABEL[kind], "meta": card_meta,
                "linked": linked}

    CARD_ASK = {
        "concept": "写一张概念卡：一句能独立成立的主张 + 为什么成立 + 边界 + 用在哪。",
        "connection": "写一张连接卡：把用户提到的东西接到库里已有的一张卡上，写清在什么情况下相互影响。",
        "question": "写一张问题卡：一个还没想清楚的问题 + 为什么值得问 + 已有线索 + 怎么判定想通了。",
    }

    def gen_card(self, body):
        """只给一句提示词，让模型按本库的卡片格式补一张卡，走的还是 add_card 那套命名和落盘规则。"""
        kind = (body.get("kind") or "").strip()
        if kind not in CARD_LABEL:
            return {"ok": False, "error": "kind 只能是 concept / connection / question"}
        ask = (body.get("prompt") or "").strip()
        if len(ask) < 2:
            return {"ok": False, "error": "先写一句想让模型补什么（例如：爬虫这个词，结合这篇讲讲）"}
        src_rel = (body.get("source") or "").strip().replace("\\", "/")
        sp = (VAULT / src_rel).resolve() if src_rel else None
        if sp is not None and not str(sp).startswith(str(VAULT.resolve())):
            sp = None
        excerpt = ""
        if sp is not None and sp.is_file():
            excerpt = fit_content(sp.read_text(encoding="utf-8", errors="replace"), 6000)
        anchors = "、".join(existing_card_names(60))
        system = (
            "你在给一个个人知识库补一张卡。" + self.CARD_ASK[kind] + "\n"
            "硬性要求：\n"
            "1. 只输出四段，第一行「卡名：」开头（8–25 字的一句主谓句，读一遍就能同意或反对；"
            "不写光杆名词、不写「关于…的思考」、不写被截断的从句）；"
            "第二行「正文：」开头（≤260 字，中文，写给三个月后的自己看）；"
            "第三行「用在哪：」开头（一句话，说不清就写「暂不确定」）；"
            "第四行「标签：」开头（2-4 个主题词，顿号分隔）。\n"
            "2. 不许编造：材料里没有的事实、数字、产品名一律不写；写不出的部分直说「材料未给出」。\n"
            "3. 概念卡只装一条主张，且要能脱离这篇内容单独成立；连接卡必须写成「A ↔ [[已有卡名]]」，"
            "另一头只能从给定的卡名里挑；问题卡合上材料也答得出，并给出可判定的「什么算想通了」。\n"
            "4. 标签只能是主题（能用它把一堆卡筛出来），不许写状态词、维度词、人名、"
            "产品名、平台名、数字，也不许写代码里的词；每个词不超过 12 字，不含 # 和空格。\n"
            "5. 排版：链接写成 [短锚文本](URL)，不许把裸 URL 夹进句子；不写表格；不写 # 标题；"
            "正文里的 * _ | 不是语法时要转义；中英文之间加一个半角空格。\n"
            "6. 不要输出多余解释、不要 Markdown 代码块。")
        user = ("我的一句话：%s\n\n" % ask
                + ("库里已有的卡名（连接卡只能接这些）：%s\n\n" % anchors if anchors else "")
                + ("当前这份材料的正文（只依据它，别外推）：\n%s" % (excerpt or "（没挂材料，凭通用知识写，且必须标注不确定的地方）")))
        try:
            out = call_deepseek(user, system=system, max_tokens=2000)
        except Exception as e:
            return {"ok": False, "error": "模型没写成这张卡：" + str(e),
                    "hint": "没配 API Key 就先用「自己写」那一栏，或到「模型配置」里填。"}
        m = dict(re.findall(r"^(卡名|正文|用在哪|标签)[：:]\s*([\s\S]*?)(?=^(?:卡名|正文|用在哪|标签)[：:]|\Z)",
                            out, re.M))
        name = card_name((m.get("卡名") or out.splitlines()[0]).strip(), maxlen=30)
        text = (m.get("正文") or out).strip()
        use = (m.get("用在哪") or "").strip()
        tags = re.split(r"[、,，;；\s]+", (m.get("标签") or "").strip())
        if len(name) < 4:
            return {"ok": False, "error": "模型给的卡名不能用（太短或不成句）", "hint": out[:160]}
        r = self.add_card({"kind": kind, "name": name, "text": text, "use": use,
                           "tags": tags, "source": src_rel, "ai": True})
        if r.get("ok"):
            r["generated"] = {"name": name, "text": text, "use": use,
                              "tags": (r.get("meta") or {}).get("tags", [])}
        r.pop("meta", None)
        return r

    # ------------------------------------------------------------ cookie / 模型 / 批注
    def save_cookie(self, body):
        import fetchers
        site = (body.get("site") or "").strip()
        content = (body.get("cookie") or "").strip()
        if not site or not content:
            return {"ok": False, "error": "要同时给站点名（如 zhihu）和 Cookie 内容"}
        info = fetchers.save_cookie(site, content, ua=(body.get("ua") or "").strip())
        if not info.get("ok"):
            return info
        info["note"] = "只存在本机 cookies/ 目录，不会进笔记、不会上传；接口也只回元信息不回内容"
        return info

    def check_cookie(self, body):
        import fetchers
        return fetchers.check_cookie((body.get("site") or "").strip())

    def drop_cookie(self, body):
        import fetchers
        site = fetchers.norm_site((body.get("site") or "").strip())
        p = fetchers.COOKIE_DIR / (site + ".txt")
        if not p.exists():
            return {"ok": False, "error": "没有这个 cookie 文件"}
        p.unlink()
        fetchers.ua_file(site).unlink(missing_ok=True)
        return {"ok": True, "site": site}

    def annotate(self, body):
        """批注写进卡片自己的「## 我的批注」小节，同时抄一份到 inbox/annotations.md 待归位。"""
        rel = (body.get("path") or "").strip()
        text = (body.get("text") or "").strip()
        if not rel or not text:
            return {"ok": False, "error": "要有卡片路径和批注内容"}
        f = (VAULT / rel).resolve()
        if not str(f).startswith(str(VAULT.resolve())) or not f.is_file():
            return {"ok": False, "error": "卡片不存在：" + rel}
        stamp = time.strftime("%Y-%m-%d")
        s = f.read_text(encoding="utf-8")
        if "## 我的批注" in s:
            s = s.replace("## 我的批注\n\n（留空）\n", "## 我的批注\n\n")
            s = s.replace("## 我的批注", "## 我的批注\n\n- %s %s" % (stamp, text), 1)
        else:
            s = s.rstrip() + "\n\n## 我的批注\n\n- %s %s\n" % (stamp, text)
        s = re.sub(r"updated: \d{4}-\d{2}-\d{2}", "updated: " + time.strftime("%Y-%m-%d"), s, count=1)
        f.write_text(s, encoding="utf-8")

        log = VAULT / "inbox" / "annotations.md"
        head = "" if log.exists() else "---\ntitle: 批注收件箱\ntype: inbox\n---\n\n# 批注收件箱\n\n> 你随手写的批注都汇到这里，等 AI 归位到概念/连接/问题卡。\n\n"
        with io.open(str(log), "a", encoding="utf-8") as fh:
            if head:
                fh.write(head)
            fh.write("- [%s] `%s`：%s\n" % (stamp, rel, text))
        return {"ok": True, "path": rel, "written": True}

    # ------------------------------------------------------------ 预览 / 自检 / 文件上传
    def preview_fetch(self, body):
        """只读不写：先让用户看清「这条链接到底能不能读、能读回多少字」。"""
        import fetchers
        url = (body.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "给我一条链接"}
        info = fetchers.fetch(url, block=int(body.get("block") or 45))
        text = info.get("text") or ""
        return {"ok": bool(info.get("ok")), "platform": info.get("platform"),
                "kind": info.get("note_kind", ""),
                "title": info.get("title", ""), "author": info.get("author", ""),
                "method": info.get("method", ""), "chars": len(text),
                "images": len(re.findall(r"!\[[^\]]*\]\(", text)),
                "preview": re.sub(r"\s+", " ", text)[:220],
                "error": info.get("error", ""), "hint": info.get("hint", "")}

    def study_summary(self, body):
        """今日 / 本周学习总结。证据分两半：
        本机行为（读了哪张、停留多久、勾了哪条）只有客户端知道，由前端传进来；
        卡片的正文由这里现读 —— 前端只带路径，不带正文，免得把整库内容在请求里搬一遍。"""
        scope = (body.get("scope") or "day").strip()
        if scope not in ("day", "week"):
            return {"ok": False, "error": "scope 只能是 day 或 week"}
        label, unit, days = ("今日", "天", 1) if scope == "day" else ("本周", "周", 7)
        cutoff = time.time() - days * 86400
        cards = scan_vault()
        by_path = {c["path"]: c for c in cards}

        # 新入库：按文件改动时间落在这个窗口里
        made = [c for c in cards if c["mtime"] >= cutoff]
        # 勾掉的条目：前端把 done log 里这一窗口的标题带过来
        done_titles = [str(t)[:60] for t in (body.get("done") or [])][:20]
        # 打开过的卡：{path, sec}
        read = []
        for r in (body.get("read") or []):
            p = str(r.get("path") or "")
            c = by_path.get(p)
            if not c:
                continue
            read.append({"c": c, "sec": max(0, int(r.get("sec") or 0))})
        read.sort(key=lambda x: -x["sec"])
        read = read[:14]

        lines = []
        for i, item in enumerate(read, 1):
            c = item["c"]
            try:
                fm, b = parse_frontmatter((VAULT / c["path"]).read_text(encoding="utf-8",
                                                                        errors="replace"))
            except Exception:
                b = ""
            gist = re.sub(r"\s+", " ", strip_code(b))[:260]
            lines.append("%d. 《%s》｜%s｜%s 字｜本机停留 %d 秒｜双链 %d 条\n   %s"
                         % (i, c["title"], KIND_LABEL.get(c["kind"], c["kind"]),
                            c["chars"], item["sec"], len(c["links"] or []), gist))
        if not (made or done_titles or read):
            return {"ok": False, "error": "这一%s没有任何可总结的记录" % unit,
                    "hint": "去读两张卡或勾一条复习，再回来点这个按钮。"}

        user = SUMMARY_PROMPT.format(
            label=label, unit=unit,
            span=time.strftime("%Y-%m-%d", time.localtime(cutoff)) + " 之后",
            made_n=len(made),
            made="、".join("《%s》(%s)" % (c["title"][:20], KIND_LABEL.get(c["kind"], ""))
                           for c in made[:20]) or "没有",
            done_n=len(done_titles), done="、".join(done_titles) or "没有",
            read_n=len(read), read="\n".join(lines) or "没有")
        try:
            out = call_deepseek(user, system=SUMMARY_SYSTEM, max_tokens=2600)
        except Exception as e:
            return {"ok": False, "error": "模型没写成这份总结：" + str(e),
                    "hint": "没配 API Key 就先到「模型配置」里填。"}
        return {"ok": True, "scope": scope, "label": label, "markdown": out.strip(),
                "evidence": {"made": len(made), "done": len(done_titles), "read": len(read)}}

    def llm_test(self):
        cfg = load_config()
        try:
            t0 = time.time()
            out = call_deepseek("只回两个字：通了", system="你是连通性测试，不要多话", max_tokens=16)
            return {"ok": True, "model": cfg.get("model"), "base_url": cfg.get("base_url"),
                    "ms": int((time.time() - t0) * 1000), "echo": out[:40]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200],
                    "hint": "在「模型配置」里填 base_url / api_key / model（OpenAI 兼容即可），"
                            "或不填也能用：消化会走规则抽取兜底"}

    def import_file(self, body):
        """前端用 FileReader 把任意文件读成 base64 传上来，落成本地文件后走同一条消化链。"""
        import base64
        import fetchers
        name = (body.get("filename") or "").strip()
        b64 = (body.get("base64") or "").strip()
        if not name or not b64:
            return {"ok": False, "error": "缺少文件名或文件内容"}
        safe = re.sub(r"[^\w.\-一-鿿]", "_", name)[-80:]
        tmp = HERE / "uploads"
        tmp.mkdir(exist_ok=True)
        target = tmp / ("%s-%s" % (time.strftime("%Y%m%d%H%M%S"), safe))
        try:
            target.write_bytes(base64.b64decode(b64))
        except Exception as e:
            return {"ok": False, "error": "文件解码失败：%s" % e}
        info = fetchers.ingest_file(str(target), assets_dir=VAULT / "raw" / "assets")
        if not info.get("ok"):
            return {"ok": False, "error": info.get("error"), "hint": info.get("hint", ""),
                    "file": str(target)}
        # ingest_file 拿不到文档内标题时会退回文件名词干，而磁盘名带了时间戳前缀
        got = (info.get("title") or "").strip()
        clean = os.path.splitext(re.sub(r"^\d{14}-", "", safe))[0]
        title = clean if got in ("", target.stem) else got
        return self.digest({"text": info["text"],
                            "url": "local-file://" + target.name,
                            "title": title,
                            "platform": info.get("platform") or "本地文件",
                            "author": info.get("author") or "",
                            "method": info.get("method") or "本地文件导入",
                            # compile 要往下传：前端那个「只入库不消化」的开关之前被这里吃掉了，
                            # 结果每次导入都会调一次模型、还顺手长卡
                            "compile": body.get("compile", True),
                            "images": False})

    def task_done(self, body):
        title = (body.get("title") or "").strip()
        if not title:
            return {"ok": False, "error": "缺少任务标题"}
        f = VAULT / "inbox" / "已完成任务.json"
        data = {"done": []}
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                data = {"done": []}
        data.setdefault("done", [])
        data.setdefault("log", [])
        if body.get("undo"):
            if title in data["done"]:
                data["done"].remove(title)
            data["log"] = [x for x in data["log"]
                           if not (isinstance(x, dict) and x.get("title") == title)]
        else:
            if title not in data["done"]:
                data["done"].append(title)
            if not any(isinstance(x, dict) and x.get("title") == title for x in data["log"]):
                data["log"].append({"title": title, "at": time.strftime("%Y-%m-%d %H:%M")})
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "done": title in data["done"]}

    def save(self, body):
        rel = (body.get("path") or "").strip()
        content = body.get("content", "")
        if not rel:
            return {"ok": False, "error": "缺少路径"}
        if not rel.lower().endswith(".md"):
            return {"ok": False, "error": "只能写 md 笔记"}
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())):
            return {"ok": False, "error": "路径不合法"}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True}

    def set_status(self, body):
        """状态就地翻：不传 status 时 待吃透 ⇄ 待实践。仪表盘和阅读页都靠它写 frontmatter。
        原料也要有状态（它同样要复习），所以 raw/ 和 wiki/ 都放行；只增不改管的是正文，不是状态。"""
        rel = (body.get("path") or "").strip().replace("\\", "/")
        if not (rel.startswith("wiki/") or rel.startswith("raw/")):
            return {"ok": False, "error": "只有原料（raw/）和卡片（wiki/）有状态可改；系统页不参与"}
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())) or not p.is_file():
            return {"ok": False, "error": "找不到这张卡"}
        want = (body.get("status") or "").strip()
        s = p.read_text(encoding="utf-8")
        cur = re.search(r"^status:\s*(.*)$", s, re.M)
        if not cur:
            return {"ok": False, "error": "这份文件没有 status 字段，先用「改写」加一行 status: 待吃透"}
        if want:
            new = norm_status(want)
            if new not in STATUSES:
                return {"ok": False,
                        "error": "状态只能是这几个值之一：%s" % "、".join(STATUSES),
                        "hint": "「%s」不是本库的状态；要表达别的意思请写进正文，不要塞进 status 字段" % want[:30]}
        else:
            new = "待吃透" if norm_status(cur.group(1)) == "待实践" else "待实践"
        s = s[:cur.start(1)] + new + s[cur.end(1):]
        p.write_text(s, encoding="utf-8")
        return {"ok": True, "status": new, "path": rel}

    def set_tags(self, body):
        """标签可以人工加删：只动 frontmatter 里那一行 tags: [...]，正文一个字不碰。"""
        rel = (body.get("path") or "").strip().replace("\\", "/")
        if not (rel.startswith("wiki/") or rel.startswith("raw/")):
            return {"ok": False, "error": "只有原料和卡片能改标签"}
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())) or not p.is_file():
            return {"ok": False, "error": "找不到这份文件"}
        raw = body.get("tags")
        if isinstance(raw, str):
            raw = re.split(r"[,，\s]+", raw)
        tags = []
        for t in (raw or []):
            t = str(t).strip().lstrip("#")
            if t and t not in tags:
                tags.append(t)
        keep = clean_tags(tags, limit=24)
        rejected = [t for t in tags if t not in keep]
        if not tags:
            return {"ok": False, "error": "标签内容为空"}
        if rejected and not keep:
            return {"ok": False,
                    "error": "这些词不能当标签：%s" % "、".join(rejected[:4]),
                    "hint": "标签要能用来筛出一堆卡：不写状态词、维度词、人名、平台名、数字、"
                            "代码里的词和色值，长度不超过 %d 字" % TAG_MAX_LEN}
        if len(tags) > 24:
            return {"ok": False, "error": "标签最多 24 个，先删掉一些"}
        s = p.read_text(encoding="utf-8")
        head, sep, rest = s.partition("\n---\n")
        if not sep:
            return {"ok": False, "error": "这份文件没有 frontmatter，加不了标签"}
        line = "tags: [%s]" % ", ".join(keep)
        m = re.search(r"^tags:.*$", head, re.M)
        if m:
            head = head[:m.start()] + line + head[m.end():]
        else:
            head = head.rstrip("\n") + "\n" + line + "\n"
        p.write_text(head + sep + rest, encoding="utf-8")
        out = {"ok": True, "tags": keep, "path": rel}
        if rejected:
            out["rejected"] = rejected
        return out

    def drop_card(self, body):
        """删 = 移进 inbox/trash/，不硬删（改回来就从这儿拿）。
        原料也可以删，但它长出来的卡片不动——那些卡已经离开原料独立存在了，
        删回来路不删去处；前端要把这句话原样告诉用户。"""
        rel = (body.get("path") or "").strip().replace("\\", "/")
        if not (rel.startswith("wiki/") or rel.startswith("raw/")):
            return {"ok": False, "error": "只有原料（raw/）和卡片（wiki/）能删；系统页请自己改文件"}
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())) or not p.is_file():
            return {"ok": False, "error": "找不到这份文件"}
        grew = []
        if rel.startswith("raw/"):
            # 这份原料长出的卡：文末那一节 + 出处里写着本原料标题的，都算
            try:
                fm0, _b0 = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                fm0 = {}
            my_title = str(fm0.get("title") or p.stem)
            for c in scan_vault():
                if not c["path"].startswith("wiki/"):
                    continue
                if my_title in c["links"] or c["source"] == rel:
                    grew.append({"path": c["path"], "title": c["title"]})
        trash = VAULT / "inbox" / "trash"
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / p.name
        i = 1
        while dest.exists():
            dest = trash / ("%s-%d.md" % (p.stem, i))
            i += 1
        shutil.move(str(p), str(dest))
        removed = 0
        f = VAULT / "inbox" / "待复习任务.json"
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            tasks = data.get("tasks", [])
            keep = [t for t in tasks if (t.get("card") or "").replace("\\", "/") != rel
                    and (t.get("raw") or "").replace("\\", "/") != rel]
            removed = len(tasks) - len(keep)
            if removed:
                data["tasks"] = keep
                f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "trash": str(dest.relative_to(VAULT)).replace("\\", "/"),
                "tasksRemoved": removed, "kind": card_kind(rel), "grew": grew}


def main():
    global VAULT
    ap = argparse.ArgumentParser(description="KB Studio 本地知识库网站")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--init", action="store_true", help="只初始化空库，不起服务")
    args = ap.parse_args()

    VAULT = Path(os.path.expanduser(args.vault))
    if args.init:
        scaffold_vault(VAULT)
        return
    if not VAULT.is_dir():
        VAULT.mkdir(parents=True, exist_ok=True)
        scaffold_vault(VAULT)
    else:
        missing = [r for r in list(VAULT_FALLBACK) + list(VAULT_FILES)
                   if not (VAULT / r).exists()]
        if missing:
            scaffold_vault(VAULT)
        print("📚 知识库：%s" % VAULT)

    # 依赖检查：以前 .bat 会挑中一个没装 bs4 的 Python，结果每次消化都 500，
    # 报错还藏在网页里 —— 现在开机就说清楚，并告诉他用的是哪个解释器。
    missing = [m for m in ("requests", "bs4")
               if importlib.util.find_spec(m) is None]
    print("🐍 解释器：%s" % sys.executable)
    if missing:
        print("❌ 缺依赖：%s" % ", ".join(missing))
        print("   一步装齐（依赖 + ffmpeg + 转写模型）：\"%s\" tools/install.py"
              % sys.executable)
        print("   只补这两个：                  \"%s\" -m pip install requests beautifulsoup4"
              % sys.executable)
        print("   Windows 也可以直接双击「安装依赖.bat」。")
        print("   如果本机有多个 Python，「启动知识库.bat」会自动挑一个装好依赖的。")
        sys.exit(1)

    probe = urllib.request.Request("http://127.0.0.1:%d/api/stats" % args.port)
    try:
        with urllib.request.urlopen(probe, timeout=1.5):
            print("⚠️  端口 %d 上已经有一个 KB Studio 在跑，这个新进程抢不到请求。" % args.port)
            print("   先看是谁：  netstat -ano | findstr :%d" % args.port)
            print("   再去任务管理器结束那个 python.exe，然后重开。")
    except Exception:
        pass

    has_key = bool(read_deepseek_key() or load_config().get("api_key"))
    print("🔑 模型：%s" % ("已配置，消化会做 AI 提炼并自动长三层卡" if has_key else
          "未配置 —— 抓取入库照常跑，消化走规则兜底（此时不会自动长卡）；"
          "在网页「模型配置」或 config.json 里填 base_url/model/api_key 即可"))

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d" % args.port
    print("🚀 已启动：%s" % url)
    print("   按 Ctrl+C 停止")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        srv.shutdown()


if __name__ == "__main__":
    main()
