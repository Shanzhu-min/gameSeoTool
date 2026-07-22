# 游戏新词趋势监控工具执行方案

## 项目目标

搭建一个“游戏新词趋势监控工具”：从 CrazyGames、Poki、Y8 等游戏站定时发现新游戏和新关键词，调用 Google Trends 类 API 判断搜索趋势和相关查询，再通过 AI 或规则筛选有价值的新词，最后推送到飞书或企业微信。

核心策略：

- 游戏站种子词自己采集。
- Google Trends 数据优先使用第三方 API。
- 筛选、评分、通知和复盘系统自己沉淀。

## 整体架构

```text
游戏站配置文件
  ↓
定时读取 sitemap / 游戏列表页
  ↓
提取新游戏、新 URL、新分类、新标题
  ↓
生成候选关键词
  ↓
调用 DataForSEO / Google Trends API
  ↓
获取趋势曲线、related queries、related topics
  ↓
规则评分 + AI 搜索意图判断
  ↓
过滤噪音词
  ↓
生成趋势图和分析摘要
  ↓
推送到飞书 / 企业微信
  ↓
写入数据库，避免重复提醒
```

## MVP 版本

MVP 目标：用较低成本验证这个工具是否真的能发现有价值的游戏 SEO 新词。

建议周期：2-4 周。

### MVP 功能范围

| 模块 | MVP 要做什么 | 暂不做什么 |
|---|---|---|
| 游戏站来源 | 配置 5-10 个站点，如 CrazyGames、Poki、Y8 | 不做复杂反爬，不做全站爬虫 |
| 种子词采集 | 每天读取 sitemap，提取新增游戏 URL、游戏名 | 不抓评论、评分、播放量 |
| 关键词生成 | 从游戏名生成基础词和组合词 | 不做复杂 NLP 扩词 |
| Trends 查询 | 调 DataForSEO Google Trends API 查询趋势曲线和 related queries | 不自己维护 pytrends 代理池 |
| 新词判断 | 判断是否首次出现、是否趋势上涨、是否相关 | 不做复杂机器学习模型 |
| AI 分析 | 用 AI 判断搜索意图和行业相关性 | 不做多轮深度内容策略 |
| 推送 | 推送到飞书或企业微信 | 不做完整后台权限系统 |
| 数据存储 | SQLite / PostgreSQL 存关键词和趋势结果 | 不做复杂 BI 报表 |

### MVP 数据源设计

MVP 阶段建议先从 CrazyGames、Poki、Y8 开始，不要一开始覆盖太多站点。

| 站点 | 作用 | 获取方式 |
|---|---|---|
| CrazyGames | 新 HTML5 / Web game 发现 | sitemap、游戏列表页 |
| Poki | 休闲小游戏趋势 | games sitemap |
| Y8 | 老牌小游戏平台 | sitemap |
| GamePix | HTML5 游戏源 | sitemap / 列表页 |
| Miniplay | 海外小游戏 | sitemap / 分类页 |
| Itch.io | 独立游戏趋势，可作为扩展 | 标签页 / RSS |
| Roblox Discover | 平台型热词，可作为扩展 | 页面抓取或第三方数据 |
| Steam New & Trending | 中重度游戏趋势，可作为扩展 | Steam API / 页面 |

配置文件示例：

```yaml
sites:
  - name: crazygames
    enabled: true
    sitemap_url: "https://www.crazygames.com/sitemap-index.xml"
    url_patterns:
      - "/game/"
      - "/g/"
    weight: 1.0

  - name: poki
    enabled: true
    sitemap_url: "https://poki.com/en/sitemaps/games.xml"
    url_patterns:
      - "/en/g/"
    weight: 1.0

  - name: y8
    enabled: true
    sitemap_url: "https://www.y8.com/sitemaps/y8/en/sitemap.xml.gz"
    url_patterns:
      - "/games/"
    weight: 0.8
```

### MVP 关键词生成规则

从 URL 和页面标题提取关键词。

示例 1：

```text
https://poki.com/en/g/going-up-rooftop
→ going up rooftop
→ going up rooftop game
→ going up rooftop online
→ going up rooftop poki
```

示例 2：

```text
https://www.crazygames.com/game/bloxdhop-io
→ bloxdhop io
→ bloxdhop io game
→ bloxdhop io unblocked
→ bloxdhop crazygames
```

MVP 阶段建议每个游戏最多生成 3-5 个候选词，否则 API 成本和噪音都会上升。

### MVP 趋势判断规则

每个候选词调用 Trends API 后，计算一个简单分数。

| 指标 | 说明 | 分数 |
|---|---|---|
| 是否新词 | 数据库从未出现 | +20 |
| 近 7 天上涨 | 当前均值 > 前期均值 | +20 |
| 峰值明显 | 最近出现明显 spike | +20 |
| related queries 有 rising | 出现 rising query | +20 |
| 游戏相关 | AI 判断与游戏强相关 | +20 |
| 噪音词 | 音乐、明星、新闻、政治等 | -50 |
| 已推送过 | 避免重复轰炸 | -100 |

推送阈值建议：

```text
score >= 60 推送
score 40-59 进入观察池
score < 40 丢弃或低优先级保存
```

### MVP 推送内容格式

```text
发现新趋势词：going up rooftop

来源站点：Poki
首次发现：2026-07-21
主关键词：going up rooftop
相关新词：
- going up rooftop game
- going up rooftop poki
- going up rooftop unblocked

趋势判断：
近 7 天增长明显，related queries 出现 rising。

搜索意图：
用户大概率在寻找该游戏的在线游玩入口、攻略或未屏蔽版本。

建议动作：
可以创建游戏介绍页 / 聚合页 / 攻略短内容。
```

如果有图，则附趋势曲线图。

### MVP 技术选型

| 部分 | 推荐方案 |
|---|---|
| 语言 | Python |
| 定时任务 | cron / GitHub Actions / 云服务器计划任务 |
| 数据库 | SQLite 起步，后续 PostgreSQL |
| 爬取 | requests + BeautifulSoup + XML parser |
| Trends API | DataForSEO Google Trends API |
| AI 分析 | OpenAI API 或其他 LLM API |
| 图表 | matplotlib / plotly |
| 通知 | 飞书自定义机器人 webhook / 企业微信群机器人 |
| 部署 | 一台轻量云服务器即可 |

### MVP 验收标准

MVP 成功标准不是“功能很多”，而是能不能发现有效词。

| 指标 | 目标 |
|---|---|
| 每天稳定读取站点 | 5-10 个站点成功读取 |
| 每天新增候选词 | 50-300 个 |
| API 查询成功率 | 90% 以上 |
| 每天有效推送 | 3-20 条 |
| 重复推送率 | 低于 10% |
| 人工判断有效率 | 至少 30% 以上有 SEO 参考价值 |

如果每天推送 100 条但只有 2 条有用，说明评分和去噪失败。

## 扩展版本

扩展版目标：从“新词提醒工具”升级成“游戏 SEO 趋势情报系统”。

建议在 MVP 跑通后再做。

### 扩展功能

| 模块 | 扩展方向 |
|---|---|
| 数据源扩展 | 增加 Steam、Roblox、itch.io、Google Play、App Store、YouTube、TikTok |
| 多语言监控 | 英语、西语、葡语、印尼语、德语、法语等 |
| 多地区趋势 | US、GB、CA、AU、BR、ID、PH、IN |
| 关键词聚类 | 把同一游戏的不同表达合并 |
| 趋势分层 | 爆发词、稳定增长词、季节词、短期噪音词 |
| 竞品监控 | 监控竞品新上线页面和标题变化 |
| 内容建议 | 自动生成 SEO 页面标题、H1、描述、FAQ、内容角度 |
| 仪表盘 | Web 后台查看趋势、历史、推送记录 |
| 人工反馈 | 标记“有用/无用”，反向优化评分 |
| API 多供应商 | DataForSEO + SerpApi + pytrends fallback |
| 报表 | 每日/每周趋势报告 |

### 扩展版高级数据源

| 数据源 | 价值 |
|---|---|
| Google Trends | 搜索趋势验证 |
| DataForSEO Labs | 搜索量、关键词建议、SERP 数据 |
| Google Autocomplete | 发现长尾词 |
| YouTube Suggest | 游戏视频趋势 |
| TikTok 搜索建议 | 年轻用户趋势 |
| Steam 新品榜 | PC 游戏趋势 |
| Roblox 热门游戏 | UGC 游戏趋势 |
| App Store / Google Play | 移动游戏趋势 |
| Reddit / Discord | 社区早期信号 |
| 竞品 sitemap | SEO 页面布局信号 |

### 扩展版评分模型

扩展后可以把评分拆成 5 类：

| 分数 | 含义 |
|---|---|
| Trend Score | 搜索趋势增长 |
| Relevance Score | 与游戏业务相关性 |
| SEO Score | 是否适合做页面 |
| Competition Score | 竞争强弱 |
| Opportunity Score | 综合机会分 |

最终输出：

```text
Opportunity Score = 趋势增长 * 相关性 * SEO价值 / 竞争强度
```

### 扩展版产品形态

可以做一个简单后台：

| 页面 | 功能 |
|---|---|
| 趋势看板 | 今日新词、上涨词、爆发词 |
| 关键词库 | 所有发现过的关键词 |
| 游戏库 | 从各站采集到的游戏 |
| 来源站管理 | 配置 CrazyGames、Poki、Y8 等来源 |
| 推送记录 | 查看已推送内容 |
| 观察池 | 暂未推送但值得跟踪的词 |
| 人工标注 | 有用、无用、噪音、已处理 |
| 报表 | 每日/每周导出 |

## 需要注册或购买的工具/API

| 类型 | 工具/API | 是否必须 | 用途 | 备注 |
|---|---|---|---|---|
| Trends 数据 | DataForSEO Google Trends API | MVP 推荐必须 | 获取趋势曲线、related queries、related topics | 比自建 pytrends 稳定 |
| AI 分析 | OpenAI API | MVP 推荐 | 判断搜索意图、过滤噪音、生成摘要 | 也可换其他 LLM |
| 通知 | 飞书自定义机器人 | 必须，二选一 | 推送趋势提醒到飞书群 | 免费，需创建 webhook |
| 通知 | 企业微信群机器人 | 必须，二选一 | 推送到企业微信群 | 免费，需企业微信群权限 |
| 服务器 | 云服务器 / VPS | 推荐必须 | 定时运行任务 | 阿里云、腾讯云、AWS、Render 等均可 |
| 数据库 | PostgreSQL 云数据库 | 可选 | 正式版存储数据 | MVP 可先 SQLite |
| 监控 | Sentry / UptimeRobot | 可选 | 监控任务失败 | MVP 可先用日志 |
| 图表 | matplotlib / plotly | 免费 | 生成趋势图 | 本地库，不需要购买 |
| 备用 API | SerpApi / SearchApi | 可选 | DataForSEO 备用 | 后期多供应商冗余 |
| 搜索量数据 | DataForSEO Labs / Google Ads Keyword Planner | 扩展版推荐 | 判断关键词真实 SEO 价值 | Keyword Planner 需要 Google Ads 账号 |
| 代理服务 | Bright Data / ScrapingBee | 暂不推荐 | 自建抓 Google Trends 时用 | 如果用 DataForSEO，前期不需要 |

### DataForSEO 采购建议

优先使用 Standard Queue，不要一开始用 Live。

原因：

- Standard 便宜，适合每天定时跑。
- Live 适合实时查询，但成本更高。
- 当前场景是“每天监控”，不是用户实时搜索，所以 Standard 足够。

当前参考价格：

- Standard Queue：约 $0.0027/task。
- Live Mode：约 $0.011/task。
- 每个 task 最多 5 个关键词。

参考资料：

- DataForSEO Google Trends API：https://docs.dataforseo.com/v3/keywords_data-google_trends-overview/
- DataForSEO Pricing：https://dataforseo.com/pricing/keywords-data/google-trends
- 飞书自定义机器人：https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=zh-CN
- 企业微信群机器人：https://cloud.tencent.com/document/product/1263/71731
- Google Trends API Alpha：https://developers.google.com/search/apis/trends

## 推荐执行节奏

### 第 1 周：数据源和采集

完成：

- 配置 CrazyGames、Poki、Y8。
- 读取 sitemap。
- 提取游戏 URL、slug、标题。
- 存入数据库。
- 每天 diff 新游戏。

产出：

```text
每日新增游戏列表
每日新增候选关键词列表
```

### 第 2 周：接入 Trends API

完成：

- 注册 DataForSEO。
- 接入 Google Trends API。
- 查询趋势曲线。
- 获取 related queries 和 related topics。
- 保存原始结果。

产出：

```text
每个候选词的趋势数据
每个候选词的 rising queries
```

### 第 3 周：评分和 AI 过滤

完成：

- 新词去重。
- 趋势增长评分。
- AI 判断搜索意图。
- 过滤音乐、影视、新闻、人物等噪音。
- 生成推送摘要。

产出：

```text
高价值关键词清单
观察池关键词清单
噪音词清单
```

### 第 4 周：通知和复盘

完成：

- 接入飞书/企业微信 webhook。
- 生成趋势图。
- 自动推送。
- 添加人工标记字段。
- 每周复盘有效率。

产出：

```text
自动趋势提醒机器人
关键词发现日报
```

## 最终建议

第一阶段不要做大平台，先做一个能稳定跑的 MVP：

```text
3 个游戏站
+
DataForSEO
+
SQLite/PostgreSQL
+
AI 意图过滤
+
飞书/企业微信推送
```

等验证“每天确实能发现有价值的新词”以后，再扩展更多站点、更多国家、多语言和后台看板。
