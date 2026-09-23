/** 密钥类表单的校验判据（与 `app/services/setup.py` 同一套规则）。
 *
 * 前端只做即时反馈：写盘前的判据在服务端（`save_secrets` 校验合并后的 config.yaml），
 * 这里与后端不一致最多让用户多试一次，不会写出非法配置。向导与设置页共用这几个常量，
 * 避免两处各写一份正则后各自漂移。
 */

export const API_ID_RE = /^\d{5,10}$/;
export const API_HASH_RE = /^[0-9a-fA-F]{32}$/;
export const BOT_TOKEN_RE = /^\d{6,12}:[A-Za-z0-9_-]{30,}$/;
export const PORT_RE = /^\d{1,5}$/;
export const MAX_PORT = 65535;
