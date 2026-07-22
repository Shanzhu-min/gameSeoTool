from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import mimetypes
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from .cli import run_command, trend_row_to_record, write_csv, write_markdown
from .config import AppConfig, load_config
from .db import Database
from .trends import DataForSEOTrendProvider


@dataclass
class TaskState:
    lock: threading.Lock
    running: bool = False
    scheduler_enabled: bool = False
    schedule_minutes: int = 1440
    last_message: str = "Idle"


class WebApp:
    def __init__(self, config_path: str | None):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.state = TaskState(lock=threading.Lock())
        db = Database(self.config.database_path)
        db.migrate()
        db.close()
        self.scheduler_thread = threading.Thread(target=self.scheduler_loop, daemon=True)
        self.scheduler_thread.start()

    def reload_config(self) -> AppConfig:
        self.config = load_config(self.config_path)
        return self.config

    def scheduler_loop(self) -> None:
        last_run = 0.0
        while True:
            time.sleep(5)
            if not self.state.scheduler_enabled:
                continue
            interval = max(1, self.state.schedule_minutes) * 60
            if time.time() - last_run < interval:
                continue
            last_run = time.time()
            self.start_task(
                {
                    "limit": 50,
                    "no_notify": True,
                    "skip_discovery": False,
                    "dry_run": False,
                    "max_sitemaps": 1,
                    "request_delay": 2.0,
                    "stop_on_error": False,
                    "source": "scheduler",
                }
            )

    def start_task(self, params: dict) -> bool:
        with self.state.lock:
            if self.state.running:
                self.state.last_message = "A task is already running"
                return False
            self.state.running = True
            self.state.last_message = "Task started"
        thread = threading.Thread(target=self.run_task_worker, args=(params,), daemon=True)
        thread.start()
        return True

    def run_task_worker(self, params: dict) -> None:
        config = self.reload_config()
        db = Database(config.database_path)
        db.migrate()
        task_id = db.create_task_run("trend_run", params)
        db.close()

        args = SimpleNamespace(
            config=self.config_path,
            limit=int(params.get("limit", 50)),
            dry_run=bool(params.get("dry_run", False)),
            no_notify=bool(params.get("no_notify", True)),
            max_sitemaps=int(params.get("max_sitemaps", 1)),
            skip_discovery=bool(params.get("skip_discovery", True)),
            request_delay=float(params.get("request_delay", 2.0)),
            stop_on_error=bool(params.get("stop_on_error", False)),
        )
        buffer = io.StringIO()
        status = "success"
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                exit_code = run_command(args)
            if exit_code != 0:
                status = "failed"
        except Exception as exc:
            status = "failed"
            buffer.write(f"\n[exception] {exc}\n")
        output = buffer.getvalue()
        db = Database(config.database_path)
        db.migrate()
        db.finish_task_run(task_id, status, output)
        db.close()
        with self.state.lock:
            self.state.running = False
            self.state.last_message = f"Task {status}"


def make_handler(app: WebApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GameSEOToolsWeb/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/":
                self.respond_html(render_dashboard(app))
            elif path == "/results":
                self.respond_html(render_results(app, query))
            elif path.startswith("/results/"):
                result_id = safe_int(path.rsplit("/", 1)[-1], 0)
                self.respond_html(render_result_detail(app, result_id))
            elif path == "/tasks":
                self.respond_html(render_tasks(app))
            elif path == "/settings":
                self.respond_html(render_settings(app))
            elif path.startswith("/download/"):
                self.serve_download(path)
            else:
                self.respond_html(render_not_found(), HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            fields = self.read_form()
            if parsed.path == "/tasks/run":
                params = {
                    "limit": safe_int(fields.get("limit", ["20"])[0], 20),
                    "no_notify": fields.get("no_notify", ["off"])[0] == "on",
                    "skip_discovery": fields.get("skip_discovery", ["off"])[0] == "on",
                    "dry_run": fields.get("dry_run", ["off"])[0] == "on",
                    "max_sitemaps": safe_int(fields.get("max_sitemaps", ["1"])[0], 1),
                    "request_delay": safe_float(fields.get("request_delay", ["2"])[0], 2.0),
                    "stop_on_error": fields.get("stop_on_error", ["off"])[0] == "on",
                    "source": "manual",
                }
                app.start_task(params)
                self.redirect("/tasks")
            elif parsed.path == "/tasks/schedule":
                app.state.scheduler_enabled = fields.get("enabled", ["off"])[0] == "on"
                app.state.schedule_minutes = safe_int(fields.get("schedule_minutes", ["1440"])[0], 1440)
                app.state.last_message = "Schedule updated"
                self.redirect("/tasks")
            elif parsed.path == "/exports/create":
                fmt = fields.get("format", ["csv"])[0]
                limit = safe_int(fields.get("limit", ["100"])[0], 100)
                create_export(app, fmt, limit)
                self.redirect("/results")
            else:
                self.respond_html(render_not_found(), HTTPStatus.NOT_FOUND)

        def read_form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            return parse_qs(raw)

        def respond_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, path: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", path)
            self.end_headers()

        def serve_download(self, path: str) -> None:
            name = unquote(path.replace("/download/", "", 1))
            target = Path("exports") / name
            if not target.exists() or not target.is_file():
                self.respond_html(render_not_found(), HTTPStatus.NOT_FOUND)
                return
            data = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f"attachment; filename={target.name}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def render_layout(title: str, content: str, active: str = "") -> str:
    nav = [
        ("/", "Dashboard", "dashboard"),
        ("/results", "Trend Results", "results"),
        ("/tasks", "Tasks", "tasks"),
        ("/settings", "Settings", "settings"),
    ]
    nav_html = "".join(
        f'<a class="{ "active" if key == active else "" }" href="{href}">{label}</a>'
        for href, label, key in nav
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(title)} - Game SEO Tools</title>
  <style>{CSS}</style>
</head>
<body>
  <aside>
    <div class="brand">Game SEO Tools</div>
    <nav>{nav_html}</nav>
  </aside>
  <main>
    <header><h1>{e(title)}</h1></header>
    {content}
  </main>
</body>
</html>"""


def render_dashboard(app: WebApp) -> str:
    config = app.reload_config()
    db = Database(config.database_path)
    db.migrate()
    stats = db.stats()
    counts = db.trend_status_counts()
    rows = db.trend_results(limit=12)
    tasks = db.recent_task_runs(limit=5)
    db.close()
    cards = [
        ("Game Pages", stats.get("game_pages", 0)),
        ("Keywords", stats.get("keywords", 0)),
        ("Trend Results", stats.get("trend_results", 0)),
        ("Push", counts.get("push", 0)),
        ("Observe", counts.get("observe", 0)),
        ("Drop", counts.get("drop", 0)),
    ]
    card_html = "".join(f'<section class="metric"><span>{label}</span><strong>{value}</strong></section>' for label, value in cards)
    content = f"""
    <section class="metrics">{card_html}</section>
    <section class="panel">
      <div class="panel-title"><h2>Recent Results</h2><a href="/results">View all</a></div>
      {render_results_table(rows)}
    </section>
    <section class="panel">
      <div class="panel-title"><h2>Recent Tasks</h2><a href="/tasks">Run task</a></div>
      {render_task_table(tasks)}
    </section>
    """
    return render_layout("Dashboard", content, "dashboard")


def render_results(app: WebApp, query: dict[str, list[str]]) -> str:
    config = app.reload_config()
    status = query.get("status", [""])[0]
    q = query.get("q", [""])[0]
    limit = safe_int(query.get("limit", ["100"])[0], 100)
    db = Database(config.database_path)
    db.migrate()
    rows = db.trend_results(limit=limit, status=status or None, query=q or None)
    db.close()
    content = f"""
    <section class="panel">
      <form class="toolbar" method="get" action="/results">
        <input type="search" name="q" value="{e(q)}" placeholder="Search keyword">
        <select name="status">
          {option("", "All status", status)}
          {option("push", "Push", status)}
          {option("observe", "Observe", status)}
          {option("drop", "Drop", status)}
        </select>
        <input type="number" name="limit" min="10" max="500" value="{limit}">
        <button type="submit">Filter</button>
      </form>
      {render_results_table(rows)}
    </section>
    <section class="panel">
      <div class="panel-title"><h2>Exports</h2></div>
      <form class="toolbar" method="post" action="/exports/create">
        <input type="number" name="limit" min="10" max="1000" value="100">
        <select name="format">{option("csv", "CSV", "csv")}{option("md", "Markdown", "csv")}</select>
        <button type="submit">Create Export</button>
        <a class="button-link" href="/download/trend_results.csv">Download CSV</a>
        <a class="button-link" href="/download/trend_results.md">Download Markdown</a>
      </form>
    </section>
    """
    return render_layout("Trend Results", content, "results")


def render_result_detail(app: WebApp, result_id: int) -> str:
    config = app.reload_config()
    db = Database(config.database_path)
    db.migrate()
    row = db.get_trend_result(result_id)
    db.close()
    if row is None:
        return render_not_found()
    record = trend_row_to_record(row)
    graph = json.loads(row["graph_values_json"] or "[]")
    related_rising = json.loads(row["related_rising_json"] or "[]")
    related_top = json.loads(row["related_top_json"] or "[]")
    reasons = json.loads(row["reasons_json"] or "[]")
    content = f"""
    <section class="detail-head">
      <div>
        <p class="eyebrow">{e(record['site_name'])}</p>
        <h2>{e(record['keyword'])}</h2>
      </div>
      <span class="badge {e(str(record['status']))}">{e(str(record['status']))}</span>
    </section>
    <section class="metrics">
      <section class="metric"><span>Score</span><strong>{record['score']}</strong></section>
      <section class="metric"><span>Peak</span><strong>{record['peak']}</strong></section>
      <section class="metric"><span>Recent Avg</span><strong>{record['recent_avg']}</strong></section>
    </section>
    <section class="panel">
      <div class="panel-title"><h2>Trend Curve</h2></div>
      {sparkline(graph, large=True)}
    </section>
    <section class="grid-two">
      <div class="panel"><h2>Rising Queries</h2>{list_items([format_related(item) for item in related_rising])}</div>
      <div class="panel"><h2>Top Queries</h2>{list_items([str(item) for item in related_top])}</div>
    </section>
    <section class="panel">
      <h2>Intent</h2>
      <p>{e(str(record['intent_summary']))}</p>
      <h2>Reasons</h2>
      {list_items([str(item) for item in reasons])}
      <p><a href="{e(str(record['game_url']))}" target="_blank" rel="noreferrer">Open source game page</a></p>
    </section>
    """
    return render_layout("Keyword Detail", content, "results")


def render_tasks(app: WebApp) -> str:
    config = app.reload_config()
    db = Database(config.database_path)
    db.migrate()
    tasks = db.recent_task_runs(limit=20)
    db.close()
    running = "Running" if app.state.running else "Idle"
    checked = "checked" if app.state.scheduler_enabled else ""
    content = f"""
    <section class="panel">
      <div class="panel-title"><h2>Task Runner</h2><span class="status-dot">{running}</span></div>
      <p class="muted">{e(app.state.last_message)}</p>
      <form class="task-form" method="post" action="/tasks/run">
        <label>Limit <input type="number" name="limit" min="1" max="500" value="20"></label>
        <label>Max sitemaps <input type="number" name="max_sitemaps" min="1" max="20" value="1"></label>
        <label>Request delay <input type="number" name="request_delay" min="0" max="30" step="0.5" value="2"></label>
        <label class="check"><input type="checkbox" name="skip_discovery" checked> Skip discovery</label>
        <label class="check"><input type="checkbox" name="no_notify" checked> No notify</label>
        <label class="check"><input type="checkbox" name="dry_run"> Dry run</label>
        <label class="check"><input type="checkbox" name="stop_on_error"> Stop on error</label>
        <button type="submit">Start Task</button>
      </form>
    </section>
    <section class="panel">
      <div class="panel-title"><h2>Schedule</h2></div>
      <form class="toolbar" method="post" action="/tasks/schedule">
        <label class="check"><input type="checkbox" name="enabled" {checked}> Enabled</label>
        <input type="number" name="schedule_minutes" min="10" max="10080" value="{app.state.schedule_minutes}">
        <button type="submit">Save Schedule</button>
      </form>
      <p class="muted">The MVP scheduler runs in this web process. Keep this server running for scheduled jobs.</p>
    </section>
    <section class="panel">
      <div class="panel-title"><h2>Recent Task Runs</h2></div>
      {render_task_table(tasks, include_output=True)}
    </section>
    """
    return render_layout("Tasks", content, "tasks")


def render_settings(app: WebApp) -> str:
    config = app.reload_config()
    provider = DataForSEOTrendProvider.from_env(config.defaults)
    dataforseo_status = "Configured" if provider else "Missing credentials"
    sites = "".join(
        f"<tr><td>{e(site.name)}</td><td>{e(site.sitemap_url)}</td><td>{', '.join(e(p) for p in site.url_patterns)}</td></tr>"
        for site in config.sites
    )
    content = f"""
    <section class="panel">
      <h2>Runtime</h2>
      <dl class="settings">
        <dt>Database</dt><dd>{e(str(config.database_path))}</dd>
        <dt>DataForSEO</dt><dd>{dataforseo_status}</dd>
        <dt>Location</dt><dd>{e(config.defaults.location_name)}</dd>
        <dt>Language</dt><dd>{e(config.defaults.language_name)}</dd>
        <dt>Date Range</dt><dd>{config.defaults.date_range_days} days</dd>
      </dl>
    </section>
    <section class="panel">
      <h2>Sites</h2>
      <table><thead><tr><th>Name</th><th>Sitemap</th><th>Patterns</th></tr></thead><tbody>{sites}</tbody></table>
    </section>
    """
    return render_layout("Settings", content, "settings")


def render_results_table(rows) -> str:
    if not rows:
        return '<div class="empty">No trend results yet.</div>'
    body = []
    for row in rows:
        record = trend_row_to_record(row)
        row_id = row["id"] if "id" in row.keys() else ""
        detail = f"/results/{row_id}" if row_id else "/results"
        graph = json.loads(row["graph_values_json"] or "[]")
        body.append(
            f"""
            <tr>
              <td><a href="{detail}">{e(str(record['keyword']))}</a><span>{e(str(record['site_name']))}</span></td>
              <td><span class="badge {e(str(record['status']))}">{e(str(record['status']))}</span></td>
              <td class="num">{record['score']}</td>
              <td>{sparkline(graph)}</td>
              <td class="num">{record['peak']}</td>
              <td class="num">{record['recent_avg']}</td>
              <td>{e(str(record['related_rising'])[:120])}</td>
            </tr>
            """
        )
    return f"""
    <table>
      <thead><tr><th>Keyword</th><th>Status</th><th>Score</th><th>Trend</th><th>Peak</th><th>Recent Avg</th><th>Rising</th></tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table>
    """


def render_task_table(tasks, include_output: bool = False) -> str:
    if not tasks:
        return '<div class="empty">No task runs yet.</div>'
    rows = []
    for task in tasks:
        params = json.loads(task["params_json"] or "{}")
        output = ""
        if include_output and task["output_text"]:
            output = f'<details><summary>Output</summary><pre>{e(task["output_text"])}</pre></details>'
        rows.append(
            f"""
            <tr>
              <td>{task['id']}</td>
              <td>{e(task['task_type'])}</td>
              <td><span class="badge {e(task['status'])}">{e(task['status'])}</span></td>
              <td>{e(json.dumps(params, ensure_ascii=False))}{output}</td>
              <td>{e(task['started_at'])}</td>
              <td>{e(task['finished_at'] or '')}</td>
            </tr>
            """
        )
    return f"<table><thead><tr><th>ID</th><th>Task</th><th>Status</th><th>Params</th><th>Started</th><th>Finished</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def create_export(app: WebApp, fmt: str, limit: int) -> None:
    config = app.reload_config()
    db = Database(config.database_path)
    db.migrate()
    rows = db.recent_trend_results(limit)
    records = [trend_row_to_record(row) for row in rows]
    output = Path("exports") / f"trend_results.{fmt}"
    output.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "md":
        write_markdown(output, records)
    else:
        write_csv(output, records)
    db.close()


def sparkline(values: list[int], large: bool = False) -> str:
    width = 520 if large else 160
    height = 160 if large else 42
    if not values:
        return f'<svg class="spark" viewBox="0 0 {width} {height}" role="img"><line x1="0" y1="{height - 6}" x2="{width}" y2="{height - 6}"/></svg>'
    max_value = max(values) or 1
    step = width / max(1, len(values) - 1)
    points = []
    for index, value in enumerate(values):
        x = round(index * step, 2)
        y = round(height - 8 - ((value / max_value) * (height - 16)), 2)
        points.append(f"{x},{y}")
    return f'<svg class="spark {"large" if large else ""}" viewBox="0 0 {width} {height}" role="img"><polyline points="{" ".join(points)}"/></svg>'


def list_items(items: list[str]) -> str:
    if not items:
        return '<div class="empty">No data</div>'
    return "<ul>" + "".join(f"<li>{e(item)}</li>" for item in items) + "</ul>"


def format_related(item) -> str:
    if isinstance(item, list | tuple) and len(item) >= 2:
        return f"{item[0]} ({item[1]})"
    return str(item)


def render_not_found() -> str:
    return render_layout("Not Found", '<section class="panel"><p>The requested page was not found.</p></section>')


def option(value: str, label: str, selected: str) -> str:
    marker = "selected" if value == selected else ""
    return f'<option value="{e(value)}" {marker}>{e(label)}</option>'


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def serve(host: str, port: int, config: str | None) -> None:
    app = WebApp(config)
    server = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"Game SEO Tools web server running at http://{host}:{port}")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Game SEO Tools Web MVP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)
    serve(args.host, args.port, args.config)
    return 0


CSS = """
:root {
  color-scheme: light;
  --bg: #f5f7fb;
  --panel: #ffffff;
  --ink: #1d2733;
  --muted: #637083;
  --line: #d9e0ea;
  --accent: #1677ff;
  --good: #0f8a5f;
  --warn: #a66700;
  --bad: #b42318;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.45 "Segoe UI", Arial, sans-serif;
}
aside {
  position: fixed;
  inset: 0 auto 0 0;
  width: 224px;
  background: #182230;
  color: white;
  padding: 22px 16px;
}
.brand {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 24px;
}
nav { display: grid; gap: 6px; }
nav a {
  color: #c8d3df;
  text-decoration: none;
  padding: 9px 10px;
  border-radius: 6px;
}
nav a.active, nav a:hover { background: #263548; color: white; }
main {
  margin-left: 224px;
  padding: 22px 28px 48px;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
}
h1 { margin: 0; font-size: 26px; }
h2 { margin: 0 0 12px; font-size: 16px; }
a { color: var(--accent); }
.metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(120px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.metric, .panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.metric { padding: 14px; }
.metric span { color: var(--muted); display: block; }
.metric strong { display: block; font-size: 24px; margin-top: 4px; }
.panel {
  padding: 16px;
  margin-bottom: 16px;
  overflow-x: auto;
}
.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
table { width: 100%; border-collapse: collapse; min-width: 820px; }
th, td {
  border-bottom: 1px solid var(--line);
  padding: 10px 8px;
  text-align: left;
  vertical-align: top;
}
th { color: var(--muted); font-size: 12px; font-weight: 600; }
td span { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge {
  display: inline-flex;
  min-width: 62px;
  justify-content: center;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 650;
  background: #eef2f6;
  color: #475467;
}
.badge.push, .badge.success { background: #e7f7ef; color: var(--good); }
.badge.observe, .badge.running { background: #fff4df; color: var(--warn); }
.badge.drop, .badge.failed { background: #ffe7e4; color: var(--bad); }
.toolbar, .task-form {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
input, select, button, .button-link {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
  font: inherit;
  background: white;
}
button, .button-link {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
  cursor: pointer;
  text-decoration: none;
}
label { color: var(--muted); }
.check { display: inline-flex; align-items: center; gap: 6px; }
.task-form label:not(.check) {
  display: grid;
  gap: 4px;
}
.spark {
  width: 160px;
  height: 42px;
  stroke: var(--accent);
  fill: none;
  stroke-width: 3;
}
.spark.large {
  width: min(100%, 760px);
  height: 220px;
}
.spark line { stroke: var(--line); stroke-width: 2; }
.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.detail-head h2 { font-size: 24px; margin: 0; }
.eyebrow { color: var(--muted); margin: 0 0 4px; }
.grid-two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.settings {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 8px 16px;
}
.settings dt { color: var(--muted); }
.settings dd { margin: 0; }
.empty, .muted { color: var(--muted); }
pre {
  max-height: 260px;
  overflow: auto;
  background: #101828;
  color: #d0d5dd;
  padding: 12px;
  border-radius: 6px;
}
@media (max-width: 900px) {
  aside { position: static; width: auto; }
  main { margin-left: 0; padding: 16px; }
  .metrics { grid-template-columns: repeat(2, 1fr); }
  .grid-two { grid-template-columns: 1fr; }
}
"""


if __name__ == "__main__":
    raise SystemExit(main())
