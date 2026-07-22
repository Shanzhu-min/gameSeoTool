# Game SEO Tools MVP

这是一个“游戏新词趋势监控工具”的 MVP 实现。

当前版本支持：

- 从配置文件读取 CrazyGames、Poki、Y8 等游戏站 sitemap。
- 解析 sitemap、sitemap index、`.gz` sitemap。
- 根据 URL slug 生成候选游戏关键词。
- 用 SQLite 保存站点、游戏、关键词、趋势结果和推送记录。
- 可选接入 DataForSEO Google Trends API。
- 可选接入 OpenAI 做搜索意图和噪音判断。
- 可选通过飞书或企业微信群机器人 webhook 推送提醒。
- 没有 API key 时也可以 dry-run 验证采集链路。

## 快速开始

```powershell
python -m gameseotools.cli run --config config/sites.example.json --dry-run --limit 20
```

如果没有安装为包，可以设置 `PYTHONPATH`：

```powershell
$env:PYTHONPATH="src"
python -m gameseotools.cli run --config config/sites.example.json --dry-run --limit 20
```

## 配置环境变量

复制 `.env.example` 为 `.env`，然后填写需要启用的密钥。程序会自动读取当前目录下的 `.env`，已有系统环境变量会优先生效。

最小可运行不需要任何密钥。要启用趋势查询和推送，至少配置：

```text
DATAFORSEO_LOGIN=your_login
DATAFORSEO_PASSWORD=your_password
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
```

OpenAI 是可选项：

```text
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4.1-mini
```

### Supabase 持久化

默认情况下，本地开发会写入 SQLite：

```text
data/gameseo.sqlite3
```

如果配置了下面任意一个环境变量，程序会自动改为写入 Supabase/Postgres：

```text
SUPABASE_DB_URL=postgresql://postgres.xxx:password@aws-xxx.pooler.supabase.com:6543/postgres
```

也兼容：

```text
DATABASE_URL=
POSTGRES_URL=
```

优先级：

```text
SUPABASE_DB_URL > DATABASE_URL > POSTGRES_URL > SQLite
```

在 Supabase 后台可以从 **Project Settings → Database → Connection string** 获取连接串。建议使用 pooler/transaction 模式连接串，并把 `[YOUR-PASSWORD]` 替换成数据库密码。

## 常用命令

只抓取并生成候选词，不调用外部 API：

```powershell
$env:PYTHONPATH="src"
python -m gameseotools.cli run --dry-run --limit 50
```

启用 DataForSEO 趋势查询，但不推送：

```powershell
$env:PYTHONPATH="src"
python -m gameseotools.cli run --limit 50 --no-notify
```

如果已经完成过 sitemap 采集，只想验证 DataForSEO：

```powershell
$env:PYTHONPATH="src"
python -m gameseotools.cli run --limit 1 --no-notify --skip-discovery
```

启用推送：

```powershell
$env:PYTHONPATH="src"
python -m gameseotools.cli run --limit 50
```

查看数据库摘要：

```powershell
$env:PYTHONPATH="src"
python -m gameseotools.cli stats
```

## Web MVP

启动本地 Web 后台：

```powershell
cd "E:\05 Project\temp_GameSEOTools"
$env:PYTHONPATH="src"
py -3.12 -m gameseotools.web --host 127.0.0.1 --port 8787 --config config/sites.example.json
```

打开：

```text
http://127.0.0.1:8787
```

Web MVP 当前支持：

- Dashboard：查看游戏页、关键词、趋势结果和状态数量。
- Trend Results：筛选 `push / observe / drop`，查看趋势曲线和关键词详情。
- Tasks：手动触发采集/趋势查询，支持 `skip discovery`、`no notify`、请求间隔和 limit。
- Schedule：在 Web 服务进程内启用轻量定时任务。
- Settings：查看当前数据库、站点配置和 DataForSEO 配置状态。

注意：MVP 定时任务运行在当前 Web 进程里，关闭 PowerShell 或停止服务后，定时任务也会停止。正式生产部署建议后续升级为独立任务队列或系统计划任务。

## Vercel 部署说明

项目包含一个 Vercel Python 入口：

```text
api/index.py
```

Vercel 官方 Python Runtime 会加载 `BaseHTTPRequestHandler` 风格的 `handler`。当前仓库也包含：

```text
vercel.json
.python-version
```

重要限制：

- Vercel 是 serverless 环境，不适合运行本地版里的长驻内存 scheduler。
- Vercel 文件系统不是长期持久化数据库，线上请配置 `SUPABASE_DB_URL` 写入 Supabase。
- 定时任务建议后续用 Vercel Cron 或外部任务触发接口，不依赖内存 scheduler。
- DataForSEO、OpenAI、飞书/企业微信 webhook 要在 Vercel Project Settings 的 Environment Variables 中配置，不要提交 `.env`。

## MVP 推荐执行方式

第一阶段建议每天跑一次：

```powershell
$env:PYTHONPATH="src"
python -m gameseotools.cli run --config config/sites.example.json --limit 200
```

等验证有效后，再把 `config/sites.example.json` 复制为正式配置，并逐步增加站点。
