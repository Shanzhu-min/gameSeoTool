# 候选关键词筛选与每日机会报告设计建议

## 1. 背景

当前 MVP 已完成基础闭环：

```text
游戏站 sitemap 发现候选游戏词
-> 关键词清洗与 canonical 合并
-> 查询 DataForSEO Trends
-> 根据趋势结果判断 push / observe / drop
-> Web 后台展示与导出
```

这个流程已经可以运行，但候选词质量仍有明显优化空间。

当前主要问题：

- Y8、Poki、CrazyGames 等游戏站游戏量很大，包含大量老游戏。
- sitemap 会不断把历史游戏页面带入系统。
- 部分老游戏、泛词、普通长尾词会进入分析流程，消耗 API 成本。
- 用户看到过多候选词后，仍然需要去 Google、YouTube、Roblox、域名工具等平台做二次验证。
- 如果每天输出太多词，业务人员早上无法在 5-10 分钟内完成判断。

下一阶段目标不是发现更多词，而是减少低质量词，让系统每天输出少量、有证据支撑、值得研究的机会词。

## 2. 产品目标

建议将产品定位从：

```text
趋势查询工具
```

升级为：

```text
游戏机会词研究助手
```

核心目标：

- 每天默认输出 3-5 个高质量候选词。
- 如果当天有明显强机会，可以超过 5 个。
- 如果当天没有好机会，可以少于 3 个，甚至为 0。
- 低于推荐分数线的词不主动展示给用户。
- 每个推荐词都需要提供充分证据，帮助用户直接判断是否值得投入。
- 尽量避免用户再去多个外部平台手动查证。

这里的 3-5 个不是系统发现上限，而是最终推荐上限。

## 3. 推荐三道漏斗

建议将整体流程设计成三道漏斗。

### 3.1 第一道：候选词预筛

假设当天从 sitemap 和外部来源发现 50 个候选词，第一步不应该全部发给 DataForSEO Trends，而是先做候选词预筛。

这一层判断的是：

```text
这个词是否值得花钱查趋势？
```

主要依据：

- 是否近期首次发现。
- 是否来自高质量游戏站。
- 是否像真实游戏名。
- 是否出现在多个游戏站。
- Google 是否有近期搜索证据。
- YouTube 是否有近期视频证据。
- Roblox 是否有相关游戏信号。
- 域名是否有注册或可用性信号。
- 是否属于老游戏、泛词、噪音词。

目标：

```text
50 个候选词 -> 筛到约 15-20 个进入 DataForSEO
```

### 3.2 第二道：DataForSEO 趋势验证

通过第一道预筛的词，再调用 DataForSEO Trends。

这一层判断的是：

```text
这个词是否真的有搜索趋势机会？
```

重点看：

- Trends 首日值。
- Trends 末日值。
- 近 7 天平均值。
- 增长率。
- 峰值。
- 是否从低位启动。
- 是否已经下滑。
- 是否有 related rising queries。
- 是否出现 codes、wiki、tier list、guide 等需求。

目标：

```text
15-20 个趋势验证词 -> 筛到约 8-10 个机会词
```

### 3.3 第三道：最终机会评分

DataForSEO 返回后，再结合第一道外部证据，计算最终机会分。

建议：

```text
最终机会分 = 候选词证据分 + 趋势验证分 + 商业可行动分
```

分层建议：

| 分数 | 处理 |
|---|---|
| >= 80 | 强推荐，进入今日报告前排 |
| 60-79 | 推荐，进入今日报告 |
| 45-59 | 观察池，不主动打扰用户 |
| < 45 | 丢弃或归档 |

低于 60 分的词，不进入用户每日早报。

目标：

```text
8-10 个机会词 -> 输出 3-5 个最终推荐词
```

## 4. 第一步：游戏站发现优化

当前游戏站来源包括：

- CrazyGames
- Poki
- Y8

后续可以继续增加更多游戏站。

### 4.1 当前 sitemap 策略

当前 MVP 从配置文件读取每个站点的：

- 站点名称。
- sitemap 地址。
- 游戏页面 URL 特征。
- 站点权重。

系统读取 sitemap 后，会执行：

1. 如果 sitemap 是索引文件，先展开子 sitemap。
2. 优先选择 URL 中包含 `game` 或 `games` 的子 sitemap。
3. 限制每次处理的 sitemap 数量，避免一次抓取过多。
4. 读取 sitemap 中的页面 URL。
5. 只保留符合游戏页面 URL 特征的页面。
6. 从 URL 最后一段提取游戏名。
7. 去重并写入数据库。

示例：

```text
https://www.y8.com/games/connect_4
```

会被识别为：

```text
slug = connect_4
title = connect 4
```

### 4.2 建议增加的判断因素

后续应从“是否像游戏页面”升级为“是否值得继续研究”。

| 判断项 | 指标 | 作用 |
|---|---|---|
| 是否首次发现 | 本系统 `first_seen_at` 是否在 1-7 天内 | 判断是否是新进入视野的词 |
| sitemap 更新时间 | `lastmod` 是否在 7-14 天内 | 判断页面是否近期新增或更新 |
| 来源站点权重 | CrazyGames / Poki / Y8 等站点不同权重 | 高质量站点来源可加分 |
| 多站点出现 | 是否同时出现在多个游戏站 | 多站点出现说明传播更广 |
| URL/title 是否干净 | 是否像真实游戏名，而不是分类页、合集页 | 降低噪音 |
| 游戏名长度 | 2-6 个单词优先 | 太短可能泛，太长可能噪音 |
| 是否包含泛词 | car game、basketball game 等 | 泛词降权或丢弃 |
| 是否包含平台词 | poki、y8、crazygames 等 | 作为变体，不作为主词 |

这一层的目标是：

```text
判断它是否是一个值得继续查证的游戏名。
```

## 5. 第二步：关键词合并与清洗优化

当前 MVP 已支持 canonical 合并。

例如：

```text
connect 4
connect 4 game
connect 4 online
connect 4 y8
```

会合并为：

```text
主关键词：connect 4
长尾变体：connect 4 game / connect 4 online / connect 4 y8
```

### 5.1 当前已处理的修饰词

系统当前会去掉尾部修饰词，例如：

- game
- games
- online
- play
- free
- unblocked
- poki
- crazygames
- y8
- coolmath

### 5.2 建议继续增强的清洗规则

| 清洗项 | 示例 | 建议 |
|---|---|---|
| 去平台尾词 | connect 4 y8 | 平台词保留为变体，不作为主词 |
| 去搜索修饰词 | connect 4 online | 搜索修饰词保留为长尾，不重复查趋势 |
| 过滤泛词 | car game、shooting game | 降权或进入低优先级池 |
| 过滤老牌游戏 | connect 4、gold miner | 除非有新增长证据，否则不推 |
| 保留品牌词 | anime squadron、soul land awakening world | 这类更可能是新机会 |
| 识别错拼与近似词 | kitten canons / kitten cannons | 后续可做相似词聚合 |
| 中英文或多语言变体 | soul land / 斗罗大陆 | 后续可考虑多语言映射 |

这一层的目标是：

```text
一个真实机会，只保留一个主关键词。
```

## 6. 老游戏识别与生命周期机制

Y8、Poki 等站点游戏量很大，且包含大量老游戏。系统必须识别“已经处理过的老游戏”，避免每天重复分析。

建议给每个 canonical keyword 增加长期生命周期状态。

### 6.1 建议状态

| 状态 | 含义 | 后续处理 |
|---|---|---|
| `new_candidate` | 新发现，尚未充分验证 | 进入预筛 |
| `watching` | 有一点信号，但不够强 | 隔几天复查 |
| `recommended` | 已进入每日报告 | 记录推荐时间，短期不重复推荐 |
| `old_game` | 已确认是老游戏 | 不再每日分析 |
| `noise` | 泛词、非游戏、歧义词 | 长期排除 |
| `archived` | 历史处理完毕 | 仅保留记录 |

核心规则：

```text
同一个 canonical keyword 一旦被确认是 old_game，
后续 sitemap 再看到它，只更新 last_seen_at，
不再进入每日候选池。
```

### 6.2 old_game 触发条件

建议满足以下条件之一或多个时，标记为 `old_game`：

- 系统已经连续多次看到，但没有增长趋势。
- DataForSEO 显示 `old_or_declining`。
- Google 搜索结果长期存在，但没有近期爆发。
- YouTube 近期没有明显新增内容。
- 首次发现时间已经超过 30 天。
- 已经被 `drop` 多次。
- 明显是常青老游戏，例如 connect 4、gold miner。

### 6.3 冷却机制

不同类型的低质量词，不应每天重复分析。

| 情况 | 建议冷却时间 | 说明 |
|---|---|---|
| 无趋势数据 | 7 天 | 短期内不再查趋势 |
| old_or_declining | 30 天 | 老词或下滑词不重复打扰 |
| watching 但无增长 | 3-7 天 | 保持低频观察 |
| recommended | 14-30 天 | 避免重复推荐同一机会 |
| noise | 永久排除 | 除非人工恢复 |
| old_game | 30-90 天或长期排除 | 仅在出现强新证据时重新打开 |

### 6.4 重新打开条件

即使某个词被标记为 old_game，也应允许在出现强信号时重新进入分析。

重新打开条件可以包括：

- Google 近 7 天出现大量 exact match 新结果。
- YouTube 近 7 天视频数量或播放量明显增长。
- Roblox 出现新版本、新活动或在线人数明显上升。
- DataForSEO related rising 出现 codes、wiki、tier list 等新需求。
- 人工手动恢复。

这样既能避免重复分析老游戏，又不会错过老游戏突然复燃的机会。

## 7. DataForSEO 前的外部证据预筛

当前 MVP 是先把主关键词发送给 DataForSEO，再看趋势质量。

下一阶段建议改为：

```text
先收集外部证据
-> 预筛出值得研究的词
-> 再发送给 DataForSEO Trends
```

这样可以降低 API 成本，也能减少低质量结果给用户造成的分析压力。

需要注意：

- DataForSEO Trends 是第二道验证。
- 如果使用 DataForSEO Google SERP API，它可以作为第一道外部证据的一部分。
- 第一层可以使用 Google SERP / YouTube / Roblox / RDAP 等证据。
- 第二层再使用 DataForSEO Trends 判断趋势质量。

## 8. 外部证据来源建议

### 8.1 Google 搜索证据

目标：

```text
判断这个词是否近期开始被网页讨论。
```

建议指标：

| 指标 | 含义 |
|---|---|
| 近 7 天/14 天是否有搜索结果 | 判断近期是否有新增讨论 |
| 搜索结果标题是否精确匹配游戏名 | 精确匹配越多，可信度越高 |
| 是否出现 codes / wiki / tier list / release date | 说明玩家搜索需求已经出现 |
| 搜索结果数量是否过高 | 过高可能是老词或泛词 |
| 是否存在非游戏歧义 | 例如音乐、电影、人物、软件等 |

建议：

- 如果使用 Google 官方 Custom Search，需要关注额度、价格和可用性限制。
- 如果希望稳定商业化运行，可以优先考虑 DataForSEO Google SERP API。
- Google SERP 证据建议用于预筛，不建议单独决定是否推荐。

### 8.2 YouTube 视频证据

目标：

```text
判断玩家内容是否已经开始出现。
```

建议指标：

| 指标 | 含义 |
|---|---|
| 近 3 天视频数 | 极早期传播信号 |
| 近 7 天视频数 | 短期传播信号 |
| 近 14 天视频数 | 稳定增长信号 |
| 视频标题精确匹配数 | 判断是否指向同一个游戏 |
| 总播放量 | 需求规模 |
| 最高单视频播放量 | 是否出现爆点 |
| 发布频道数量 | 多个频道发布说明扩散更真实 |
| 标题关键词 | codes、gameplay、update、roblox、trailer 等 |

强信号示例：

```text
近 7 天有 12 个视频
3 个以上不同频道发布
最高视频 5 万播放
标题多次出现 exact keyword + codes
```

这种词即使 Google Trends 还没有明显上升，也值得进入 DataForSEO 验证。

### 8.3 Roblox 证据

目标：

```text
判断它是否是 Roblox 生态里的新游戏或新玩法。
```

建议指标：

| 指标 | 含义 |
|---|---|
| 是否有精确匹配体验 | 判断是不是 Roblox 游戏 |
| 创建日期 | 是否近期上线 |
| 最近更新时间 | 是否近期活跃 |
| 当前在线人数 | 当前热度 |
| visits / favorites | 累计规模 |
| creator / group | 是否来自可信开发者 |
| 是否出现 codes 需求 | Roblox SEO 中非常重要 |

建议：

- Roblox 数据适合作为增强证据。
- 不建议让 MVP 强依赖 Roblox API，因为部分数据接口稳定性和权限需要进一步验证。
- 可以先做搜索链接和人工可查证字段，再逐步接入自动化数据。

### 8.4 域名注册证据

目标：

```text
判断是否已经有人开始围绕该词布局网站或攻略站。
```

建议检查域名：

```text
keyword.com
keyword.net
keyword.org
keyword.wiki
keyword.gg
keyword.io
keywordcodes.com
keywordwiki.com
```

建议指标：

| 指标 | 含义 |
|---|---|
| 是否已注册 | 有人抢注说明市场有动作 |
| 注册日期是否在 7/14/30 天内 | 越新越像机会 |
| 多个后缀同时被注册 | 强信号 |
| .wiki 是否被注册 | 游戏攻略词常见信号 |
| 是否仍可注册 | 对站群或内容站有价值 |

注意：

- 域名信号不能单独决定推荐。
- 很多好游戏没有被注册域名。
- 很多垃圾词也可能被批量抢注。
- 域名更适合作为加分项或辅助判断。

## 9. Evidence Score 预评分建议

建议在 DataForSEO Trends 前增加 Evidence Score。

满分 100，初始建议权重：

| 模块 | 权重 |
|---|---|
| 游戏站新发现 | 20 |
| Google 近期搜索证据 | 25 |
| YouTube 近期视频证据 | 25 |
| Roblox 证据 | 15 |
| 域名注册/可用性证据 | 10 |
| 去噪与歧义判断 | -30 到 0 |

建议分层：

```text
Evidence Score >= 60：进入 DataForSEO Trends 验证
Evidence Score 45-59：进入观察池
Evidence Score < 45：丢弃或仅归档
```

特殊放行规则：

```text
YouTube 近 7 天视频明显爆发
或 Roblox 当前在线人数明显高
或 Google 近 7 天出现大量 exact match 页面
```

即使总分未达到 60，也可以进入 DataForSEO Trends。

## 10. 最终机会评分建议

DataForSEO Trends 返回后，应结合前置证据计算最终 Opportunity Score。

建议评分结构：

| 模块 | 建议权重 |
|---|---|
| 前置 Evidence Score | 40 |
| Trends 趋势质量 | 35 |
| 商业可行动性 | 15 |
| 风险与噪音扣分 | -30 到 0 |

趋势质量重点指标：

- Trends 首日值。
- Trends 末日值。
- 近 7 天均值。
- 增长率。
- 峰值。
- 是否从低位启动。
- 是否已经下滑。
- related rising queries 是否有可行动需求。

商业可行动性重点指标：

- 是否适合做 wiki。
- 是否适合做 codes。
- 是否适合做 tier list。
- 是否适合做 beginner guide。
- 是否适合做 review / release date / gameplay 内容。

最终推荐规则：

```text
Opportunity Score >= 80：High，强推荐
Opportunity Score 60-79：Medium，推荐
Opportunity Score 45-59：Watch，观察
Opportunity Score < 45：Drop，丢弃
```

每日早报只展示 60 分以上的词。

## 11. 每日早报呈现建议

用户每天早上只有 5-10 分钟浏览新词报告，因此展示顺序必须是：

```text
先结论
再理由
再关键数据
最后才是完整证据
```

不要让用户先看截图。截图应该是证据附件，而不是第一屏主体。

### 11.1 今日摘要

第一屏需要 30 秒内看完。

建议展示：

```text
今日发现：50 个候选词
进入趋势验证：18 个
最终推荐：4 个
强推荐：1 个
观察池：7 个
```

然后展示推荐词清单：

```text
1. Soul Land Awakening World | High | Score 86
   判断：近 7 天视频增长明显，Google 出现 codes/wiki 需求，Trends 从 0 升至 96。

2. Anime Squadron | High | Score 82
   判断：Roblox 相关，近期搜索和视频同步增长。
```

### 11.2 推荐词卡片

每个推荐词卡片只展示最关键字段。

| 字段 | 示例 |
|---|---|
| 推荐等级 | High |
| 机会分 | 86 |
| 趋势判断 | passed |
| 趋势变化 | 0 -> 96 |
| 视频信号 | 近 7 天视频结果明显增长 |
| 搜索需求 | codes / wiki / tier list |
| 域名信号 | .wiki / .com 部分已注册 |
| 建议动作 | 做 wiki / codes / guide 内容 |

### 11.3 完整详情

详情页再展示完整证据，包括：

- 域名可用性。
- 基本信息。
- Trends 链接。
- Google Search 链接。
- Wiki Search 链接。
- Trends 截图。
- SEO 工具截图。
- Search 截图。
- YouTube 视频数据。
- Roblox 数据。
- 推荐理由。
- 风险提示。

## 12. 单个关键词报告结构建议

单个关键词报告建议采用以下结构：

```text
关键词名称
推荐等级
机会分
一句话结论
为什么推荐
关键数据
建议动作
详细证据
风险提示
```

示例：

```text
Soul Land Awakening World

推荐等级：High
机会分：86

一句话结论：
该词近期具备明显游戏 SEO 机会，YouTube 内容和 Google 搜索需求同步出现，Trends 从低位快速上升。

为什么推荐：
- 6 月新上线，具备新鲜度。
- Google 搜索结果出现 Steam、GameFAQs、视频结果。
- 相关需求包含 codes / wiki / PC / review。
- Trends 从 0 上升到 96。
- 部分核心域名已被注册，说明市场已有动作。

建议动作：
优先制作 wiki、codes、tier list、beginner guide 类内容。
```

具体模块：

| 模块 | 展示内容 |
|---|---|
| 来源 | 来自哪个游戏站、首次发现时间、原始 URL |
| 关键词组 | 主词 + 长尾变体 |
| Google | 最近结果、标题、日期、Search URL |
| YouTube | 近 7 天视频数、播放量、代表视频 |
| Roblox | 是否 Roblox 游戏、上线/更新时间、热度 |
| 域名 | 可用/已注册、注册日期 |
| Trends | DataForSEO 趋势图、峰值、最新值 |
| 判断 | 推荐、观察或丢弃的原因 |

## 13. 建议开发优先级

### 优先级一：关键词生命周期与冷却机制

先解决 Y8、Poki 等大站老游戏反复进入分析的问题。

需要新增：

- canonical keyword 生命周期状态。
- old_game 标识。
- noise 标识。
- watching / recommended 冷却期。
- 重新打开条件。

### 优先级二：候选词证据层

先接入：

- Google SERP 证据。
- YouTube 视频证据。
- RDAP 域名证据。

Roblox 可作为后续增强。

### 优先级三：Evidence Score

在 DataForSEO Trends 前先筛一层。

目标：

- 降低 API 成本。
- 减少低质量结果。
- 控制每天进入人工判断的词数量。

### 优先级四：最终 Opportunity Score

把前置证据、DataForSEO Trends、商业可行动性合并成一个最终推荐分。

低于 60 分的词不进入用户早报。

### 优先级五：每日机会报告

新增：

- 今日摘要。
- 推荐词卡片。
- 观察池。
- 完整详情报告。
- 导出 Markdown / HTML / 飞书消息。

### 优先级六：AI 总结

AI 不应该替代数据采集，但可以把数据翻译成业务判断。

例如：

```text
该词值得关注，因为近 7 天 YouTube 视频数量增加，Google 搜索结果出现 codes 和 wiki 需求，同时 Trends 最近值高于早期值。
```

## 14. 暂定结论

下一阶段重点不应放在“发现更多词”，而应放在：

```text
候选词来源质量
关键词清洗与合并质量
老游戏识别与冷却机制
DataForSEO 前的证据预筛
DataForSEO 后的二次验证
面向用户的每日机会报告
```

最终目标：

```text
不是让系统每天给用户一张关键词表，
而是每天早上给用户一份 5-10 分钟能看完、可信、有证据、可行动的游戏新词机会报告。
```

这版文档是讨论底稿，后续可以继续根据业务偏好、预算、API 可用性和真实数据表现调整评分权重与流程。
