from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from .ai import OpenAIIntentAnalyzer, RuleIntentAnalyzer
from .config import load_config
from .db import Database
from .keywords import generate_keywords
from .notify import configured_notifiers
from .scoring import score_keyword
from .sitemap import discover_game_pages
from .trends import DataForSEOTrendProvider, EmptyTrendProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Game SEO trend monitor MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run sitemap discovery and keyword scoring")
    run_parser.add_argument("--config", default=None, help="Path to JSON config file")
    run_parser.add_argument("--limit", type=int, default=100, help="Max keywords to process")
    run_parser.add_argument("--dry-run", action="store_true", help="Skip external trend, AI, and notification calls")
    run_parser.add_argument("--no-notify", action="store_true", help="Do not send webhook notifications")
    run_parser.add_argument("--max-sitemaps", type=int, default=8, help="Max child sitemaps per sitemap index")
    run_parser.add_argument("--skip-discovery", action="store_true", help="Skip sitemap discovery and process existing keywords")
    run_parser.add_argument("--request-delay", type=float, default=1.0, help="Seconds to wait between trend requests")
    run_parser.add_argument("--stop-on-error", action="store_true", help="Stop the batch when a keyword request fails")

    stats_parser = subparsers.add_parser("stats", help="Show local database stats")
    stats_parser.add_argument("--config", default=None, help="Path to JSON config file")

    check_parser = subparsers.add_parser("dataforseo-check", help="Check DataForSEO API credentials without trend query cost")
    check_parser.add_argument("--config", default=None, help="Path to JSON config file")

    export_parser = subparsers.add_parser("export", help="Export recent trend results for manual review")
    export_parser.add_argument("--config", default=None, help="Path to JSON config file")
    export_parser.add_argument("--limit", type=int, default=50, help="Max trend results to export")
    export_parser.add_argument("--format", choices=["csv", "md"], default="csv", help="Export format")
    export_parser.add_argument("--output", default=None, help="Output file path")

    args = parser.parse_args(argv)
    if args.command == "run":
        return run_command(args)
    if args.command == "stats":
        return stats_command(args)
    if args.command == "dataforseo-check":
        return dataforseo_check_command(args)
    if args.command == "export":
        return export_command(args)
    return 1


def run_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.database_path)
    db.migrate()

    total_pages = 0
    total_candidates = 0
    if args.skip_discovery:
        print("[site] skipped sitemap discovery")
    else:
        for site in config.sites:
            print(f"[site] reading {site.name}: {site.sitemap_url}")
            try:
                pages = discover_game_pages(site, max_sitemaps=args.max_sitemaps)
            except Exception as exc:
                print(f"[site-error] {site.name}: {exc}")
                continue
            inserted, updated = db.upsert_pages(pages)
            total_pages += len(pages)
            candidates = []
            for page in pages:
                candidates.extend(generate_keywords(page, max_keywords=config.defaults.max_keywords_per_game))
            added, seen = db.upsert_keywords(candidates)
            total_candidates += len(candidates)
            print(
                f"[site] {site.name}: pages={len(pages)} inserted={inserted} "
                f"updated={updated} keywords_new={added} keywords_seen={seen}"
            )

    if args.dry_run:
        stats = db.stats()
        print(f"[dry-run] pages_seen={total_pages} candidates_seen={total_candidates} stats={stats}")
        db.close()
        return 0

    provider = DataForSEOTrendProvider.from_env(config.defaults) or EmptyTrendProvider()
    analyzer = OpenAIIntentAnalyzer.from_env() or RuleIntentAnalyzer()
    notifiers = [] if args.no_notify else configured_notifiers()
    rows = db.get_keywords_for_processing(args.limit)
    print(f"[process] provider={provider.name} keywords={len(rows)} notifiers={len(notifiers)}")

    pushed = 0
    observed = 0
    dropped = 0
    failed = 0
    for index, row in enumerate(rows, start=1):
        keyword = row["keyword"]
        try:
            trend = provider.fetch(keyword)
        except RuntimeError as exc:
            failed += 1
            print(f"[request-error] {index}/{len(rows)} keyword='{keyword}' error={exc}")
            if args.stop_on_error:
                db.close()
                return 2
            if args.request_delay > 0:
                time.sleep(args.request_delay)
            continue

        intent_summary, is_game_related, is_noise = analyzer.analyze(keyword, trend)
        score = score_keyword(
            keyword=keyword,
            trend=trend,
            intent_summary=intent_summary,
            is_game_related=is_game_related,
            is_noise=is_noise,
            was_pushed=db.keyword_was_pushed(keyword),
            push_threshold=config.defaults.push_score_threshold,
            observe_threshold=config.defaults.observe_score_threshold,
        )
        db.save_trend_result(trend, score)

        if score.status == "push":
            pushed += 1
            sent_successfully = False
            for notifier in notifiers:
                success, payload, response_text = notifier.send(score, trend, row["game_url"], row["site_name"])
                db.log_notification(keyword, notifier.channel, payload, success, response_text)
                sent_successfully = sent_successfully or success
            if sent_successfully:
                db.mark_pushed(keyword)
            print(f"[push] {index}/{len(rows)} {keyword} score={score.score}")
        elif score.status == "observe":
            observed += 1
            print(f"[observe] {index}/{len(rows)} {keyword} score={score.score}")
        else:
            dropped += 1

        if args.request_delay > 0 and index < len(rows):
            time.sleep(args.request_delay)

    print(
        f"[done] pushed={pushed} observed={observed} dropped={dropped} "
        f"failed={failed} db={config.database_path}"
    )
    db.close()
    return 0 if failed == 0 else 1


def stats_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.database_path)
    db.migrate()
    for key, value in db.stats().items():
        print(f"{key}: {value}")
    db.close()
    return 0


def dataforseo_check_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    provider = DataForSEOTrendProvider.from_env(config.defaults)
    if provider is None:
        print("[error] DATAFORSEO_LOGIN or DATAFORSEO_PASSWORD was not found. Please fill .env first.")
        return 2
    try:
        response = provider.check_account()
    except RuntimeError as exc:
        print(f"[error] DataForSEO API Access check failed: {exc}")
        print("[hint] Check API Access credentials, account verification, balance, and API permissions.")
        return 2

    status_code = response.get("status_code")
    status_message = response.get("status_message")
    print(f"[ok] DataForSEO API Access available: status_code={status_code} status_message={status_message}")
    tasks = response.get("tasks") or []
    if tasks:
        result = (tasks[0].get("result") or [{}])[0]
        money = result.get("money")
        if money is not None:
            print(f"[info] balance={money}")
    return 0


def export_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.database_path)
    db.migrate()
    rows = db.recent_trend_results(args.limit)
    output = Path(args.output or f"exports/trend_results.{args.format}")
    output.parent.mkdir(parents=True, exist_ok=True)

    records = [trend_row_to_record(row) for row in rows]
    if args.format == "csv":
        write_csv(output, records)
    else:
        write_markdown(output, records)
    db.close()
    print(f"[export] wrote {len(records)} rows to {output}")
    return 0


def trend_row_to_record(row) -> dict[str, str | int]:
    graph_values = json.loads(row["graph_values_json"] or "[]")
    related_top = json.loads(row["related_top_json"] or "[]")
    related_rising = json.loads(row["related_rising_json"] or "[]")
    reasons = json.loads(row["reasons_json"] or "[]")
    peak = max(graph_values) if graph_values else ""
    recent = graph_values[-7:] if len(graph_values) >= 7 else graph_values
    recent_avg = round(sum(recent) / len(recent), 2) if recent else ""
    return {
        "keyword": row["keyword"],
        "score": row["score"],
        "status": row["status"],
        "site_name": row["site_name"] or "",
        "game_url": row["game_url"] or "",
        "peak": peak,
        "recent_avg": recent_avg,
        "is_noise": row["is_noise"],
        "related_rising": "; ".join(format_rising(item) for item in related_rising[:8]),
        "related_top": "; ".join(str(item) for item in related_top[:8]),
        "intent_summary": row["intent_summary"] or "",
        "reasons": "; ".join(str(item) for item in reasons),
        "created_at": row["created_at"],
    }


def format_rising(item) -> str:
    if isinstance(item, list | tuple) and len(item) >= 2:
        return f"{item[0]} ({item[1]})"
    return str(item)


def write_csv(output: Path, records: list[dict[str, str | int]]) -> None:
    fieldnames = [
        "keyword",
        "score",
        "status",
        "site_name",
        "peak",
        "recent_avg",
        "is_noise",
        "related_rising",
        "related_top",
        "intent_summary",
        "reasons",
        "game_url",
        "created_at",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def write_markdown(output: Path, records: list[dict[str, str | int]]) -> None:
    lines = ["# Trend Results Review", ""]
    for record in records:
        lines.extend(
            [
                f"## {record['keyword']}",
                "",
                f"- Score: {record['score']}",
                f"- Status: {record['status']}",
                f"- Site: {record['site_name']}",
                f"- Peak: {record['peak']}",
                f"- Recent Avg: {record['recent_avg']}",
                f"- Rising: {record['related_rising'] or 'N/A'}",
                f"- Top: {record['related_top'] or 'N/A'}",
                f"- Intent: {record['intent_summary']}",
                f"- Reasons: {record['reasons']}",
                f"- URL: {record['game_url']}",
                "",
            ]
        )
    output.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
