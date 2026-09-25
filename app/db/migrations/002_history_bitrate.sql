-- 002: history 补 bitrate 列（下载页「码率」列，来自 Telegram 音频元数据）
ALTER TABLE history ADD COLUMN bitrate INTEGER;
