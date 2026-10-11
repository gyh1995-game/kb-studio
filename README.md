# KB Studio · 会自己长卡片的个人知识库

> 你刷到好内容 → 把链接丢进来 → 它抓成 Markdown 原料 → 自动长出**概念 / 连接 / 问题**三层卡 → 归进 Obsidian，
> 并且用复习队列逼你**再遇见一次**。
>
> 痛点不是"存不下来"，是"存了再也不看"。

本地跑、零构建、纯 Python 标准库后端 + 原生前端。模型和 cookie 用你自己的。

## 三步跑起来

```bash
# 1) 装依赖（Python 3.10+；Windows 直接双击「安装依赖.bat」）
python tools/install.py              # 依赖 → ffmpeg → 转写模型 → 自动体检

# 2) 起服务（Windows 可双击「启动知识库.bat」）
python server.py                     # → http://127.0.0.1:8777，浏览器自动打开
```

3) 网页里「模型配置」填一次 API（OpenAI 兼容即可：DeepSeek / 通义 / Kimi / 智谱 / 本地 Ollama），
需要登录态的平台在「Cookie 通行证」里贴一次。之后就是**丢链接 → 消化 → 长卡 → 复习**。

4) （可选）用 Obsidian「打开文件夹作为仓库」指到同一个库目录，就能在 Obsidian 里看双链和图谱；
只用网页界面也能跑，但图谱视图要 Obsidian 生成一次 `.obsidian/graph.json` 才有。

**没配 API 也能跑**：抓取、入库、语音转写、阅读、复习队列都照常；
但「图片/画面识字」和「AI 提炼」是调模型做的，没 key 时识字会跳过、提炼退化成规则抽取（会明确告诉你它退化了）。

## 它能做什么

| | |
|---|---|
| **取数** | 知乎 / 抖音 / 小红书 / B站 / Instagram / Facebook / 普通网页；本地 md·txt·html·pdf·docx·图片·音视频 |
| **图文 vs 视频自动分流** | 图文走图片识字；视频走「下载 → 中文语音转写（FunASR，退回 faster-whisper）→ 固定帧抽字幕识字」，逐字稿按语义分段，时间戳点了能跳 |
| **消化** | 八段骨架：核心观点 / 可执行清单 / 概念候选 / 连接候选 / 我的疑问 / 值得记住的原话 / 下一步 / 主题标签 |
| **长卡** | 概念 ≤3、连接 ≤2、问题 3；概念名不足 8 字判为从句不建卡；四段不齐不建卡；同一主张只留一张卡（跨来源**回填**而不是另起近义名） |
| **自动接线** | 每张新卡（消化长出来的、一句话生成的、你自己手写的）入库后都会和库里比一遍，接得上的写进正文「接上的线」；跨原料优先 |
| **复习队列** | 两个入口：每份原料一条「重读原料」(+7 天)、每条疑问一条「回看原文」(+3 天)。每条带 类型 / 预估分钟 / 来源 |
| **口径统一** | 字数只算正文净字（剥 frontmatter / 代码 / 裸链 / 语法字符，中文按字、英文按词）；时长常数取自公开实现，视频直接取原片时长 |
| **AI 学习总结** | 首页两个按钮：今日 / 本周总结。证据 = 本机阅读停留 + 现读卡片正文 + 这一窗口勾掉的复习；没记录就拒绝生成，不编 |
| **库体检** | `tools/lint.py` 九项：悬空双链、孤儿卡、薄卡、重复卡列表、处理日志混进正文、导航页幽灵链接… |
| **可回退** | 删除一律进 `inbox/trash/`；批量改写前先备份并打 diff |
| **独立爬虫** | `extract-skill/` 可单独安装单独跑：一条链接 → 一份带 frontmatter 的 markdown 原料，`--digest` 再凝练一节 |

## 文档在哪（先看这张表，别翻目录）

| 我想知道… | 打开 |
|---|---|
| **这个项目能干什么、怎么跑起来** | 本文件 `README.md` |
| **每个环节的提示词原文**（消化 / 三类卡 / 一句话生卡 / 新卡接线 / 图片识字 / 抽帧识字 / 学习总结） | `docs/提示词全集.md` ← **由 `tools/dump_prompts.py` 从 `server.py` 生成，别手改** |
| **产出该长什么样**（排版契约、三层卡骨架、元数据契约、时长口径、标签治理、正反例） | `skill/PROMPTS.md` |
| **想改代码，改哪一处、会影响什么** | `docs/二次开发.md` |
| **用户的协作偏好与硬性约束**（重复模块、凭据、验收口径） | `AGENTS.md` ← 任何 AI 接手前先读这份 |
| **下一步要做什么升级**（日历 / 待办 / 提醒 / 微信 / 页面效果 / 3D 工作台），可直接交给另一个 AI 执行 | `docs/后续升级方向.md` |
| **单独那份爬虫工具怎么用** | `extract-skill/README.md`（给人看）+ `extract-skill/SKILL.md`（给 AI 用） |

## 目录

```
KB-Studio/
├── server.py            网页服务 + 消化编排 + 建卡（唯一入口；所有提示词也在这里）
├── fetchers.py          各平台取数、转写、抽帧、正文清洗
├── static/              前端（原生 HTML/CSS/JS，无构建）
├── AGENTS.md            ★ 用户偏好与硬性约束，接手先读
├── skill/PROMPTS.md     ★ 内容输出规范（权威口径）
├── docs/
│   ├── 提示词全集.md     ★ 全部提示词（自动生成）
│   ├── 二次开发.md       ★ 改哪一处、怎么验证
│   └── 后续升级方向.md   ★ 交给下一个 AI 的升级说明书
├── extract-skill/       独立可安装的多平台内容提取工具（fetchers.py/prompts.py 由脚本生成）
├── templates/           空库骨架：system.md（宪法）+ learner_profile.md（你的取舍标准）
├── scripts/             B 站官方脚本（项目自带，不依赖外部 skill）
├── tools/               install / doctor / selftest / spec_check / lint / dump_prompts / build_extract_skill
├── config.example.json  模型配置样例（复制成 config.json 再填 key；config.json 不进 git）
└── cookies/             你贴的登录态（.gitignore 已挡住，永远不会被提交）
```

## 常用命令

```bash
python server.py --vault D:/我的库            # 指定知识库目录（空目录会自动初始化骨架）
python server.py --init --vault D:/我的库     # 只初始化，不起服务
python server.py --port 8899 --no-browser
python tools/doctor.py --fix-hint             # 这台机器能干什么、缺什么
python tools/lint.py                          # 库体检
python tools/spec_check.py                    # 57 项离线断言（不打网络、不调模型）
python tools/selftest.py                      # 端到端自检（会调真模型，指到临时库再跑）
python tools/dump_prompts.py                  # 重新生成 docs/提示词全集.md
python tools/build_extract_skill.py           # 重新生成 extract-skill/ 的共享代码
```

## 注意

- 服务只监听 `127.0.0.1`，**没有任何鉴权**。别把它直接暴露到公网；要远程用就自己加反代 + 认证。
- 平台改版需要维护：抖音/小红书这类站点的取数链路会失效，cookie 你自己更新，代码坏了看 `docs/二次开发.md` 第 1 节。
- 账号有封禁风险：低频、只读、自用是低风险，但不是零风险。

## 许可与致谢

思路来自 Andrej Karpathy 的 LLM Wiki（原料 / 卡片 / 规范三层 + ingest / query / **lint** 三个动作）、
Zettelkasten 的原子笔记，以及吴乐旻的苏格拉底学习法。
