from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import mimetypes
import os
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
from .reporting import (
    domain_candidates,
    roblox_search_url,
    search_url,
    trends_url,
    wiki_search_url,
    youtube_search_url,
)
from .trends import DataForSEOTrendProvider


APP_BUILD = "quality-v6-zh-cn-ui"


@dataclass
class TaskState:
    lock: threading.Lock
    running: bool = False
    scheduler_enabled: bool = False
    schedule_minutes: int = 1440
    last_message: str = "空闲"


class WebApp:
    def __init__(
        self,
        config_path: str | None,
        run_startup_migration: bool = True,
        enable_scheduler_thread: bool = True,
    ):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.state = TaskState(lock=threading.Lock())
        self.scheduler_thread = None
        if run_startup_migration:
            db = Database(self.config.database_path)
            db.migrate()
            db.close()
        if enable_scheduler_thread:
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
                    "max_pages_per_site": 10,
                    "request_delay": 2.0,
                    "stop_on_error": False,
                    "source": "scheduler",
                }
            )

    def start_task(self, params: dict) -> bool:
        with self.state.lock:
            if self.state.running:
                self.state.last_message = "已有任务正在运行"
                return False
            self.state.running = True
            self.state.last_message = "任务已开始"
        if os.getenv("VERCEL"):
            self.run_task_worker(params)
            return True
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
            max_pages_per_site=int(params.get("max_pages_per_site", 0)),
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
        self.state.last_message = f"任务{translate_task_status(status)}"


def make_handler(app: WebApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GameSEOToolsWeb/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/":
                self.respond_html(render_dashboard(app))
            elif path == "/health":
                self.respond_html(render_health())
            elif path == "/db-check":
                self.respond_html(render_db_check(app))
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
                    "max_pages_per_site": safe_int(fields.get("max_pages_per_site", ["10"])[0], 10),
                    "request_delay": safe_float(fields.get("request_delay", ["2"])[0], 2.0),
                    "stop_on_error": fields.get("stop_on_error", ["off"])[0] == "on",
                    "source": "manual",
                }
                app.start_task(params)
                self.redirect("/tasks")
            elif parsed.path == "/tasks/schedule":
                app.state.scheduler_enabled = fields.get("enabled", ["off"])[0] == "on"
                app.state.schedule_minutes = safe_int(fields.get("schedule_minutes", ["1440"])[0], 1440)
                app.state.last_message = "定时配置已更新"
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
        ("/", "每日报告", "dashboard"),
        ("/results", "趋势结果", "results"),
        ("/tasks", "任务", "tasks"),
        ("/settings", "设置", "settings"),
    ]
    nav_html = "".join(
        f'<a class="{ "active" if key == active else "" }" href="{href}">{label}</a>'
        for href, label, key in nav
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(title)} - Game SEO Tools</title>
  <style>{CSS}</style>
</head>
<body>
  <aside>
    <div class="brand">Game SEO Tools</div>
    <div class="build">{e(APP_BUILD)}</div>
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
    lifecycle = db.lifecycle_counts()
    recommended = db.trend_results(limit=5, status="push")
    watching = db.trend_results(limit=8, status="observe")
    tasks = db.recent_task_runs(limit=5)
    db.close()
    cards = [
        ("游戏页", stats.get("game_pages", 0)),
        ("关键词", stats.get("keywords", 0)),
        ("趋势结果", stats.get("trend_results", 0)),
        ("已推荐", lifecycle.get("recommended", counts.get("push", 0))),
        ("观察中", lifecycle.get("watching", counts.get("observe", 0))),
        ("老游戏/噪音", lifecycle.get("old_game", 0) + lifecycle.get("noise", 0)),
    ]
    card_html = "".join(f'<section class="metric"><span>{label}</span><strong>{value}</strong></section>' for label, value in cards)
    content = f"""
    <section class="report-hero">
      <p class="eyebrow">每日机会报告</p>
      <h2>优先查看今天真正值得判断的少量关键词。</h2>
      <p>这里展示达到推荐分数线的关键词。观察池中的词会低频跟踪，不会直接进入早报打扰用户。</p>
    </section>
    <section class="metrics">{card_html}</section>
    <section class="panel">
      <div class="panel-title"><h2>今日推荐</h2><a href="/results?status=push">查看全部</a></div>
      {render_report_cards(recommended)}
    </section>
    <section class="panel">
      <div class="panel-title"><h2>观察池</h2><a href="/results?status=observe">查看全部</a></div>
      {render_results_table(watching)}
    </section>
    <section class="panel">
      <div class="panel-title"><h2>最近任务</h2><a href="/tasks">运行任务</a></div>
      {render_task_table(tasks)}
    </section>
    """
    return render_layout("每日报告", content, "dashboard")


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
        <input type="search" name="q" value="{e(q)}" placeholder="搜索关键词">
        <select name="status">
          {option("", "全部状态", status)}
          {option("push", "Push", status)}
          {option("observe", "Observe", status)}
          {option("drop", "Drop", status)}
        </select>
        <input type="number" name="limit" min="10" max="500" value="{limit}">
        <button type="submit">筛选</button>
      </form>
      {render_results_table(rows)}
    </section>
    <section class="panel">
      <div class="panel-title"><h2>导出</h2></div>
      <form class="toolbar" method="post" action="/exports/create">
        <input type="number" name="limit" min="10" max="1000" value="100">
        <select name="format">{option("csv", "CSV", "csv")}{option("md", "Markdown", "csv")}</select>
        <button type="submit">生成导出文件</button>
        <a class="button-link" href="/download/trend_results.csv">下载 CSV</a>
        <a class="button-link" href="/download/trend_results.md">下载 Markdown</a>
      </form>
    </section>
    """
    return render_layout("趋势结果", content, "results")


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
        <h2>{e(record['canonical_keyword'])}</h2>
        <p class="muted">长尾变体：{e(record['variants'] or 'N/A')}</p>
      </div>
      <span class="badge {e(str(record['status']))}">{e(str(record['status']))}</span>
    </section>
    <section class="metrics">
      <section class="metric"><span>机会分</span><strong>{record['opportunity_score']}</strong></section>
      <section class="metric"><span>证据分</span><strong>{record['evidence_score']}</strong></section>
      <section class="metric"><span>生命周期</span><strong>{record['lifecycle_status'] or 'new_candidate'}</strong></section>
      <section class="metric"><span>趋势验证</span><strong>{record['validation_status']}</strong></section>
      <section class="metric"><span>推荐等级</span><strong>{record['recommendation']}</strong></section>
      <section class="metric"><span>首值 / 末值</span><strong>{record['first']} / {record['last']}</strong></section>
      <section class="metric"><span>峰值</span><strong>{record['peak']}</strong></section>
      <section class="metric"><span>近期均值</span><strong>{record['recent_avg']}</strong></section>
    </section>
    <section class="panel">
      <div class="panel-title"><h2>趋势曲线</h2></div>
      {sparkline(graph, large=True)}
    </section>
    <section class="grid-two">
      <div class="panel"><h2>上升相关查询</h2>{list_items([format_related(item) for item in related_rising])}</div>
      <div class="panel"><h2>热门相关查询</h2>{list_items([str(item) for item in related_top])}</div>
    </section>
    <section class="grid-two">
      <div class="panel">
        <h2>外部链接</h2>
        {list_items([
          '<a href="' + e(trends_url(str(record['canonical_keyword']))) + '" target="_blank" rel="noreferrer">Google Trends 对比</a>',
          '<a href="' + e(search_url(str(record['canonical_keyword']))) + '" target="_blank" rel="noreferrer">Google Search</a>',
          '<a href="' + e(wiki_search_url(str(record['canonical_keyword']))) + '" target="_blank" rel="noreferrer">Wiki 搜索</a>',
          '<a href="' + e(youtube_search_url(str(record['canonical_keyword']))) + '" target="_blank" rel="noreferrer">YouTube 搜索</a>',
          '<a href="' + e(roblox_search_url(str(record['canonical_keyword']))) + '" target="_blank" rel="noreferrer">Roblox 搜索</a>',
        ], escape_items=False)}
      </div>
      <div class="panel">
        <h2>域名 DNS 检查</h2>
        {list_items([f"{domain}: {status}" for domain, status in domain_candidates(str(record['canonical_keyword']))])}
      </div>
    </section>
    <section class="panel">
      <h2>推荐摘要</h2>
      <p>{e(recommendation_sentence(record))}</p>
      <p class="muted">生命周期原因：{e(record['lifecycle_reason'] or 'N/A')}</p>
      <p class="muted">冷却截止：{e(record['cooldown_until'] or 'N/A')}</p>
      <h2>意图判断</h2>
      <p>{e(str(record['intent_summary']))}</p>
      <h2>评分原因</h2>
      {list_items([str(item) for item in reasons])}
      <p><a href="{e(str(record['game_url']))}" target="_blank" rel="noreferrer">打开来源游戏页面</a></p>
    </section>
    """
    return render_layout("关键词详情", content, "results")


def render_tasks(app: WebApp) -> str:
    config = app.reload_config()
    db = Database(config.database_path)
    db.migrate()
    db.mark_stale_task_runs(max_age_minutes=1 if os.getenv("VERCEL") else 30)
    tasks = db.recent_task_runs(limit=20)
    db.close()
    running = "运行中" if app.state.running else "空闲"
    checked = "checked" if app.state.scheduler_enabled else ""
    content = f"""
    <section class="panel">
      <div class="panel-title"><h2>任务执行</h2><span class="status-dot">{running}</span></div>
      <p class="muted">{e(app.state.last_message)}</p>
      <form class="task-form" method="post" action="/tasks/run">
        <label>处理数量 <input type="number" name="limit" min="1" max="500" value="20"></label>
        <label>最多 sitemap <input type="number" name="max_sitemaps" min="1" max="20" value="1"></label>
        <label>每站最多页面 <input type="number" name="max_pages_per_site" min="0" max="5000" value="10"></label>
        <label>请求间隔 <input type="number" name="request_delay" min="0" max="30" step="0.5" value="2"></label>
        <label class="check"><input type="checkbox" name="skip_discovery" checked> 跳过站点发现</label>
        <label class="check"><input type="checkbox" name="no_notify" checked> 不发送通知</label>
        <label class="check"><input type="checkbox" name="dry_run"> 试运行</label>
        <label class="check"><input type="checkbox" name="stop_on_error"> 出错即停止</label>
        <button type="submit">开始任务</button>
      </form>
    </section>
    <section class="panel">
      <div class="panel-title"><h2>定时任务</h2></div>
      <form class="toolbar" method="post" action="/tasks/schedule">
        <label class="check"><input type="checkbox" name="enabled" {checked}> 启用</label>
        <input type="number" name="schedule_minutes" min="10" max="10080" value="{app.state.schedule_minutes}">
        <button type="submit">保存定时设置</button>
      </form>
      <p class="muted">当前 MVP 的定时任务运行在 Web 服务进程中。需要保持服务运行，定时任务才会执行。</p>
    </section>
    <section class="panel">
      <div class="panel-title"><h2>最近任务记录</h2></div>
      {render_task_table(tasks, include_output=True)}
    </section>
    """
    return render_layout("任务", content, "tasks")


def render_settings(app: WebApp) -> str:
    config = app.reload_config()
    provider = DataForSEOTrendProvider.from_env(config.defaults)
    dataforseo_status = "已配置" if provider else "缺少凭证"
    database_backend = "unknown"
    database_status = "未检查"
    try:
        db = Database(config.database_path)
        db.migrate()
        database_backend = db.backend_name() if hasattr(db, "backend_name") else "unknown"
        database_status = "已连接"
        db.close()
    except Exception as exc:
        database_status = f"错误：{exc}"
    sites = "".join(
        f"<tr><td>{e(site.name)}</td><td>{e(site.sitemap_url)}</td><td>{', '.join(e(p) for p in site.url_patterns)}</td></tr>"
        for site in config.sites
    )
    content = f"""
    <section class="panel">
      <h2>运行环境</h2>
      <dl class="settings">
        <dt>数据库后端</dt><dd>{e(database_backend)}</dd>
        <dt>数据库状态</dt><dd>{e(database_status)}</dd>
        <dt>构建版本</dt><dd>{e(APP_BUILD)}</dd>
        <dt>SQLite 备用路径</dt><dd>{e(str(config.database_path))}</dd>
        <dt>DataForSEO</dt><dd>{dataforseo_status}</dd>
        <dt>地区</dt><dd>{e(config.defaults.location_name)}</dd>
        <dt>语言</dt><dd>{e(config.defaults.language_name)}</dd>
        <dt>趋势时间范围</dt><dd>{config.defaults.date_range_days} 天</dd>
      </dl>
    </section>
    <section class="panel">
      <h2>监控站点</h2>
      <table><thead><tr><th>名称</th><th>Sitemap</th><th>URL 规则</th></tr></thead><tbody>{sites}</tbody></table>
    </section>
    """
    return render_layout("设置", content, "settings")


def render_results_table(rows) -> str:
    if not rows:
        return '<div class="empty">暂无趋势结果。</div>'
    body = []
    for row in rows:
        record = trend_row_to_record(row)
        row_id = row["id"] if "id" in row.keys() else ""
        detail = f"/results/{row_id}" if row_id else "/results"
        graph = json.loads(row["graph_values_json"] or "[]")
        body.append(
            f"""
            <tr>
              <td><a href="{detail}">{e(str(record['canonical_keyword']))}</a><span>{e(str(record['variants'] or record['site_name']))}</span></td>
              <td><span class="badge {e(str(record['status']))}">{e(str(record['status']))}</span></td>
              <td class="num">{record['opportunity_score']}</td>
              <td class="num">{record['evidence_score']}</td>
              <td>{e(str(record['lifecycle_status'] or 'new_candidate'))}</td>
              <td>{e(str(record['validation_status']))}</td>
              <td>{sparkline(graph)}</td>
              <td class="num">{record['last']}</td>
              <td class="num">{record['peak']}</td>
              <td>{e(str(record['related_rising'])[:120])}</td>
            </tr>
            """
        )
    return f"""
    <table>
      <thead><tr><th>关键词组</th><th>状态</th><th>机会分</th><th>证据分</th><th>生命周期</th><th>趋势验证</th><th>趋势</th><th>末值</th><th>峰值</th><th>上升查询</th></tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table>
    """


def render_report_cards(rows) -> str:
    if not rows:
        return '<div class="empty">暂无推荐机会。</div>'
    cards = []
    for row in rows:
        record = trend_row_to_record(row)
        detail = f"/results/{row['id']}"
        summary = recommendation_sentence(record)
        cards.append(
            f"""
            <article class="report-card">
              <div>
                <p class="eyebrow">{e(record['site_name'])}</p>
                <h3><a href="{detail}">{e(str(record['canonical_keyword']))}</a></h3>
                <p>{e(summary)}</p>
                <p class="muted">长尾变体：{e(str(record['variants'] or 'N/A'))}</p>
              </div>
              <div class="score-stack">
                <span>机会分</span><strong>{record['opportunity_score']}</strong>
                <small>证据分 {record['evidence_score']}</small>
              </div>
            </article>
            """
        )
    return '<div class="report-cards">' + "".join(cards) + "</div>"


def recommendation_sentence(record: dict) -> str:
    validation = str(record.get("validation_status", ""))
    rising = str(record.get("related_rising", ""))
    last = record.get("last", "")
    peak = record.get("peak", "")
    if validation == "passed":
        return f"趋势验证已通过，末值为 {last}，峰值为 {peak}。建议评估 wiki、codes、guide 或 review 内容机会。"
    if rising:
        return "已出现上升相关查询。建议判断这些需求是否能落到可执行的游戏 SEO 内容上。"
    return "该词由证据分和趋势分共同推荐。投入内容资源前，建议先打开详情页复核。"


def render_task_table(tasks, include_output: bool = False) -> str:
    if not tasks:
        return '<div class="empty">暂无任务记录。</div>'
    rows = []
    for task in tasks:
        params = json.loads(task["params_json"] or "{}")
        output = ""
        if include_output and task["output_text"]:
            output = f'<details><summary>输出日志</summary><pre>{e(task["output_text"])}</pre></details>'
        rows.append(
            f"""
            <tr>
              <td>{task['id']}</td>
              <td>{e(task['task_type'])}</td>
              <td><span class="badge {e(task['status'])}">{e(translate_task_status(task['status']))}</span></td>
              <td>{e(json.dumps(params, ensure_ascii=False))}{output}</td>
              <td>{e(task['started_at'])}</td>
              <td>{e(task['finished_at'] or '')}</td>
            </tr>
            """
        )
    return f"<table><thead><tr><th>ID</th><th>任务</th><th>状态</th><th>参数</th><th>开始时间</th><th>结束时间</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


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


def list_items(items: list[str], escape_items: bool = True) -> str:
    if not items:
        return '<div class="empty">暂无数据</div>'
    if escape_items:
        return "<ul>" + "".join(f"<li>{e(item)}</li>" for item in items) + "</ul>"
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def format_related(item) -> str:
    if isinstance(item, list | tuple) and len(item) >= 2:
        return f"{item[0]} ({item[1]})"
    return str(item)


def render_not_found() -> str:
    return render_layout("未找到页面", '<section class="panel"><p>请求的页面不存在。</p></section>')


def render_health() -> str:
    content = f'<section class="panel"><p>OK</p><p class="muted">构建版本：{e(APP_BUILD)}</p></section>'
    return render_layout("健康检查", content)


def render_db_check(app: WebApp) -> str:
    config = app.reload_config()
    try:
        db = Database(config.database_path)
        db.migrate()
        backend = db.backend_name() if hasattr(db, "backend_name") else "unknown"
        stats = db.stats()
        db.close()
        rows = "".join(f"<tr><td>{e(key)}</td><td>{value}</td></tr>" for key, value in stats.items())
        content = f"""
        <section class="panel">
          <h2>数据库检查</h2>
          <p><strong>状态：</strong>已连接</p>
          <p><strong>后端：</strong>{e(backend)}</p>
          <table><tbody>{rows}</tbody></table>
        </section>
        """
    except Exception as exc:
        content = f"""
        <section class="panel">
          <h2>数据库检查</h2>
          <p><strong>状态：</strong>错误</p>
          <pre>{e(exc)}</pre>
        </section>
        """
    return render_layout("数据库检查", content, "settings")


def option(value: str, label: str, selected: str) -> str:
    marker = "selected" if value == selected else ""
    return f'<option value="{e(value)}" {marker}>{e(label)}</option>'


def translate_task_status(status: str) -> str:
    return {
        "success": "成功",
        "failed": "失败",
        "running": "运行中",
    }.get(status, status)


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
  margin-bottom: 6px;
}
.build {
  color: #8ea0b5;
  font-size: 11px;
  margin-bottom: 20px;
  overflow-wrap: anywhere;
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
.report-hero {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 18px;
  margin-bottom: 16px;
}
.report-hero h2 {
  font-size: 22px;
  margin-bottom: 6px;
}
.report-hero p { max-width: 860px; }
.report-cards {
  display: grid;
  gap: 10px;
}
.report-card {
  display: grid;
  grid-template-columns: 1fr 120px;
  gap: 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
}
.report-card h3 {
  margin: 0 0 6px;
  font-size: 18px;
}
.score-stack {
  display: grid;
  align-content: center;
  justify-items: end;
  color: var(--muted);
}
.score-stack strong {
  color: var(--ink);
  font-size: 28px;
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
  .report-card { grid-template-columns: 1fr; }
  .score-stack { justify-items: start; }
}
"""


if __name__ == "__main__":
    raise SystemExit(main())
