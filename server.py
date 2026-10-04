#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KB Studio — 本地个人知识库网站（零依赖，仅用 Python 标准库）

启动:  python server.py           默认 http://127.0.0.1:8777
       python server.py --port 9000
       python server.py --vault "C:/Users/ADMIN/Documents/我的知识库"

数据本体就是 Obsidian vault 里的 .md 文件，本服务只做读取、索引和写入，
不额外维护数据库——Obsidian 里改了，网站刷新即可见。
"""
import os
import re
import io
import sys
import json
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
        return (cfg.get("env") or {}).get("ANTHROPIC_AUTH_TOKEN", "")
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


SYSTEM_FILES = {"system.md", "index.md", "log.md", "learner_profile.md",
                "progress.md", "revision_notes.md"}

# 与 Obsidian 的 userIgnoreFilters 保持同一口径，但这里用白名单：
# 只有这些位置的内容才算卡片，根目录其余散件（部署说明、工具文件、旧笔记）一律不进仪表盘。
INDEX_DIRS = {"wiki", "raw"}
INDEX_ROOT_FILES = SYSTEM_FILES | {"首页.md"}

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
    if "/" not in rel_path and rel_path in SYSTEM_FILES:
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
        tags += [t.lstrip("#") for t in re.findall(r"(?<![\w\[])#([\w\u4e00-\u9fa5]+)", body)]
        # 过滤纯数字 / 纯符号（正文里的 #1、#2 序号会被误当成标签）
        tags = sorted(set(t for t in tags if t and not re.fullmatch(r"[\d\W_]+", t)))
        # 摘要：取正文里第一段有意义的话
        summary = ""
        for ln in body.split("\n"):
            s = ln.strip().lstrip("#").strip()
            if len(s) > 12 and not s.startswith("---"):
                summary = s[:120]
                break
        title = fm.get("title") or p.stem
        cards.append({
            "path": rel,
            "name": p.stem,
            "title": str(title),
            "kind": kind,
            "kindLabel": KIND_LABEL[kind],
            "level": str(fm.get("level", "")),
            "status": str(fm.get("status", "")),
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

DIGEST_SYSTEM = """你是一个个人知识库的「收藏夹消化器」。你的输出会直接存成 Obsidian 笔记。

规则：
1. 只输出 Markdown，不要任何前后缀说明。
2. 严格按给定的模板结构输出，章节标题一字不改。
3. 摘要要「核心观点 + 可执行清单」，拒绝空泛的套话。
4. 概念、连接、引文必须来自原文真实出现过的内容，禁止编造；原文没给的写「原文未给出」。
5.「我的疑问」是这份产出里最值钱的部分，宁可少写观点也不要少写疑问。
6. 语言：中文。
"""

DIGEST_PROMPT = """把下面的内容消化成知识库卡片。严格按模板输出，只输出 Markdown。
处理规范的完整版本见 skill/PROMPTS.md（概念/连接/疑问的判定口径以它为准）。

## 核心观点
3-5 条，每条一句话，只写这篇真正的主张；不要复述背景，不要写「本文介绍了」。

## 可执行清单
能照做的步骤 / 清单 / 参数；纯观点类就列支撑论据。没有写「无」。

## 概念候选
**最多 3 个。** 概念是知识块，不是这条内容的履历。三条全过才算：
1. 换个平台、换个人、把这条内容删掉，这句话照样成立 —— 讲的是机制 / 因果 / 方法 / 判断标准 / 取舍；
2. 是一个能被同意或被反对的说法，不是名词。写「量产变体不等于流量」，不写「100 个变体」；
3. 说得出它用在你的哪个场景（这栏写不出来的，就别建卡）。

**下面这些一律不许当概念**（它们记录的是"这一条"，不是知识，留在原文里就够）：
产品名 / 项目名 / 仓库名 / 人名 / 账号名；许可证、版本号、运行环境、安装命令；
点赞、收藏、评论、转发、播放量、star 数、藏赞比这类互动数据；作者 IP 归属、发布时间；
被当成卖点写出来的数字口号（「1 亿播放量」那种）。

每张概念卡是一张**会不断长大的页**，不是一句摘要。每行格式（四段都要有，缺一段就别提这条）：
- 概念名 —— 主张一句话｜为什么成立：这篇里能支撑它的依据（例子 / 数据 / 推演，1-2 句）｜边界：什么条件下不成立、别在哪些场景用它｜用在哪：什么场景会用到它

「为什么成立」里的依据必须能在原文里指认出来（一个数字、一个例子、一句原话、一个步骤都行）；
只有断言没有论证的，就把它写进「我的疑问」而不是概念卡。「边界」写不出来的，这条也别提 ——
放到哪都对的句子是废话，不是知识。四段加起来要够 90 字，一两句话的口号会被库体检判成薄卡。

**先查文末的「库里已有的卡」**：如果这篇其实是在印证一张已有的卡，不要另起一个近义名字，
写成 `- 并入：<已有卡名原文> —— …`（同样带依据 / 边界 / 用在哪），系统会把这篇追加到那张卡上。
一个主张只留一张卡，重复起名字等于把这个库搞脏。

## 连接候选
**最多 2 条，宁缺毋滥。** 连接的价值是把新东西接到已有认知上：**另一头必须是文末清单里的卡名**；
接不上就写「无」，不要为了凑数现编一个假关系。
格式：- 本篇的主张 ↔ 已有卡名 —— 关系（前置 / 矛盾 / 同结论不同路径 / 上下位）｜为什么真的相关

## 我的疑问
**3 条**，覆盖下面四类中至少两类，这是本库最值钱的产出：
- 边界：什么条件下不成立？换个平台 / 人群 / 预算还成立吗？
- 证据：作者的数据和例子够不够？哪里只有断言没有论证？
- 与我：这条和我正在做的哪件事直接相关？能改我哪个动作？
- 待验证：哪一条我一周内能自己试？成败怎么判定？
每条是一个**能在 20 字内问完的短问题**（它直接当卡片标题），后面可跟一句为什么值得问。
不要把整句原文抄成问题，也不要写「是不是」「如何做」这种没有指向的问句。

## 值得记住的原话
1-3 句，用 > 引用格式，只挑信息密度高的；不为凑数而摘。

## 下一步
1-3 条具体动作（要查什么、要试什么、还要丢哪些链接进来）。

硬规则：概念、数据、作者、原文句子都必须来自给定内容，**不许编造**；读不出来的就写「原文未给出」。

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


def questions_from_digest(md):
    """从消化稿的「## 我的疑问」里取出条目，变成待复习任务。"""
    m = re.search(r"^##+\s*我的疑问\s*$\n(.*?)(?=^##+\s|\Z)", md or "", re.M | re.S)
    if not m:
        return []
    items = []
    for ln in m.group(1).splitlines():
        q = re.sub(r"^[-*\d.、\s]+", "", ln).strip()
        if len(q) >= 6 and not q.startswith("（"):
            items.append(q[:80])
    return items


def append_review_tasks(questions, card_rel):
    """疑问进 inbox/待复习任务.json；按标题去重，不动已完成的任务。"""
    if not questions:
        return 0
    f = VAULT / "inbox" / "待复习任务.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except Exception:
        data = {}
    tasks = data.get("tasks", [])
    have = {t.get("title") for t in tasks}
    due = time.strftime("%Y-%m-%d", time.localtime(time.time() + 3 * 86400))
    added = 0
    for q in questions:
        title = "回看原文：" + q
        if title in have:
            continue
        tasks.append({"title": title, "category": "消化提问", "priority": 3,
                      "due": due, "card": card_rel})
        have.add(title)
        added += 1
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
STATUSES = ("未读", "已读", "待吃透", "待实践", "已内化")

# 原料里的卡列表：全项目只有 render_card_section() 一个写入方，且每次重写而不是追加。
# 之前 digest() 往消化稿正文里塞「## 本次长成的卡」、迁移脚本又在文末补
# 「## 这张原料长成的卡」，两份同名不同序，看起来像同一段输出被生成了两次。
CARD_SECTION = "这张原料长成的卡"
CARD_HEADING_RE = re.compile(r"^\s*#{2,4}\s*(?:本次长成的卡|%s)\b" % CARD_SECTION)


def strip_card_sections(md):
    """删掉文本里所有卡列表小节（含模型自己 echo 出来的那种），保证只剩一份。
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


def render_card_section(made, dropped=()):
    """这一份原料长成了哪些卡 —— 用编译后的真实卡名，不用模型的原文回声。"""
    if not made and not dropped:
        return ""
    lines = ["", "---", "", "## %s" % CARD_SECTION]
    merged = [(n, r) for k, n, r, s in made if s == "merged"]
    merged_names = {n for n, _r in merged}
    for kind in ("concept", "connection", "question"):
        names = []
        for k, n, _r, _s in made:
            if k == kind and n not in merged_names and n not in names:
                names.append(n)
        if names:
            lines.append("- %s：%s" % (CARD_LABEL[kind],
                                       "、".join("[[%s]]" % n for n in names)))
    if merged:
        lines.append("- 并入已有卡（没另起同名近义卡）：%s"
                     % "、".join("[[%s]]" % n for n, _r in merged))
    if not made:
        lines.append("- 这一份没长出卡（原文里的说法都只属于这一条，不构成知识块）")
    return "\n".join(lines) + "\n"


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


def append_to_card(name, blocks, src_title, url, platform, author):
    """把这篇追加进一张已有的概念页（跨来源累积），而不是另起一张近义卡。
    同一篇来源已经引过就不重复写；frontmatter 的 sources 会一路长下去。"""
    rel = find_card_file(name)
    if not rel:
        return write_card("concept", name, blocks, src_title, url, platform, author)
    p = VAULT / rel
    old = p.read_text(encoding="utf-8")
    if ("[[%s]]" % src_title) in old or ("来自《%s》" % src_title) in old:
        return rel, "exists"
    n_src = len(set(re.findall(r"来自《([^》]+)》", old)))
    new = old.rstrip("\n") + "\n\n---\n\n## 补：来自《%s》（%s）\n\n%s\n" % (src_title, platform,
                                                                            "\n".join(blocks))
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


def write_card(kind, name, blocks, src_title, url, platform, author, extra_tags=None):
    """建一张卡；同名已存在就追加一段，绝不覆盖原内容。"""
    rel = "%s/%s.md" % (CARD_DIR[kind], card_name(name))
    p = VAULT / rel
    tags = ["消化生成"] + [t for t in (extra_tags or []) if t]
    head = "---\ntitle: %s\ntype: %s\nsource: %s\nplatform: %s\nauthor: %s\n" \
           "captured_at: %s\nfrom: %s\nsources:\n  - \"%s|%s\"\nlevel: L1\nstatus: 待吃透\ntags: [%s]\n---\n\n" \
           % (name, kind, url or "本地文件", platform, author,
              time.strftime("%Y-%m-%d"), src_title,
              src_title.replace('"', "'"), (url or "本地").replace('"', "'"), ", ".join(tags))
    body = "\n".join(blocks)
    if p.exists():
        old = p.read_text(encoding="utf-8")
        if ("[[%s]]" % src_title) in old or ("来自《%s》" % src_title) in old:
            return rel, "exists"
        p.write_text(old.rstrip("\n") + "\n\n---\n\n## 补：来自《%s》\n\n%s\n"
                     % (src_title, body), encoding="utf-8")
        return rel, "appended"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(head + "# %s\n\n%s\n" % (name, body), encoding="utf-8")
    return rel, "created"


def compile_cards(md, src_title, url, platform, author):
    """把消化稿的三段候选编译成卡，返回 ([(kind, 卡名, 路径, 状态)], [被拦下的说明])。

    数量和准入是硬闸门：一次消化最多 3 概念 / 2 连接 / 3 问题，互动数据一律不成卡，
    连接必须接到库里已有的卡上。「每条内容都长出十几张卡」正是这个库要治的病（见 [[双链滥用症]]）。
    """
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
        if len(name) < 4:
            continue
        if is_meta(name):     # 只看名字：正解释里引用「1 亿播放量」是允许的，那不代表这张卡是数据卡
            dropped.append("概念「%s」是互动数据 / 项目身份 / 技术规格，不是知识 → 没建卡" % name)
            continue
        target = (match_concept(merge_into) if merge_into else None) or None
        if not target and not merge_into:
            target = match_concept(name)   # 模型没写「并入」但起了个近义名，也当回填处理
        blocks = ["> %s" % (gloss or line), ""]
        blocks += ["**为什么成立**：%s" % (why or "（这篇没给依据 —— 要么你补上，要么这张卡该删）"), ""]
        blocks += ["**边界**：%s" % (edge or "（说不清什么条件下不成立 —— 那它还不是知识，先当你自己的疑问处理）"), ""]
        blocks += ["用在哪：%s" % (card_name(use, maxlen=40) if use else "（说不出来就别留这张卡）"),
                   "", "这是模型的口径，**用自己的话重写它，才算进过你的库**。",
                   "", "出处：%s（%s）" % (src_link, platform)]
        if target:
            rel, state = append_to_card(target, blocks, src_title, url, platform, author)
            made.append(("concept", target) + (rel, state))
            continue
        made.append(("concept", name) + write_card(
            "concept", name, blocks, src_title, url, platform, author))
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
        side = card_name(re.sub(r"^(本篇|本文|这一篇|这个主张|该主张|这一主张|主张|新观点)[的以是]?", "", a), maxlen=16)
        if len(side) < 2 or side == "未命名":
            own = [n for k, n, _r, _s in made if k == "concept"]
            side = own[0] if own else card_name(src_title, maxlen=16)
        # 两边各自留预算，别让 card_name 从右往左砍掉 —— 曾经砍到只剩左半边，
        # 结果连接卡和概念卡同名（先质疑问题本身再回答它.md），图谱上就是一个幽灵
        name = card_name("%s 与 %s" % (card_name(side, maxlen=14),
                                       card_name(anchor, maxlen=20)), maxlen=38)
        blocks = ["%s ↔ [[%s]]：%s" % (side, anchor, rel_desc),
                  "", "一侧是这篇的新主张，另一头接的是库里已经想明白的东西 —— 接不上就不该是张卡。",
                  "", "出处：%s（%s）" % (src_link, platform)]
        made.append(("connection", name) + write_card(
            "connection", name, blocks, src_title, url, platform, author))

    for line in bullets(section_of(md, "我的疑问"))[:3]:
        m = re.match(r"^(边界|证据|与我|待验证|反例|前提)\s*[：:]\s*(.+)$", line)
        dim, q = (m.group(1), m.group(2)) if m else ("追问", line)
        name = card_name(q, maxlen=22)
        if len(name) < 4:
            continue
        blocks = ["> %s" % q, "", "现在还没答案。写得出「什么算想通了」，这道题才算你的：", "",
                  "- [ ] 判定标准：", "", "出处：%s（%s）" % (src_link, platform)]
        made.append(("question", name) + write_card(
            "question", name, blocks, src_title, url, platform, author, extra_tags=[dim]))

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
                 ".svg": "image/svg+xml"}.get(f.suffix, "application/octet-stream")
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
                return self._json({"tasks": self.tasks()})
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
        tags = {}
        for c in cards:
            for t in c["tags"]:
                tags[t] = tags.get(t, 0) + 1
        return {"vault": str(VAULT), "total": len(cards), "byKind": by_kind,
                "tasksTotal": len(tasks), "tasksDue": len(due),
                "topTags": sorted(tags.items(), key=lambda x: -x[1])[:12]}

    def card(self, rel):
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())) or not p.is_file():
            return {"error": "文件不存在"}
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(text)
        cards = scan_vault()
        backlinks = [{"path": c["path"], "title": c["title"]}
                     for c in cards if p.stem in c["links"] or p.name[:-3] in c["links"]]
        return {"path": rel, "frontmatter": fm, "body": body, "raw": text,
                "backlinks": backlinks}

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
            if u.path == "/api/task/done":
                return self._json(self.task_done(body))
            if u.path == "/api/save":
                return self._json(self.save(body))
            if u.path == "/api/card/drop":
                return self._json(self.drop_card(body))
            if u.path == "/api/config":
                return self._json(masked_config() if body else load_config())
            if u.path == "/api/llm/test":
                return self._json(self.llm_test())
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
        made, dropped = [], []
        if body.get("compile", True) and ai_used:
            made, dropped = compile_cards(md, title, meta["url"], meta["platform"],
                                          meta["author"] or "未知")
            if not made:
                note = (note + " " if note else "") + "消化稿里没解析出可用的候选，未建卡"
            if dropped:
                note = (note + " " if note else "") + "拦下 %d 条不该成卡的" % len(dropped)
        # 卡列表只由 render_card_section 写一次；模型自己 echo 的同名小节先剥掉。
        md = strip_card_sections(md)
        cards_md = render_card_section(made, dropped)

        # 6) 写原料（只增不改）
        clip = VAULT / "raw" / "clips" / ("%s-%s.md" % (today, base))
        clip.parent.mkdir(parents=True, exist_ok=True)
        n = 1
        while clip.exists():
            clip = VAULT / "raw" / "clips" / ("%s-%s-%d.md" % (today, base, n))
            n += 1
        original = meta["text"][:60000]
        for extra in (ocr_blk, frame_blk):
            if extra:
                original += "\n\n" + extra
        media_rel = "media: %s\n" % meta.get("media") if meta.get("media") else ""
        clip.write_text(
            "---\ntitle: %s\naliases:\n  - \"%s\"\nsource: %s\nplatform: %s\nauthor: %s\n"
            "captured_at: %s\nmethod: %s\nimages: %d\nocr_images: %d\nframes: %d\n%slevel: L1\nstatus: 已读\n---\n\n"
            "# %s\n\n> 来源：%s ｜ 平台：%s ｜ 作者：%s ｜ 图片：%d 张%s\n\n"
            "# 原文（抓取所得，未加工）\n\n%s\n\n---\n\n"
            "# 消化稿（模型提炼，属于衍生内容）\n\n"
            "> 下面这一节不是作者原话，是处理结果；要引用请以「原文」一节为准。\n\n%s\n%s"
            % (title, title.replace('"', "'"), meta["url"], meta["platform"], meta["author"] or "未知",
               time.strftime("%Y-%m-%d"), meta["method"], imgs, ocr_n, frame_n, media_rel,
               title, meta["url"], meta["platform"], meta["author"] or "未知", imgs,
               "，其中 %d 张已识字" % ocr_n if ocr_blk else "",
               original, md, cards_md),
            encoding="utf-8")

        # 7) 消化出来的疑问自动进「待复习」队列（勾选仍需先打开原文）
        rel = str(clip.relative_to(VAULT)).replace("\\", "/")
        asked = append_review_tasks(questions_from_digest(md), rel)

        return {"ok": True, "clip": rel,
                "title": title, "platform": meta["platform"],
                "kind": meta.get("note_kind", ""),
                "method": meta["method"], "digest": md, "images": imgs,
                "ocrImages": ocr_n if ocr_blk else 0,
                "frames": frame_n if frame_blk else 0,
                "questions": asked, "chars": len(meta["text"]), "note": note,
                "dropped": dropped,
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

        note = ""
        try:
            md = call_deepseek(DIGEST_PROMPT.format(
                title=title, platform=platform, url=url, author=author,
                content=fit_content(raw), existing=existing_card_titles()))
        except Exception as e:
            return {"ok": False,
                    "error": "重新消化失败：%s" % str(e)[:160],
                    "hint": "原文已保存，没有动过；改一下「模型配置」或把过长的图片说明删掉再试"}

        made, dropped = [], []
        if body.get("compile", True):
            made, dropped = compile_cards(md, title, url, platform, author)
            note = ("重新长出 %d 张卡" % len(made)) if made \
                else "消化稿重写了，但没解析出可建卡的候选"
            if dropped:
                note += "，拦下 %d 条" % len(dropped)
        md = strip_card_sections(md)

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
                          "> 最近一次重生成：%s\n\n%s\n%s"
                          % (stamp, md, render_card_section(made, dropped)))
        p.write_text(strip_card_sections(head).rstrip("\n") + "\n\n---\n\n"
                     + digest_section, encoding="utf-8")

        asked = append_review_tasks(questions_from_digest(md), rel)
        return {"ok": True, "clip": rel, "title": title, "digest": md,
                "cards": [{"kind": k, "label": CARD_LABEL[k], "name": n,
                           "path": r, "state": s} for k, n, r, s in made],
                "dropped": dropped, "questions": asked, "note": note,
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
        digest = strip_card_sections(digest)
        made, dropped = compile_cards(digest, title, fm.get("source") or "",
                                      fm.get("platform") or "网页", fm.get("author") or "未知")
        if not made and not dropped:
            # 消化稿里没有可编译的候选（比如只有核心观点）—— 那就一个字都不动，
            # 否则会把这份原料原有的卡列表抹掉
            return {"ok": True, "clip": rel, "title": title, "cards": [], "dropped": [],
                    "questions": 0, "note": "这份消化稿里没有可编译的候选，卡片和卡列表都没动"}
        p.write_text(head.rstrip("\n") + "\n\n---\n\n" + digest.rstrip("\n") + "\n"
                     + render_card_section(made, dropped), encoding="utf-8")
        asked = append_review_tasks(questions_from_digest(digest), rel)
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
        else:
            src_title, url = (body.get("from") or "手动添加").strip(), ""
            platform, author, src_link = "手动", "本人", ""

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
        if src_link:
            blocks += ["", "出处：%s（%s）" % (src_link, platform)]
        blocks += ["", "（这张是我自己写的，不是模型口径。）"]

        rel, state = write_card(kind, name, blocks, src_title, url, platform, author,
                                extra_tags=["手写"])
        return {"ok": True, "path": rel, "name": name, "state": state,
                "kind": kind, "label": CARD_LABEL[kind]}

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
        if body.get("undo"):
            if title in data["done"]:
                data["done"].remove(title)
        elif title not in data["done"]:
            data["done"].append(title)
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
        """卡片状态就地翻：待吃透 ⇄ 待实践。仪表盘那一格靠它，不是靠手改 frontmatter。"""
        rel = (body.get("path") or "").strip().replace("\\", "/")
        if not rel.startswith("wiki/"):
            return {"ok": False, "error": "只有 wiki 下面的卡能标状态；原料不参与复习流转"}
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())) or not p.is_file():
            return {"ok": False, "error": "找不到这张卡"}
        want = (body.get("status") or "").strip()
        s = p.read_text(encoding="utf-8")
        cur = re.search(r"^status:\s*(.*)$", s, re.M)
        if not cur:
            return {"ok": False, "error": "这张卡没有 status 字段，先用「改写」加一行 status: 待吃透"}
        new = want if want in STATUSES else (
            "待吃透" if cur.group(1).strip() == "待实践" else "待实践")
        s = s[:cur.start(1)] + new + s[cur.end(1):]
        p.write_text(s, encoding="utf-8")
        return {"ok": True, "status": new, "path": rel}

    def drop_card(self, body):
        """删卡 = 移进 inbox/trash/，不硬删（改回来就从这儿拿）。原料 raw/ 不给删：只增不改是规矩。"""
        rel = (body.get("path") or "").strip().replace("\\", "/")
        if not rel.startswith("wiki/"):
            return {"ok": False,
                    "error": "只有 wiki 下面的卡能删；原料（raw/）按规矩只增不改"}
        p = (VAULT / rel).resolve()
        if not str(p).startswith(str(VAULT.resolve())) or not p.is_file():
            return {"ok": False, "error": "找不到这张卡"}
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
            keep = [t for t in tasks if (t.get("card") or "").replace("\\", "/") != rel]
            removed = len(tasks) - len(keep)
            if removed:
                data["tasks"] = keep
                f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "trash": str(dest.relative_to(VAULT)).replace("\\", "/"),
                "tasksRemoved": removed}


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
        print("   给这个解释器装上：  \"%s\" -m pip install requests beautifulsoup4"
              % sys.executable)
        print("   或者直接用「启动知识库.bat」，它会自动挑一个装好依赖的 Python。")
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
