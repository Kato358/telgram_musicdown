-- 004: 去掉同步子系统的三列（自动同步开关、同步间隔、同步游标）。
-- 决策：不做任何同步操作——加源只加源，入库只由搜索/粘贴链接/Bot 转发这些手动动作触发。
-- 无人再读写这三列，留着只会让 sources 表「看起来有自动同步这个能力」。
-- SQLite 3.35+ 支持 DROP COLUMN；这三列上没有索引与约束。
ALTER TABLE sources DROP COLUMN auto_sync;
ALTER TABLE sources DROP COLUMN sync_interval_sec;
ALTER TABLE sources DROP COLUMN last_message_id;
