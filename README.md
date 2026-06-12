# ai-radar

每天自动抓取 AI / LLM / Agent / RAG / Coding 相关信息源，生成中文 Markdown 日报；每周汇总过去 7 天的高优先级内容。

当前版本只做 RSS、手动链接 inbox、JSON 缓存和 GitHub Actions，不接 X API、不接 Notion、不做前端。

## 项目结构

```text
.
├── config.yaml                 # 成本控制、模型和筛选配置
├── sources.yaml                # RSS / Atom 信息源
├── inbox/links.md              # 手动粘贴链接入口
├── src/ai_radar/main.py        # 主程序
├── scripts/check.py            # 验收脚本
├── data/cache.json             # URL 去重缓存
├── data/items/                 # 每日结构化 JSON 归档
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
OPENAI_API_KEY=你的 OpenAI API Key
AI_RADAR_MODEL=gpt-4.1-mini
```

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
- `OPENAI_API_KEY` 是否存在
- `notes/daily`、`notes/weekly`、`data` 目录是否可用
- 尝试抓取前 1-2 个测试源

## GitHub Actions

已有两个 workflow：

- `Daily AI Radar`：每天北京时间 9:00 运行，生成 `notes/daily/YYYY-MM-DD.md`
- `Weekly AI Radar`：每周一北京时间 9:10 运行，生成 `notes/weekly/YYYY-WW.md`

需要在 GitHub 仓库配置：

`Settings` -> `Secrets and variables` -> `Actions`

Secrets：

- `OPENAI_API_KEY`：必填，OpenAI API Key

Variables：

- `AI_RADAR_MODEL`：可选，默认 `gpt-4.1-mini`

Actions 会自动 commit：

- 日报：`notes/daily`、`data/items`、`data/cache.json`
- 周报：`notes/weekly`

## 配置成本控制

编辑 `config.yaml`：

```yaml
model: gpt-4.1-mini
max_llm_items_per_day: 20
max_daily_items: 50
high_priority_limit: 10
low_priority_llm_min_score: 3
cache_keep_days: 14
```

含义：

- `max_llm_items_per_day`：每天最多调用 LLM 摘要的条数
- `max_daily_items`：日报最多输出多少条
- `high_priority_limit`：日报高优先级最多保留多少条
- `low_priority_llm_min_score`：低于该启发式分数的候选不调用 LLM，使用降级摘要
- `cache_keep_days`：URL 去重缓存保留天数

## 添加信息源

编辑 `sources.yaml`：

```yaml
sources:
  - name: Your Source Name
    url: https://example.com/feed.xml
    kind: official
    enabled: true
```

`kind` 会影响可信度：

- `official` / `docs` / `github` / `arxiv`：可信度可以高
- `news` / `blog`：可信度中等
- `secondary` / `social`：可信度偏低
- X / Twitter 链接可信度最高为 3

单个源失败不会中断整体运行，日志会显示 `[WARN] source failed ...`。

## 手动 inbox

把 X、知乎、GitHub、博客链接粘到 `inbox/links.md`：

```markdown
- https://github.com/vercel/ai/releases
- https://x.com/example/status/123456789
```

程序每天会读取这些链接，标记为 `manual_input: true`，并进入日报候选。手动输入如果是 X / 二手来源，可信度会按规则限制。

## 日报字段

每条内容都会包含：

- `title`
- `source`
- `url`
- `summary_cn`
- `tags`
- `importance`
- `confidence`
- `action`
- `reason`

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

结构化归档：

```text
data/items/YYYY-MM-DD.json
```
