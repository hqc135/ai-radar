from __future__ import annotations

import os
import sys
from pathlib import Path

import feedparser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_radar.main import load_config, load_sources  # noqa: E402


def ok(message: str) -> None:
    print(f"[ai-radar-check] [OK] {message}")


def warn(message: str) -> None:
    print(f"[ai-radar-check] [WARN] {message}")


def fail(message: str) -> None:
    print(f"[ai-radar-check] [FAIL] {message}")
    raise SystemExit(1)


def main() -> None:
    sources_path = ROOT / "sources.yaml"
    config_path = ROOT / "config.yaml"
    output_dir = ROOT / "notes" / "daily"
    weekly_dir = ROOT / "notes" / "weekly"
    cache_path = ROOT / "data" / "cache.json"

    try:
        config = load_config(config_path)
        ok(f"config.yaml readable max_llm_items_per_day={config.max_llm_items_per_day}")
    except Exception as exc:
        fail(f"config.yaml invalid: {exc}")

    try:
        sources = load_sources(sources_path)
        enabled = [source for source in sources.sources if source.enabled]
        if not enabled:
            fail("sources.yaml has no enabled sources")
        ok(f"sources.yaml readable enabled_sources={len(enabled)}")
    except Exception as exc:
        fail(f"sources.yaml invalid: {exc}")

    if os.getenv("OPENAI_API_KEY"):
        ok("OPENAI_API_KEY is set")
    else:
        warn("OPENAI_API_KEY is not set; daily run will use fallback summaries or fail if strict usage is added later")

    output_dir.mkdir(parents=True, exist_ok=True)
    weekly_dir.mkdir(parents=True, exist_ok=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    ok(f"output directories ready daily={output_dir} weekly={weekly_dir}")

    for source in enabled[:2]:
        feed = feedparser.parse(str(source.url))
        entries = getattr(feed, "entries", [])
        if entries:
            ok(f"test fetch source={source.name} entries={len(entries)}")
        else:
            warn(f"test fetch returned no entries source={source.name}")

    ok("check complete")


if __name__ == "__main__":
    main()
