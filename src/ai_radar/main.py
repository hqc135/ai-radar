from __future__ import annotations

import argparse
import calendar
import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse

import feedparser
import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, HttpUrl


BEIJING_TZ = timezone(timedelta(hours=8))
URL_RE = re.compile(r"https?://[^\s)>\]]+")


class RadarConfig(BaseModel):
    model: str = "gpt-4.1-mini"
    daily_summary_model: str = "gpt-4.1-mini"
    weekly_summary_model: str = "gpt-4.1"
    max_llm_items_per_day: int = 20
    max_daily_items: int = 50
    high_priority_limit: int = 10
    deep_research_candidates_per_week: int = Field(default=3, ge=1, le=3)
    low_priority_llm_min_score: int = 3
    cache_keep_days: int = 14
    keywords: list[str] = Field(
        default_factory=lambda: [
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
            "cursor",
            "windsurf",
        ]
    )


class Source(BaseModel):
    name: str
    url: HttpUrl
    kind: str = "blog"
    enabled: bool = True


class SourcesConfig(BaseModel):
    sources: list[Source]


class CandidateItem(BaseModel):
    title: str
    source: str
    url: str
    published_at: datetime
    raw_summary: str = ""
    source_kind: str = "blog"
    manual_input: bool = False
    heuristic_score: int = 1


class ItemAnalysis(BaseModel):
    summary_cn: str = Field(description="100-150 字中文摘要")
    tags: list[str] = Field(description="3-5 个标签")
    importance: int = Field(ge=1, le=5)
    confidence: int = Field(ge=1, le=5)
    action: str
    reason: str


class AnalyzedItem(CandidateItem):
    analysis: ItemAnalysis


class WeeklySummary(BaseModel):
    overview_cn: str


def log(level: str, message: str) -> None:
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ai-radar] [{now}] [{level.upper()}] {message}")


def normalize_url(url: str) -> str:
    url, _fragment = urldefrag(url.strip())
    return url.rstrip("/")


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: Path) -> RadarConfig:
    return RadarConfig.model_validate(load_yaml(path))


def load_sources(path: Path) -> SourcesConfig:
    return SourcesConfig.model_validate(load_yaml(path))


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_urls": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("seen_urls", {})
    return data


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
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
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if not value:
            continue
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    return None


def source_confidence_cap(item: CandidateItem) -> int:
    kind = item.source_kind.lower()
    host = urlparse(item.url).netloc.lower()
    if "x.com" in host or "twitter.com" in host:
        return 3
    if kind in {"official", "docs", "github", "arxiv"}:
        return 5
    if kind in {"news", "blog"}:
        return 4
    if kind in {"social", "secondary"}:
        return 3
    return 4


def source_base_confidence(item: CandidateItem) -> int:
    kind = item.source_kind.lower()
    if kind in {"official", "docs", "github", "arxiv"}:
        return 5
    if kind in {"news", "blog"}:
        return 3
    if kind in {"social", "secondary"}:
        return 2
    return 3


def heuristic_score(title: str, summary: str, source_kind: str, manual_input: bool) -> int:
    text = f"{title}\n{summary}".lower()
    score = 1
    high_terms = [
        "release",
        "launch",
        "announcing",
        "introducing",
        "paper",
        "benchmark",
        "agent",
        "rag",
        "coding",
        "model",
        "inference",
        "open source",
        "github",
    ]
    score += sum(1 for term in high_terms if term in text)
    if source_kind.lower() in {"official", "docs", "github", "arxiv"}:
        score += 1
    if manual_input:
        score += 1
    return max(1, min(score, 5))


def is_relevant(item: CandidateItem, keywords: list[str]) -> bool:
    text = f"{item.title}\n{item.raw_summary}\n{item.url}".lower()
    return any(keyword.lower() in text for keyword in keywords)


def fetch_recent_items(config: SourcesConfig, since: datetime, radar_config: RadarConfig) -> list[CandidateItem]:
    items: list[CandidateItem] = []
    seen_in_run: set[str] = set()

    for source in config.sources:
        if not source.enabled:
            continue
        try:
            feed = feedparser.parse(str(source.url))
        except Exception as exc:
            log("warn", f"source failed name={source.name} url={source.url} error={exc}")
            continue

        if getattr(feed, "bozo", False):
            log("warn", f"source parsed with warnings name={source.name} url={source.url}")

        entries = getattr(feed, "entries", [])
        log("info", f"source fetched name={source.name} entries={len(entries)}")
        for entry in entries:
            link = entry.get("link")
            title = (entry.get("title") or "").strip()
            published_at = parse_entry_date(entry)
            if not link or not title or published_at is None or published_at < since:
                continue

            url = normalize_url(link)
            if url in seen_in_run:
                continue
            seen_in_run.add(url)

            raw_summary = (entry.get("summary") or entry.get("description") or "").strip()
            item = CandidateItem(
                title=title,
                source=source.name,
                url=url,
                published_at=published_at,
                raw_summary=raw_summary,
                source_kind=source.kind,
            )
            item.heuristic_score = heuristic_score(title, raw_summary, source.kind, False)
            if is_relevant(item, radar_config.keywords):
                items.append(item)

    return sorted(items, key=lambda item: item.published_at, reverse=True)


def read_manual_inbox(path: Path, now: datetime, radar_config: RadarConfig) -> list[CandidateItem]:
    if not path.exists():
        return []

    items: list[CandidateItem] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        for match in URL_RE.findall(line):
            url = normalize_url(match.rstrip(".,;"))
            if url in seen:
                continue
            seen.add(url)
            host = urlparse(url).netloc.lower()
            kind = "social" if "x.com" in host or "twitter.com" in host else "secondary"
            title = line.strip("- ").strip() or url
            item = CandidateItem(
                title=title[:180],
                source="manual_input",
                url=url,
                published_at=now,
                raw_summary="手动 inbox 链接，程序未抓取正文。",
                source_kind=kind,
                manual_input=True,
            )
            item.heuristic_score = heuristic_score(title, item.raw_summary, kind, True)
            if is_relevant(item, radar_config.keywords):
                items.append(item)
    log("info", f"manual inbox loaded path={path} links={len(items)}")
    return items


def fallback_analysis(item: CandidateItem) -> ItemAnalysis:
    confidence = min(source_base_confidence(item), source_confidence_cap(item))
    importance = min(item.heuristic_score, 3)
    summary = item.raw_summary.strip()
    if not summary:
        summary = f"{item.title}。该条目来自 {item.source}，建议打开原文确认细节。"
    summary = re.sub(r"<[^>]+>", "", summary)
    summary = summary[:150]
    return ItemAnalysis(
        summary_cn=summary,
        tags=["AI", item.source_kind, "待复查"],
        importance=importance,
        confidence=confidence,
        action="打开原文快速判断是否需要深入跟进。",
        reason="未调用 LLM，使用来源可信度和标题关键词做保守判断。",
    )


def analyze_item(client: OpenAI, model: str, item: CandidateItem) -> ItemAnalysis:
    prompt = {
        "title": item.title,
        "source": item.source,
        "url": item.url,
        "published_at": item.published_at.isoformat(),
        "raw_summary": item.raw_summary[:3000],
        "source_kind": item.source_kind,
        "manual_input": item.manual_input,
        "confidence_cap": source_confidence_cap(item),
    }
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 AI 行业研究助手。只输出 JSON，字段必须是 "
                    "summary_cn, tags, importance, confidence, action, reason。"
                    "summary_cn 控制在 100-150 个中文字符。importance 和 confidence 是 1-5。"
                    "可信度规则：官方博客/官方文档/GitHub/arXiv 可以高；新闻报道/博客中等；"
                    "X 或二手来源最高 3；没有原始链接不能进高优先级。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请评估这条 AI/LLM/Agent/RAG/Coding 信息的价值，"
                    "给出中文摘要、标签、重要性、可信度、建议动作和入选原因。\n\n"
                    f"{json.dumps(prompt, ensure_ascii=False)}"
                ),
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    analysis = ItemAnalysis.model_validate_json(content)
    analysis.confidence = min(analysis.confidence, source_confidence_cap(item))
    if not item.url and analysis.importance >= 4:
        analysis.importance = 3
    return analysis


def select_llm_items(items: list[CandidateItem], radar_config: RadarConfig) -> list[CandidateItem]:
    eligible = [item for item in items if item.heuristic_score >= radar_config.low_priority_llm_min_score]
    return sorted(eligible, key=lambda item: (item.heuristic_score, item.published_at), reverse=True)[
        : radar_config.max_llm_items_per_day
    ]


def write_daily_note(path: Path, date: datetime, items: list[AnalyzedItem], dry_run: bool) -> None:
    date_str = date.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
    high_priority = [item for item in items if item.analysis.importance >= 4][:10]
    other_items = [item for item in items if item not in high_priority]
    lines = [
        f"# AI Radar 日报 - {date_str}",
        "",
        f"生成时间：{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')} 北京时间",
        f"条目数：{len(items)}",
        f"高优先级：{len(high_priority)}",
        f"dry_run：{dry_run}",
        "",
    ]

    if not items:
        lines.append("过去 24 小时没有抓取到新的相关内容。")
    else:
        lines.append("## 高优先级")
        lines.append("")
        if not high_priority:
            lines.append("今天没有高优先级内容。")
            lines.append("")
        for index, item in enumerate(high_priority, start=1):
            lines.extend(format_daily_item(index, item))

        if other_items:
            lines.append("## 其他候选")
            lines.append("")
            for index, item in enumerate(other_items, start=1):
                lines.extend(format_daily_item(index, item))

    if dry_run:
        log("info", f"dry-run daily output skipped path={path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def format_daily_item(index: int, item: AnalyzedItem) -> list[str]:
    published = item.published_at.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
    tags = "、".join(item.analysis.tags)
    return [
        f"### {index}. {item.title}",
        "",
        f"- title：{item.title}",
        f"- source：{item.source}",
        f"- url：{item.url}",
        f"- published_at：{published} 北京时间",
        f"- manual_input：{item.manual_input}",
        f"- summary_cn：{item.analysis.summary_cn}",
        f"- tags：{tags}",
        f"- importance：{item.analysis.importance}/5",
        f"- confidence：{item.analysis.confidence}/5",
        f"- action：{item.analysis.action}",
        f"- reason：{item.analysis.reason}",
        "",
    ]


def write_daily_archive(path: Path, items: list[AnalyzedItem], dry_run: bool) -> None:
    if dry_run:
        log("info", f"dry-run archive output skipped path={path}")
        return
    save_json(path, [item.model_dump(mode="json") for item in items])


def load_archive_items(path: Path) -> list[AnalyzedItem]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [AnalyzedItem.model_validate(item) for item in data]


def weekly_key(item: AnalyzedItem) -> str:
    tags = "-".join(sorted(tag.lower() for tag in item.analysis.tags[:2]))
    words = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff ]", " ", item.title.lower()).split()
    return f"{tags}:{' '.join(words[:6])}"


def build_weekly_sections(items: list[AnalyzedItem]) -> dict[str, list[AnalyzedItem]]:
    buckets = {
        "本周重要产品更新": [],
        "本周重要论文": [],
        "本周重要 repo": [],
        "本周值得动手试的东西": [],
        "本周 Deep Research 候选": [],
    }
    seen: set[str] = set()
    for item in sorted(items, key=lambda x: (x.analysis.importance, x.analysis.confidence), reverse=True):
        key = weekly_key(item)
        if key in seen:
            continue
        seen.add(key)
        text = f"{item.title} {' '.join(item.analysis.tags)} {item.source_kind}".lower()
        if "arxiv" in text or "paper" in text or "论文" in text:
            buckets["本周重要论文"].append(item)
        elif "github" in text or "repo" in text or "release" in text:
            buckets["本周重要 repo"].append(item)
        elif "try" in text or "demo" in text or "sdk" in text or "动手" in text:
            buckets["本周值得动手试的东西"].append(item)
        elif item.analysis.importance >= 5 or "research" in text:
            buckets["本周 Deep Research 候选"].append(item)
        else:
            buckets["本周重要产品更新"].append(item)
    return {name: values[:10] for name, values in buckets.items()}


def select_deep_research_candidates(items: list[AnalyzedItem], limit: int) -> list[AnalyzedItem]:
    candidates = [
        item
        for item in items
        if item.analysis.importance >= 4
        and item.analysis.confidence >= 4
        and item.source_kind.lower() in {"official", "docs", "github", "arxiv"}
    ]
    return sorted(candidates, key=lambda item: (item.analysis.importance, item.analysis.confidence), reverse=True)[
        :limit
    ]


def summarize_weekly(client: OpenAI, model: str, items: list[AnalyzedItem]) -> WeeklySummary:
    payload = [
        {
            "title": item.title,
            "source": item.source,
            "url": item.url,
            "summary_cn": item.analysis.summary_cn,
            "tags": item.analysis.tags,
            "importance": item.analysis.importance,
            "confidence": item.analysis.confidence,
        }
        for item in items[:40]
    ]
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": "你是 AI 行业周报编辑。只输出 JSON，字段为 overview_cn。overview_cn 控制在 200-300 字。",
            },
            {
                "role": "user",
                "content": "请基于过去 7 天高优先级条目，写一段中文周报概览，突出产品、论文、repo 和可动手尝试方向。\n\n"
                f"{json.dumps(payload, ensure_ascii=False)}",
            },
        ],
    )
    return WeeklySummary.model_validate_json(response.choices[0].message.content or "{}")


def fallback_weekly_summary(items: list[AnalyzedItem]) -> WeeklySummary:
    return WeeklySummary(overview_cn=f"本周共汇总 {len(items)} 条高优先级内容。请优先查看产品更新、论文、repo 和 Deep Research 候选部分。")


def write_weekly_digest(
    output_dir: Path,
    archive_dir: Path,
    now: datetime,
    dry_run: bool,
    radar_config: RadarConfig,
) -> Path:
    load_dotenv()
    week_id = now.astimezone(BEIJING_TZ).strftime("%G-W%V")
    start_date = now.astimezone(BEIJING_TZ).date() - timedelta(days=6)
    items: list[AnalyzedItem] = []
    for offset in range(7):
        day = start_date + timedelta(days=offset)
        items.extend(load_archive_items(archive_dir / f"{day.isoformat()}.json"))
    items = [item for item in items if item.analysis.importance >= 4]
    sections = build_weekly_sections(items)
    sections["本周 Deep Research 候选"] = select_deep_research_candidates(
        items, radar_config.deep_research_candidates_per_week
    )
    summary = fallback_weekly_summary(items)
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key and items and not dry_run:
        model = os.getenv("AI_RADAR_WEEKLY_MODEL", radar_config.weekly_summary_model)
        try:
            log("info", f"summarizing weekly model={model}")
            summary = summarize_weekly(OpenAI(api_key=api_key), model, items)
        except Exception as exc:
            log("warn", f"weekly summary failed error={exc}")
    path = output_dir / f"{week_id}.md"
    lines = [
        f"# AI Radar 周报 - {week_id}",
        "",
        f"生成时间：{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')} 北京时间",
        f"汇总范围：{start_date.isoformat()} 至 {now.astimezone(BEIJING_TZ).date().isoformat()}",
        f"高优先级条目数：{len(items)}",
        f"Deep Research 候选上限：{radar_config.deep_research_candidates_per_week}",
        "",
        "## 本周概览",
        "",
        summary.overview_cn,
        "",
    ]
    for section, section_items in sections.items():
        lines.append(f"## {section}")
        lines.append("")
        if not section_items:
            lines.append("暂无。")
            lines.append("")
            continue
        for index, item in enumerate(section_items, start=1):
            lines.extend(
                [
                    f"### {index}. {item.title}",
                    "",
                    f"- source：{item.source}",
                    f"- url：{item.url}",
                    f"- summary_cn：{item.analysis.summary_cn}",
                    f"- importance：{item.analysis.importance}/5",
                    f"- confidence：{item.analysis.confidence}/5",
                    f"- action：{item.analysis.action}",
                    f"- reason：{item.analysis.reason}",
                    "",
                ]
            )
    if dry_run:
        log("info", f"dry-run weekly output skipped path={path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


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


def run_daily(args: argparse.Namespace, radar_config: RadarConfig) -> None:
    load_dotenv()
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)
    note_date = now.astimezone(BEIJING_TZ)

    sources = load_sources(args.sources)
    cache = load_cache(args.cache)
    prune_cache(cache, now - timedelta(days=radar_config.cache_keep_days))

    rss_items = fetch_recent_items(sources, since, radar_config)
    manual_items = read_manual_inbox(args.inbox, now, radar_config)
    candidates_by_url = {item.url: item for item in rss_items + manual_items}
    new_items = [item for item in candidates_by_url.values() if item.url not in cache["seen_urls"]]
    new_items = sorted(new_items, key=lambda item: (item.heuristic_score, item.published_at), reverse=True)
    log("info", f"candidates total={len(candidates_by_url)} new={len(new_items)}")

    llm_items = set(item.url for item in select_llm_items(new_items, radar_config))
    client: OpenAI | None = None
    if llm_items and not args.dry_run:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            log("warn", "OPENAI_API_KEY missing; using fallback summaries")
        else:
            client = OpenAI(api_key=api_key)

    analyzed: list[AnalyzedItem] = []
    model = os.getenv("AI_RADAR_DAILY_MODEL", os.getenv("AI_RADAR_MODEL", radar_config.daily_summary_model))
    for item in new_items:
        use_llm = item.url in llm_items and client is not None
        if use_llm:
            log("info", f"analyzing title={item.title[:80]}")
            try:
                analysis = analyze_item(client, model, item)
            except Exception as exc:
                log("warn", f"OpenAI failed url={item.url} error={exc}")
                analysis = fallback_analysis(item)
        else:
            analysis = fallback_analysis(item)
        analyzed.append(AnalyzedItem(**item.model_dump(), analysis=analysis))
        cache["seen_urls"][item.url] = now.isoformat()

    analyzed = sorted(
        analyzed,
        key=lambda item: (item.analysis.importance, item.analysis.confidence, item.published_at),
        reverse=True,
    )
    high_count = 0
    for item in analyzed:
        if item.analysis.importance >= 4:
            high_count += 1
            if high_count > radar_config.high_priority_limit:
                item.analysis.importance = 3
                item.analysis.reason = f"{item.analysis.reason}；因每日高优先级上限降级。"
    analyzed = analyzed[: radar_config.max_daily_items]

    output_path = args.output_dir / f"{note_date.strftime('%Y-%m-%d')}.md"
    archive_path = args.archive_dir / f"{note_date.strftime('%Y-%m-%d')}.json"
    if not analyzed and output_path.exists():
        log("info", f"no new items; kept existing daily note path={output_path}")
    else:
        write_daily_note(output_path, note_date, analyzed, args.dry_run)
        write_daily_archive(archive_path, analyzed, args.dry_run)

    if not args.dry_run:
        save_json(args.cache, cache)
    log("info", f"daily done items={len(analyzed)} output={output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate AI radar markdown from RSS feeds and manual links.")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--sources", type=Path, default=Path("sources.yaml"))
    parser.add_argument("--cache", type=Path, default=Path("data/cache.json"))
    parser.add_argument("--inbox", type=Path, default=Path("inbox/links.md"))
    parser.add_argument("--output-dir", type=Path, default=Path("notes/daily"))
    parser.add_argument("--weekly-output-dir", type=Path, default=Path("notes/weekly"))
    parser.add_argument("--archive-dir", type=Path, default=Path("data/items"))
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--weekly", action="store_true", help="Generate weekly digest instead of daily note.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    radar_config = load_config(args.config)
    if args.weekly:
        path = write_weekly_digest(
            args.weekly_output_dir,
            args.archive_dir,
            datetime.now(timezone.utc),
            args.dry_run,
            radar_config,
        )
        log("info", f"weekly done output={path}")
    else:
        run_daily(args, radar_config)


if __name__ == "__main__":
    main()
