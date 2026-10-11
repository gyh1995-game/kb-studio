# content-extract · 单独可用的多平台内容提取

从 `KB-Studio` 里拆出来的**只干一件事**的文件夹：给一条链接，还你一份 markdown 原料。
不依赖主程序，不需要起网页服务，拷到任何一台装了 Python 3.10+ 的机器都能跑。

```
extract-skill/
├── SKILL.md              给 AI agent 看的用法说明（触发条件、命令、输出契约、排错）
├── extract.py            命令行入口
├── fetchers.py           平台抓取层（由主程序生成，别手改）
├── prompts.py            凝练用的提示词（同样由主程序生成）
├── install.py            一键装依赖 + 体检
├── requirements.txt
├── cookies/              你自己放（已 gitignore，绝不外传）
└── out/                  产物
```

## 三分钟跑通

```bash
python install.py                     # 装 requests + beautifulsoup4，建好目录
python extract.py "https://www.ruanyifeng.com/blog/2026/08/weekly-issue-410.html"
```

产出在 `out/20261007-科技爱好者周刊….md`。要视频通道再跑 `python install.py --video`。

知乎 / 小红书 / 抖音要先放 cookie（浏览器 F12 复制整串，存成 `cookies/zhihu.txt`），
然后：

```bash
python extract.py "https://www.zhihu.com/question/xxx/answer/yyy" --digest
```

`--digest` 是唯一需要模型的一步，会额外追加一节「凝练总结」。不配 API key 也能跑，
只是没有那一节。

## 实测过的（2026-10-07，本机）

| 输入 | 结果 |
|---|---|
| 阮一峰周刊（网页） | ✅ 9803 字 + 30 张图本地化 |
| 知乎专栏（cookie） | ✅ 7042 字 + 8 图，`--digest` 出八段总结 |
| 知乎回答（cookie） | ✅ 875 字，作者识别正确 |
| 直接粘正文 | ✅ 存成原料，标明"本人粘贴，未加工" |
| 抓不到的站点 | ✅ `ok:false` + 具体原因 + 下一步动作，不给空壳 |

## 和主程序的关系

`fetchers.py` / `prompts.py` 由主仓库的 `tools/build_extract_skill.py` 生成，
保证这份独立工具的行为和 KB Studio 里那条抓取管道**是同一份代码**，不会各跑各的。
`python tools/build_extract_skill.py --check` 可以在发布前验一下有没有漂移。
