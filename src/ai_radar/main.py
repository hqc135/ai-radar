from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import urllib.request
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
    model: str = "deepseek-v4-flash"
    daily_summary_model: str = "deepseek-v4-flash"
    weekly_summary_model: str = "deepseek-v4-flash"
    max_llm_items_per_day: int = 20
    max_candidates_per_day: int = 80
    max_daily_items: int = 50
    max_arxiv_items_per_day: int = 12
    high_priority_limit: int = 10
    must_read_limit: int = 5
    follow_up_limit: int = 10
    category_section_limit: int = 8
    deep_research_candidates_per_week: int = Field(default=3, ge=1, le=3)
    low_priority_llm_min_score: int = 3
    cache_keep_days: int = 14
    estimated_daily_tokens_per_llm_item: int = 1000
    estimated_weekly_summary_tokens: int = 3000
    theme_cluster_limit: int = 3
    min_daily_items: int = 8
    fallback_lookback_hours: int = 72
    backfill_limit: int = 12
    release_noise_max_importance: int = 2
    quality_score_threshold: int = 70
    quality_retry_lookback_hours: int = 96
    source_state_keep_days: int = 14
    source_limit_min: int = 1
    source_limit_max: int = 10
    source_fetch_timeout_seconds: int = 20
    source_observation_days: int = 3
    source_observation_daily_limit: int = 1
    personal_topic_weights: dict[str, int] = Field(
        default_factory=lambda: {
            "agent": 3,
            "agents": 3,
            "coding": 3,
            "code": 2,
            "rag": 3,
            "retrieval": 2,
            "eval": 2,
            "reasoning": 2,
        }
    )
    high_signal_terms: list[str] = Field(
        default_factory=lambda: [
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
            "sdk",
            "eval",
            "reasoning",
        ]
    )
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
    category: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    tier: int | None = Field(default=None, ge=1, le=3)
    daily_limit: int | None = None
    enabled: bool = True


class SourcesConfig(BaseModel):
    sources: list[Source]


class FeedbackRule(BaseModel):
    titles: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class FeedbackConfig(BaseModel):
    like: FeedbackRule = Field(default_factory=FeedbackRule)
    hide: FeedbackRule = Field(default_factory=FeedbackRule)
    source_weights: dict[str, int] = Field(default_factory=dict)
    keyword_weights: dict[str, int] = Field(default_factory=dict)


class CandidateItem(BaseModel):
    title: str
    source: str
    url: str
    published_at: datetime
    raw_summary: str = ""
    source_kind: str = "blog"
    category: str = "product"
    source_priority: int = 3
    manual_input: bool = False
    manual_tags: list[str] = Field(default_factory=list)
    manual_source: str | None = None
    force_high: bool = False
    backfilled_from_cache: bool = False
    heuristic_score: int = 1
    preference_score: int = 0
    source_tier: int = 3


class RelatedItem(BaseModel):
    title: str
    source: str
    url: str
    source_tier: int = 3
    importance: int = 1
    confidence: int = 1


class ItemAnalysis(BaseModel):
    summary_cn: str = Field(description="100-150 字中文摘要")
    tags: list[str] = Field(description="3-5 个标签")
    importance: int = Field(ge=1, le=5)
    confidence: int = Field(ge=1, le=5)
    action: str
    reason: str


class AnalyzedItem(CandidateItem):
    analysis: ItemAnalysis
    related_items: list[RelatedItem] = Field(default_factory=list)


class WeeklySummary(BaseModel):
    overview_cn: str
    trend_judgement: list[str] = Field(default_factory=list)
    tools_to_try: list[str] = Field(default_factory=list)
    research_questions: list[str] = Field(default_factory=list)
    watchlist_next_week: list[str] = Field(default_factory=list)


class RunStats(BaseModel):
    sources_total: int = 0
    sources_success: int = 0
    sources_failed: int = 0
    source_warnings: int = 0
    rss_items: int = 0
    manual_items: int = 0
    candidates_total: int = 0
    candidates_new: int = 0
    candidates_selected: int = 0
    llm_planned: int = 0
    llm_succeeded: int = 0
    llm_failed: int = 0
    fallback_items: int = 0
    lookback_hours: int = 24
    fallback_lookback_used: bool = False
    backfill_items: int = 0
    output_items: int = 0
    content_health: str = "ok"
    health_reasons: list[str] = Field(default_factory=list)
    must_read_items: int = 0
    low_quality_release_items: int = 0
    low_quality_release_ratio: float = 0.0
    quality_score: int = 100
    quality_needs_review: bool = False
    quality_signals: dict[str, Any] = Field(default_factory=dict)
    source_failures: list[str] = Field(default_factory=list)
    source_warning_names: list[str] = Field(default_factory=list)
    source_adjustments: dict[str, int] = Field(default_factory=dict)
    output_path: str = ""
    archive_path: str = ""

    def estimated_tokens(self, radar_config: RadarConfig) -> int:
        return self.llm_planned * radar_config.estimated_daily_tokens_per_llm_item


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


def load_feedback(path: Path) -> FeedbackConfig:
    return FeedbackConfig.model_validate(load_yaml(path))


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


def load_source_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "sources": {}, "last_quality": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("version", 1)
    data.setdefault("sources", {})
    data.setdefault("last_quality", {})
    return data


def source_history_last_days(source_state: dict[str, Any], source_name: str, now: datetime, days: int) -> list[dict[str, Any]]:
    cutoff = now.astimezone(BEIJING_TZ).date() - timedelta(days=days - 1)
    history = source_state.get("sources", {}).get(source_name, {}).get("history", [])
    recent: list[dict[str, Any]] = []
    for row in history:
        try:
            row_date = datetime.fromisoformat(str(row.get("date"))).date()
        except ValueError:
            continue
        if row_date >= cutoff:
            recent.append(row)
    return recent


def source_state_metrics(source_state: dict[str, Any], source_name: str, now: datetime) -> dict[str, int | float]:
    history = source_history_last_days(source_state, source_name, now, 7)
    effective = sum(int(row.get("effective_items", 0)) for row in history)
    output = sum(int(row.get("output_items", 0)) for row in history)
    noise = sum(int(row.get("noise_items", 0)) for row in history)
    failed = sum(int(row.get("failed", 0)) for row in history)
    return {
        "effective_items_7d": effective,
        "output_items_7d": output,
        "noise_items_7d": noise,
        "failures_7d": failed,
        "noise_ratio_7d": round(noise / output, 3) if output else 0.0,
    }


def adjusted_daily_limit(
    source: Source,
    source_state: dict[str, Any],
    radar_config: RadarConfig,
    now: datetime,
) -> int | None:
    if source.daily_limit is None:
        return None
    metrics = source_state_metrics(source_state, source.name, now)
    limit = source.daily_limit
    known_sources = source_state.get("sources", {})
    if known_sources and source.name not in known_sources:
        limit = min(limit, radar_config.source_observation_daily_limit)
    if metrics["effective_items_7d"] >= 5 and metrics["noise_ratio_7d"] <= 0.3:
        limit += 1
    if metrics["output_items_7d"] >= 3 and metrics["noise_ratio_7d"] >= 0.6:
        limit -= 1
    if metrics["output_items_7d"] == 0 and metrics["failures_7d"] >= 3:
        limit -= 1
    return max(radar_config.source_limit_min, min(radar_config.source_limit_max, limit))


def apply_source_state_limits(
    sources: SourcesConfig,
    source_state: dict[str, Any],
    radar_config: RadarConfig,
    now: datetime,
    stats: RunStats,
) -> SourcesConfig:
    adjusted_sources: list[Source] = []
    for source in sources.sources:
        updated = source.model_copy(deep=True)
        adjusted = adjusted_daily_limit(source, source_state, radar_config, now)
        if adjusted is not None and adjusted != source.daily_limit:
            updated.daily_limit = adjusted
            stats.source_adjustments[source.name] = adjusted
        adjusted_sources.append(updated)
    return SourcesConfig(sources=adjusted_sources)


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


def parse_feed_url(url: str, timeout_seconds: int) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ai-radar/0.1 (+https://github.com/hqc135/ai-radar)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        data = response.read()
    return feedparser.parse(data)


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


def source_tier_for_kind(source_kind: str) -> int:
    kind = source_kind.lower()
    if kind in {"official", "docs", "github", "arxiv"}:
        return 1
    if kind == "blog":
        return 2
    return 3


def source_tier_for_source(source: Source) -> int:
    if source.tier is not None:
        return source.tier
    kind = source.kind.lower()
    name = source.name.lower()
    if kind in {"github", "arxiv", "docs"}:
        return 1
    if kind == "official":
        if source.category == "paper" or any(term in name for term in ("news", "changelog", "release", "releases")):
            return 1
        if "blog" in name:
            return 2
        return 1
    return source_tier_for_kind(kind)


def source_tier_label(tier: int) -> str:
    labels = {
        1: "Tier 1：官方公告 / GitHub release / 论文",
        2: "Tier 2：高质量博客 / 工程实践",
        3: "Tier 3：新闻 / 二手信息 / 趋势榜",
    }
    return labels.get(tier, labels[3])


def topic_preference_score(title: str, summary: str, topic_weights: dict[str, int]) -> int:
    text = f"{title}\n{summary}".lower()
    score = 0
    for topic, weight in topic_weights.items():
        if topic.lower() in text:
            score += max(weight, 0)
    return min(score, 6)


def normalized_terms(values: list[str]) -> list[str]:
    return [value.strip().lower() for value in values if value.strip()]


def feedback_text(item: CandidateItem) -> str:
    return f"{item.title}\n{item.raw_summary}\n{' '.join(item.manual_tags)}\n{item.url}".lower()


def matches_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in normalized_terms(terms))


def feedback_rule_matches(item: CandidateItem, rule: FeedbackRule) -> bool:
    text = feedback_text(item)
    source = item.source.lower()
    if matches_any(item.title.lower(), rule.titles):
        return True
    if any(term == source or term in source for term in normalized_terms(rule.sources)):
        return True
    if matches_any(text, rule.tags):
        return True
    if matches_any(text, rule.keywords):
        return True
    return False


def should_hide_by_feedback(item: CandidateItem, feedback: FeedbackConfig) -> bool:
    return feedback_rule_matches(item, feedback.hide)


def feedback_preference_delta(item: CandidateItem, feedback: FeedbackConfig) -> int:
    delta = 0
    if feedback_rule_matches(item, feedback.like):
        delta += 3
    source_key = item.source.lower()
    for source, weight in feedback.source_weights.items():
        source_term = source.strip().lower()
        if source_term and (source_term == source_key or source_term in source_key):
            delta += weight
    text = feedback_text(item)
    for keyword, weight in feedback.keyword_weights.items():
        if keyword.strip().lower() in text:
            delta += weight
    return max(-6, min(6, delta))


def effective_topic_weights(radar_config: RadarConfig, feedback: FeedbackConfig) -> dict[str, int]:
    weights = dict(radar_config.personal_topic_weights)
    for term in feedback.like.tags + feedback.like.keywords:
        if term.strip():
            weights[term.strip().lower()] = max(weights.get(term.strip().lower(), 0), 2)
    for term, weight in feedback.keyword_weights.items():
        if term.strip():
            key = term.strip().lower()
            weights[key] = weights.get(key, 0) + weight
    return weights


def effective_keywords(radar_config: RadarConfig, feedback: FeedbackConfig) -> list[str]:
    values = list(radar_config.keywords)
    values.extend(feedback.like.tags)
    values.extend(feedback.like.keywords)
    values.extend(feedback.keyword_weights.keys())
    seen: set[str] = set()
    keywords: list[str] = []
    for value in values:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            keywords.append(value)
    return keywords


def apply_feedback_to_item(item: CandidateItem, feedback: FeedbackConfig) -> CandidateItem:
    delta = feedback_preference_delta(item, feedback)
    if delta:
        item.preference_score = max(-6, min(8, item.preference_score + delta))
        item.heuristic_score = max(1, min(5, item.heuristic_score + (1 if delta >= 3 else 0)))
    return item


def is_release_noise(item: CandidateItem) -> bool:
    if item.source_kind.lower() != "github":
        return False
    title = item.title.lower()
    raw = item.raw_summary.lower()
    text = f"{title}\n{raw}"
    if "patch changes" in raw and (
        "updated dependencies" in raw
        or "canary" in title
        or re.search(r"(?:<li>|\n)\s*@?[a-z0-9_.-]+(?:/[a-z0-9_.-]+)?@\d", raw)
    ):
        return True
    noise_terms = (
        "patch changes",
        "updated dependencies",
        "dependency",
        "dependencies",
        "bump ",
        "chore:",
        "docs:",
        "test:",
        "canary",
    )
    has_noise = any(term in text for term in noise_terms)
    has_signal = any(
        term in text
        for term in (
            "breaking",
            "security",
            "vulnerability",
            "rag",
            "retrieval",
            "reasoning",
            "sandbox",
            "codex",
            "claude code",
        )
    )
    return has_noise and not has_signal


def apply_release_noise_rules(item: CandidateItem, analysis: ItemAnalysis, radar_config: RadarConfig) -> ItemAnalysis:
    if not is_release_noise(item):
        return analysis
    max_importance = max(1, min(radar_config.release_noise_max_importance, 5))
    if analysis.importance > max_importance:
        analysis.importance = max_importance
    if "常规版本/依赖更新降噪" not in analysis.reason:
        analysis.reason = f"{analysis.reason}；常规版本/依赖更新降噪。"
    if analysis.action.lower() not in {"ignore", "info"}:
        analysis.action = "info"
    return analysis


def heuristic_score(
    title: str,
    summary: str,
    source_kind: str,
    source_priority: int,
    manual_input: bool,
    high_signal_terms: list[str],
) -> int:
    text = f"{title}\n{summary}".lower()
    score = 1
    score += min(source_priority - 1, 3)
    score += sum(1 for term in high_signal_terms if term.lower() in text)
    if source_kind.lower() in {"official", "docs", "github", "arxiv"}:
        score += 1
    if manual_input:
        score += 2
    return max(1, min(score, 5))


def parse_manual_line(line: str) -> tuple[list[str], str | None, bool]:
    tags = [part[1:] for part in line.split() if part.startswith("#") and len(part) > 1]
    source = next((part[1:] for part in line.split() if part.startswith("@") and len(part) > 1), None)
    force_high = "!high" in line.split()
    return tags, source, force_high


def is_relevant(item: CandidateItem, keywords: list[str]) -> bool:
    text = f"{item.title}\n{item.raw_summary}\n{item.url}".lower()
    return any(keyword.lower() in text for keyword in keywords)


def infer_category(source: Source, title: str, summary: str) -> str:
    if source.category:
        return source.category
    text = f"{title}\n{summary}\n{source.name}".lower()
    if source.kind == "arxiv" or "paper" in text:
        return "paper"
    if source.kind == "github" or "release" in text or "repo" in text:
        return "repo"
    if "sdk" in text or "demo" in text or "example" in text:
        return "tool"
    return "product"


def candidate_rank_key(item: CandidateItem) -> tuple[int, int, int, int, datetime]:
    kind_bonus = 1 if item.source_kind.lower() in {"official", "github", "arxiv", "docs"} else 0
    manual_bonus = 2 if item.manual_input else 0
    return (
        item.heuristic_score,
        item.preference_score,
        item.source_priority,
        kind_bonus + manual_bonus,
        item.published_at,
    )


def apply_source_limits(items: list[CandidateItem], sources: SourcesConfig) -> list[CandidateItem]:
    limits = {source.name: source.daily_limit for source in sources.sources if source.daily_limit is not None}
    if not limits:
        return items
    selected: list[CandidateItem] = []
    counts: dict[str, int] = {}
    for item in sorted(items, key=candidate_rank_key, reverse=True):
        limit = limits.get(item.source)
        count = counts.get(item.source, 0)
        if limit is not None and count >= limit:
            continue
        selected.append(item)
        counts[item.source] = count + 1
    return selected


def prefilter_candidates(
    items: list[CandidateItem],
    sources: SourcesConfig,
    radar_config: RadarConfig,
) -> list[CandidateItem]:
    limited = apply_source_limits(items, sources)
    arxiv_count = 0
    selected: list[CandidateItem] = []
    for item in sorted(limited, key=candidate_rank_key, reverse=True):
        if item.source_kind == "arxiv":
            arxiv_count += 1
            if arxiv_count > radar_config.max_arxiv_items_per_day:
                continue
        selected.append(item)
        if len(selected) >= radar_config.max_candidates_per_day:
            break
    return selected


def fetch_recent_items(
    config: SourcesConfig,
    since: datetime,
    radar_config: RadarConfig,
    feedback: FeedbackConfig,
    stats: RunStats | None = None,
) -> list[CandidateItem]:
    items: list[CandidateItem] = []
    seen_in_run: set[str] = set()
    latest_allowed = datetime.now(timezone.utc) + timedelta(hours=6)

    for source in config.sources:
        if not source.enabled:
            continue
        if stats:
            stats.sources_total += 1
        try:
            feed = parse_feed_url(str(source.url), radar_config.source_fetch_timeout_seconds)
        except Exception as exc:
            if stats:
                stats.sources_failed += 1
                stats.source_failures.append(source.name)
            log("warn", f"source failed name={source.name} url={source.url} error={exc}")
            continue

        if getattr(feed, "bozo", False):
            if stats:
                stats.source_warnings += 1
                stats.source_warning_names.append(source.name)
            log("warn", f"source parsed with warnings name={source.name} url={source.url}")

        entries = getattr(feed, "entries", [])
        if stats:
            stats.sources_success += 1
        log("info", f"source fetched name={source.name} entries={len(entries)}")
        for entry in entries:
            link = entry.get("link")
            title = (entry.get("title") or "").strip()
            published_at = parse_entry_date(entry)
            if not link or not title or published_at is None or published_at < since:
                continue
            if published_at > latest_allowed:
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
                category=infer_category(source, title, raw_summary),
                source_priority=source.priority,
                source_tier=source_tier_for_source(source),
            )
            item.heuristic_score = heuristic_score(
                title,
                raw_summary,
                source.kind,
                source.priority,
                False,
                radar_config.high_signal_terms,
            )
            item.preference_score = topic_preference_score(title, raw_summary, effective_topic_weights(radar_config, feedback))
            if is_release_noise(item):
                item.heuristic_score = min(item.heuristic_score, radar_config.low_priority_llm_min_score)
            item = apply_feedback_to_item(item, feedback)
            if should_hide_by_feedback(item, feedback):
                continue
            if is_relevant(item, effective_keywords(radar_config, feedback)):
                items.append(item)

    if stats:
        stats.rss_items = len(items)
    return sorted(items, key=lambda item: item.published_at, reverse=True)


def read_manual_inbox(
    path: Path,
    now: datetime,
    radar_config: RadarConfig,
    feedback: FeedbackConfig,
) -> tuple[list[CandidateItem], list[str]]:
    if not path.exists():
        return [], []

    items: list[CandidateItem] = []
    seen: set[str] = set()
    processed_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line_items_before = len(items)
        for match in URL_RE.findall(line):
            url = normalize_url(match.rstrip(".,;"))
            if url in seen:
                continue
            seen.add(url)
            manual_tags, manual_source, force_high = parse_manual_line(line)
            host = urlparse(url).netloc.lower()
            kind = "social" if "x.com" in host or "twitter.com" in host else "secondary"
            category = "repo" if "github.com" in host else "manual"
            title = line.strip("- ").strip() or url
            item = CandidateItem(
                title=title[:180],
                source="manual_input",
                url=url,
                published_at=now,
                raw_summary="手动 inbox 链接，程序未抓取正文。",
                source_kind=kind,
                category=category,
                source_priority=5,
                manual_input=True,
                manual_tags=manual_tags,
                manual_source=manual_source,
                force_high=force_high,
                source_tier=source_tier_for_kind(kind),
            )
            item.heuristic_score = heuristic_score(
                title,
                item.raw_summary,
                kind,
                5,
                True,
                radar_config.high_signal_terms,
            )
            item.preference_score = topic_preference_score(
                title,
                item.raw_summary + " " + " ".join(manual_tags),
                effective_topic_weights(radar_config, feedback),
            )
            if force_high:
                item.heuristic_score = 5
            item = apply_feedback_to_item(item, feedback)
            if should_hide_by_feedback(item, feedback):
                continue
            if is_relevant(item, effective_keywords(radar_config, feedback)):
                items.append(item)
        if len(items) > line_items_before:
            processed_lines.append(line)
    log("info", f"manual inbox loaded path={path} links={len(items)}")
    return items, processed_lines


def fallback_analysis(item: CandidateItem) -> ItemAnalysis:
    confidence = min(source_base_confidence(item), source_confidence_cap(item))
    importance = 4 if item.force_high else min(item.heuristic_score, 3)
    summary = item.raw_summary.strip()
    if not summary or summary.lower() in {"no content.", "no content"}:
        summary = f"{item.title}。该条目来自 {item.source}，建议打开原文确认细节。"
    summary = re.sub(r"<[^>]+>", "", summary)
    summary = summary[:150]
    return ItemAnalysis(
        summary_cn=summary,
        tags=(item.manual_tags or ["AI", item.source_kind, "待复查"])[:5],
        importance=importance,
        confidence=confidence,
        action="打开原文快速判断是否需要深入跟进。",
        reason="未调用 LLM，使用来源可信度和标题关键词做保守判断。",
    )


def get_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")


def create_llm_client(api_key: str) -> OpenAI:
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def completion_extra_body() -> dict[str, Any] | None:
    base_url = os.getenv("OPENAI_BASE_URL", "")
    disabled = os.getenv("AI_RADAR_DISABLE_THINKING", "true").lower() not in {"0", "false", "no"}
    if "deepseek.com" in base_url and disabled:
        return {"thinking": {"type": "disabled"}}
    return None


def analyze_item(client: OpenAI, model: str, item: CandidateItem) -> ItemAnalysis:
    prompt = {
        "title": item.title,
        "source": item.source,
        "url": item.url,
        "published_at": item.published_at.isoformat(),
        "raw_summary": item.raw_summary[:3000],
        "source_kind": item.source_kind,
        "category": item.category,
        "source_priority": item.source_priority,
        "source_tier": item.source_tier,
        "preference_score": item.preference_score,
        "heuristic_score": item.heuristic_score,
        "manual_input": item.manual_input,
        "confidence_cap": source_confidence_cap(item),
    }
    extra_body = completion_extra_body()
    request_kwargs: dict[str, Any] = {}
    if extra_body:
        request_kwargs["extra_body"] = extra_body
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        **request_kwargs,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 AI 行业研究助手。只输出 JSON，字段必须是 "
                    "summary_cn, tags, importance, confidence, action, reason。"
                    "summary_cn 控制在 100-150 个中文字符。importance 和 confidence 是 1-5。"
                    "可信度规则：官方博客/官方文档/GitHub/arXiv 可以高；新闻报道/博客中等；"
                    "X 或二手来源最高 3；没有原始链接不能进高优先级。"
                    "用户偏好 Agent、Coding、RAG、Eval、Reasoning，相关内容可适度提高重要性。"
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
    return sorted(
        eligible,
        key=lambda item: (item.heuristic_score, item.preference_score, item.published_at),
        reverse=True,
    )[: radar_config.max_llm_items_per_day]


def analyzed_rank_key(item: AnalyzedItem) -> tuple[int, int, int, int, datetime]:
    return (
        item.analysis.importance,
        item.analysis.confidence,
        item.preference_score,
        -item.source_tier,
        item.published_at,
    )


def event_signature(item: CandidateItem) -> str:
    host = urlparse(item.url).netloc.lower()
    path_parts = [part for part in urlparse(item.url).path.lower().split("/") if part]
    if "github.com" in host and len(path_parts) >= 2:
        repo = "/".join(path_parts[:2])
        release_family = github_release_family(item.title)
        if release_family:
            return f"github:{repo}:{release_family}"
        title = normalize_event_text(item.title)
        if not title or title in {"v", "release"} or re.fullmatch(r"v?\d+", title):
            return f"github:{repo}:release-stream"
        return f"github:{repo}:{title}"
    return normalize_event_text(item.title)


def github_release_family(title: str) -> str:
    package_match = re.match(r"@?([a-z0-9_.-]+/[a-z0-9_.-]+)@v?\d", title, re.IGNORECASE)
    if package_match:
        return f"package:{package_match.group(1).lower()}"
    package_match = re.match(r"([a-z0-9_.-]+)==v?\d", title, re.IGNORECASE)
    if package_match:
        return f"package:{package_match.group(1).lower()}"
    return ""


def normalize_event_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"@?[\w.-]+@v?\d[\w.\-=+%/-]*", " ", text)
    text = re.sub(r"==\s*v?\d[\w.\-=+%/-]*", " ", text)
    text = re.sub(r"\bv?\d+(?:\.\d+)+(?:[-+][a-z0-9.-]+)?\b", " ", text)
    text = re.sub(r"\b(canary|beta|alpha|rc|patch|changes|release|released|version)\b", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(part for part in text.split() if len(part) > 1)


def event_tokens(item: CandidateItem) -> set[str]:
    text = normalize_event_text(f"{item.title} {' '.join(item.manual_tags)}")
    return {token for token in text.split() if token not in {"the", "and", "for", "with", "from", "now"}}


def is_same_event(left: AnalyzedItem, right: AnalyzedItem) -> bool:
    if event_signature(left) and event_signature(left) == event_signature(right):
        return True
    left_tokens = event_tokens(left)
    right_tokens = event_tokens(right)
    if len(left_tokens) < 3 or len(right_tokens) < 3:
        return False
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return overlap >= 0.72


def merge_duplicate_events(items: list[AnalyzedItem]) -> list[AnalyzedItem]:
    groups: list[list[AnalyzedItem]] = []
    for item in sorted(items, key=analyzed_rank_key, reverse=True):
        for group in groups:
            if any(is_same_event(item, existing) for existing in group):
                group.append(item)
                break
        else:
            groups.append([item])

    merged: list[AnalyzedItem] = []
    for group in groups:
        representative = sorted(group, key=analyzed_rank_key, reverse=True)[0]
        related = [item for item in group if item.url != representative.url]
        representative.related_items.extend(
            RelatedItem(
                title=item.title,
                source=item.source,
                url=item.url,
                source_tier=item.source_tier,
                importance=item.analysis.importance,
                confidence=item.analysis.confidence,
            )
            for item in related
        )
        if related:
            sources = "、".join(sorted({item.source for item in related}))
            representative.analysis.reason = f"{representative.analysis.reason}；已合并同事件来源：{sources}。"
            representative.analysis.confidence = min(
                5,
                max(representative.analysis.confidence, max(item.analysis.confidence for item in related)),
            )
        merged.append(representative)
    return sorted(merged, key=analyzed_rank_key, reverse=True)


def topic_label_for_item(item: AnalyzedItem) -> str:
    text = f"{item.title} {' '.join(item.analysis.tags)} {item.analysis.summary_cn}".lower()
    buckets = [
        ("Agent / Coding", ("agent", "agents", "coding", "code", "claude code", "cursor", "windsurf")),
        ("RAG / Knowledge", ("rag", "retrieval", "knowledge", "context", "wiki", "okf")),
        ("Model / Eval", ("model", "benchmark", "eval", "reasoning", "inference")),
        ("Research / Paper", ("paper", "arxiv", "research", "论文", "研究")),
        ("Infra / SDK", ("sdk", "workflow", "release", "repo", "github", "infrastructure")),
    ]
    for label, terms in buckets:
        if any(term in text for term in terms):
            return label
    return "Product / Ecosystem"


def build_theme_clusters(items: list[AnalyzedItem], limit: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[AnalyzedItem]] = {}
    for item in items:
        buckets.setdefault(topic_label_for_item(item), []).append(item)

    clusters: list[dict[str, Any]] = []
    for label, values in buckets.items():
        ranked = sorted(values, key=analyzed_rank_key, reverse=True)
        score = sum(item.analysis.importance + item.preference_score for item in ranked)
        top = ranked[:3]
        reason = "；".join(item.title for item in top[:2])
        clusters.append({"label": label, "score": score, "items": top, "reason": reason})
    return sorted(clusters, key=lambda cluster: cluster["score"], reverse=True)[:limit]


def build_action_suggestions(sections: dict[str, list[AnalyzedItem]]) -> list[str]:
    actions: list[str] = []
    for item in sections["今日必看"][:3]:
        actions.append(f"先读 [{item.title}]({item.url})：{item.analysis.action}")
    for item in sections["值得跟进"][:2]:
        actions.append(f"加入跟进清单 [{item.title}]({item.url})：{item.analysis.reason}")
    if not actions:
        actions.append("今天没有明确高优先级行动；建议只扫三件事和维护性更新。")
    return actions[:5]


def write_daily_note(
    path: Path,
    date: datetime,
    items: list[AnalyzedItem],
    dry_run: bool,
    radar_config: RadarConfig,
    stats: RunStats | None = None,
) -> None:
    date_str = date.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
    sections = build_daily_sections(items, radar_config)
    clusters = build_theme_clusters(items, radar_config.theme_cluster_limit)
    tier_counts = {tier: sum(1 for item in items if item.source_tier == tier) for tier in (1, 2, 3)}
    merged_count = sum(len(item.related_items) for item in items)
    lines = [
        f"# AI Radar 日报 - {date_str}",
        "",
        (
            f"{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M')} 更新 | "
            f"{len(items)} 条 | 必看 {len(sections['今日必看'])} | "
            f"跟进 {len(sections['值得跟进'])} | 合并 {merged_count} 条重复来源"
        ),
        f"来源：Tier 1 {tier_counts[1]} / Tier 2 {tier_counts[2]} / Tier 3 {tier_counts[3]}",
        f"质量分：{stats.quality_score if stats else 100}/100"
        + (" | 需回看" if stats and stats.quality_needs_review else ""),
        "",
    ]
    if stats and stats.content_health != "ok":
        lines.extend(["## 内容健康", ""])
        lines.extend(f"- {reason}" for reason in stats.health_reasons)
        lines.append("")

    if not items:
        lines.append("过去 24 小时没有抓取到新的相关内容。")
    else:
        lines.extend(
            [
                "## 今天主要发生的 3 件事",
                "",
            ]
        )
        if clusters:
            for index, cluster in enumerate(clusters, start=1):
                item_links = "、".join(f"[{item.title}]({item.url})" for item in cluster["items"][:2])
                lines.append(f"{index}. **{cluster['label']}**：{cluster['reason']}。{item_links}")
        else:
            lines.append("暂无足够内容形成主题聚类。")
        lines.append("")

        if sections["今日必看"]:
            lines.append("## 今日必看")
            lines.append("")
            for index, item in enumerate(sections["今日必看"], start=1):
                lines.extend(format_daily_item(index, item))

        lines.append("## 行动建议")
        lines.append("")
        lines.extend(f"- {action}" for action in build_action_suggestions(sections))
        lines.append("")

        lines.extend(["<details>", "<summary>展开详细内容</summary>", ""])
        for section_name in ("值得跟进", "重要论文", "重要 Repo", "产品更新"):
            section_items = sections[section_name]
            if not section_items:
                continue
            lines.append(f"## {section_name}")
            lines.append("")
            for index, item in enumerate(section_items, start=1):
                lines.extend(format_daily_item(index, item))

        maintenance = sections["维护性更新"]
        if maintenance:
            lines.append("## 维护性更新")
            lines.append("")
            for index, item in enumerate(maintenance, start=1):
                lines.append(format_compact_item(index, item))
            lines.append("")

        low_priority = sections["低优先级链接"]
        if low_priority:
            lines.append("## 其他略过")
            lines.append("")
            for index, item in enumerate(low_priority[:10], start=1):
                lines.append(format_compact_item(index, item))
            lines.append("")
        lines.append("</details>")
        lines.append("")

    if dry_run:
        log("info", f"dry-run daily output skipped path={path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_daily_sections(items: list[AnalyzedItem], radar_config: RadarConfig) -> dict[str, list[AnalyzedItem]]:
    sections: dict[str, list[AnalyzedItem]] = {
        "今日必看": [],
        "值得跟进": [],
        "重要论文": [],
        "重要 Repo": [],
        "产品更新": [],
        "维护性更新": [],
        "低优先级链接": [],
    }
    used: set[str] = set()
    ranked = sorted(
        items,
        key=lambda item: (
            item.analysis.importance,
            item.analysis.confidence,
            item.preference_score,
            -item.source_tier,
            item.heuristic_score,
            item.source_priority,
            item.published_at,
        ),
        reverse=True,
    )

    def take(name: str, predicate: Any, limit: int) -> None:
        for item in ranked:
            if item.url in used or not predicate(item):
                continue
            sections[name].append(item)
            used.add(item.url)
            if len(sections[name]) >= limit:
                break

    take("今日必看", lambda item: item.analysis.importance >= 4 and item.analysis.confidence >= 4, radar_config.must_read_limit)
    take("值得跟进", lambda item: item.analysis.importance >= 3, radar_config.follow_up_limit)
    take(
        "重要论文",
        lambda item: item.analysis.importance >= 3 and (item.category == "paper" or item.source_kind == "arxiv"),
        radar_config.category_section_limit,
    )
    take(
        "重要 Repo",
        lambda item: item.analysis.importance >= 3 and (item.category == "repo" or item.source_kind == "github"),
        radar_config.category_section_limit,
    )
    take("产品更新", lambda item: item.analysis.importance >= 3 and item.category == "product", radar_config.category_section_limit)
    take(
        "维护性更新",
        lambda item: item.analysis.importance <= 2 and (item.category == "repo" or item.source_kind == "github"),
        radar_config.category_section_limit,
    )

    sections["低优先级链接"] = [item for item in ranked if item.url not in used]
    return sections


def format_daily_item(index: int, item: AnalyzedItem) -> list[str]:
    published = item.published_at.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
    tags = "、".join(item.analysis.tags)
    lines = [
        f"### {index}. [{item.title}]({item.url})",
        "",
        f"{item.analysis.summary_cn}",
        "",
        f"- 来源：{item.source} | {published} | 重要性 {item.analysis.importance}/5 | 可信度 {item.analysis.confidence}/5",
        f"- 标签：{tags}",
        f"- 为什么看：{item.analysis.reason}",
        f"- 建议动作：{item.analysis.action}",
    ]
    if item.related_items:
        related = "；".join(f"[{related.title}]({related.url})" for related in item.related_items[:3])
        more = f" 等 {len(item.related_items)} 条" if len(item.related_items) > 3 else ""
        lines.append(f"- 合并来源：{related}{more}")
    lines.append("")
    return lines


def format_compact_item(index: int, item: AnalyzedItem) -> str:
    reason = low_priority_reason(item)
    return (
        f"{index}. [{item.title}]({item.url}) - {item.source} | "
        f"{item.analysis.importance}/5 | 略过原因：{reason}"
    )


def low_priority_reason(item: AnalyzedItem) -> str:
    reasons: list[str] = []
    if item.analysis.importance <= 2:
        reasons.append("重要性低")
    if item.analysis.confidence <= 3:
        reasons.append("来源可信度或信息完整度不足")
    if item.source_tier >= 3:
        reasons.append("Tier 3 二手或趋势信息")
    if item.preference_score == 0:
        reasons.append("与个人偏好主题弱相关")
    if "依赖" in item.analysis.summary_cn or "补丁" in item.analysis.summary_cn or "canary" in item.title.lower():
        reasons.append("偏常规版本/依赖更新")
    return "、".join(reasons[:3]) or "排序分低于主列表内容"


def archive_processed_inbox(inbox_path: Path, processed_path: Path, processed_lines: list[str], dry_run: bool) -> None:
    if dry_run or not processed_lines or not inbox_path.exists():
        return
    original_lines = inbox_path.read_text(encoding="utf-8").splitlines()
    processed_set = set(processed_lines)
    remaining_lines = [line for line in original_lines if line not in processed_set]
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    with processed_path.open("a", encoding="utf-8") as f:
        f.write(f"\n## Processed {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')} 北京时间\n\n")
        for line in processed_lines:
            f.write(f"- {line.strip('- ').strip()}\n")
    inbox_path.write_text("\n".join(remaining_lines).rstrip() + "\n", encoding="utf-8")


def write_daily_archive(path: Path, items: list[AnalyzedItem], dry_run: bool) -> None:
    if dry_run:
        log("info", f"dry-run archive output skipped path={path}")
        return
    save_json(path, [item.model_dump(mode="json") for item in items])


def write_run_summary(path: Path, stats: RunStats, radar_config: RadarConfig, dry_run: bool) -> None:
    summary = stats.model_dump(mode="json")
    summary["estimated_llm_tokens"] = stats.estimated_tokens(radar_config)
    log(
        "info",
        "run summary "
        f"sources={stats.sources_success}/{stats.sources_total} "
        f"failed_sources={stats.sources_failed} warnings={stats.source_warnings} "
        f"rss_items={stats.rss_items} manual_items={stats.manual_items} "
        f"candidates={stats.candidates_total} new={stats.candidates_new} selected={stats.candidates_selected} "
        f"llm_planned={stats.llm_planned} llm_ok={stats.llm_succeeded} llm_failed={stats.llm_failed} "
        f"fallback={stats.fallback_items} estimated_tokens={summary['estimated_llm_tokens']} "
        f"output_items={stats.output_items} content_health={stats.content_health} "
        f"quality_score={stats.quality_score} needs_review={stats.quality_needs_review} "
        f"lookback_hours={stats.lookback_hours} backfill={stats.backfill_items} "
        f"output={stats.output_path}",
    )
    if dry_run:
        log("info", f"dry-run run summary output skipped path={path}")
        return
    save_json(path, summary)


def load_archive_items(path: Path) -> list[AnalyzedItem]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [AnalyzedItem.model_validate(item) for item in data]


def merge_with_existing_daily_archive(path: Path, new_items: list[AnalyzedItem]) -> list[AnalyzedItem]:
    if not path.exists():
        return new_items
    combined: dict[str, AnalyzedItem] = {}
    for item in load_archive_items(path):
        combined[item.url] = item
    for item in new_items:
        combined[item.url] = item
    return sorted(combined.values(), key=analyzed_rank_key, reverse=True)


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
    extra_body = completion_extra_body()
    request_kwargs: dict[str, Any] = {}
    if extra_body:
        request_kwargs["extra_body"] = extra_body
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        **request_kwargs,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 AI 行业周报编辑。只输出 JSON，字段为 overview_cn, trend_judgement, "
                    "tools_to_try, research_questions, watchlist_next_week。overview_cn 控制在 200-300 字，"
                    "其余字段都是 3-5 条中文字符串数组。"
                ),
            },
            {
                "role": "user",
                "content": "请基于过去 7 天高优先级条目，写一份偏决策材料的周报摘要，突出趋势判断、可试用工具、值得深挖的问题和下周观察清单。\n\n"
                f"{json.dumps(payload, ensure_ascii=False)}",
            },
        ],
    )
    return WeeklySummary.model_validate_json(response.choices[0].message.content or "{}")


def fallback_weekly_summary(items: list[AnalyzedItem]) -> WeeklySummary:
    top_tags: dict[str, int] = {}
    for item in items:
        for tag in item.analysis.tags:
            top_tags[tag] = top_tags.get(tag, 0) + 1
    tags = "、".join(tag for tag, _ in sorted(top_tags.items(), key=lambda x: x[1], reverse=True)[:5])
    return WeeklySummary(
        overview_cn=f"本周共汇总 {len(items)} 条高优先级内容。主要主题集中在 {tags or '产品更新、论文和开源项目'}。请优先查看趋势判断、值得试用工具和 Deep Research 候选。",
        trend_judgement=[
            "优先关注官方发布和 GitHub release 中反复出现的能力方向。",
            "论文和 repo 如果同时出现相似主题，通常值得进入下周观察。",
            "手动 inbox 中被标记 !high 的内容应优先人工复核。",
        ],
        tools_to_try=[item.title for item in items if item.category in {"tool", "repo"}][:3],
        research_questions=[f"{item.title} 背后的技术路线是否会影响现有 Agent/RAG 工作流？" for item in items[:3]],
        watchlist_next_week=[item.source for item in items[:5]],
    )


def load_archive_range(archive_dir: Path, start_date: Any, days: int) -> list[AnalyzedItem]:
    items: list[AnalyzedItem] = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        items.extend(load_archive_items(archive_dir / f"{day.isoformat()}.json"))
    return items


def weekly_theme_counts(items: list[AnalyzedItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        label = topic_label_for_item(item)
        counts[label] = counts.get(label, 0) + 1
    return counts


def weekly_theme_changes(current_items: list[AnalyzedItem], previous_items: list[AnalyzedItem]) -> list[str]:
    current = weekly_theme_counts(current_items)
    previous = weekly_theme_counts(previous_items)
    labels = sorted(set(current) | set(previous), key=lambda label: current.get(label, 0), reverse=True)
    rows: list[str] = []
    for label in labels[:5]:
        delta = current.get(label, 0) - previous.get(label, 0)
        direction = "增加" if delta > 0 else "减少" if delta < 0 else "持平"
        rows.append(f"{label}：本周 {current.get(label, 0)} 条，上周 {previous.get(label, 0)} 条，{direction} {abs(delta)}。")
    return rows or ["暂无足够历史数据判断主题变化。"]


def weekly_continuous_themes(items: list[AnalyzedItem]) -> list[str]:
    days_by_theme: dict[str, set[str]] = {}
    for item in items:
        day = item.published_at.astimezone(BEIJING_TZ).date().isoformat()
        days_by_theme.setdefault(topic_label_for_item(item), set()).add(day)
    rows = [
        f"{label}：连续/多日出现 {len(days)} 天。"
        for label, days in sorted(days_by_theme.items(), key=lambda row: len(row[1]), reverse=True)
        if len(days) >= 2
    ]
    return rows[:5] or ["暂无连续多日出现的明确方向。"]


def weekly_noise_items(items: list[AnalyzedItem]) -> list[str]:
    noise = [
        item
        for item in items
        if item.analysis.importance <= 2 or (item.source_kind.lower() == "github" and is_release_noise(item))
    ]
    rows = [
        f"{item.source}：{item.title}（{low_priority_reason(item)}）"
        for item in sorted(noise, key=lambda item: item.published_at, reverse=True)[:6]
    ]
    return rows or ["本周没有明显需要单独标记的噪音。"]


def weekly_watch_sources(items: list[AnalyzedItem]) -> list[str]:
    by_source: dict[str, dict[str, int]] = {}
    for item in items:
        record = by_source.setdefault(item.source, {"effective": 0, "must_read": 0, "noise": 0})
        if item.analysis.importance >= 3 and item.analysis.confidence >= 3:
            record["effective"] += 1
        if item.analysis.importance >= 4 and item.analysis.confidence >= 4:
            record["must_read"] += 1
        if item.analysis.importance <= 2 or (item.source_kind.lower() == "github" and is_release_noise(item)):
            record["noise"] += 1
    ranked = sorted(
        by_source.items(),
        key=lambda row: (row[1]["must_read"], row[1]["effective"], -row[1]["noise"]),
        reverse=True,
    )
    rows = [
        f"{source}：必看 {stats['must_read']}，有效 {stats['effective']}，噪音 {stats['noise']}。"
        for source, stats in ranked[:5]
        if stats["effective"] or stats["must_read"]
    ]
    return rows or ["暂无足够数据推荐下周重点来源。"]


def deep_research_prompt(item: AnalyzedItem) -> str:
    return (
        "请对以下 AI/LLM/Agent/RAG/Coding 主题做 Deep Research：\n"
        f"标题：{item.title}\n"
        f"来源：{item.source}\n"
        f"链接：{item.url}\n"
        f"摘要：{item.analysis.summary_cn}\n"
        "请重点回答：1. 这件事为什么重要；2. 和现有方案相比有什么变化；"
        "3. 对产品/研发/投资判断有什么影响；4. 有哪些一周内可以验证的行动。"
    )


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
    all_week_items = load_archive_range(archive_dir, start_date, 7)
    previous_week_items = load_archive_range(archive_dir, start_date - timedelta(days=7), 7)
    items = [item for item in all_week_items if item.analysis.importance >= 4]
    sections = build_weekly_sections(items)
    sections["本周 Deep Research 候选"] = select_deep_research_candidates(
        items, radar_config.deep_research_candidates_per_week
    )
    theme_changes = weekly_theme_changes(all_week_items, previous_week_items)
    continuous_themes = weekly_continuous_themes(all_week_items)
    noise_rows = weekly_noise_items(all_week_items)
    watch_sources = weekly_watch_sources(all_week_items)
    summary = fallback_weekly_summary(items)
    api_key = get_api_key()
    if api_key and items and not dry_run:
        model = os.getenv("AI_RADAR_WEEKLY_MODEL", radar_config.weekly_summary_model)
        try:
            log("info", f"summarizing weekly model={model}")
            summary = summarize_weekly(create_llm_client(api_key), model, items)
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
        "## 本周趋势判断",
        "",
        *format_bullets(summary.trend_judgement),
        "",
        "## 本周主题变化",
        "",
        *format_bullets(theme_changes),
        "",
        "## 连续出现的方向",
        "",
        *format_bullets(continuous_themes),
        "",
        "## 本周噪音",
        "",
        *format_bullets(noise_rows),
        "",
        "## 值得试用的 3 个工具",
        "",
        *format_bullets(summary.tools_to_try[:3]),
        "",
        "## 值得深挖的研究问题",
        "",
        *format_bullets(summary.research_questions[:5]),
        "",
        "## 下周观察清单",
        "",
        *format_bullets(summary.watchlist_next_week[:5]),
        "",
        "## 下周应盯的源 / 项目",
        "",
        *format_bullets(watch_sources),
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
            if section == "本周 Deep Research 候选":
                lines.extend(["```text", deep_research_prompt(item), "```", ""])
    if dry_run:
        log("info", f"dry-run weekly output skipped path={path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def format_bullets(items: list[str]) -> list[str]:
    if not items:
        return ["- 暂无。"]
    return [f"- {item}" for item in items]


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


def select_candidate_items(
    candidates_by_url: dict[str, CandidateItem],
    cache: dict[str, Any],
    sources: SourcesConfig,
    radar_config: RadarConfig,
    ignore_cache: bool,
) -> tuple[list[CandidateItem], int]:
    if ignore_cache:
        items = list(candidates_by_url.values())
    else:
        items = [item for item in candidates_by_url.values() if item.url not in cache["seen_urls"]]
    before_prefilter = len(items)
    selected = prefilter_candidates(items, sources, radar_config)
    return sorted(selected, key=candidate_rank_key, reverse=True), before_prefilter


def backfill_from_cache(
    candidates_by_url: dict[str, CandidateItem],
    cache: dict[str, Any],
    selected: list[CandidateItem],
    sources: SourcesConfig,
    radar_config: RadarConfig,
) -> list[CandidateItem]:
    selected_urls = {item.url for item in selected}
    cached = [
        item
        for item in candidates_by_url.values()
        if item.url in cache["seen_urls"] and item.url not in selected_urls
    ]
    backfill = prefilter_candidates(cached, sources, radar_config)
    needed = max(0, radar_config.min_daily_items - len(selected))
    limit = min(radar_config.backfill_limit, needed)
    for item in backfill[:limit]:
        item.backfilled_from_cache = True
    return selected + backfill[:limit]


def daily_quality_score(stats: RunStats, items: list[AnalyzedItem], radar_config: RadarConfig) -> tuple[int, dict[str, Any]]:
    output_count = len(items)
    must_read_count = sum(1 for item in items if item.analysis.importance >= 4 and item.analysis.confidence >= 4)
    low_quality_release_count = sum(
        1
        for item in items
        if item.source_kind.lower() == "github" and (is_release_noise(item) or item.analysis.importance <= 2)
    )
    release_ratio = low_quality_release_count / output_count if output_count else 0.0

    score = 100
    if output_count < radar_config.min_daily_items:
        shortfall = radar_config.min_daily_items - output_count
        score -= min(40, shortfall * 8)
    if must_read_count == 0:
        score -= 15
    elif must_read_count < min(2, radar_config.must_read_limit):
        score -= 6
    if release_ratio > 0.35:
        score -= min(25, int((release_ratio - 0.35) * 100))
    if stats.sources_total:
        source_problem_ratio = (stats.sources_failed + stats.source_warnings) / stats.sources_total
        score -= min(20, int(source_problem_ratio * 40))
    if stats.fallback_lookback_used:
        score -= 5
    if stats.backfill_items:
        score -= 5

    signals = {
        "output_items": output_count,
        "min_daily_items": radar_config.min_daily_items,
        "must_read_items": must_read_count,
        "low_quality_release_items": low_quality_release_count,
        "low_quality_release_ratio": round(release_ratio, 3),
        "sources_failed": stats.sources_failed,
        "source_warnings": stats.source_warnings,
        "fallback_lookback_used": stats.fallback_lookback_used,
        "backfill_items": stats.backfill_items,
    }
    return max(0, min(100, score)), signals


def update_content_health(stats: RunStats, items: list[AnalyzedItem], radar_config: RadarConfig) -> None:
    stats.output_items = len(items)
    stats.quality_score, stats.quality_signals = daily_quality_score(stats, items, radar_config)
    stats.must_read_items = int(stats.quality_signals["must_read_items"])
    stats.low_quality_release_items = int(stats.quality_signals["low_quality_release_items"])
    stats.low_quality_release_ratio = float(stats.quality_signals["low_quality_release_ratio"])
    stats.quality_needs_review = stats.quality_score < radar_config.quality_score_threshold
    reasons: list[str] = []
    if len(items) < radar_config.min_daily_items:
        stats.content_health = "low"
        reasons.append(f"主列表条目数 {len(items)} 低于阈值 {radar_config.min_daily_items}。")
    else:
        stats.content_health = "ok"
    if stats.must_read_items == 0:
        reasons.append("今日必看为空，可能需要扩大回看或提高高贡献源权重。")
    if stats.low_quality_release_ratio > 0.35:
        reasons.append(
            f"低质量 release 占比 {stats.low_quality_release_ratio:.0%} 偏高，下一轮会压低噪音源限额。"
        )
    if stats.fallback_lookback_used:
        reasons.append(f"新内容偏少，已自动把回看窗口扩展到 {stats.lookback_hours} 小时。")
    if stats.backfill_items:
        reasons.append(f"从近期缓存补入 {stats.backfill_items} 条，避免重复运行后日报变空。")
    if stats.rss_items < radar_config.min_daily_items:
        reasons.append(f"RSS 相关候选只有 {stats.rss_items} 条，可能是信息源当日更新少。")
    if stats.source_warnings or stats.sources_failed:
        reasons.append(f"信息源抓取异常：失败 {stats.sources_failed} 个，警告 {stats.source_warnings} 个。")
    if stats.quality_needs_review:
        stats.content_health = "low"
        reasons.append(
            f"质量分 {stats.quality_score}/100 低于阈值 {radar_config.quality_score_threshold}，已标记下次运行回看。"
        )
    if not reasons:
        reasons.append(f"内容量正常；质量分 {stats.quality_score}/100，筛选、去重和来源抓取未发现明显异常。")
    stats.health_reasons = reasons


def update_source_state(
    source_state: dict[str, Any],
    sources: SourcesConfig,
    items: list[AnalyzedItem],
    stats: RunStats,
    radar_config: RadarConfig,
    now: datetime,
) -> None:
    today = now.astimezone(BEIJING_TZ).date().isoformat()
    source_state["last_quality"] = {
        "date": today,
        "score": stats.quality_score,
        "needs_review": stats.quality_needs_review,
        "content_health": stats.content_health,
        "reasons": stats.health_reasons,
    }
    source_state.setdefault("sources", {})
    output_by_source: dict[str, list[AnalyzedItem]] = {}
    for item in items:
        output_by_source.setdefault(item.source, []).append(item)
    failure_names = set(stats.source_failures)
    warning_names = set(stats.source_warning_names)
    configured_names = {source.name for source in sources.sources}
    for source_name in sorted(configured_names | set(output_by_source) | failure_names | warning_names):
        source_items = output_by_source.get(source_name, [])
        effective = sum(1 for item in source_items if item.analysis.importance >= 3 and item.analysis.confidence >= 3)
        must_read = sum(1 for item in source_items if item.analysis.importance >= 4 and item.analysis.confidence >= 4)
        noise = sum(
            1
            for item in source_items
            if item.source_kind.lower() == "github" and (is_release_noise(item) or item.analysis.importance <= 2)
        )
        row = {
            "date": today,
            "output_items": len(source_items),
            "effective_items": effective,
            "must_read_items": must_read,
            "noise_items": noise,
            "failed": 1 if source_name in failure_names else 0,
            "warning": 1 if source_name in warning_names else 0,
        }
        record = source_state["sources"].setdefault(source_name, {"history": []})
        history = [entry for entry in record.get("history", []) if entry.get("date") != today]
        history.append(row)
        cutoff = now.astimezone(BEIJING_TZ).date() - timedelta(days=radar_config.source_state_keep_days - 1)
        kept = []
        for entry in history:
            try:
                entry_date = datetime.fromisoformat(str(entry.get("date"))).date()
            except ValueError:
                continue
            if entry_date >= cutoff:
                kept.append(entry)
        record["history"] = sorted(kept, key=lambda entry: entry["date"])
        record.update(source_state_metrics(source_state, source_name, now))
        if source_name in stats.source_adjustments:
            record["current_adjusted_daily_limit"] = stats.source_adjustments[source_name]


def run_daily(args: argparse.Namespace, radar_config: RadarConfig) -> None:
    load_dotenv()
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)
    note_date = now.astimezone(BEIJING_TZ)
    stats = RunStats()
    stats.lookback_hours = args.hours

    sources = load_sources(args.sources)
    feedback = load_feedback(args.feedback)
    source_state = load_source_state(args.source_state)
    if (
        source_state.get("last_quality", {}).get("needs_review")
        and radar_config.quality_retry_lookback_hours > stats.lookback_hours
    ):
        stats.lookback_hours = radar_config.quality_retry_lookback_hours
        since = now - timedelta(hours=stats.lookback_hours)
        stats.fallback_lookback_used = True
        log("info", f"previous daily quality was low; using lookback_hours={stats.lookback_hours}")
    sources = apply_source_state_limits(sources, source_state, radar_config, now, stats)
    cache = load_cache(args.cache)
    prune_cache(cache, now - timedelta(days=radar_config.cache_keep_days))

    rss_items = fetch_recent_items(sources, since, radar_config, feedback, stats)
    manual_items, processed_manual_lines = read_manual_inbox(args.inbox, now, radar_config, feedback)
    stats.manual_items = len(manual_items)
    candidates_by_url = {item.url: item for item in rss_items + manual_items}
    new_items, before_prefilter = select_candidate_items(
        candidates_by_url,
        cache,
        sources,
        radar_config,
        args.ignore_cache,
    )
    if len(new_items) < radar_config.min_daily_items and radar_config.fallback_lookback_hours > stats.lookback_hours:
        fallback_since = now - timedelta(hours=radar_config.fallback_lookback_hours)
        fallback_rss_items = fetch_recent_items(sources, fallback_since, radar_config, feedback, None)
        candidates_by_url = {item.url: item for item in fallback_rss_items + manual_items}
        new_items, before_prefilter = select_candidate_items(
            candidates_by_url,
            cache,
            sources,
            radar_config,
            args.ignore_cache,
        )
        stats.fallback_lookback_used = True
        stats.lookback_hours = radar_config.fallback_lookback_hours
        stats.rss_items = len(fallback_rss_items)

    if len(new_items) < radar_config.min_daily_items and not args.ignore_cache:
        before_backfill = len(new_items)
        new_items = backfill_from_cache(candidates_by_url, cache, new_items, sources, radar_config)
        stats.backfill_items = len(new_items) - before_backfill

    stats.candidates_total = len(candidates_by_url)
    stats.candidates_new = before_prefilter
    stats.candidates_selected = len(new_items)
    log(
        "info",
        f"candidates total={len(candidates_by_url)} new={before_prefilter} selected={len(new_items)} "
        f"lookback_hours={stats.lookback_hours} backfill={stats.backfill_items}",
    )

    llm_items = set(item.url for item in select_llm_items(new_items, radar_config))
    stats.llm_planned = len(llm_items)
    client: OpenAI | None = None
    if llm_items and not args.dry_run:
        api_key = get_api_key()
        if not api_key:
            log("warn", "OPENAI_API_KEY or DEEPSEEK_API_KEY missing; using fallback summaries")
        else:
            client = create_llm_client(api_key)

    analyzed: list[AnalyzedItem] = []
    model = os.getenv("AI_RADAR_DAILY_MODEL", os.getenv("AI_RADAR_MODEL", radar_config.daily_summary_model))
    for item in new_items:
        use_llm = item.url in llm_items and client is not None
        if use_llm:
            log("info", f"analyzing title={item.title[:80]}")
            try:
                analysis = analyze_item(client, model, item)
                stats.llm_succeeded += 1
            except Exception as exc:
                log("warn", f"OpenAI failed url={item.url} error={exc}")
                analysis = fallback_analysis(item)
                stats.llm_failed += 1
                stats.fallback_items += 1
        else:
            analysis = fallback_analysis(item)
            stats.fallback_items += 1
        analysis = apply_release_noise_rules(item, analysis, radar_config)
        if item.backfilled_from_cache:
            analysis.reason = f"{analysis.reason}；今日新内容偏少，从近期缓存中补入供回看。"
        analyzed.append(AnalyzedItem(**item.model_dump(), analysis=analysis))
        cache["seen_urls"][item.url] = now.isoformat()

    analyzed = sorted(
        analyzed,
        key=analyzed_rank_key,
        reverse=True,
    )
    output_path = args.output_dir / f"{note_date.strftime('%Y-%m-%d')}.md"
    archive_path = args.archive_dir / f"{note_date.strftime('%Y-%m-%d')}.json"
    summary_path = args.run_summary_dir / f"{note_date.strftime('%Y-%m-%d')}.json"
    if analyzed:
        analyzed = merge_with_existing_daily_archive(archive_path, analyzed)
    analyzed = merge_duplicate_events(analyzed)
    high_count = 0
    for item in analyzed:
        if item.analysis.importance >= 4:
            high_count += 1
            if high_count > radar_config.high_priority_limit:
                item.analysis.importance = 3
                item.analysis.reason = f"{item.analysis.reason}；因每日高优先级上限降级。"
    analyzed = sorted(analyzed, key=analyzed_rank_key, reverse=True)
    analyzed = analyzed[: radar_config.max_daily_items]
    update_content_health(stats, analyzed, radar_config)

    stats.output_path = str(output_path)
    stats.archive_path = str(archive_path)
    if not analyzed and output_path.exists():
        log("info", f"no new items; kept existing daily note path={output_path}")
    else:
        write_daily_note(output_path, note_date, analyzed, args.dry_run, radar_config, stats)
        write_daily_archive(archive_path, analyzed, args.dry_run)

    if not args.dry_run:
        save_json(args.cache, cache)
        update_source_state(source_state, sources, analyzed, stats, radar_config, now)
        save_json(args.source_state, source_state)
        archive_processed_inbox(args.inbox, args.processed_inbox, processed_manual_lines, args.dry_run)
    write_run_summary(summary_path, stats, radar_config, args.dry_run)
    log("info", f"daily done items={len(analyzed)} output={output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate AI radar markdown from RSS feeds and manual links.")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--sources", type=Path, default=Path("sources.yaml"))
    parser.add_argument("--feedback", type=Path, default=Path("feedback.yaml"))
    parser.add_argument("--cache", type=Path, default=Path("data/cache.json"))
    parser.add_argument("--source-state", type=Path, default=Path("data/source-state.json"))
    parser.add_argument("--inbox", type=Path, default=Path("inbox/links.md"))
    parser.add_argument("--processed-inbox", type=Path, default=Path("inbox/processed.md"))
    parser.add_argument("--output-dir", type=Path, default=Path("notes/daily"))
    parser.add_argument("--weekly-output-dir", type=Path, default=Path("notes/weekly"))
    parser.add_argument("--archive-dir", type=Path, default=Path("data/items"))
    parser.add_argument("--run-summary-dir", type=Path, default=Path("data/run-summary"))
    parser.add_argument("--hours", type=int, default=36)
    parser.add_argument("--ignore-cache", action="store_true", help="Analyze candidates even if their URLs are already cached.")
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
