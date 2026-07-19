from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AlertEvaluation:
    current_path: Path | None
    current: dict[str, Any]
    previous_path: Path | None
    previous: dict[str, Any]
    consecutive_low_quality: bool
    api_failure: bool


def is_low_quality(summary: dict[str, Any]) -> bool:
    return bool(summary.get("quality_needs_review")) or summary.get("content_health") != "ok"


def evaluate_alerts(summary_dir: Path) -> AlertEvaluation:
    summaries = sorted(summary_dir.glob("*.json"))
    if not summaries:
        return AlertEvaluation(None, {}, None, {}, False, False)

    current_path = summaries[-1]
    current = json.loads(current_path.read_text(encoding="utf-8"))
    previous_path = summaries[-2] if len(summaries) >= 2 else None
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path else {}

    consecutive_dates = False
    if previous_path:
        consecutive_dates = (
            date.fromisoformat(current_path.stem) - date.fromisoformat(previous_path.stem)
        ).days == 1

    return AlertEvaluation(
        current_path=current_path,
        current=current,
        previous_path=previous_path,
        previous=previous,
        consecutive_low_quality=(
            is_low_quality(current)
            and is_low_quality(previous)
            and consecutive_dates
        ),
        api_failure=int(current.get("llm_failed", 0)) > 0,
    )


def workflow_run_url() -> str:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY", "hqc135/ai-radar")
    run_id = os.getenv("GITHUB_RUN_ID", "unknown")
    return f"{server}/{repository}/actions/runs/{run_id}"


def write_outputs(evaluation: AlertEvaluation, output_file: Path, temp_dir: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    if not evaluation.current_path:
        output_file.write_text("needs_issue=false\napi_failure=false\n", encoding="utf-8")
        return

    path = evaluation.current_path
    data = evaluation.current
    previous_path = evaluation.previous_path
    previous_data = evaluation.previous

    quality_body = [
        "Daily quality was low for two consecutive days.",
        "",
        f"Current: `{path.name}`",
        f"- quality_score: {data.get('quality_score', 'unknown')}",
        f"- content_health: {data.get('content_health', 'unknown')}",
        f"- output_items: {data.get('output_items', 'unknown')}",
        f"- must_read_items: {data.get('must_read_items', 'unknown')}",
        f"- low_quality_release_ratio: {data.get('low_quality_release_ratio', 'unknown')}",
        f"- sources_failed: {data.get('sources_failed', 'unknown')}",
        "",
        f"Previous: `{previous_path.name if previous_path else 'none'}`",
        f"- quality_score: {previous_data.get('quality_score', 'unknown')}",
        f"- content_health: {previous_data.get('content_health', 'unknown')}",
        "",
        "Current reasons:",
        *(f"- {reason}" for reason in data.get("health_reasons", [])),
        "",
        f"Workflow run: {workflow_run_url()}",
    ]
    quality_body_path = temp_dir / f"{path.stem}-quality-issue.md"
    quality_body_path.write_text("\n".join(quality_body) + "\n", encoding="utf-8")

    api_body = [
        f"Daily LLM API calls failed in `{path.name}`.",
        "",
        f"- llm_planned: {data.get('llm_planned', 'unknown')}",
        f"- llm_succeeded: {data.get('llm_succeeded', 'unknown')}",
        f"- llm_failed: {data.get('llm_failed', 'unknown')}",
        f"- fallback_items: {data.get('fallback_items', 'unknown')}",
        "",
        f"Workflow run: {workflow_run_url()}",
    ]
    api_body_path = temp_dir / f"{path.stem}-api-issue.md"
    api_body_path.write_text("\n".join(api_body) + "\n", encoding="utf-8")

    outputs = [
        f"needs_issue={str(evaluation.consecutive_low_quality).lower()}",
        f"api_failure={str(evaluation.api_failure).lower()}",
        f"date={path.stem}",
        f"score={data.get('quality_score', 'unknown')}",
        f"health={data.get('content_health', 'unknown')}",
        f"body_path={quality_body_path.as_posix()}",
        f"api_body_path={api_body_path.as_posix()}",
    ]
    output_file.write_text("\n".join(outputs) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate daily quality and API alert conditions.")
    parser.add_argument("--summary-dir", type=Path, default=Path("data/run-summary"))
    parser.add_argument("--output-file", type=Path, default=Path(os.environ.get("GITHUB_OUTPUT", "alerts.out")))
    parser.add_argument("--temp-dir", type=Path, default=Path(os.environ.get("RUNNER_TEMP", ".")))
    args = parser.parse_args()
    write_outputs(evaluate_alerts(args.summary_dir), args.output_file, args.temp_dir)


if __name__ == "__main__":
    main()
