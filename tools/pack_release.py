# -*- coding: utf-8 -*-
"""打一个可分发的开源包：只装代码和文档，绝不带上你的知识库内容和任何凭据。

    python tools/pack_release.py                       # 吐到系统临时目录
    python tools/pack_release.py --out D:/release/kb-studio
    python tools/pack_release.py --out D:/release/kb-studio --allow-dirty   # 只看不拦

扫到下面任何一样就直接失败、不产出包：
  · 凭据：cookie 值、api_key、sk- 开头的 token
  · 个人机器路径（写死的绝对路径会坑住下一台机器）
  · 知识库内容：原料 / 卡片 / 回收站，一个 md 都不许进包
"""
import argparse
import io
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
# 默认吐到系统临时目录：落在项目树里，上层 git 仓库会把它当成一堆未跟踪文件（踩过）。
OUT_DEFAULT = Path(tempfile.gettempdir()) / "kb-studio-release"

INCLUDE_FILES = ["server.py", "fetchers.py", "requirements.txt", "requirements-asr.txt",
                 "config.example.json", ".gitignore", "README.md", "AGENTS.md",
                 "启动知识库.bat", "安装依赖.bat"]
INCLUDE_DIRS = ["static", "skill", "scripts", "tools", "templates", "docs", "extract-skill"]
NEVER = {"__pycache__", ".venv", "cookies", "media", "uploads", "vendor", "models",
         ".obsidian", "node_modules", "out", "shots-v2", "shots-v3"}

# 凭据形状：值本身长这样才算，字段名出现不算（代码里到处是 "api_key"）
SECRET_PATTERNS = [
    (re.compile(r"SESSDATA\s*=\s*[A-Za-z0-9_\-]{16,}"), "像 B 站 SESSDATA 的 cookie 值"),
    (re.compile(r"(bili|ttwid|sid|passport_csrf_token|web_session|dy_surl|a1)[_-]?\s*[=:]\s*[A-Za-z0-9]{24,}",
                re.I), "像真实 cookie 值"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "像 API key"),
    (re.compile(r"""api[_-]?key["']?\s*[:=]\s*["'][A-Za-z0-9\-]{20,}["']""", re.I), "硬编码的 api_key"),
]
PERSONAL = [
    (re.compile(r"[A-Za-z]:\\+Users\\+[^\\\n\"']+", re.I), "Windows 用户目录绝对路径"),
    (re.compile(r"/home/[a-z0-9_\-]{3,}/"), "Linux home 绝对路径"),
    # 拼接而不是直接写字面量：否则这个文件自己就会命中自己的检查
    (re.compile("网站" + "简单整理"), "原作者的个人目录名"),
]
# 包里允许出现的 .md：只有文档、模板和那份独立 skill 的说明，出现别的一律视为知识库内容泄漏
MD_OK = re.compile(r"^(README\.md|AGENTS\.md|docs/|skill/|templates/|extract-skill/)")


def gather():
    files = []
    for name in INCLUDE_FILES:
        p = HERE / name
        if p.is_file():
            files.append(p)
        else:
            print("  ！缺文件（跳过）：%s" % name)
    for d in INCLUDE_DIRS:
        root = HERE / d
        if not root.is_dir():
            print("  ！缺目录（跳过）：%s/" % d)
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and not (set(p.parts) & NEVER):
                files.append(p)
    return files


def scan(files):
    bad = []
    for p in files:
        rel = p.relative_to(HERE).as_posix()
        if p.suffix == ".md" and not MD_OK.match(rel):
            bad.append((rel, "知识库内容（.md 不在文档白名单里）"))
        if p.suffix not in (".py", ".md", ".js", ".css", ".html", ".txt", ".json", ".bat", ".svg"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            bad.append((rel, "读不了：%s" % e))
            continue
        for pat, why in SECRET_PATTERNS + PERSONAL:
            m = pat.search(text)
            if m:
                bad.append((rel, "%s：%s" % (why, m.group(0)[:48])))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    files = gather()
    print("准备 %d 个文件 → %s" % (len(files), out))
    bad = scan(files)
    if bad:
        print("\n❌ 扫到不该进包的东西，已停止（没写任何文件）：")
        for rel, why in bad[:20]:
            print("   %-42s %s" % (rel, why))
        if len(bad) > 20:
            print("   …另有 %d 条" % (len(bad) - 20))
        return 1 if not args.allow_dirty else 0
    print("✅ 脱敏检查通过：无凭据、无个人路径、无知识库内容")

    if out.exists():
        shutil.rmtree(out)
    for p in files:
        dst = out / p.relative_to(HERE)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print("✅ 已产出 %s（%d 个文件，%.1f KB）" % (out, len(files), size / 1024.0))
    print("   下一步：cd \"%s\" && git init && git add -A && git commit -m 'init' && git push" % out)
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.exit(main())
