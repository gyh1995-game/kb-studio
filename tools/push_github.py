# -*- coding: utf-8 -*-
"""用 GitHub REST API 推一个目录到仓库 —— 不走 git 传输。

    python tools/push_github.py --dry-run          # 只算要改什么，不写
    python tools/push_github.py -m "提交说明"       # 真推

为什么不用 git push：本机 github.com 走 443 连不上（连接被重置），
但 api.github.com 是通的。GitHub 的 Data API 能直接建 blob → tree → commit → 挪 ref，
效果等价，而且只传变化的文件。

安全线：
  · 推之前必须已经跑过 tools/pack_release.py —— 它负责扫凭据、扫个人路径、挡知识库内容。
    本脚本不重复做脱敏，它只负责搬运，所以 **--src 一定要指到产出目录，别指项目目录本身**。
  · 默认 --allow-delete：仓库里存在但产出目录里没有的文件会被删掉（用户要的是「替换旧内容」）。
    不想删就加 --no-delete。
"""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
# pack_release.py 的默认产出位置，两边对齐，免得推错目录
SRC_DEFAULT = Path(tempfile.gettempdir()) / "kb-studio-release"
# 产出目录里这些一律不推（即便 pack_release 漏了，这里再挡一道）
SKIP_DIRS = {".git", "__pycache__", "node_modules", "cookies", "uploads", "media",
             "vendor", "models", "out", "shots-v2", "shots-v3", ".obsidian"}
# 名字里带这些字样的文件一律不推（凭据最后一道闸）
SKIP_NAMES = {"config.json", ".env", "id_rsa", "credentials"}
TEXTISH = {".py", ".md", ".js", ".css", ".html", ".txt", ".json", ".bat", ".svg", ".yml", ".yaml", ".base"}


def token():
    t = os.environ.get("GITHUB_TOKEN")
    if t:
        return t
    out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit("拿不到 token：装 gh 并 gh auth login，或设 GITHUB_TOKEN")
    return out.stdout.strip()


def api(tok, method, path, payload=None, tries=4):
    import time
    body = json.dumps(payload).encode() if payload is not None else None
    for n in range(tries):
        req = urllib.request.Request(API + path, data=body, method=method, headers={
            "Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
            "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode()) if r.status != 204 else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (404, 409, 422) and n == 0:
                # 409 = 空仓库没有 refs，404 = ref 还不存在：这两种要单独报错，别白重试
                raise SystemExit("%s %s → %s\n%s" % (method, path, e.code, detail))
            if e.code in (403, 429) and "rate limit" in detail.lower() and n < tries - 1:
                time.sleep(30)
                continue
            if n < tries - 1:
                time.sleep(3 * (n + 1))
                continue
            raise SystemExit("%s %s → %s\n%s" % (method, path, e.code, detail))
        except Exception as e:
            if n < tries - 1:
                time.sleep(3 * (n + 1))
                continue
            raise SystemExit("%s %s 失败：%s" % (method, path, e))


def local_files(root):
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if set(rel.split("/")[:-1]) & SKIP_DIRS or p.name in SKIP_NAMES:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        out[rel] = p
    return out


def git_blob_sha(data):
    """git 的 blob 号 = sha1("blob <字节数>\\0" + 内容)。长度那一截不能省 ——
    省了算出来的 sha 和远端对不上，建 tree 时会报 "is not a valid blob"。"""
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def blob_sha(tok, owner_repo, data, cache):
    """先本地算，仓库里已经有了就不必再传一次。"""
    sha = git_blob_sha(data)
    if sha in cache:
        return sha
    try:
        api(tok, "GET", "/repos/%s/git/blobs/%s" % (owner_repo, sha))
        cache.add(sha)
        return sha
    except SystemExit:
        pass  # 不存在，往下建
    b64 = base64.b64encode(data).decode()
    api(tok, "POST", "/repos/%s/git/blobs" % owner_repo, {"content": b64, "encoding": "base64"})
    cache.add(sha)
    return sha


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC_DEFAULT))
    ap.add_argument("--repo", default="gyh1995-game/kb-studio")
    ap.add_argument("--branch", default="main")
    ap.add_argument("-m", "--message", default="update")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-delete", action="store_true")
    a = ap.parse_args()

    src = Path(a.src).resolve()
    if not src.is_dir():
        raise SystemExit("产出目录不存在：%s（先跑 python tools/pack_release.py --out ...）" % src)
    # 别让人把项目目录本身指进来：那会把 cookies/ 和知识库内容一起推上去
    if (src / "cookies").is_dir() and (src / "server.py").is_file() and src.name != "kb-studio-release":
        print("！%s 看起来是项目本体而不是脱敏产出目录，先跑 pack_release" % src)
        return 1
    files = local_files(src)
    if not files:
        raise SystemExit("产出目录里没有可推的文件")

    tok = token()
    print("仓库 %s ｜ 本地 %d 个文件 ｜ %.1f KB"
          % (a.repo, len(files), sum(p.stat().st_size for p in files.values()) / 1024.0))

    head = api(tok, "GET", "/repos/%s/git/ref/heads/%s" % (a.repo, a.branch))
    head_sha = head["object"]["sha"]
    commit = api(tok, "GET", "/repos/%s/git/commits/%s" % (a.repo, head_sha))
    old_tree = commit["tree"]["sha"]
    remote = {t["path"]: t["sha"] for t in
              api(tok, "GET", "/repos/%s/git/trees/%s?recursive=1" % (a.repo, old_tree)).get("tree", [])
              if t["type"] == "blob"}
    print("远端现有 %d 个文件（HEAD %s）" % (len(remote), head_sha[:7]))

    adds = [r for r in files if r not in remote]
    mods = [r for r in files if r in remote
            and git_blob_sha(files[r].read_bytes()) != remote[r]]
    dels = [r for r in remote if r not in files]
    print("  新增 %d ｜ 改动 %d ｜ 删除 %d" % (len(adds), len(mods), len(dels)))
    for r in (adds + mods)[:12]:
        print("   + %s" % r)
    for r in dels[:12]:
        print("   - %s" % r)
    if len(adds) + len(mods) > 12:
        print("   …另有 %d 个改动" % (len(adds) + len(mods) - 12))
    if a.dry_run:
        print("\n--dry-run：什么都没写")
        return 0
    if not adds and not mods and not (dels and not a.no_delete):
        print("没有变化，不用推")
        return 0

    cache = set()
    tree = []
    for rel in adds + mods:
        data = files[rel].read_bytes()
        tree.append({"path": rel, "mode": "100755" if data[:2] == b"#!" else "100644",
                     "type": "blob", "sha": blob_sha(tok, a.repo, data, cache)})
    if not a.no_delete:
        tree += [{"path": rel, "mode": "100644", "type": "blob", "sha": None} for rel in dels]
    new_tree = api(tok, "POST", "/repos/%s/git/trees" % a.repo,
                   {"base_tree": old_tree, "tree": tree})["sha"]
    new_commit = api(tok, "POST", "/repos/%s/git/commits" % a.repo,
                     {"message": a.message, "tree": new_tree, "parents": [head_sha]})["sha"]
    ref = api(tok, "PATCH", "/repos/%s/git/refs/heads/%s" % (a.repo, a.branch),
              {"sha": new_commit, "force": False})
    print("\n✅ 已推：%s" % ref["object"]["sha"][:8])
    print("   https://github.com/%s/tree/%s" % (a.repo, a.branch))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.exit(main())
