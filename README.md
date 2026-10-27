# SakuraMedia JavBus 元数据插件

JavDB 未收录影片时，从 JavBus 按番号补全元数据。

## 工作方式

- 只按番号查询，不提供关键词搜索、浏览或榜单。
- JavDB 始终优先；只有 JavDB 明确未找到时才调用本插件。JavDB 超时、限流等错误不会触发回退。
- 适用于按番号导入、媒体文件导入，以及导入失败项的手动元数据匹配。
- 提供番号、标题、发行日期、时长（均必填）、片商、导演、系列、演员、标签、封面和剧照。JavBus 没有简介，简介为空。
- 演员和标签交给宿主处理：演员用姓名匹配 JavDB 演员资料，无法明确匹配的丢弃；标签按名称复用或新建。
- 封面和剧照下载到插件 `data/metadata-tmp/` 交付，宿主导入后删除。
- 补缺的影片没有 JavDB ID，宿主会定时尝试从 JavDB 补录。

## 环境要求

宿主需支持 `catalog.metadata_source` 插件扩展点与插件多语言基座（Host API 12 及以上）。

## 安装

将插件根目录打包为 zip（根目录包含 `manifest.json`），从 SakuraMedia 的「系统设置 → 插件」安装并启用，然后重启 API 和 APS 服务。

## 配置

在插件管理页配置，保存后重启服务生效。

```json
{
  "timeout_seconds": 20
}
```

`timeout_seconds` 是单次请求超时时间（秒），范围 1–120，默认 20。


## 多语言 / i18n

本插件声明 Host API 12，支持 `zh-CN` / `en`：

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
