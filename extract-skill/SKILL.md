---
name: content-extract
description: 把国内主流内容平台（知乎 / 抖音 / 小红书 / B站 / 公众号）以及任意网页、纯文本，提取成一份带 frontmatter 的 markdown 原料；可选再调一次模型，凝练成结构化总结。当用户丢来一条链接说"帮我存下来 / 抓正文 / 总结一下 / 做成笔记"，或要给某个知识库、笔记系统喂原料时使用。需要登录态的站点走本机 cookie。
---

# content-extract · 多平台内容提取

一条链接进来，一份 markdown 出去。**只用 Python 标准库 + requests + beautifulsoup4**，
不引框架、不引 SDK、不上传任何内容到第三方。

## 什么时候用这个 skill

- 用户丢一条链接，要正文、要存档、要总结、要做成笔记；
- 用户在搭自己的知识库，需要"原料"这一层；
- 用户说"这个抓不下来"——先按本 skill 的 hint 判断是缺 cookie 还是缺依赖，别直接说"抓不到"。

**不要**用它做的：批量爬站、监控竞品、绕过付费墙、抓任何用户没授权你抓的东西。
入口永远是"这一条链接"，不是"去这个平台搜一圈"。

## 怎么跑

```bash
python install.py                 # 第一次：装依赖 + 建 cookies/ + 体检
python extract.py "<链接>"         # 抓成 out/YYYYMMDD-标题.md
python extract.py "<链接>" --digest # 再凝练一节
python extract.py "<整段正文>" --title 名字   # 抓不到时的兜底：直接粘
python extract.py "<链接>" --json   # 给程序用，stdout 只有一份 JSON
```

`--json` 的返回：

```json
{"ok": true, "path": "…/out/20261007-xxx.md", "platform": "知乎",
 "title": "…", "author": "…", "url": "…", "chars": 7042,
 "images": 8, "method": "知乎专栏 API", "digest": true, "digest_error": ""}
```

`ok: false` 时一定有 `error` 和 `hint`，**照 hint 告诉用户下一步动作**，不要自己编原因。

## 输出契约

```
---
title / source / platform / author / captured_at / method / chars / tags
---
# 标题
> 来源：… ｜ 平台：… ｜ 作者：…
## 原文（抓取所得，未加工）      ← 作者的话，一个字都不许改写
## 凝练总结（模型写的，不是作者原话）  ← 只有 --digest 时才有
```

两条不能破：

1. **抓不到就说抓不到。** 绝不编正文、编作者、编数据、编点赞数。
   空正文一律判失败（`ok: false`），不会给你一份空壳。
2. **原文和总结必须分节**，且标明哪部分是模型写的。混在一起，回头就分不清
   哪句是作者说的、哪句是模型猜的。

## 各平台现在到底能不能读

| 平台 | 状态 | 需要什么 |
|---|---|---|
| 网页 / 博客 | ✅ 直连 | 无 |
| 公众号 | ⚠️ 多数直连 | 读不到就粘正文 |
| 知乎（回答 / 专栏） | ✅ 需 cookie | `cookies/zhihu.txt` |
| 小红书 | ✅ 需 cookie | `cookies/xiaohongshu.txt`；图文走图片识字 |
| 抖音 | ✅ 需 cookie | `cookies/douyin.txt` + `yt-dlp`；视频再要 ffmpeg + 转写 |
| B站 | ✅ | 有字幕最好；没字幕要 ffmpeg + faster-whisper |
| Instagram / Facebook | ⚠️ | cookie；读不到就粘正文 |

**cookie 怎么拿**：浏览器登录该平台 → F12 → Network → 刷新 → 点第一个请求 →
复制 Request Headers 里 `Cookie:` 后面整串 → 存成 `cookies/<站点>.txt`。
cookie 只落本机这个目录，不进输出文件、不上传、`--json` 里也不出现。

## 模型配置（只有 `--digest` 需要）

优先级：`extract-config.json` 的 `api_key` → 环境变量 `DEEPSEEK_API_KEY`
→ `~/.claude/settings.json` 的 `env.ANTHROPIC_AUTH_TOKEN`（`sk-ant-` 开头的**不用** ——
那是 Anthropic 的凭据，发给 DeepSeek 等于把一套服务的钥匙递给另一套）。

任何 OpenAI 兼容接口都行，改 `extract-config.json` 的 `base_url` / `model`。
**不配也能跑**：不加 `--digest` 就完全不需要模型，抓取功能不受影响。

## 提示词在哪、能不能改

凝练用的两份提示词在本目录的 `prompts.py`：`DIGEST_SYSTEM`（角色与硬规则）、
`DIGEST_PROMPT`（八段骨架 + 概念准入 + 排版硬规则 + 反面例子）。

⚠️ `prompts.py` 和 `fetchers.py` 是**从主程序 `KB-Studio` 自动生成**的
（`python tools/build_extract_skill.py`）。单独改这里会被下次生成覆盖。
要定制提示词：复制一份改名，在 `extract.py` 里换成读你自己的那份，
并把 `build_extract_skill.py` 的 `--check` 从你的发布流程里去掉。

## 排错

| 现象 | 原因 | 动作 |
|---|---|---|
| `连上了但没读到任何正文` | 登录墙 / 内容长在图片里 / 纯前端渲染 | 补 cookie，或直接粘正文 |
| `HTTP 403` | 本机 IP 被风控 | 上面两条，换网络也行 |
| 知乎只拿到半截 | 长回答分页渲染 | 用"分享 → 复制链接"拿到的完整链接重试 |
| 抖音 `/video/<id>` 读不到 | 该地址已不再服务端渲染数据 | 换成"分享"出来的 `v.douyin.com/…` 或 `jingxuan?modal_id=` 链接 |
| B站没有内容 | 没字幕且缺 ffmpeg | 装 `--video`，或接受只存标题简介 |
| 正文里链接断成三行 | 站点就是这么排的 | 已由 `unbreak_urls()` 在入库前拼回，别去改 CSS |
