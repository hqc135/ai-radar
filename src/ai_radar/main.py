from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag

import feedparser
import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, HttpUrl


BEIJING_TZ = timezone(timedelta(hours=8))
DEFAULT_KEYWORDS = (
    "ai",
    "llm",
    "agent",
    "agents",
    "rag",
    "coding",
    "code",
    "openai",
    "anthropic",
    "gemini",
    "claude",
    "model",
    "inference",
    "retrieval",
)


class Source(BaseModel):
    name: str
    url: HttpUrl


class SourcesConfig(BaseModel):
    sources: list[Source]


class FeedItem(BaseModel):
    source: str
    title: str
    url: str
    published_at: datetime
    raw_summary: str = ""


class ItemAnalysis(BaseModel):
    summary_zh: str = Field(description="中文摘要，1-2 句话")
    tags: list[str] = Field(description="中文或英文标签，3-5 个")
    importance: int = Field(ge=1, le=5, description="重要性评分，1-5")
    suggested_action: str = Field(description="建议动作，简短中文")


class AnalyzedItem(FeedItem):
    analysis: ItemAnalysis


def normalize_url(url: str) -> str:
    url, _fragment = urldefrag(url.strip())
    return url.rstrip("/")


def load_sources(path: Path) -> SourcesConfig:
    with path.open("r", encoding="utf-8") as f:
        return SourcesConfig.model_validate(yaml.safe_load(f))


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_urls": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("seen_urls", {})
    return data


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def parse_entry_date(entry: Any) -> datetime | None:
    for key in ("published", "updated", "created"):
        value = entry.get(key)
        if not value:
            continue
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def is_relevant(item: FeedItem) -> bool:
    text = f"{item.title}\n{item.raw_summary}".lower()
    return any(keyword in text for keyword in DEFAULT_KEYWORDS)


def fetch_recent_items(config: SourcesConfig, since: datetime) -> list[FeedItem]:
    items: list[FeedItem] = []
    seen_in_run: set[str] = set()

    for source in config.sources:
        feed = feedparser.parse(str(source.url))
        if getattr(feed, "bozo", False):
            print(f"Warning: failed to fully parse feed: {source.name}")

        for entry in feed.entries:
            link = entry.get("link")
            title = (entry.get("title") or "").strip()
            published_at = parse_entry_date(entry)
            if not link or not title or published_at is None or published_at < since:
                continue

            url = normalize_url(link)
            if url in seen_in_run:
                continue
            seen_in_run.add(url)

            item = FeedItem(
                source=source.name,
                title=title,
                url=url,
                published_at=published_at,
                raw_summary=(entry.get("summary") or entry.get("description") or "").strip(),
            )
            if is_relevant(item):
                items.append(item)

    return sorted(items, key=lambda item: item.published_at, reverse=True)


def analyze_item(client: OpenAI, model: str, item: FeedItem) -> ItemAnalysis:
    prompt = {
        "title": item.title,
        "source": item.source,
        "url": item.url,
        "published_at": item.published_at.isoformat(),
        "raw_summary": item.raw_summary[:3000],
    }
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 AI 行业研究助手。请只输出 JSON，字段为 "
                    "summary_zh, tags, importance, suggested_action。"
                    "importance 是 1 到 5 的整数。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请判断这条 AI/LLM/Agent/RAG/Coding 相关信息的价值，"
                    "生成中文摘要、标签、重要性评分和建议动作。\n\n"
                    f"{json.dumps(prompt, ensure_ascii=False)}"
                ),
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    return ItemAnalysis.model_validate_json(content)


def write_daily_note(path: Path, date: datetime, items: list[AnalyzedItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    date_str = date.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
    lines = [
        f"# AI Radar 日报 - {date_str}",
        "",
        f"生成时间：{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"条目数：{len(items)}",
        "",
    ]

    if not items:
        lines.append("过去 24 小时没有抓取到新的相关内容。")
    else:
        for index, item in enumerate(items, start=1):
            published = item.published_at.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
            tags = "、".join(item.analysis.tags)
            lines.extend(
                [
                    f"## {index}. {item.title}",
                    "",
                    f"- 来源：{item.source}",
                    f"- 发布时间：{published} 北京时间",
                    f"- 链接：{item.url}",
                    f"- 重要性：{item.analysis.importance}/5",
                    f"- 标签：{tags}",
                    f"- 摘要：{item.analysis.summary_zh}",
                    f"- 建议动作：{item.analysis.suggested_action}",
                    "",
                ]
            )

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def prune_cache(cache: dict[str, Any], cutoff: datetime) -> None:
    seen_urls = cache.setdefault("seen_urls", {})
    for url, first_seen in list(seen_urls.items()):
        try:
            dt = datetime.fromisoformat(first_seen)
        except ValueError:
            del seen_urls[url]
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < cutoff:
            del seen_urls[url]


def run(args: argparse.Namespace) -> None:
    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required.")

    model = os.getenv("AI_RADAR_MODEL", "gpt-4.1-mini")
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)
    note_date = now.astimezone(BEIJING_TZ)

    sources = load_sources(args.sources)
    cache = load_cache(args.cache)
    prune_cache(cache, now - timedelta(days=14))

    items = fetch_recent_items(sources, since)
    new_items = [item for item in items if item.url not in cache["seen_urls"]]

    client = OpenAI(api_key=api_key)
    analyzed: list[AnalyzedItem] = []
    for item in new_items:
        print(f"Analyzing: {item.title}")
        try:
            analysis = analyze_item(client, model, item)
        except Exception as exc:
            print(f"Warning: OpenAI analysis failed for {item.url}: {exc}")
            analysis = ItemAnalysis(
                summary_zh="摘要生成失败，请打开原文查看。",
                tags=["待复查"],
                importance=1,
                suggested_action="人工检查这条内容是否值得跟进。",
            )
        analyzed.append(AnalyzedItem(**item.model_dump(), analysis=analysis))
        cache["seen_urls"][item.url] = now.isoformat()

    output_path = args.output_dir / f"{note_date.strftime('%Y-%m-%d')}.md"
    if not analyzed and output_path.exists():
        save_cache(args.cache, cache)
        print(f"No new items. Kept existing {output_path}.")
        return

    write_daily_note(output_path, note_date, analyzed)
    save_cache(args.cache, cache)
    print(f"Wrote {output_path} with {len(analyzed)} items.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate daily AI radar markdown from RSS feeds.")
    parser.add_argument("--sources", type=Path, default=Path("sources.yaml"))
    parser.add_argument("--cache", type=Path, default=Path("data/cache.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("notes/daily"))
    parser.add_argument("--hours", type=int, default=24)
    return parser


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
