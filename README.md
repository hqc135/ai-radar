# ai-radar

每天自动抓取 AI / LLM / Agent / RAG / Coding 相关信息源，生成中文 Markdown 日报；每周汇总过去 7 天的高优先级内容。

当前版本只做 RSS、手动链接 inbox、JSON 缓存和 GitHub Actions，不接 X API、不接 Notion、不做前端。

## 项目结构

```text
.
├── config.yaml                 # 成本控制、模型和筛选配置
├── sources.yaml                # RSS / Atom 信息源
├── inbox/links.md              # 手动粘贴链接入口
├── inbox/processed.md          # 已处理手动链接归档
├── src/ai_radar/main.py        # 主程序
├── scripts/check.py            # 验收脚本
├── data/cache.json             # URL 去重缓存
├── data/items/                 # 每日结构化 JSON 归档
├── data/run-summary/           # 每次运行摘要
├── notes/daily/                # 每日 Markdown 日报
├── notes/weekly/               # 每周 Markdown 周报
└── .github/workflows/          # daily / weekly Actions
```

## 本地运行

Python 需要 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

编辑 `.env`：

```text
DEEPSEEK_API_KEY=你的 DeepSeek API Key
OPENAI_BASE_URL=https://api.deepseek.com
AI_RADAR_DAILY_MODEL=deepseek-v4-flash
AI_RADAR_WEEKLY_MODEL=deepseek-v4-flash
```

项目使用 OpenAI 兼容 SDK，默认按 DeepSeek 接口配置。也可以继续使用
`OPENAI_API_KEY`；如果要切回 OpenAI 官方接口，删除 `OPENAI_BASE_URL` 并把模型改回 OpenAI 模型即可。

生成日报：

```powershell
python src/ai_radar/main.py
```

生成周报：

```powershell
python src/ai_radar/main.py --weekly
```

只演练不写文件：

```powershell
python src/ai_radar/main.py --dry-run
python src/ai_radar/main.py --weekly --dry-run
```

## 验收

```powershell
python scripts/check.py
```

验收脚本会检查：

- `config.yaml` 是否可读
- `sources.yaml` 是否可读且有启用源
- `OPENAI_API_KEY` 或 `DEEPSEEK_API_KEY` 是否存在
- `notes/daily`、`notes/weekly`、`data` 目录是否可用
- 尝试抓取前 1-2 个测试源

## GitHub-only 托管运行

项目可以完全托管在 GitHub Actions 上运行，不需要在本地定时运行。推荐流程：

1. 把仓库推到 GitHub。
2. 打开 `Settings` -> `Secrets and variables` -> `Actions`。
3. 在 `Secrets` 添加 `DEEPSEEK_API_KEY`。
4. 确认 `Settings` -> `Actions` -> `General` -> `Workflow permissions` 允许 `Read and write permissions`。
5. 到 `Actions` 页面手动运行一次 `Daily AI Radar`，确认能生成日报并自动 commit。

已有两个 workflow：

- `Daily AI Radar`：每天北京时间 9:17 左右运行，生成 `notes/daily/YYYY-MM-DD.md`
- `Weekly AI Radar`：每周一北京时间 9:10 运行，生成 `notes/weekly/YYYY-WW.md`

Secrets：

- `DEEPSEEK_API_KEY`：推荐，DeepSeek API Key
- `OPENAI_API_KEY`：可选，兼容 OpenAI 官方或其他 OpenAI-compatible 服务

Variables：

- `OPENAI_BASE_URL`：可选，默认 `https://api.deepseek.com`
- `AI_RADAR_DAILY_MODEL`：可选，日报普通 RSS 摘要模型，默认 `deepseek-v4-flash`
- `AI_RADAR_WEEKLY_MODEL`：可选，周报总结模型，默认 `deepseek-v4-flash`
- `AI_RADAR_DISABLE_THINKING`：可选，DeepSeek 下默认 `true`，用于省 token

Actions 会自动 commit：

- 日报：`notes/daily`、`data/items`、`data/cache.json`
- 运行摘要：`data/run-summary`
- 手动 inbox 处理：`inbox/links.md`、`inbox/processed.md`
- 周报：`notes/weekly`

如果没有配置 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`，workflow 会直接失败，避免自动提交未调用模型的降级摘要。

后续日常维护也可以只在 GitHub 网页完成：

- 改 RSS 源：编辑 `sources.yaml`
- 改成本和条数：编辑 `config.yaml`
- 临时补链接：编辑 `inbox/links.md`
- 立即运行：进入 `Actions`，手动触发 `Daily AI Radar`
- 重建当天日报：手动触发 `Daily AI Radar` 时设置 `hours=72`、勾选 `ignore_cache`

## GitHub Pages

项目会从 `notes/daily`、`notes/weekly` 和 `data/items` 生成静态站点，并部署到 GitHub Pages。

第一次启用：

1. 打开仓库 `Settings` -> `Pages`
2. `Build and deployment` -> `Source` 选择 `GitHub Actions`
3. 进入 `Actions`，手动运行 `Deploy AI Radar Pages`

部署后地址：

```text
https://hqc135.github.io/ai-radar/
```

后续 `Daily AI Radar` 和 `Weekly AI Radar` 成功运行后，会自动重新部署 Pages。`Deploy AI Radar Pages` 也可以手动运行，用于只刷新站点、不重新抓取 RSS。

## 配置成本控制

编辑 `config.yaml`：

```yaml
daily_summary_model: deepseek-v4-flash
weekly_summary_model: deepseek-v4-flash
max_llm_items_per_day: 20
max_candidates_per_day: 80
max_daily_items: 50
max_arxiv_items_per_day: 12
high_priority_limit: 10
must_read_limit: 5
follow_up_limit: 10
category_section_limit: 8
deep_research_candidates_per_week: 3
low_priority_llm_min_score: 3
cache_keep_days: 14
estimated_daily_tokens_per_llm_item: 1000
estimated_weekly_summary_tokens: 3000
theme_cluster_limit: 3
min_daily_items: 8
fallback_lookback_hours: 72
backfill_limit: 12
release_noise_max_importance: 2
personal_topic_weights:
  agent: 3
  coding: 3
  rag: 3
```

含义：

- `max_llm_items_per_day`：每天最多调用 LLM 摘要的条数
- `max_candidates_per_day`：预筛选后最多进入日报分析流程的候选数
- `max_daily_items`：日报最多输出多少条
- `max_arxiv_items_per_day`：每天最多保留多少篇 arXiv 候选
- `high_priority_limit`：日报高优先级最多保留多少条
- `must_read_limit`：`今日必看` 最多多少条
- `follow_up_limit`：`值得跟进` 最多多少条
- `category_section_limit`：论文、Repo、产品更新等分组最多多少条
- `deep_research_candidates_per_week`：周报里 Deep Research 候选数量，只允许 1-3 条
- `low_priority_llm_min_score`：低于该启发式分数的候选不调用 LLM，使用降级摘要
- `cache_keep_days`：URL 去重缓存保留天数
- `estimated_daily_tokens_per_llm_item`：运行摘要里估算日报 LLM token 用量
- `estimated_weekly_summary_tokens`：运行摘要里估算周报总结 token 用量
- `theme_cluster_limit`：日报顶部“今天主要发生的几件事”最多输出多少个主题
- `min_daily_items`：低于该条数时，日报标记内容偏少并触发补偿逻辑
- `fallback_lookback_hours`：新内容不足时自动扩展到多少小时回看
- `backfill_limit`：重复运行或新内容偏少时，最多从近期缓存补入多少条
- `release_noise_max_importance`：纯补丁、canary、依赖 bump 这类维护性 release 的最高重要性
- `personal_topic_weights`：个人偏好主题权重，默认更关注 Agent、Coding、RAG、Eval、Reasoning，影响预筛选、LLM 调用顺序和主列表排序

模型策略：

- 普通 RSS 摘要：使用 `daily_summary_model` / `AI_RADAR_DAILY_MODEL`，默认 DeepSeek `deepseek-v4-flash`。
- 高优先级候选：日报只筛出来，不自动做 Deep Research，适合人工丢给 ChatGPT 或 Deep Research。
- 周报总结：使用 `weekly_summary_model` / `AI_RADAR_WEEKLY_MODEL`，默认同样用便宜模型；如果周报质量不够，再单独换更强模型。
- Deep Research：周报只输出 1-3 个候选，不自动调用 Deep Research。

## 添加信息源

编辑 `sources.yaml`：

```yaml
sources:
  - name: Your Source Name
    url: https://example.com/feed.xml
    kind: official
    category: product
    priority: 4
    tier: 1
    daily_limit: 5
    enabled: true
```

`kind` 会影响可信度：

- `official` / `docs` / `github` / `arxiv`：可信度可以高
- `news` / `blog`：可信度中等
- `secondary` / `social`：可信度偏低
- X / Twitter 链接可信度最高为 3

`category` 会影响日报分组：

- `product`：产品更新
- `paper`：论文
- `repo`：Repo / release
- `tool`：值得动手试的工具
- `manual`：手动 inbox

`priority` 是 1-5，影响候选排序；`daily_limit` 用来限制单个源每天最多进入候选池的条数。
`tier` 可选，用于覆盖自动源分层：`1` 代表官方公告 / GitHub release / 论文，`2` 代表高质量博客 / 工程实践，`3` 代表新闻 / 二手信息 / 趋势榜。

单个源失败不会中断整体运行，日志会显示 `[WARN] source failed ...`。

## 手动 inbox

把 X、知乎、GitHub、博客链接粘到 `inbox/links.md`：

```markdown
- https://github.com/vercel/ai/releases #repo #ai-sdk @github !high
- https://x.com/example/status/123456789 #agent @x
```

程序每天会读取这些链接，标记为 `manual_input: true`，并进入日报候选。手动输入如果是 X / 二手来源，可信度会按规则限制。

支持的手动标记：

- `#tag`：手动标签，会进入日报 tags
- `@source`：手动来源，例如 `@x`、`@zhihu`、`@github`
- `!high`：强制进入高优先级候选，仍受可信度规则约束

非 dry-run 成功运行后，已处理的链接行会从 `inbox/links.md` 移动到 `inbox/processed.md`。

## 日报字段

日报按以下分组输出：

- `今天主要发生的 3 件事`：按主题聚类给出当天核心变化，不只是逐条 RSS 摘要
- `内容健康`：说明候选少、回看补充、源抓取异常或缓存补入等情况
- `来源分层`：统计 Tier 1 / Tier 2 / Tier 3 的内容占比
- `今日必看`：高重要性、高可信度，最多 5 条
- `值得跟进`：中高重要性候选，最多 10 条
- `重要论文`
- `重要 Repo`
- `产品更新`
- `维护性更新`：集中放置低价值 GitHub release、canary、依赖更新
- `低优先级链接`：只保留紧凑标题、链接和“不值得进主列表”的原因

源分层规则：

- Tier 1：官方公告、官方文档、GitHub release、arXiv / 论文
- Tier 2：高质量博客、工程实践
- Tier 3：新闻、二手信息、社交媒体、趋势榜

同一事件如果来自多个源，日报会合并为一条代表内容，并在 `related_sources` 中保留其他来源链接。

每条内容都会包含：

- `title`
- `source`
- `source_tier`
- `url`
- `summary_cn`
- `tags`
- `importance`
- `confidence`
- `preference_score`
- `action`
- `reason`
- `related_sources`

OpenAI API 失败时会降级输出原始摘要或标题摘要，避免整次运行失败。

## 输出位置

日报：

```text
notes/daily/YYYY-MM-DD.md
```

周报：

```text
notes/weekly/YYYY-WW.md
```

周报会输出：

- 本周概览
- 本周趋势判断
- 值得试用的 3 个工具
- 值得深挖的研究问题
- 下周观察清单
- 本周 Deep Research 候选，以及可复制 prompt

结构化归档：

```text
data/items/YYYY-MM-DD.json
```

运行摘要：

```text
data/run-summary/YYYY-MM-DD.json
```
