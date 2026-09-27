-- 007: 在线源开关由「一个总布尔」细化为「逐平台开关」，老总开关迁进新键后退休。
-- 判据：音乐源页要能逐平台启停（网易云 / QQ 音乐 / 酷狗），而「总开关 + 逐平台开关」两层门
-- 会让逐平台开关在总开关关着时点了没反应。故 chksz_providers 是唯一事实源，
-- chksz_enabled 不再是配置项：迁移前它还是唯一事实，故按它的取值播种新键（认
-- true/1/on/yes 即三个平台全开 —— 与旧 _bool_setting 同一套认法），播种后删掉。
-- 平台键顺序照 app.domain.PROVIDER_SCOPES（{"163","qq","kugo"}），SQL 里只能写字面量。
INSERT INTO settings (key, value)
SELECT
    'chksz_providers',
    CASE
        WHEN lower(trim(COALESCE((SELECT value FROM settings WHERE key = 'chksz_enabled'), '')))
             IN ('true', '1', 'on', 'yes')
        THEN '163,qq,kugo'
        ELSE ''
    END
WHERE NOT EXISTS (SELECT 1 FROM settings WHERE key = 'chksz_providers');

DELETE FROM settings WHERE key = 'chksz_enabled';
