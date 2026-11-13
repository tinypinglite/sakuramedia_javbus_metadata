# SakuraMedia JavBus 元数据插件

按番号从 JavBus 提供影片元数据，并支持订阅演员影片列表同步，可作为宿主的元数据来源之一。

## 工作方式

- 元数据只按番号查询；演员同步只使用宿主传入的演员名/别名解析 JavBus star ID。
- 宿主按元数据源启用顺序依次尝试，命中第一个成功来源后停止；建议把资料更全的来源排在 JavBus 前面。
- 适用于按番号导入、媒体文件导入，以及导入失败项的手动元数据匹配。
- 提供番号、标题、发行日期、时长（均必填）、片商、导演、系列、演员、标签、封面和剧照。JavBus 没有简介，简介为空。
- 演员只交付姓名，本地匹配与资料补全交给宿主和演员资料补全插件；标签按名称复用或新建。
- JavBus 没有评分、评论和互动数，本插件不提供这些数据。
- 元数据源用于新建影片和人工匹配导入；已存在的番号直接复用，不会刷新。
- 封面和剧照下载到插件 `data/metadata-tmp/` 交付，宿主导入后删除。
- 订阅演员同步首次自动全量，后续按发行日期增量；使用 `existmag=all` 获取演员全部作品，不只取有磁力的影片。

## 环境要求

宿主需支持 `catalog.metadata_source` 插件扩展点与插件多语言基座（Host API 18 及以上）。

## 安装

将插件根目录打包为 zip（根目录包含 `manifest.json`），从 SakuraMedia 的「系统设置 → 插件」安装并启用，然后重启 API 和 APS 服务。

## 配置

在插件管理页配置，保存后重启服务生效。

```json
{
  "timeout_seconds": 20,
  "min_request_interval_seconds": 3.0
}
```

`timeout_seconds` 是单次请求超时时间（秒），范围 1–120，默认 20。

`min_request_interval_seconds` 是 APS 内 JavBus 页面请求的最小间隔，默认 3 秒，
范围 0.5–60 秒。API 进程不启用该限速；图片下载不计入。

## 订阅演员同步

- 后端定时任务默认传 `mode=incremental`，但插件在本地没有该演员状态时仍会自动执行首次全量。
- 手动触发 `mode=full` 会强制重新全量扫描。
- 状态保存在 `data/javbus/actors.json`：JavBus star ID 列表、演员姓名/别名/性别指纹、
  全量完成时间、最后发行日期和本轮 pending 日期。v1 的单值 `source_actor_id`
  读取时会自动迁移为列表。
- 同一演员在 JavBus 可能对应多个 star 页面（有码/无码、历史拆页），检索命中多个时
  全部保留，按页面顺序依次翻完并合并番号。
- 翻页游标形如 `<star 下标>:<页码>`（如 `1:3`），宿主只当作不透明字符串回传。
- 全量只有翻完所有 star 的所有页后才写 `full_synced_at`；中途失败不会误标记为已完成。
- 增量只取最后发行日期前 30 天内的作品，遇到更旧日期即停止当前 star；宿主会按番号去重。
- 演员页 404 时会清除旧 star ID 并重新解析；单个 star 首页 404 只跳过该 star，
  人机验证或第二页及以后异常按来源失败处理。


## 多语言 / i18n

本插件声明 Host API 18，支持 `zh-CN` / `en`：

- 语言包放在 `i18n/zh-CN.json`、`i18n/en.json`；`translations.py` 只负责在
  插件导入时读取它们并生成静态 `*_i18n` 字典。
- 插件名：`manifest.json` 的 `display_name_i18n` +
  `PluginRegistration.display_name_i18n`（两种语言都是 “JavBus”）。
- 设置项标题：Pydantic `Field` 的 `json_schema_extra={"title_i18n": ...}`。
- 运行时文本（页面缺少必填字段、站点要求人机验证等失败原因）：
  `context.i18n.t(key, default=..., **params)`。`register()` 把 `context.i18n`
  注入 `JavBusMetadataSource`，消息按当次请求语言生成。
- 解析 JavBus 页面用的站点字段名（`識別碼` / `發行日期` / `長度` 等）与日期、
  时长正则是站点页面原文，不参与翻译。
- 新增语言时，在 `i18n/` 增加同名 JSON，并补 manifest / registration 的
  `display_name_i18n`。


## License

本项目采用 [PolyForm Noncommercial License 1.0.0](./LICENSE)，仅允许非商业用途。

Copyright (C) 2026 tinypinglite
