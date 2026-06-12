# ai-radar

每天自动抓取 AI / LLM / Agent / RAG / Coding 相关 RSS 内容，用 OpenAI API 生成中文 Markdown 日报。

第一版只包含 RSS、JSON 缓存和 GitHub Actions，不接 X API、不接 Notion、不做前端。

## 功能

- 从 `sources.yaml` 读取 RSS 源
- 抓取最近 24 小时内容
- 按 URL 去重
- 用 OpenAI API 为每条内容生成中文摘要、标签、重要性评分和建议动作
- 输出到 `notes/daily/YYYY-MM-DD.md`
- GitHub Actions 每天北京时间 9:00 自动运行并提交结果

## 本地运行

Python 需要 3.11 或更高版本。

使用 `pip`：

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

编辑 `.env`，填入：

```bash
OPENAI_API_KEY=你的 OpenAI API Key
```

运行：

```bash
python src/ai_radar/main.py
```

也可以指定参数：

```bash
python src/ai_radar/main.py --hours 24 --sources sources.yaml --output-dir notes/daily
```

## GitHub Actions 配置

在 GitHub 仓库中进入：

`Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

需要配置：

- `OPENAI_API_KEY`：你的 OpenAI API Key

可选配置：

- `AI_RADAR_MODEL`：默认是 `gpt-4.1-mini`

Actions 已配置为每天北京时间 9:00 运行，也可以在 Actions 页面手动触发。

## 添加 RSS 源

编辑 `sources.yaml`：

```yaml
sources:
  - name: OpenAI Blog
    url: https://openai.com/blog/rss.xml
  - name: Your Source Name
    url: https://example.com/feed.xml
```

每个源需要：

- `name`：日报里显示的来源名称
- `url`：RSS 或 Atom feed 地址

## 日报位置

每天生成的 Markdown 日报在：

```text
notes/daily/YYYY-MM-DD.md
```

例如：

```text
notes/daily/2026-06-12.md
```

## 缓存

已处理 URL 会记录在 `data/cache.json`，用于避免重复生成摘要。缓存会保留最近 14 天的数据。
