-- 006: 去掉 sources 表里**没有执行者**的配置列（媒体范围、源级过滤、三项源级覆盖、备注）。
-- 决策：这六列从未进过任何链路——`scope_allows` / `SourceFilters` 只有单测在调（生产代码零调用），
-- 三项 override 没进过 `render_path`（它只吃全局 TemplateConfig），`note` 没有任何界面或链路读取；
-- 而 `PUT /api/sources/{id}` 每次还把这三项 override 与 note 无条件写成 NULL。
-- 留着只会让 sources 表「看起来有按源过滤与按源命名覆盖这两个能力」——同 004 的理由，一并删掉。
-- SQLite 3.35+ 支持 DROP COLUMN；这六列上没有索引与约束（唯一约束只在 telegram_chat_id 上）。
ALTER TABLE sources DROP COLUMN media_scope;
ALTER TABLE sources DROP COLUMN filters_json;
ALTER TABLE sources DROP COLUMN save_path_override;
ALTER TABLE sources DROP COLUMN dir_template_override;
ALTER TABLE sources DROP COLUMN file_template_override;
ALTER TABLE sources DROP COLUMN note;
