"""fetchers.py — KB Studio 自包含的平台内容获取层。

设计目标（与 skill/ 里的处理规范配套，方便开源给别的用户）：
  1. 一条任意平台的链接进来，能读到就读成 markdown 正文，读不到就给**明确的下一步动作**；
  2. 被风控/登录墙挡住的站点，统一走 cookie 通道（cookies/<site>.txt，用户从浏览器 F12 复制）；
  3. 图片一并抓进 raw/assets/，正文里的引用改成本地相对路径；
  4. 失败绝不编造：返回 ok=False + error + hint。

平台能力（2026-10-03 本机实测）：
  网页/博客  ✅ 直连 + 正文清洗
  公众号     ⚠️ 直连，失败要 cookie 或粘贴
  知乎       ❌ 本机 IP 被风控（指纹直连 / answer API / 专栏 API 全 403）→ 需要 cookie
  B站        ✅ 委托 personal-kb 的 bilibili.py（字幕→转写），拿不到就提示
  抖音       ✅ 需 cookie：jingxuan?modal_id= 页的 React 流式分块里能拿到文案/作者/时长/
                章节/可下载 playApi → 下载后本地 whisper 转写（2026-10-03 实测跑通）
                注意 /video/<id> 已不再服务端渲染数据，_ROUTER_DATA 只剩老页面还有
  小红书     ⚠️ 未实测 → 同样走 cookie
  ins/fb     ⚠️ 未实测 → 走 cookie，读不到就粘贴
"""
import io
import codecs
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import warnings
from pathlib import Path
from urllib.parse import quote, urlparse

warnings.filterwarnings("ignore")

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 "
             "Version/17.0 Mobile/15E148 Safari/604.1")

BASE_DIR = Path(__file__).resolve().parent
# cookie 默认放程序目录（已在 .gitignore 里）。想让凭据彻底离开项目/笔记库，
# 就设环境变量 KB_COOKIE_DIR 指到别处 —— 开源给别人时这条很重要。
COOKIE_DIR = Path(os.environ.get("KB_COOKIE_DIR") or (BASE_DIR / "cookies")).expanduser()

# B站链路复用的脚本。项目内 scripts/ 优先 —— 开源用户 clone 下来就该带着它，
# 不能要求对方先装过某个 agent 的 skill 目录。老位置留作兜底。
SCRIPT_CANDIDATES = [
    BASE_DIR / "scripts",
    Path.home() / ".workbuddy" / "skills" / "personal-kb" / "scripts",
    Path.home() / ".claude" / "skills" / "personal-kb" / "scripts",
]

HOW_TO_COOKIE = ("浏览器登录该平台 → F12 → Network → 刷新 → 点第一个请求 → "
                 "Request Headers 里整串复制 `Cookie:` → 粘贴到本页「Cookie 通行证」")

# ------------------------------------------------------------- 底层 HTTP


def chrome_headers(referer=None, mobile=False):
    h = {
        "User-Agent": MOBILE_UA if mobile else UA,
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,*/*;q=0.8"),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }
    if not mobile:
        h.update({
            "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not.A/Brand";v="8"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        })
    if referer:
        h["Referer"] = referer
    return h


HOST_KEYS = [("zhihu.com", "zhihu"), ("douyin.com", "douyin"), ("iesdouyin.com", "douyin"),
             ("xiaohongshu.com", "xiaohongshu"), ("xhslink.com", "xiaohongshu"),
             ("instagram.com", "instagram"), ("instagr.am", "instagram"),
             ("facebook.com", "facebook"), ("fb.watch", "facebook"),
             ("bilibili.com", "bilibili"), ("b23.tv", "bilibili"),
             ("mp.weixin.qq.com", "weixin")]

# 用户会用什么名字点这一站，全部收进来（前端下拉 / 手输都可能）
SITE_ALIASES = {
    "zhihu": "zhihu", "知乎": "zhihu", "zhihu.com": "zhihu",
    "douyin": "douyin", "抖音": "douyin", "iesdouyin": "douyin", "douyin.com": "douyin",
    "xiaohongshu": "xiaohongshu", "小红书": "xiaohongshu", "xhs": "xiaohongshu",
    "instagram": "instagram", "ins": "instagram", "ig": "instagram", "照片墙": "instagram",
    "facebook": "facebook", "fb": "facebook", "脸书": "facebook",
    "bilibili": "bilibili", "b站": "bilibili", "bili": "bilibili", "哔哩哔哩": "bilibili",
    "weixin": "weixin", "公众号": "weixin", "wechat": "weixin",
}

# 哪些键才代表「真的登录了」——只有设备指纹键的 cookie 存进去也读不到东西
LOGIN_KEYS = {
    "zhihu": {"any": ["z_c0"], "weak": ["d_c0"],
              "note": "z_c0 是登录态；只有 d_c0 一般是游客身份"},
    "douyin": {"any": ["sessionid", "sessionid_ss", "uid_tt", "sid_tt"], "weak": ["ttwid", "msToken"],
               "note": "sessionid / uid_tt 才代表已登录；只有 ttwid 是游客"},
    "xiaohongshu": {"any": ["web_session"], "weak": ["a1", "webId"],
                    "note": "web_session 是登录态；只有 a1 是游客"},
    "instagram": {"any": ["sessionid"], "weak": ["csrftoken", "ds_user_id"],
                  "note": "sessionid 是登录态"},
    "facebook": {"all": ["c_user", "xs"], "weak": ["datr"], "note": "c_user + xs 才代表已登录"},
    "bilibili": {"any": ["SESSDATA"], "weak": ["buvid3", "b_nut"], "note": "SESSDATA 是登录态"},
    "weixin": {},
}

# 各站探测「cookie 到底能不能用」用的 URL 和成功标记
COOKIE_PROBES = {
    "zhihu": ("https://www.zhihu.com/", ["\"userType\":\"registered\"", "\"isLogin\":true",
                                         "我的主页", "\"name\":"], "知乎对本机 IP 有风控时，"
                                         "即使 cookie 正确也会 403 —— 换网络（手机热点）再试"),
    "douyin": ("https://www.douyin.com/jingxuan/", ["awemeId", "\"nickname\"", "videoInfoRes"],
               "抖音的登录数据在流式分块里，拿不到多半是 cookie 过期"),
    "xiaohongshu": ("https://www.xiaohongshu.com/explore", ["web_session", "\"loggedIn\":true",
                                                            "\"nickname\""], "小红书基本必须登录态"),
    "instagram": ("https://www.instagram.com/", ["\"username\"", "sessionid"], "ins 常要求完整浏览器指纹"),
    "facebook": ("https://www.facebook.com/", ["\"name\":", "c_user"], "fb 风控较严"),
    "bilibili": ("https://api.bilibili.com/x/web-interface/nav", ["\"isLogin\":true"], "B 站一般不需要 cookie"),
}


def norm_site(name_or_url):
    s = (name_or_url or "").strip().lower()
    if not s:
        return ""
    if s in SITE_ALIASES:
        return SITE_ALIASES[s]
    for key, site in HOST_KEYS:
        if key in s:
            return site
    s2 = re.sub(r"\.txt$", "", s)
    return SITE_ALIASES.get(s2, s2)


def cookie_name(url):
    host = (urlparse(url).hostname or "").lower()
    for key, name in HOST_KEYS:
        if key in host:
            return name + ".txt"
    return None


def parse_cookie_input(text):
    """把用户可能粘的四种形态统一成 `a=1; b=2`。
       返回 (cookie串, 说明dict)；认不出来就返回空串 + 原因。"""
    raw = (text or "").strip().strip('"').strip()
    info = {"format": "", "pairs": 0, "note": ""}
    if not raw:
        return "", {"format": "", "pairs": 0, "note": "空的"}

    # 1) JSON 数组：Cookie-Editor / EditThisCookie 导出
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if data is not None:
            pairs = []
            if isinstance(data, dict) and all(isinstance(v, (str, int, float)) for v in data.values()):
                pairs = ["%s=%s" % (k, v) for k, v in data.items()]     # {"a":"1"} 直接映射
            else:
                for it in (data if isinstance(data, list) else data.get("cookies", []) or []):
                    if isinstance(it, dict) and it.get("name"):
                        pairs.append("%s=%s" % (it["name"], it.get("value", "")))
            if pairs:
                info.update({"format": "JSON 导出", "pairs": len(pairs)})
                return "; ".join(pairs), info
            return "", {"format": "JSON", "pairs": 0, "note": "JSON 里没有 name/value 字段"}

    # 2) Netscape cookies.txt（含 #HttpOnly_ 前缀）
    lines = [l for l in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n") if l.strip()]
    tabbed = [l for l in lines if "\t" in l and not l.startswith("#")]
    if len(tabbed) >= 2 or (tabbed and "\t" in tabbed[0]):
        pairs = []
        for l in tabbed:
            f = l.split("\t")
            if len(f) >= 7:
                pairs.append("%s=%s" % (f[5], f[6]))
        if pairs:
            info.update({"format": "Netscape cookies.txt", "pairs": len(pairs)})
            return "; ".join(pairs), info

    # 3) 请求头整串（DevTools 可能把它折成多行：折出去的那半截没有 = 号，接回上一行）
    body = re.sub(r"^\s*cookie\s*:\s*", "", "\n".join(lines), flags=re.I)
    merged = []
    for l in (x.strip() for x in body.split("\n")):
        if not l:
            continue
        if merged and "=" not in l.split(";")[0]:
            merged[-1] += l
        else:
            merged.append(l)
    pairs = [p.strip().rstrip(";") for p in ";".join(merged).split(";") if "=" in p]
    if len(pairs) >= 2:
        info.update({"format": "请求头整串", "pairs": len(pairs)})
        return "; ".join(pairs), info
    return "", {"format": "无法识别", "pairs": len(pairs),
                "note": "没看出键值对。请粘浏览器请求头里 Cookie: 后面的整串，"
                        "或 cookies.txt / JSON 导出"}


def cookie_keys(cookie_str):
    return [p.split("=")[0].strip() for p in (cookie_str or "").split(";") if "=" in p]


def audit_cookie(site, cookie_str):
    """判断这份 cookie 像不像登录态 —— 只看键名，不看值。"""
    keys = set(cookie_keys(cookie_str))
    spec = LOGIN_KEYS.get(norm_site(site), {})
    need_all = [k for k in spec.get("all", []) if k not in keys]
    any_ok = any(k in keys for k in spec.get("any", [])) if spec.get("any") else None
    weak = [k for k in spec.get("weak", []) if k in keys]
    logged = (any_ok if any_ok is not None else (not need_all)) if spec else bool(keys)
    return {"pairs": len(keys), "logged_in": bool(logged),
            "missing": need_all + ([] if any_ok or not spec.get("any") else spec["any"]),
            "only_visitor_keys": (not logged) and bool(weak),
            "note": spec.get("note", "")}


def load_cookie(url_or_site):
    """norm_site 同时吃 URL / 站点名 / 中文名，所以调用方给什么都行。"""
    site = norm_site(url_or_site)
    if not site:
        return ""
    p = COOKIE_DIR / (site + ".txt")
    if not p.exists():
        return ""
    ck, _info = parse_cookie_input(p.read_text(encoding="utf-8", errors="replace"))
    return ck


def ua_file(site):
    return COOKIE_DIR / (norm_site(site) + ".ua")


def saved_ua(site):
    p = ua_file(site)
    return p.read_text(encoding="utf-8", errors="replace").strip() if p.exists() else ""


def save_cookie(site, content, ua=""):
    site = norm_site(site)
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    ck, info = parse_cookie_input(content)
    if not ck:
        return {"ok": False, "error": info.get("note") or "没解析出任何 cookie 键值对",
                "format": info.get("format", "")}
    (COOKIE_DIR / (site + ".txt")).write_text(ck, encoding="utf-8")
    if ua:
        ua_file(site).write_text(ua.strip(), encoding="utf-8")
    au = audit_cookie(site, ck)
    out = {"ok": True, "site": site + ".txt", "bytes": len(ck), "format": info["format"],
           "pairs": info["pairs"], "saved_at": time.strftime("%Y-%m-%d %H:%M"),
           "logged_in_guess": au["logged_in"], "missing_keys": au["missing"], "note": au["note"]}
    if not au["logged_in"]:
        out["warning"] = ("这份里没看到登录标记键（%s）。存是存下了，但大概率读不到登录态内容 —— "
                          "确认浏览器里该站点确实是登录状态后再复制一次。"
                          % ("、".join(au["missing"]) or "未知"))
    return out


def cookie_status():
    """只回元信息，绝不把 cookie 内容带回前端。"""
    out = []
    if COOKIE_DIR.is_dir():
        for p in sorted(COOKIE_DIR.glob("*.txt")):
            site = p.name[:-4]
            ck, _ = parse_cookie_input(p.read_text(encoding="utf-8", errors="replace"))
            au = audit_cookie(site, ck)
            s = p.stat()
            out.append({"site": p.name, "bytes": s.st_size, "pairs": au["pairs"],
                        "logged_in_guess": au["logged_in"], "missing_keys": au["missing"],
                        "ua": saved_ua(site) or "（用内置指纹）",
                        "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(s.st_mtime))})
    return out


# ----------------------------------------------------- 频控：别把账号推到风控上
# 一次抓取只发一个请求，不并发、不在 403/429 上重试轰炸；
# 同一站点两次请求之间强制留间隔。批量消化时这条是保护他账号的。

HIT_AT = {}
MIN_GAP = {"douyin": 3.0, "xiaohongshu": 3.0, "zhihu": 2.5, "instagram": 3.0,
           "facebook": 3.0, "bilibili": 1.5}
DEFAULT_GAP = 1.2


def throttle(url):
    site = norm_site((urlparse(url).hostname or ""))
    gap = MIN_GAP.get(site, DEFAULT_GAP)
    last = HIT_AT.get(site)
    wait = 0.0 if last is None else gap - (time.time() - last)
    if wait > 0:
        time.sleep(min(wait, 15.0))
    HIT_AT[site] = time.time()


def headers_for(url=None, referer=None, mobile=False, site=None):
    """按站点走：用户存过 UA 就用他的 UA，并让 sec-ch-ua 跟着 UA 的版本号走，
    避免「UA 说 131、client hints 说 126」这种自相矛盾的指纹。"""
    site = norm_site(site or (url or ""))
    ua = saved_ua(site)
    h = chrome_headers(referer, mobile)
    if ua:
        h["User-Agent"] = ua
        m = re.search(r"(?:Chrome|Chromium|Edg|Firefox|Safari)/(\d+)", ua)
        if m and "sec-ch-ua" in h:
            v = m.group(1)
            h["sec-ch-ua"] = ('"Chromium";v="%s", "Google Chrome";v="%s", '
                              '"Not.A/Brand";v="8"' % (v, v))
            h["sec-ch-ua-platform"] = ('"macOS"' if "Mac" in ua else
                                       '"Android"' if "Android" in ua else
                                       '"iOS"' if "iPhone" in ua else '"Windows"')
    return h


def http_get(url, referer=None, mobile=False, cookie=None, timeout=30, site=None):
    throttle(url)
    h = headers_for(url=url, referer=referer, mobile=mobile, site=site)
    if cookie:
        h["Cookie"] = cookie
    return fix_encoding(requests.get(url, headers=h, timeout=timeout, verify=False))


def fix_encoding(r):
    """requests 对没在 HTTP 头里声明 charset 的中文站会猜成 ISO-8859-1 → 整篇乱码，
       文件名和正文都会坏。先信 HTTP 头，再信页面 meta，最后才用探测。"""
    ctype = (r.headers.get("content-type") or "").lower()
    if "charset=" in ctype:
        return r
    head = r.content[:4096].decode("ascii", "ignore") if r.content else ""
    import re as _re
    m = _re.search(r"charset=\W{0,2}([A-Za-z0-9_.-]+)", head, _re.I)
    if m:
        try:
            r.encoding = m.group(1)
            return r
        except Exception:
            pass
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1", ""):
        try:
            r.encoding = r.apparent_encoding or "utf-8"
        except Exception:
            r.encoding = "utf-8"
    return r


def check_cookie(site):
    """体检：这份 cookie 存进去之后，到底能不能真读到东西。
    只看状态码和页面上有没有登录标记，不回传任何内容。"""
    site = norm_site(site)
    ck = load_cookie(site)
    if not ck:
        return {"ok": False, "site": site, "error": "还没存这个站点的 cookie"}
    probe = COOKIE_PROBES.get(site)
    au = audit_cookie(site, ck)
    out = {"site": site, "pairs": au["pairs"], "logged_in_keys": au["logged_in"],
           "missing_keys": au["missing"], "key_note": au["note"],
           "ua": saved_ua(site) or "内置指纹"}
    if not probe:
        out.update({"ok": au["logged_in"], "verdict": "这个站点没配探测地址，只能按键名判断"})
        return out
    url, marks, why = probe
    try:
        r = http_get(url, referer="https://%s/" % (urlparse(url).hostname or ""),
                     cookie=ck, site=site, timeout=40)
    except Exception as e:
        out.update({"ok": False,
                    "error": "请求没发出去：%s: %s" % (type(e).__name__, str(e)[:90])})
        return out
    body = r.text or ""
    hit = [m for m in marks if m in body]
    out.update({"http": r.status_code, "bytes": len(body), "markers": hit[:3]})
    if r.status_code in (403, 451):
        out.update({"ok": False,
                    "verdict": "HTTP %s —— 被风控挡在门外，跟 cookie 无关（多半是这台机器的 IP）。%s"
                               % (r.status_code, why)})
    elif r.status_code in (301, 302):
        out.update({"ok": False, "verdict": "被重定向到登录页，这份 cookie 不是有效登录态"})
    elif r.status_code != 200:
        out.update({"ok": False, "verdict": "HTTP %s，不是正常响应" % r.status_code})
    elif hit:
        out.update({"ok": True,
                    "verdict": "看到登录态标记（%s），这份 cookie 可以用" % "、".join(hit[:3])})
    elif au["logged_in"]:
        out.update({"ok": True,
                    "verdict": "键名里有登录标记、页面也正常返回，但没抓到预期标记 —— 可能页面改版了；"
                              "拿一条真实链接「先探一下」更准"})
    else:
        out.update({"ok": False,
                    "verdict": "页面能打开但读不到登录内容 —— 这份 cookie 大概率不是登录态。"
                              "确认浏览器里该站点已登录，再整串复制一次"})
    return out


# ------------------------------------------------------------- HTML → Markdown


def _text(el):
    return re.sub(r"[ \t]+", " ", el.get_text(" ", strip=True))


def html_to_md(el):
    """块级结构转 Markdown：标题/段落/列表(带嵌套)/表格/代码/引用/图片/链接。"""
    if isinstance(el, str):
        return el.strip()
    out = []

    def inline(node):
        if isinstance(node, str):
            return re.sub(r"\s+", " ", node).strip()
        name = (node.name or "").lower()
        if name in ("img",):
            src = abs_url(node.get("src") or node.get("data-src") or "", BASE_URL_STACK[-1])
            alt = (node.get("alt") or "图片").strip() or "图片"
            return "![%s](%s)" % (alt, LOCAL_IMG.get(src, src)) if src else ""
        if name == "a":
            href = node.get("href") or ""
            t = "".join(inline(c) for c in node.children).strip() or href
            return "[%s](%s)" % (t, href) if href.startswith("http") else t
        if name == "code":
            return "`%s`" % node.get_text(" ", strip=True)
        if name in ("strong", "b"):
            return "**%s**" % "".join(inline(c) for c in node.children)
        if name in ("em", "i"):
            return "*%s*" % "".join(inline(c) for c in node.children)
        if name == "br":
            return "\n"
        return "".join(inline(c) for c in getattr(node, "children", []))

    def walk(node, depth=0):
        for child in getattr(node, "children", []):
            if isinstance(child, str):
                t = re.sub(r"\s+", " ", child).strip()
                if t:
                    out.append(t)
                continue
            n = (child.name or "").lower()
            if n in ("h1", "h2", "h3", "h4", "h5", "h6"):
                out.append("#" * int(n[1]) + " " + inline(child).strip())
            elif n == "p":
                t = inline(child).strip()
                if t:
                    out.append(t)
            elif n == "blockquote":
                body = "\n".join("> " + l for l in collect(child).splitlines() if l.strip())
                if body:
                    out.append(body)
            elif n == "pre":
                code = child.get_text("\n", strip=False)
                out.append("```\n%s\n```" % code.strip("\n"))
            elif n in ("ul", "ol"):
                out.append(render_list(child, n, depth))
            elif n == "table":
                out.append(render_table(child))
            elif n == "img":
                out.append(inline(child))
            elif n in ("script", "style", "noscript", "svg"):
                continue
            else:
                walk(child, depth)

    def collect(node):
        return "\n\n".join(node.stripped_strings)

    def render_list(node, kind, depth):
        lines = []
        i = 0
        for li in node.find_all("li", recursive=False):
            i += 1
            sub = li.find(["ul", "ol"], recursive=False)
            head = ("%d. " % i if kind == "ol" else "- ") + "  " * depth
            own = " ".join(re.sub(r"\s+", " ", s).strip()
                           for s in li.stripped_strings if s is not None)
            if sub is not None:
                own = inline_text_wo_lists(li)
            lines.append(head + own)
            if sub is not None:
                lines.append(render_list(sub, (sub.name or "ul"), depth + 1))
        return "\n".join(lines)

    def inline_text_wo_lists(li):
        clone = BeautifulSoup(str(li), "html.parser")
        for bad in clone.find_all(["ul", "ol"]):
            bad.decompose()
        return inline(clone).strip()

    def render_table(table):
        rows = []
        for tr in table.find_all("tr"):
            cells = [_text(td).replace("|", "／") for td in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        md = ["| " + " | ".join(rows[0]) + " |",
              "|" + "---|" * width]
        md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(md)

    walk(el)
    return "\n\n".join(x for x in out if str(x).strip())


BASE_URL_STACK = [""]
LOCAL_IMG = {}


def abs_url(src, base):
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith(("http://", "https://")):
        return src
    return ""


def download_images(md_text, page_url, assets_dir, cookie="", limit=30, rel_prefix=""):
    """把正文图片抓到 raw/assets/<slug>/，正文引用改成本地相对路径。失败不阻塞。"""
    global LOCAL_IMG
    LOCAL_IMG = {}
    urls = [u for u in re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", md_text)][:limit]
    if not urls:
        return md_text, 0
    try:
        os.makedirs(str(assets_dir), exist_ok=True)
    except Exception:
        return md_text, 0
    n = 0
    for u in urls:
        try:
            r = fix_encoding(requests.get(u, headers=chrome_headers(referer=page_url, mobile=True),
                             timeout=20, verify=False))
            if r.status_code != 200 or len(r.content) < 600:
                continue
            ext = (".jpg" if "jpeg" in (r.headers.get("content-type") or "") else
                   "." + (r.headers.get("content-type") or "image/jpeg").split("/")[-1].split(";")[0])
            if ext not in (".jpg", ".png", ".gif", ".webp", ".bmp"):
                ext = ".jpg"
            fn = "%02d-%s%s" % (n + 1, uuid.uuid4().hex[:6], ext)
            with open(str(assets_dir / fn), "wb") as f:
                f.write(r.content)
            LOCAL_IMG[u] = (rel_prefix + "/" if rel_prefix else "") + fn
            n += 1
        except Exception:
            continue
    if n:
        md_text = re.sub(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)",
                         lambda m: "![%s](%s)" % (m.group(1), LOCAL_IMG.get(m.group(2), m.group(2))),
                         md_text)
    return md_text, n


# ------------------------------------------------------------- 各平台


def meta_title_author(soup, url):
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = _text(h1)
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    for prop in ("og:title", "twitter:title"):
        m = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if m and m.get("content") and not title:
            title = m["content"].strip()
    author = ""
    m = soup.find("meta", attrs={"name": "author"})
    if m:
        author = (m.get("content") or "").strip()
    if not author:
        m = soup.find("meta", attrs={"property": "article:author"})
        if m:
            author = (m.get("content") or "").strip()
    return (re.sub(r"\s*[-|–]\s*(知乎|微信公众平台|小红书|抖音|Bilibili|哔哩哔哩).*$",
                   "", title or url), author)


def fetch_generic(url, host):
    r = http_get(url, referer="https://www.google.com/")
    if r.status_code != 200:
        raise RuntimeError("HTTP %s" % r.status_code)
    soup = BeautifulSoup(r.text, "html.parser")
    for bad in soup(["script", "style", "noscript", "iframe", "nav", "footer", "aside", "form"]):
        bad.decompose()
    root = (soup.find("article") or soup.select_one("#content") or
            soup.select_one(".Post-RichTextContainer") or soup.select_one(".RichText") or soup.body)
    body = html_to_md(root)
    gtitle, gauthor = meta_title_author(soup, url)
    return {"ok": True, "title": gtitle, "author": gauthor,
            "text": body, "method": "直连抓取 + 正文清洗"}


def fetch_zhihu(url):
    """知乎三级降级：完整指纹 → answer API → 专栏 API；有 cookie 就带上。"""
    ck = load_cookie(url)
    referer = "https://www.zhihu.com/"
    errs = []
    try:
        r = http_get(url, referer=referer, cookie=ck)
        if r.status_code == 200 and len(r.text) > 2000:
            soup = BeautifulSoup(r.text, "html.parser")
            title, author = meta_title_author(soup, url)
            if not author:      # 知乎页面不在 meta 里放作者，作者名在正文作者条里
                ael = (soup.select_one(".AuthorInfo-name") or soup.select_one(".UserLink")
                       or soup.select_one("[itemprop='name']"))
                if ael:
                    author = _text(ael)
            el = (soup.select_one(".Post-RichTextContainer") or soup.select_one(".RichText")
                  or soup.select_one(".QuestionAnswer") or soup.select_one("article"))
            if el is not None:
                body = html_to_md(el)
                if len(body) > 200:
                    return {"ok": True, "title": title, "author": author, "text": body,
                            "method": "指纹直连" + ("+cookie" if ck else "")}
            m = re.search(r'<script id="js-initialData"[^>]*>(.*?)</script>', r.text, re.S)
            if m:
                try:
                    ent = json.loads(m.group(1)).get("initialState", {}).get("entities", {})
                    ans = list((ent.get("answers") or {}).values()) + list((ent.get("articles") or {}).values())
                    if ans:
                        a = ans[0]
                        soup2 = BeautifulSoup(a.get("content") or "", "html.parser")
                        return {"ok": True,
                                "title": title or (a.get("question", {}) or {}).get("name", "") or "知乎",
                                "author": ((a.get("author") or {}) if isinstance(a.get("author"), dict)
                                           else {}).get("name", author),
                                "text": html_to_md(soup2), "method": "页面内嵌 initialData JSON"}
                except Exception as e:
                    errs.append("initialData: %s" % e)
            errs.append("页面 200 但没定位到正文容器")
        else:
            errs.append("页面 HTTP %s" % r.status_code)
    except Exception as e:
        errs.append("页面: %s" % e)

    m = re.search(r"/answer/(\d+)", url)
    if m:
        try:
            r = http_get("https://www.zhihu.com/api/v4/answers/%s?include=content,question,author"
                         % m.group(1), referer=referer, cookie=ck)
            if r.status_code == 200:
                d = r.json()
                body = html_to_md(BeautifulSoup(d.get("content") or "", "html.parser"))
                if body:
                    return {"ok": True, "title": (d.get("question") or {}).get("title", "知乎回答"),
                            "author": (d.get("author") or {}).get("name", ""), "text": body,
                            "method": "知乎回答 API"}
            errs.append("answer API HTTP %s" % r.status_code)
        except Exception as e:
            errs.append("answer API: %s" % e)

    m = re.search(r"zhuanlan\.zhihu\.com/p/(\d+)", url)
    if m:
        for tag, api in (("zhuanlan", "https://zhuanlan.zhihu.com/api/articles/%s" % m.group(1)),
                         ("api.zhihu", "https://api.zhihu.com/articles/%s" % m.group(1))):
            try:
                r = http_get(api, referer=referer, cookie=ck)
                if r.status_code == 200:
                    d = r.json()
                    body = html_to_md(BeautifulSoup(d.get("content") or "", "html.parser"))
                    if body:
                        return {"ok": True, "title": d.get("title", "知乎专栏"),
                                "author": (d.get("author") or {}).get("name", ""),
                                "text": body, "method": "知乎专栏 API"}
                errs.append("专栏 %s: HTTP %s" % (tag, r.status_code))
            except Exception as e:
                errs.append("专栏 %s: %s" % (tag, str(e)[:40]))

    if not ck:
        hint = "知乎对本机 IP 有风控（指纹直连 / answer API / 专栏 API 全部 403）。" + HOW_TO_COOKIE
    else:
        hint = "已带 cookie 仍被拒（多半是 cookie 过期或 IP 风控）。重新复制一份 Cookie 再试，或直接把正文粘过来。"
    return {"ok": False, "platform": "知乎", "error": "；".join(errs[-3:]) or "知乎抓取失败", "hint": hint}


DOUYIN_PUSH = re.compile(r'__pace_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\s*\]', re.S)
_JSTR = r'((?:[^"\\]|\\.)*)'
MEDIA_DIR = Path(__file__).resolve().parent / "media"


def flight_blob(html):
    """抖音现在把数据塞进 React 流式分块。分块是 JS 字符串，unicode_escape 解完
    还要 latin-1→utf-8 绕一次，否则中文全是 mojibake（2026-10-03 实测）。"""
    parts = []
    for c in DOUYIN_PUSH.findall(html or ""):
        try:
            s = codecs.decode('"' + c + '"', "unicode_escape")
        except Exception:
            s = c.replace('\\"', '"')
        try:
            s = s.encode("latin-1", "ignore").decode("utf-8", "ignore")
        except Exception:
            pass
        parts.append(s.replace("\u0022", '"'))
    return "\n".join(parts)


def _unq(s):
    return (s or "").replace('\\"', '"').replace("\\n", "\n").replace("\\t", " ").strip()


def douyin_item(blob, vid, window=40000):
    """取一条视频的字段。desc 取窗口内最长的那条 —— 章节名（「引言」这种）也在同一个
    对象里，短的会把真文案挤掉。"""
    for m in re.finditer(r'"awemeId":"%s"' % re.escape(vid), blob):
        w = blob[m.start():m.start() + window]
        descs = [_unq(d) for d in re.findall(r'"desc":"%s"' % _JSTR, w) if _unq(d).strip()]
        play = re.search(r'"playApi":"%s"' % _JSTR, w)
        nick = re.search(r'"nickname":"%s"' % _JSTR, w)
        dur = re.search(r'"duration":(\d+)', w)
        chapters = []
        for mm in re.finditer(r'"desc":"%s","detail":"%s","timestamp":(\d+)' % (_JSTR, _JSTR), w):
            t2, _d, ts = mm.group(1), mm.group(2), mm.group(3)
            if _unq(t2):
                chapters.append((int(ts) // 1000, _unq(t2)))
        if not descs and not play:
            continue
        return {
            "aweme_id": vid,
            "desc": max(descs, key=len) if descs else "",
            "nickname": _unq(nick.group(1)) if nick else "",
            "duration_ms": int(dur.group(1)) if dur else 0,
            "play_url": (play.group(1).replace("\\/", "/") if play else ""),
            "chapters": chapters[:12],
        }
    return None


def douyin_router(html):
    """老结构（部分页面还在发）：window._ROUTER_DATA，带点赞评论数。"""
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", html or "", re.S)
    if not m:
        return None
    try:
        ld = json.loads(m.group(1).strip()).get("loaderData") or {}
        for v in ld.values():
            items = (((v or {}).get("videoInfoRes") or {}).get("item_list")) or []
            if items:
                it = items[0]
                st, au = it.get("statistics") or {}, it.get("author") or {}
                return {"aweme_id": it.get("aweme_id", ""),
                        "desc": re.sub(r"\s+", " ", it.get("desc") or "").strip(),
                        "nickname": au.get("nickname", ""),
                        "duration_ms": it.get("duration") or 0,
                        "play_url": ((it.get("video") or {}).get("play_addr") or {}).get("url_list", [""])[0],
                        "digg": st.get("digg_count"), "comment": st.get("comment_count"),
                        "collect": st.get("collect_count"), "chapters": []}
    except Exception:
        return None
    return None


def download_media(url, dest, referer="https://www.douyin.com/", max_mb=200):
    """把视频落到本地，供 ffmpeg 抽音频。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    try:
        with requests.get(url, headers=headers_for(site="douyin", referer=referer), stream=True,
                          timeout=300) as r:
            if r.status_code not in (200, 206):
                return {"ok": False, "error": "媒体返回 %s" % r.status_code}
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
                    n += len(chunk)
                    if n > max_mb * 1024 * 1024:
                        return {"ok": False, "error": "文件超过 %dMB 上限，已中止" % max_mb}
        return {"ok": n > 0, "bytes": n, "path": str(dest)}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def douyin_note_images(blob, limit=12):
    """从流式分块里取抖音图文贴的图集地址（每个 urlList 的第一个可用项）。"""
    urls, seen = [], set()
    for m in re.finditer(r'"urlList":\s*\[([^\]]{0,4000})\]', blob or ""):
        first = re.search(r'"(https:[^"]+?)"', m.group(1))
        if not first:
            continue
        u = first.group(1)
        if "aweme-images" not in u and "image" not in u:
            continue                     # 头像 / 表情 / 封面预览不要
        key = u.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        urls.append(u)
        if len(urls) >= limit:
            break
    return urls


def fetch_douyin(url, transcript=True):
    """抖音：/video/<id> 已不再服务端渲染数据，带 cookie 走 jingxuan 的 modal_id 页，
    从流式分块里取文案 + 可下载的 playApi，再本地转写成逐字稿。"""
    vid = None
    m = re.search(r"/(?:video|note)/(\d{10,})", url) or re.search(r"modal_id=(\d{10,})", url)
    if m:
        vid = m.group(1)
    if not vid and "v.douyin.com" in url:
        try:
            r = requests.head(url, allow_redirects=True, timeout=20,
                              headers=chrome_headers(mobile=True))
            m2 = re.search(r"/(?:video|note)/(\d{10,})", r.url or "")
            if m2:
                vid = m2.group(1)
        except Exception:
            pass
    if not vid:
        return {"ok": False, "platform": "抖音", "error": "没从链接里认出视频 ID",
                "hint": "请粘贴抖音 App 里「复制链接」得到的完整链接（含 v.douyin.com 也行）"}

    ck = load_cookie(url)
    if not ck:
        return {"ok": False, "platform": "抖音",
                "error": "抖音的网页数据只在登录态下下发，当前没有 cookie",
                "hint": (HOW_TO_COOKIE + "；或者更快：在抖音里点「复制文案」把文字粘给我。")}

    item = None
    for page in ("https://www.douyin.com/jingxuan/?modal_id=" + vid,
                 "https://www.douyin.com/video/" + vid):
        try:
            r = http_get(page, referer="https://www.douyin.com/", cookie=ck)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        item = douyin_router(r.text) or douyin_item(flight_blob(r.text), vid)
        if item and (item.get("desc") or item.get("play_url")):
            break

    if not item or not (item.get("desc") or item.get("play_url")):
        return {"ok": False, "platform": "抖音",
                "error": "带 cookie 也没读到这条视频（可能已删除 / 仅自己可见 / 被风控降级成空壳页）",
                "hint": "在浏览器里打开这条链接确认能看到；能看到就把 Cookie 重新复制一次再试，"
                        "或者直接在 App 里「复制文案」粘给我。"}

    dur = item.get("duration_ms") or 0
    lines = ["**文案**：%s" % (item.get("desc") or "（这条没有文字文案）"),
             "", "**作者**：%s" % (item.get("nickname") or "未知"),
             "**时长**：%s" % ("%d 分 %d 秒" % (dur // 60000, dur % 60000 // 1000) if dur else "未知"),
             "**aweme_id**：%s" % vid]
    if item.get("digg") is not None:
        lines.append("**数据**：赞 %s / 评 %s / 藏 %s"
                     % (item.get("digg"), item.get("comment"), item.get("collect")))
    if item.get("chapters"):
        lines.append("")
        lines.append("**章节**：")
        lines += ["- `%02d:%02d` %s" % (s // 60, s % 60, ti) for s, ti in item["chapters"]]

    method = "登录态 Cookie · 流式分块解析" if not item.get("digg") else "登录态 Cookie · _ROUTER_DATA"
    # 抖音文案里带换行，不压平会把文件名和 frontmatter 的 YAML 一起弄坏
    one_line = re.sub(r"\s+", " ", item.get("desc") or "").strip()
    out = {"ok": True, "title": (one_line or "抖音视频 " + vid)[:48],
           "author": item.get("nickname", ""), "text": "\n".join(lines),
           "method": method, "aweme_id": vid, "duration_ms": dur,
           "play_url": item.get("play_url", ""),
           "note_kind": "video" if item.get("play_url") else "text"}

    if not item.get("play_url"):
        imgs = douyin_note_images(flight_blob(r.text))
        if imgs:
            out["note_kind"] = "image"
            out["text"] += ("\n\n**图集 %d 张**\n" % len(imgs)
                            + "\n".join("![](%s)" % u for u in imgs))
        else:
            out["note_kind"] = "text"

    # 逐字稿：拿到可播放地址 → 下载 → ffmpeg 抽音频 → faster-whisper
    if transcript and item.get("play_url"):
        mp4 = MEDIA_DIR / ("%s.mp4" % vid)
        dl = download_media(item["play_url"], mp4)
        if not dl.get("ok"):
            out["text"] += "\n\n> 视频下载失败，未做转写：%s" % dl.get("error")
            out["note"] = "下载失败：" + str(dl.get("error"))
        else:
            out["media"] = mp4.name          # 阅读页用它做本地播放器，逐字稿时间戳点了就能跳
            tr = transcribe(str(mp4))
            if tr.get("ok"):
                out["text"] += ("\n\n## 逐字稿（机器转写，%d 字，可能有错字）\n\n%s"
                                % (len(tr["text"]), tr["text"]))
                out["method"] += " + " + (tr.get("method") or "音频转写")
            else:
                out["text"] += "\n\n> 转写没成功：%s" % tr.get("error")
                out["note"] = "转写失败：" + str(tr.get("error"))
    elif transcript:
        out["note"] = "这条没拿到可播放地址，只入库了文案"
    return out


def js_json(text):
    """把页面里内嵌的 JS 对象字面量修成合法 JSON。
    小红书的状态里有 `undefined` 和 `new Map([...])`，直接 json.loads 必炸
    （2026-10-03 实测：noteDetailMap:new Map([])）。"""
    s = re.sub(r"\bundefined\b", "null", text or "")
    for ctor in ("new Map(", "new Set("):
        while True:
            i = s.find(ctor)
            if i < 0:
                break
            j = i + len(ctor) - 1          # 指向 '('
            depth, k = 0, j
            while k < len(s):
                if s[k] == "(":
                    depth += 1
                elif s[k] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            s = s[:i] + s[j + 1:k] + s[k + 1:]   # 去掉构造器，留下中括号内容
    return s


def _initial_state(html):
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})</script>", html or "", re.S)
    if not m:
        return None
    try:
        return json.loads(js_json(m.group(1)))
    except Exception:
        return None


def _note_map(d):
    """noteDetailMap 可能是 dict，也可能是 new Map 拆出来的 [[k,v],...]。"""
    ndm = ((d or {}).get("note") or {}).get("noteDetailMap")
    if isinstance(ndm, dict):
        return list(ndm.values())
    if isinstance(ndm, list):
        return [v for pair in ndm if isinstance(pair, list) and len(pair) > 1 for v in [pair[1]]]
    return []


def fetch_xiaohongshu(url, transcript=True):
    ck = load_cookie(url)
    if not ck:
        return {"ok": False, "platform": "小红书", "error": "小红书必须登录态才能读笔记",
                "hint": HOW_TO_COOKIE + "；或者把笔记正文/图片说明直接粘给我"}

    # App 里「复制链接」给的是 xhslink.com 短链，先跟一跳拿真实地址
    if "xhslink.com" in (url or ""):
        try:
            h = requests.get(url, allow_redirects=True, timeout=25,
                             headers=chrome_headers(mobile=True))
            if h.url and "xiaohongshu.com" in h.url:
                url = h.url
        except Exception:
            pass

    try:
        r = http_get(url, referer="https://www.xiaohongshu.com/", cookie=ck, site="xiaohongshu")
    except Exception as e:
        return {"ok": False, "platform": "小红书", "error": "抓取异常：%s" % str(e)[:120],
                "hint": "粘贴正文最稳"}
    d = _initial_state(r.text)
    if d is None:
        return {"ok": False, "platform": "小红书",
                "error": "带 cookie 也没解析出 __INITIAL_STATE__（HTTP %s）" % r.status_code,
                "hint": "多半是 cookie 过期或页面改版；在浏览器里确认能看到这条笔记后再复制一次 Cookie"}

    notes = _note_map(d)
    info = (notes[0] or {}).get("note") if notes else None
    info = info or {}
    title = (info.get("title") or "").strip()
    desc = (info.get("desc") or "").strip()
    if not (title or desc):
        # 登录墙后的页面照样带 __INITIAL_STATE__，但 noteDetailMap 是空的 ——
        # 这里必须判空，否则会「成功」地入库一条空正文（2026-10-03 实测踩过）
        blocked = re.search(r'"needLogin"|扫码|登录后查看|登录发现更多精彩', r.text or "")
        want_tok = "xsec_token" not in (url or "")
        hint = []
        if want_tok:
            hint.append("这条链接少了 xsec_token：请在浏览器里打开这条笔记，"
                        "把地址栏的完整链接（带 ?xsec_token=…）复制给我，"
                        "或 App 里点「复制链接」得到的短链也可以")
        if blocked:
            hint.append("页面提示要登录 —— 确认这份 cookie 是登录态（体检按钮能验）")
        hint.append("实在拿不到就把笔记文字粘给我")
        return {"ok": False, "platform": "小红书",
                "error": "页面能连上，但笔记正文是空的（%s）"
                         % ("要登录才能看" if blocked else "noteDetailMap 为空"),
                "hint": "；".join(hint)}

    user = (info.get("user") or {}).get("nickname", "")
    it = info.get("interactInfo") or {}
    imgs = [x.get("urlDefault") or x.get("url") for x in (info.get("imageList") or [])]
    imgs = [u for u in imgs if u]
    # desc 里通常已经带 "#话题[话题]#"，再补一行干净话题时只加没出现过的，避免重复
    tags = " ".join("#" + t.get("name") for t in (info.get("tagList") or [])
                    if t.get("name") and t.get("name") not in desc)
    when = info.get("time")
    parts = [title and "# %s\n" % title or "", desc, tags and "\n%s" % tags or ""]
    meta = ["**作者**：%s" % (user or "未知")]
    if it:
        meta.append("**互动**：赞 %s / 藏 %s / 评 %s / 转 %s" % (
            it.get("likedCount") or "0", it.get("collectedCount") or "0",
            it.get("commentCount") or "0", it.get("shareCount") or "0"))
    if info.get("ipLocation"):
        meta.append("**IP 归属**：%s" % info["ipLocation"])
    if when:
        meta.append("**发布**：%s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(when / 1000.0)))
    parts.append("\n" + "\n".join(meta))
    if imgs:
        kind = "视频封面" if (info.get("video") or info.get("type") == "video") else "图片"
        parts.append("\n\n**%s %d 张**\n" % (kind, len(imgs))
                     + "\n".join("![](%s)" % u for u in imgs))

    out = {"ok": True, "title": (title or re.sub(r"\s+", " ", desc))[:48] or "小红书笔记",
           "author": user, "text": "\n".join(p for p in parts if p).strip(),
           "method": "登录态 Cookie · __INITIAL_STATE__",
           "note_id": info.get("noteId", ""), "images": len(imgs)}

    is_video = bool(info.get("video")) or info.get("type") == "video"
    out["note_kind"] = "video" if is_video else "image"

    # 视频笔记：拿到可播放地址就顺手转写
    if transcript:
        vid = info.get("video") or {}
        media = vid.get("media") or {}
        vnode = media.get("video") or {}          # 这一层只有 md5 / bizId，别在它下面找 stream
        play = (vnode.get("url") or vnode.get("masterUrl")
                or media.get("masterUrl") or vid.get("url") or "")
        streams = media.get("stream") or vnode.get("stream") or {}
        if not play:
            for fr in (streams or {}).values():
                for f in (fr or []):
                    # 键名是 backupUrls（复数）—— 只找单数会取不到，2026-10-03 那条视频笔记就是这么漏的
                    cand = [f.get("masterUrl")] + list(f.get("backupUrls") or []) + [f.get("backupUrl")]
                    play = next((u for u in cand if u), "")
                    if play:
                        break
                if play:
                    break
        if not play:                      # 有些版本只给个文件 key，得走 consumer 那条路
            consumer = (info.get("video") or {}).get("consumer") or {}
            play = consumer.get("originVideoKey") or ""
        if not play and is_video:
            out["note"] = ("这是视频笔记，但没从页面里取到可播放地址（多半是 cookie 权限不够或字段又改了）"
                           "→ 只入库了文案和封面，逐字稿缺失")
        if play:
            mp4 = MEDIA_DIR / ("xhs_%s.mp4" % (out["note_id"] or uuid.uuid4().hex[:8]))
            dl = download_media(play, mp4, referer="https://www.xiaohongshu.com/")
            if dl.get("ok"):
                out["media"] = mp4.name
                tr = transcribe(str(mp4))
                if tr.get("ok"):
                    out["text"] += ("\n\n## 逐字稿（机器转写，%d 字，可能有错字）\n\n%s"
                                    % (len(tr["text"]), tr["text"]))
                    out["method"] += " + " + (tr.get("method") or "音频转写")
                else:
                    out["note"] = "视频下下来了但转写失败：" + str(tr.get("error"))[:80]
            else:
                out["note"] = "视频下载失败：" + str(dl.get("error"))[:80]
    return out


def fetch_og(url, platform, referer=None):
    """ins / fb 一类：优先带 cookie 抓页面，退回 og:title/og:description。"""
    ck = load_cookie(url)
    try:
        r = http_get(url, referer=referer or "https://www.google.com/", cookie=ck)
        soup = BeautifulSoup(r.text, "html.parser")
        title, _au = meta_title_author(soup, url)
        desc = ""
        for prop in ("og:description", "description", "twitter:description"):
            m = (soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop}))
            if m and m.get("content"):
                desc = m["content"].strip()
                break
        body_el = soup.select_one("article") or soup.find("main")
        text = html_to_md(body_el) if body_el else ""
        if (title and desc) or len(text) > 400:
            return {"ok": True, "title": title, "author": "",
                    "text": text or desc, "method": "og 元信息" + ("+cookie" if ck else "")}
        return {"ok": False, "platform": platform,
                "error": "%s 只返回登录墙/占位页（HTTP %s）" % (platform, r.status_code),
                "hint": (HOW_TO_COOKIE if not ck else "cookie 可能过期；或把帖子的文字粘给我")}
    except Exception as e:
        return {"ok": False, "platform": platform, "error": "抓取异常：%s" % e,
                "hint": "粘贴正文最稳"}


def fetch_bilibili(url, block=45, force_transcribe=False, model="small"):
    """bilibili.py 的入口收 BV 号（不是 URL），而且**成功时返回的 dict 里没有 ok 字段**
    —— 以前拿 info.get("ok") 当唯一判据，把每次成功的抓取都判成了失败（2026-10-04 踩到）。
    """
    m = re.search(r"(BV[0-9A-Za-z]{9,11})", url or "")
    if not m:
        return {"ok": False, "platform": "B站", "error": "没认出 BV 号",
                "hint": "给我 bilibili.com/video/BVxxxx 或 b23.tv 短链形式的链接"}
    vid = m.group(1)
    for d in SCRIPT_CANDIDATES:
        if not (d / "bilibili.py").exists():
            continue
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
        try:
            import bilibili
            info = bilibili.fetch(vid, block=block, force_transcribe=force_transcribe,
                                  model=model) or {}
        except Exception as e:
            # 脚本自己抛的 BiliError 里带"怎么解"（比如要 SESSDATA），别吞成一句"偶发风控"
            msg = str(e) or type(e).__name__
            return {"ok": False, "platform": "B站", "error": "B站：%s" % msg[:300],
                    "hint": "B站要登录态：网页「Cookie 通行证」里贴 bilibili 的 SESSDATA；"
                            "或浏览器打开过该视频再试"}
        text = (info.get("text") or "").strip()
        if text or info.get("title"):
            info["ok"] = True
            info["platform"] = "B站"
            info["note_kind"] = "video"
            info.setdefault("method", "bilibili.py")
            # 正文标题只标「这段字是哪来的」，不写引擎名：换引擎时旧原料不该跟着改名
            if text and not text.startswith("#"):
                tag = ("## 字幕原文" if str(info.get("method", "")).startswith("官方字幕")
                       else "## 逐字稿（机器转写，可能有错字）")
                text = tag + "\n\n" + text
            info["text"] = text
            return info
        return {"ok": False, "platform": "B站",
                "error": info.get("error") or "B站返回了空正文",
                "hint": "这条视频可能只有 AI 字幕之外的内容；把文案/字幕粘给我最快"}
    return {"ok": False, "platform": "B站", "error": "找不到 scripts/bilibili.py",
            "hint": "项目内 KB-Studio/scripts/ 应该自带；被删了就从仓库重新拉一份"}


PLATFORMS = [
    (r"zhihu\.com", "知乎", fetch_zhihu),
    (r"douyin\.com|iesdouyin\.com", "抖音", fetch_douyin),
    (r"xiaohongshu\.com|xhslink\.com", "小红书", fetch_xiaohongshu),
    (r"instagram\.com|instagr\.am", "Instagram",
     lambda u: fetch_og(u, "Instagram", "https://www.instagram.com/")),
    (r"facebook\.com|fb\.watch", "Facebook",
     lambda u: fetch_og(u, "Facebook", "https://www.facebook.com/")),
    (r"bilibili\.com|b23\.tv|BV[0-9A-Za-z]{10}", "B站", fetch_bilibili),
]


def detect_platform(url):
    for pat, name, _ in PLATFORMS:
        if re.search(pat, url or "", re.I):
            return name
    if "mp.weixin.qq.com" in (url or ""):
        return "公众号"
    return "网页"


URL_IN_TEXT = re.compile(r"""https?://[^\s\u3000\u200b，,、'"）】>]+""")
PLATFORM_KEYS = ("v.douyin.com", "douyin.com", "iesdouyin.com", "xiaohongshu.com",
                 "xhslink.com", "b23.tv", "bilibili.com", "zhihu.com", "zhuanlan.zhihu.com")


def extract_url(text):
    """从 App「复制链接」带出来的脏文本里把链接抠出来。

    他粘过来的一直是这种形态：`8.88 复制打开抖音 https://v.douyin.com/xxxx/ 01/04 e@o.DN :9pm`
    —— 前后都是口令和中文，直接当 URL 用必挂（2026-10-03 用户报的问题）。
    """
    t = (text or "").strip()
    if not t:
        return "", ""
    if re.match(r"^https?://\S+$", t):
        return t, ""
    cands = [u.rstrip("。，,、)】>") for u in URL_IN_TEXT.findall(t)]
    if not cands:
        return "", t
    for u in cands:
        if any(k in u for k in PLATFORM_KEYS):
            return u, t
    return cands[0], t


def fetch(url, **kw):
    """统一入口：返回 {ok, platform, title, author, text, method, error?, hint?}"""
    url = (url or "").strip()
    if not url.startswith("http"):
        # 他从小红书 / 抖音「分享 → 复制链接」拿到的一直是整段文案（口令+中文+链接+尾巴）
        got, _ = extract_url(url)
        if got:
            url = got
    if not url.startswith("http"):
        if re.match(r"^BV[0-9A-Za-z]{9}$", url):
            url = "https://www.bilibili.com/video/" + url
        else:
            return {"ok": False, "error": "不是可识别的链接", "hint": "给我 http(s) 链接，或直接粘正文"}
    global BASE_URL_STACK
    BASE_URL_STACK = [url]
    plat = detect_platform(url)
    handler = None
    for pat, name, fn in PLATFORMS:
        if name == plat:
            handler = fn
            break
    try:
        if handler is fetch_bilibili:
            r = handler(url, block=kw.get("block", 45),
                        force_transcribe=bool(kw.get("force_transcribe", False)))
        elif handler is fetch_douyin:
            r = handler(url, transcript=bool(kw.get("transcript", True)))
        elif handler is fetch_xiaohongshu:
            r = handler(url, transcript=bool(kw.get("transcript", True)))
        elif handler:
            r = handler(url)
        else:
            r = fetch_generic(url, plat)
    except Exception as e:
        r = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160]),
             "hint": "该站点可能反爬或需要登录；粘贴正文是最稳的兜底"}
    # 全局兜底：任何平台「成功但没正文」都算失败，别让空卡片入库
    if r.get("ok") and not (r.get("text") or "").strip():
        r = {"ok": False,
             "error": "连上了但没读到任何正文（登录墙 / 内容在图片里 / 前端渲染）",
             "hint": "把正文复制过来最快；图片型内容（小红书、公众号截图）请连图片说明一起粘"}
    r["platform"] = plat
    r["url"] = url
    return r


# ------------------------------------------------------------- 本地文件导入


SENT_SPLIT_RE = re.compile(r"(?<=[。！？；…])|(?<=\.)\s+|(?<=[!?])\s+")
BREAK_BEFORE_RE = re.compile(
    r"^(另外|此外|同时|然后|接着|接下来|最后|首先|其次|再者|比如|例如|所以|因此|但是|不过|"
    r"效果如何|注意|需要说明|更重要的是|跑通|使用|整体|这是|我的|给新人的|拿出|先在|这套|该|"
    r"第[一二三四五六七八九十]{1,2}|[一二三四五六七八九十]{1,3}是|[（(]?\d{1,2}[）)、.．])")
KEEP_LINE_RE = re.compile(r"^\s*([#|>\-*•]|\d+[.)、]\s|!\[|`|https?://\S{40,})")


URL_HEAD_RE = re.compile(r"^https?://$")
URL_FRAG_RE = re.compile(r"^[A-Za-z0-9._\-/?=:%&+~#$@'!*()\[\]]+$")


def unbreak_urls(text):
    """把被硬断行的链接拼回一条。

    知乎 / 公众号的正文里实测见过这种：`https://` 独占一行，域名和路径各占一行，
    中间还夹空行（页面结构本来就这样，抓取时一起带下来了）。这种文本喂给模型会把一条链接
    读成三个词，前端也只能渲染出三段断字 —— 「链接莫名其妙换行」的根因在数据里，不在样式里。

    只做一件保守的事：某行的尾巴正好是 `http(s)://`，就把它后面那些「整行只有 URL 字符」的行
    接上来（跳过空行）。含空格、含中文的行一律不接，不会把句子并进链接。"""
    if not text or "http" not in text:
        return text
    lines = text.split("\n")
    out, i, joined = [], 0, 0
    while i < len(lines):
        cur = lines[i].rstrip()
        if not URL_HEAD_RE.match(cur.strip()):
            out.append(lines[i])
            i += 1
            continue
        buf, k = cur, i + 1
        while k < len(lines):
            nxt = lines[k].strip()
            if not nxt:
                k += 1
                continue
            if not URL_FRAG_RE.match(nxt):
                break
            buf += nxt
            k += 1
        out.append(buf)
        joined += 1
        i = k
    return "\n".join(out) if joined else text


def reflow_text(text, target=150, hard=240):
    """把「一整段被压成一行」的文本恢复成段落。
    从知乎 / 小红书 / 微信复制、以及老版本导入的 txt，常见整个回答挤在一行 ——
    这种文本喂给模型会丢结构，人在手机上也读不动。
    先拼回被硬断行的链接（那要删掉换行），再插段落换行；除此之外一个字不改。
    """
    if not text:
        return text
    text = unbreak_urls(text)
    out = []
    for line in text.split("\n"):
        if len(line) <= hard or KEEP_LINE_RE.match(line):
            out.append(line)
            continue
        # 先按句末标点切，再贪心拼到 target 字；遇到话题词 / 序号优先断
        pieces, buf, depth = [], "", 0
        for sent in SENT_SPLIT_RE.split(line):
            if not sent:
                continue
            depth += sent.count("「") + sent.count("（") + sent.count("(") + sent.count("“")
            depth -= sent.count("」") + sent.count("）") + sent.count(")") + sent.count("”")
            if buf and (len(buf) >= target or
                        (BREAK_BEFORE_RE.match(sent.strip()) and len(buf) >= 60)) and depth <= 0:
                pieces.append(buf)
                buf = sent
            else:
                buf += sent
        if buf.strip():
            pieces.append(buf)
        # 太碎的小块并回去，避免出现三五个字的孤行
        merged = []
        for p in pieces:
            if merged and len(merged[-1]) < 45 and len(merged[-1]) + len(p) <= target:
                merged[-1] += p
            else:
                merged.append(p)
        out.append("\n\n".join(x.strip() for x in merged if x.strip()))
    return "\n".join(out)


def ingest_file(path, assets_dir=None):
    """把本地文件读成 markdown 原料。支持 md/txt/html/pdf/docx/图片/音频视频。"""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "文件不存在：" + str(p)}
    ext = p.suffix.lower()
    try:
        if ext in (".md", ".markdown", ".txt"):
            text = p.read_text(encoding="utf-8", errors="replace")
            title = re.search(r"^#\s+(.+)$", text, re.M)
            return {"ok": True, "title": (title.group(1).strip() if title else p.stem),
                    "author": "", "text": reflow_text(text), "method": "本地文本导入",
                    "platform": "本地文件"}
        if ext in (".html", ".htm"):
            soup = BeautifulSoup(p.read_text(encoding="utf-8", errors="replace"), "html.parser")
            for bad in soup(["script", "style"]):
                bad.decompose()
            htitle, hauthor = meta_title_author(soup, p.name)
            return {"ok": True, "title": htitle, "author": hauthor,
                    "text": html_to_md(soup.body or soup), "method": "本地 HTML 转 Markdown",
                    "platform": "本地文件"}
        if ext == ".pdf":
            import pymupdf
            doc = pymupdf.open(str(p))
            text = "\n\n".join(pg.get_text() for pg in doc)
            return {"ok": True, "title": p.stem, "author": "", "text": text,
                    "method": "PDF 文本提取（pymupdf，%d 页）" % doc.page_count, "platform": "PDF"}
        if ext == ".docx":
            import zipfile
            with zipfile.ZipFile(str(p)) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            paras = [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)]
            body = "\n\n".join(x.strip() for x in paras if x.strip())
            return {"ok": True, "title": p.stem, "author": "", "text": body,
                    "method": "DOCX 解析（zipfile，无第三方依赖）", "platform": "文档"}
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            if assets_dir:
                os.makedirs(str(assets_dir), exist_ok=True)
                dst = Path(assets_dir) / p.name
                shutil.copy2(str(p), str(dst))
            return {"ok": True, "title": p.stem, "author": "",
                    "text": "![%s](%s)\n\n（图片说明：待补。需要文字内容就说一声，我用多模态模型描述或你手动补。）" % (p.stem, p.name),
                    "method": "图片入库到 raw/assets", "platform": "图片"}
        if ext in (".mp3", ".m4a", ".wav", ".aac", ".flac", ".mp4", ".mov", ".webm", ".mkv"):
            t = transcribe(str(p))
            if t.get("ok"):
                t["title"] = p.stem
                t["platform"] = "音视频"
                return t
            return t
        return {"ok": False, "error": "暂不支持 %s 文件" % ext,
                "hint": "可以先转成 md/txt/pdf 再丢进来"}
    except Exception as e:
        return {"ok": False, "error": "读取文件失败：%s: %s" % (type(e).__name__, e)}


# 找 ffmpeg / 模型：先看环境变量，再看项目内 vendor|models（开源用户新机器上的位置），
# 最后才回落到 ~/kb-tools（本机老位置）。三条路都留着，老库不用搬家。
_HERE = Path(__file__).resolve().parent
FFMPEG_CANDIDATES = [Path(p) for p in [os.environ.get("KB_FFMPEG", "")] if p] + [
    _HERE / "vendor" / "ffmpeg.exe", _HERE / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe",
    Path.home() / "kb-tools" / "bin" / "ffmpeg.exe", Path("ffmpeg")]
WHISPER_MODEL = next((c for c in [
    Path(os.environ["KB_WHISPER_MODEL"]) if os.environ.get("KB_WHISPER_MODEL") else None,
    _HERE / "models" / "faster-whisper-small",
    Path.home() / "kb-tools" / "models" / "faster-whisper-small"] if c and c.exists()),
    Path.home() / "kb-tools" / "models" / "faster-whisper-small")


def ffmpeg_bin():
    return next((str(c) for c in FFMPEG_CANDIDATES if c.exists() or shutil.which(str(c))), None)


def duration_of(path):
    """从 ffmpeg 的 -i 横幅里读时长；不依赖 ffprobe（本机只装了 ffmpeg）。"""
    ff = ffmpeg_bin()
    if not ff:
        return 0.0
    try:
        p = subprocess.run([ff, "-i", path], capture_output=True, timeout=60)
        m = re.search(rb"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
        if not m:
            return 0.0
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        return 0.0


def sample_frames(path, out_dir, max_frames=8, min_gap=1.5):
    """按固定间隔抽帧，用来认画面里烧录的字幕 / 专有名词（抖音、小红书常见）。
    平台不提供字幕文件时，这是唯一能把「Happy 了」这类词认对的路子。"""
    ff = ffmpeg_bin()
    if not ff:
        return [], "找不到 ffmpeg，没法抽帧"
    dur = duration_of(path)
    if dur <= 0:
        return [], "读不到视频时长，跳过抽帧"
    n = max(1, min(int(max_frames), int(dur // min_gap)))
    step = dur / (n + 1)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(n):
        t = step * (i + 1)
        dst = out_dir / ("frame-%02d-%02d%02d.jpg" % (i + 1, int(t) // 60, int(t) % 60))
        try:
            subprocess.run([ff, "-y", "-ss", "%.2f" % t, "-i", path, "-frames:v", "1",
                            "-vf", "scale=720:-2", "-q:v", "3", str(dst)],
                           capture_output=True, timeout=120, check=True)
        except Exception:
            continue
        if dst.is_file() and dst.stat().st_size > 3000:
            frames.append(dst)
    return frames, "" if frames else "抽帧后没留下可用图片"


def _mmss(sec):
    sec = int(max(0, round(sec)))
    return "%02d:%02d" % (sec // 60, sec % 60)


def group_segments(segs, max_gap=5.0, max_span=25.0, max_chars=150):
    """把 whisper 的碎句拼成段：一句一行读起来像乱码，一段一段才看得出讲的是什么。
    断段条件：停顿超过 max_gap 秒、这段已经跨了 max_span 秒、或攒够 max_chars 字。"""
    paras, cur, cur_start, last_end = [], [], None, None
    for s in segs:
        text = (s.text or "").strip()
        if not text:
            continue
        if not cur:
            cur, cur_start, last_end = [text], s.start, s.end
            continue
        if (s.start - last_end) > max_gap or (s.end - cur_start) > max_span \
                or sum(len(x) for x in cur) + len(text) > max_chars:
            paras.append((cur_start, last_end, "".join(cur)))
            cur, cur_start = [text], s.start
        else:
            cur.append(text)
        last_end = s.end
    if cur:
        paras.append((cur_start, last_end, "".join(cur)))
    return paras


PUNCT_SKIP = set(" \t\u3000")
SENT_END = "。？！；?!\n"


def _transcribe_funasr(wav):
    """FunASR 的长音频正规配方：fsmn-vad 先按真人声切段（时间轴来自静音，不靠 token 猜），
    每段单独 paraformer-zh + ct-punc 转写。中文准确率明显高于 faster-whisper small
    （实测「爆款 / 场景 / 形象 / 信息流 / 带货」全对，whisper 听成「报款 / 常理 / 行销 / 星系流 / 代货」）。
    返回 [(起秒, 止秒, 段落)]；没装 funasr 或任何一步失败 → None，让调用方退回 whisper。
    """
    try:
        from funasr import AutoModel
    except Exception:
        return None
    ff = ffmpeg_bin()
    if not ff:
        return None
    tmp = Path(os.environ.get("TEMP", ".")) / ("kbvad_%s" % uuid.uuid4().hex[:8])
    try:
        vad = AutoModel(model="fsmn-vad", disable_update=True, device="cpu")
        vr = vad.generate(input=wav, max_single_segment_time=15000)
        raw = (vr[0]["value"] if isinstance(vr, list) and vr else (vr or {}).get("value", [])) or []
        chunks = []
        for a0, b0 in ((x[0] / 1000.0, x[1] / 1000.0) for x in raw):
            if b0 - a0 <= 0.4:
                continue
            # 演示视频里有一段整分钟不停顿的连续音，VAD 切不开 —— 按 15 秒硬切。
            # 每个子块的起止仍是它自己那段音频的真实范围，不是按比例猜出来的。
            n = max(1, int(-(- (b0 - a0) // 15)))
            step = (b0 - a0) / n
            chunks += [(a0 + i * step, a0 + (i + 1) * step) for i in range(n)]
        if not chunks:
            return None
        asr = AutoModel(model="paraformer-zh", punc_model="ct-punc",
                        disable_update=True, device="cpu")
        tmp.mkdir(parents=True, exist_ok=True)
        units = []
        for i, (a, b) in enumerate(chunks):
            dst = tmp / ("c%03d.wav" % i)
            subprocess.run([ff, "-y", "-ss", "%.3f" % a, "-t", "%.3f" % (b - a), "-i", wav,
                            "-ar", "16000", "-ac", "1", str(dst)],
                           capture_output=True, timeout=120, check=True)
            r = asr.generate(input=str(dst), language_hints=["zh", "en"])
            text = ((r[0]["text"] if isinstance(r, list) and r else "") or "")
            # 只吃汉字之间的空格（paraformer 逐字输出带的），英文词之间的空格留着
            text = re.sub(r"(?<=[\u4e00-\u9fff。，、？！：；])\s+", "", text).strip()
            dst.unlink(missing_ok=True)
            if len(text) >= 2:
                units.append((a, b, text))
        if not units:
            return None
    except Exception:
        return None
    finally:
        try:
            tmp.rmdir()
        except Exception:
            pass

    # 小段拼成能读的段落：停顿 >1.2s / 跨 >25s / 攒够 150 字就断
    paras, cur, cur_a, last_b, chars = [], [], None, None, 0
    for a, b, t in units:
        if not cur:
            cur, cur_a, last_b, chars = [t], a, b, len(t)
            continue
        if (a - last_b) > 1.2 or (b - cur_a) > 25.0 or chars + len(t) > 150:
            paras.append((cur_a, last_b, "".join(cur)))
            cur, cur_a, chars = [t], a, len(t)
        else:
            cur.append(t)
            chars += len(t)
        last_b = b
    if cur:
        paras.append((cur_a, last_b, "".join(cur)))
    return paras or None


def transcribe(path, model="small"):
    """抽音轨 → 转写。引擎优先 FunASR(paraformer-zh)，没装或失败退回 faster-whisper；
    两条路都输出「按语义分段的段落 + 起止时间轴」。"""
    wav = str(Path(os.environ.get("TEMP", ".")) / ("kbstranscribe_%s.wav" % uuid.uuid4().hex[:8]))
    ff = ffmpeg_bin()
    if not ff:
        return {"ok": False, "error": "找不到 ffmpeg", "hint": "装 ffmpeg 或放到 ~/kb-tools/bin/"}
    try:
        subprocess.run([ff, "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", wav],
                       capture_output=True, timeout=600, check=True)
    except Exception as e:
        return {"ok": False, "error": "抽音频失败：%s" % e}
    try:
        paras = _transcribe_funasr(wav)
        method = "FunASR paraformer-zh 本地转写（分段）"
        if paras is None:
            from faster_whisper import WhisperModel
            mp = str(WHISPER_MODEL) if WHISPER_MODEL.exists() else model
            m = WhisperModel(mp, device="cpu", compute_type="int8")
            segs, _info = m.transcribe(wav, language="zh", vad_filter=True,
                                       initial_prompt="以下是普通话字幕，使用简体中文和常见标点。",
                                       condition_on_previous_text=True)
            paras = group_segments(list(segs))
            method = "faster-whisper 本地转写（分段）"
        body = "\n\n".join("**[%s – %s]** %s" % (_mmss(a), _mmss(b), t) for a, b, t in paras)
        return {"ok": bool(body), "author": "", "text": body,
                "method": method, "platform": "音视频",
                "segments": len(paras),
                "error": "" if body else "转写结果为空"}
    except Exception as e:
        return {"ok": False, "error": "转写失败：%s: %s" % (type(e).__name__, e),
                "hint": "确认 faster-whisper 或 funasr 至少装了一个"}
    finally:
        try:
            os.remove(wav)
        except Exception:
            pass
