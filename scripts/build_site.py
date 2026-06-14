from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TITLE = "AI Radar"
URL_RE = re.compile(r"(https?://[^\s<>()]+)")


def page_shell(title: str, body: str, active: str = "") -> str:
    nav = [
        ("index.html", "首页", "home"),
        ("daily/index.html", "日报", "daily"),
        ("weekly/index.html", "周报", "weekly"),
    ]
    links = []
    for href, label, key in nav:
        cls = ' class="active"' if key == active else ""
        links.append(f'<a href="/ai-radar/{href}"{cls}>{label}</a>')
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - {TITLE}</title>
  <link rel="stylesheet" href="/ai-radar/assets/styles.css">
</head>
<body>
  <header class="site-header">
    <div>
      <a class="brand" href="/ai-radar/index.html">AI Radar</a>
      <p>AI / LLM / Agent / RAG / Coding 自动雷达</p>
    </div>
    <nav>{"".join(links)}</nav>
  </header>
  <main>{body}</main>
  <footer>由 GitHub Actions 自动生成。更新时间：{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</footer>
</body>
</html>
"""


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return URL_RE.sub(lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener">{m.group(1)}</a>', escaped)


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    in_ul = False
    in_code = False
    code_lines: list[str] = []

    def close_ul() -> None:
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            if in_code:
                out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                close_ul()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line in {"<details>", "</details>"}:
            close_ul()
            out.append(line)
            continue
        if line.startswith("<summary>") and line.endswith("</summary>"):
            close_ul()
            out.append(line)
            continue
        if not line.strip():
            close_ul()
            continue
        if line.startswith("#"):
            close_ul()
            level = min(len(line) - len(line.lstrip("#")), 4)
            text = line[level:].strip()
            cls = ""
            if text in {"维护性更新", "其他略过"}:
                cls = ' class="muted-section"'
            out.append(f"<h{level}{cls}>{inline_markdown(text)}</h{level}>")
            continue
        if line.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            item = line[2:].strip()
            if item.startswith("url："):
                url = html.escape(item.removeprefix("url：").strip())
                out.append(f'<li>url：<a href="{url}" target="_blank" rel="noopener">{url}</a></li>')
            else:
                out.append(f"<li>{inline_markdown(item)}</li>")
            continue
        close_ul()
        out.append(f"<p>{inline_markdown(line)}</p>")

    close_ul()
    if in_code:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    return "\n".join(out)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_daily_items(data_dir: Path) -> dict[str, list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(data_dir.glob("*.json")):
        try:
            items[path.stem] = read_json(path)
        except Exception:
            items[path.stem] = []
    return items


def load_run_summaries(summary_dir: Path) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for path in sorted(summary_dir.glob("*.json")):
        try:
            summaries[path.stem] = read_json(path)
        except Exception:
            summaries[path.stem] = {}
    return summaries


def topic_label(item: dict[str, Any]) -> str:
    analysis = item.get("analysis", {})
    text = " ".join(
        [
            str(item.get("title", "")),
            " ".join(str(tag) for tag in analysis.get("tags", [])),
            str(analysis.get("summary_cn", "")),
        ]
    ).lower()
    buckets = [
        ("Agent / Coding", ("agent", "agents", "coding", "code", "claude code", "cursor", "windsurf")),
        ("RAG / Knowledge", ("rag", "retrieval", "knowledge", "context")),
        ("Model / Eval", ("model", "benchmark", "eval", "reasoning", "inference")),
        ("Research / Paper", ("paper", "arxiv", "research", "论文", "研究")),
        ("Infra / SDK", ("sdk", "workflow", "release", "repo", "github")),
    ]
    for label, terms in buckets:
        if any(term in text for term in terms):
            return label
    return "Product / Ecosystem"


def recent_trends(daily_items: dict[str, list[dict[str, Any]]], days: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for date in sorted(daily_items.keys(), reverse=True)[:days]:
        items = daily_items.get(date, [])
        topics: dict[str, int] = {}
        must_read = 0
        for item in items:
            analysis = item.get("analysis", {})
            topics[topic_label(item)] = topics.get(topic_label(item), 0) + 1
            if analysis.get("importance", 0) >= 4 and analysis.get("confidence", 0) >= 4:
                must_read += 1
        top_topics = sorted(topics.items(), key=lambda row: row[1], reverse=True)[:3]
        rows.append(
            {
                "date": date,
                "items": len(items),
                "must_read": must_read,
                "topics": top_topics,
            }
        )
    return rows


def render_item_card(item: dict[str, Any]) -> str:
    analysis = item.get("analysis", {})
    title = item.get("title", "Untitled")
    url = item.get("url", "")
    source = item.get("source", "")
    summary = analysis.get("summary_cn", "")
    importance = analysis.get("importance", "-")
    confidence = analysis.get("confidence", "-")
    tags = analysis.get("tags", [])[:5]
    tag_html = "".join(f"<span>{html.escape(str(tag))}</span>" for tag in tags)
    title_html = html.escape(str(title))
    if url:
        title_html = f'<a href="{html.escape(str(url))}" target="_blank" rel="noopener">{title_html}</a>'
    return f"""
<article class="item-card">
  <h3>{title_html}</h3>
  <p class="meta">{html.escape(str(source))} · 重要性 {importance}/5 · 可信度 {confidence}/5</p>
  <p>{html.escape(str(summary))}</p>
  <div class="tags">{tag_html}</div>
</article>
"""


def render_trend_row(row: dict[str, Any], summaries: dict[str, dict[str, Any]]) -> str:
    summary = summaries.get(row["date"], {})
    quality = summary.get("quality_score", "-")
    health = summary.get("content_health", "ok")
    topic_html = "".join(
        f"<span>{html.escape(label)} · {count}</span>"
        for label, count in row["topics"]
    )
    return f"""
<article class="trend-row">
  <h3><a href="/ai-radar/daily/{html.escape(row["date"])}.html">{html.escape(row["date"])}</a></h3>
  <p class="meta">{row["items"]} 条 · 必看 {row["must_read"]} · 质量 {html.escape(str(quality))}/100 · {html.escape(str(health))}</p>
  <div class="tags">{topic_html}</div>
</article>
"""


def build_index(output_dir: Path, daily_items: dict[str, list[dict[str, Any]]], summaries: dict[str, dict[str, Any]]) -> None:
    latest_date = max(daily_items.keys(), default="")
    latest_items = sorted(
        daily_items.get(latest_date, []),
        key=lambda item: (
            item.get("analysis", {}).get("importance", 0),
            item.get("analysis", {}).get("confidence", 0),
        ),
        reverse=True,
    )[:8]
    if latest_date:
        cards = "\n".join(render_item_card(item) for item in latest_items)
        trends = "\n".join(render_trend_row(row, summaries) for row in recent_trends(daily_items))
        intro = f"""
<section class="hero">
  <p class="eyebrow">Latest Daily Radar</p>
  <h1>AI Radar 日报</h1>
  <p>最新一期：<a href="/ai-radar/daily/{latest_date}.html">{latest_date}</a>，共 {len(daily_items.get(latest_date, []))} 条内容。</p>
</section>
<section class="trend-panel">
  <h2>最近 3 天趋势</h2>
  <div class="trend-grid">{trends}</div>
</section>
<h2 class="section-title">最新必看</h2>
<section class="grid">{cards}</section>
"""
    else:
        intro = """
<section class="hero">
  <p class="eyebrow">Waiting for first run</p>
  <h1>AI Radar 日报</h1>
  <p>还没有生成日报。第一次 GitHub Actions 成功运行后，这里会自动出现内容。</p>
</section>
"""
    output_dir.joinpath("index.html").write_text(page_shell("首页", intro, "home"), encoding="utf-8")


def build_note_pages(output_dir: Path, source_dir: Path, section: str, title: str) -> None:
    target_dir = output_dir / section
    target_dir.mkdir(parents=True, exist_ok=True)
    note_paths = sorted(source_dir.glob("*.md"), reverse=True)
    links = []
    for path in note_paths:
        body = f'<article class="note">{markdown_to_html(path.read_text(encoding="utf-8"))}</article>'
        target_dir.joinpath(f"{path.stem}.html").write_text(page_shell(path.stem, body, section), encoding="utf-8")
        links.append(f'<li><a href="/ai-radar/{section}/{path.stem}.html">{path.stem}</a></li>')
    if links:
        body = f"<section class=\"list-page\"><h1>{html.escape(title)}</h1><ul>{''.join(links)}</ul></section>"
    else:
        body = f"<section class=\"list-page\"><h1>{html.escape(title)}</h1><p>暂无内容。</p></section>"
    target_dir.joinpath("index.html").write_text(page_shell(title, body, section), encoding="utf-8")


def write_assets(output_dir: Path) -> None:
    assets = output_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    assets.joinpath("styles.css").write_text(
        """
:root {
  color-scheme: light;
  --bg: #f7f8fb;
  --text: #182230;
  --muted: #64748b;
  --line: #d9e0ea;
  --card: #ffffff;
  --accent: #1f6feb;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif;
  color: var(--text);
  background: var(--bg);
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.site-header {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: center;
  padding: 24px clamp(20px, 5vw, 64px);
  border-bottom: 1px solid var(--line);
  background: #fff;
}
.brand { font-size: 22px; font-weight: 700; color: var(--text); }
.site-header p { margin: 4px 0 0; color: var(--muted); }
nav { display: flex; gap: 8px; }
nav a {
  padding: 8px 12px;
  border-radius: 8px;
  color: var(--text);
}
nav a.active, nav a:hover { background: #eef4ff; text-decoration: none; }
main { width: min(1120px, calc(100% - 32px)); margin: 0 auto; padding: 32px 0 56px; }
.hero {
  padding: 28px 0;
  border-bottom: 1px solid var(--line);
  margin-bottom: 24px;
}
.hero h1 { margin: 0 0 8px; font-size: 40px; line-height: 1.15; }
.hero p { color: var(--muted); max-width: 760px; }
.section-title { margin: 28px 0 14px; }
.eyebrow {
  margin: 0 0 8px;
  text-transform: uppercase;
  letter-spacing: .08em;
  font-size: 12px;
  font-weight: 700;
  color: var(--accent) !important;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}
.trend-panel {
  padding: 20px 0 28px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 24px;
}
.trend-panel h2 { margin: 0 0 14px; }
.trend-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
.trend-row {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
}
.trend-row h3 { margin: 0 0 6px; font-size: 16px; }
.item-card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 18px;
}
.item-card h3 { margin: 0 0 8px; font-size: 18px; line-height: 1.35; }
.item-card p { line-height: 1.65; }
.meta { color: var(--muted); font-size: 13px; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 12px; }
.tags span {
  padding: 3px 8px;
  border-radius: 999px;
  background: #eef4ff;
  color: #2556a3;
  font-size: 12px;
}
.note, .list-page {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: clamp(18px, 4vw, 36px);
}
.note h1 { margin-top: 0; font-size: 34px; }
.note h2 { margin-top: 32px; padding-top: 18px; border-top: 1px solid var(--line); }
.note h2.muted-section {
  color: var(--muted);
  font-size: 20px;
}
.note h3 { margin-top: 22px; }
.note li, .note p { line-height: 1.75; }
.note ul { padding-left: 22px; }
details {
  border-top: 1px solid var(--line);
  margin-top: 28px;
  padding-top: 16px;
}
summary {
  cursor: pointer;
  color: var(--accent);
  font-weight: 700;
}
.list-page ul { line-height: 2; }
pre {
  overflow-x: auto;
  padding: 14px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
}
footer {
  border-top: 1px solid var(--line);
  padding: 20px clamp(20px, 5vw, 64px);
  color: var(--muted);
  background: #fff;
}
@media (max-width: 680px) {
  .site-header { align-items: flex-start; flex-direction: column; }
  nav { width: 100%; }
  nav a { flex: 1; text-align: center; }
  .hero h1 { font-size: 32px; }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def build_site(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    write_assets(output_dir)
    daily_items = load_daily_items(ROOT / "data" / "items")
    summaries = load_run_summaries(ROOT / "data" / "run-summary")
    build_index(output_dir, daily_items, summaries)
    build_note_pages(output_dir, ROOT / "notes" / "daily", "daily", "日报归档")
    build_note_pages(output_dir, ROOT / "notes" / "weekly", "weekly", "周报归档")
    output_dir.joinpath(".nojekyll").write_text("", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build GitHub Pages static site.")
    parser.add_argument("--output", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    build_site(args.output)
    print(f"site built at {args.output}")


if __name__ == "__main__":
    main()
