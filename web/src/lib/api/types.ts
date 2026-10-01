/** 后端 API 的响应类型（app/web/routes/schemas.py、__init__.py 的 *_dict 输出）。
 *
 * 字段名与后端 JSON 严格一致：这里是唯一契约，页面不得自行发明字段。
 */

export interface MeResponse {
  display_name: string | null;
  username: string | null;
  premium: boolean;
  connected: boolean;
}

/** 代理（回读时只有协议/地址/端口，用户名密码不出网，NFR-02）。 */
export interface SetupProxy {
  scheme: string;
  hostname: string;
  port: number;
  username?: string | null;
  password?: string | null;
}

/** `GET /api/setup/status`：放行 = 密钥 + 登录（源可选，向导第 3 步）。 */
export interface SetupStatus {
  complete: boolean;
  has_api_id: boolean;
  has_api_hash: boolean;
  has_bot_token: boolean;
  /** 搜索模式（FR-SEARCH-01）：`sources` = 逐源搜索（需要音乐源）/ `global` = 全账号搜索。 */
  search_mode: string;
  proxy: SetupProxy | null;
  connected: boolean;
  display_name: string | null;
  username: string | null;
  /** 在线源：只回「有没有 Key」与「开没开」，不回明文（NFR-02）。 */
  has_chksz_key: boolean;
  chksz_enabled: boolean;
}

/** 提交的代理（用户名密码可选；回读时不返回，NFR-02）。 */
export interface SetupProxyInput {
  scheme: string;
  hostname: string;
  port: number;
  username?: string;
  password?: string;
}

export interface SetupSecretsPayload {
  api_id?: number;
  api_hash?: string;
  bot_token?: string;
  /** 明确给 `null` = 清除 config.yaml 里的代理段（关掉「走代理」后保存）。 */
  proxy?: SetupProxyInput | null;
  /** ChKSz 在线源 Key（只写不回显；留空 = 不改动）。 */
  chksz_api_key?: string;
}

/** 音质档位（语义值，跨平台同名）：`320k` 在网易那边叫 exhigh，映射由服务端做。
 *
 *  `sky` / `jyeffect` 是网易独有两套**混音**（杜比全景声、臻品音效），不是更高保真的
 *  版本，QQ 与酷狗没有。列表里排在保真度阶梯之下（见 `app/chksz/quality.py`）。 */
export type QualityTier =
  | "128k"
  | "320k"
  | "lossless"
  | "hires"
  | "master"
  | "sky"
  | "jyeffect";

export interface QualityOption {
  tier: QualityTier;
  label: string;
  /** 该平台最高档。 */
  best: boolean;
}

/** `GET /api/settings/qualities`：各在线源平台的音质阶梯（服务端唯一数据源）。 */
export interface QualitiesResponse {
  providers: Record<string, QualityOption[]>;
}

export interface SendCodeResponse {
  code_hash: string;
  /** 已有有效会话：没发码，直接可用。 */
  authorized: boolean;
  me: MeResponse | null;
}

/** Web 控制台准入（`GET /api/auth/session`，FR-WEB-02）。
 *
 * `required=false` 是免登录模式：本机免密，或 `web_login_enabled` 显式关闭登录。
 * 前端据此直接渲染控制台，不出现登录页。 */
export interface WebSessionStatus {
  required: boolean;
  authenticated: boolean;
}

/** 候选源（`GET /api/sources/discover`，FR-SRC-05）。 */
export interface DiscoverCandidate {
  chat_id: number;
  title: string;
  username: string | null;
  type: string;
  /** 成员数；Telegram 没给就是 null，不猜。 */
  members: number | null;
  tags: string[];
}

export interface DiscoverResponse {
  items: DiscoverCandidate[];
}

/** `GET /api/sources` 的行（FR-SRC-01）：只有身份与启用开关，没有别的配置。 */
export interface SourceRow {
  id: number;
  telegram_chat_id: number;
  username: string | null;
  title: string;
  type: string;
  enabled: boolean;
}

/** 在线源平台（`GET /api/sources/online`）：音乐源页逐平台启停的行。
 *
 *  `id` 就是搜索用的 scope——与 `SearchSource.id` 是同一套负号保留值（频道是 sources.id），
 *  所以行上的开关拨完，搜索页药丸的勾选值不用换一套。三行恒定、顺序同后端 `PROVIDER_SCOPES`。 */
export interface OnlineSourceRow {
  id: number;
  provider: string;
  title: string;
  enabled: boolean;
}

/** `GET /api/sources/online`：`has_key` 只是提示（没 Key 时开关照样拨得动，只是不生效）。 */
export interface OnlineSourcesResponse {
  has_key: boolean;
  providers: OnlineSourceRow[];
}

export interface TaskRow {
  id: number;
  type: string;
  status: TaskStatus;
  title: string | null;
  artist: string | null;
  /** 关联 history 行（或入队 meta）带来的专辑与时长：队列行的元信息用。 */
  album: string | null;
  duration_sec: number | null;
  progress_bytes: number;
  speed: number | null;
  total_bytes: number | null;
  retry_count: number;
  error: string | null;
}

export type TaskStatus =
  "queued" | "downloading" | "paused" | "success" | "failed" | "skipped" | "cancelled";

export interface HistoryRow {
  id: number;
  source_id: number | null;
  chat_id: number;
  message_id: number;
  title: string | null;
  artist: string | null;
  album: string | null;
  duration_sec: number | null;
  file_size: number | null;
  /** 音频码率（kbps，Telegram 元数据给得出才有）：下载页「码率」列用。 */
  bitrate: number | null;
  save_path: string | null;
  status: string;
  error: string | null;
  created_at: string;
  /** 这条记录当前挂着的任务 id（台账被删过则为 null）：实时读数与暂停/继续/取消用它。 */
  task_id: number | null;
}

/** `GET /api/stats`：统计卡与系统状态的数据源（后端一次算全，前端不自己数列表）。 */
export interface StatsResponse {
  tasks: {
    queued: number;
    downloading: number;
    paused: number;
    failed: number;
    success: number;
  };
  /** `tracks` 只算已写盘的行（save_path 非空）。 */
  library: {
    tracks: number;
    bytes: number;
    failed: number;
    /** 本地曲库台账（local_tracks 扫描索引）读数：侧栏曲库卡与曲库页同一套数字。 */
    local_present: number;
    local_missing: number;
    local_bytes: number;
  };
  sources: {
    total: number;
    enabled: number;
  };
  uptime_sec: number;
}

/** 本地曲库行（`GET /api/local-library`，扫描 downloads 落盘文件的台账）。 */
export interface LocalTrackRow {
  id: number;
  rel_path: string;
  file_name: string;
  ext: string | null;
  title: string | null;
  artist: string | null;
  album: string | null;
  duration_sec: number | null;
  file_size: number | null;
  bitrate: number | null;
  /** 文件已不在磁盘（记录保留，可重新下载）。 */
  missing: boolean;
  /** 挂着的下载记录 id（该记录被删则为 null，重新下载不可用）。 */
  history_id: number | null;
  chat_id: number | null;
  message_id: number | null;
  first_seen_at: string;
  scanned_at: string;
}

/** `GET /api/local-library`：items + 当前筛选 total + 全局计数与占用。 */
export interface LocalLibraryResponse {
  items: LocalTrackRow[];
  total: number;
  counts: { present: number; missing: number; all: number };
  bytes: number;
}

/** 试听 / 封面缓存占用（`GET /api/settings/cache`，FR-PLAY-02 + FR-DL-08）。
 *
 * 口径是磁盘实际字节：`preview_*`（LRU，受 `max_bytes` 与 50 条约束）与 `cover_*`
 * （同目录、按 mtime 一起进同一预算）。`browser_*` 是浏览器下载**待取走**的临时文件：
 * 不进那份字节预算（另有 TTL 与并存上限），单列显示，「清理缓存」会一并清掉。
 * 清理接口返回清理后的同一结构。 */
export interface CacheStats {
  total_bytes: number;
  max_bytes: number;
  preview_bytes: number;
  preview_count: number;
  cover_bytes: number;
  cover_count: number;
  /** 搜索二级缓存（L2）：条数与 payload 字节，不进试听/封面那份字节预算。 */
  search_entries: number;
  search_bytes: number;
  browser_bytes: number;
  browser_count: number;
}

export interface SearchResult {  chat_id: number;
  message_id: number;
  title: string | null;
  artist: string | null;
  duration_sec: number | null;
  file_size: number | null;
  ext: string | null;
  mime: string | null;
  channel_title: string | null;
  message_date: string | null;
  caption: string | null;
  file_unique_id: string | null;
  /** 音频来源：`telegram` = 音乐源频道，其余为在线源平台（163/qq/kugo）。 */
  provider: string;
  /** 在线源曲目的平台 id；下载时原样回传（Telegram 行为 null）。 */
  ref: string | null;
}

/** 可搜来源（`GET /api/search/sources`）：音乐源频道与在线源平台的同一份清单。 */
export interface SearchSource {
  /** 勾选用的 id：频道是 sources.id（≥1），在线源是保留负号 scope。 */
  id: number;
  title: string;
  provider: string;
  online: boolean;
}

export interface SearchResponse {
  results: SearchResult[];
  meta: Record<string, unknown>;
}

/** `POST /api/search/browser-download`（FR-DL-08）的 SSE 事件。
 *
 *  服务端边取回边推：若干条 `preparing`（字节进度，`total` 可能报不出来）→ 一条终态。
 *  `ready` 带的 `url` 是 `Content-Disposition: attachment` 的附件流：浏览器顶层导航过去
 *  （cookie 会话随请求带上）即落进**本机**下载目录，页面不跳走。该地址在服务端 TTL
 *  （默认 2 小时）内**可重复取用**——浏览器分段取（Range）与暂停后续传都要靠它；
 *  过期或被淘汰后是 404，重新点按钮即可（导航前先 `HEAD` 探一次，见 search store）。
 *
 *  失败走流内终态，形状仍是标准错误包络（SDD §4.1）：取数失败要到途中才知道，
 *  那时响应头早发出去了，状态码改不动。 */
export interface BrowserDownloadEvent {
  state: "preparing" | "ready" | "failed";
  /** `preparing`：已写字节 / 总字节（总量报不出来时是 null，界面退回不确定态）。 */
  loaded?: number;
  total?: number | null;
  /** `ready`：附件定位与浏览器保存的文件名（曲库同一份模板 + 扩展名）。 */
  token?: string;
  file_name?: string;
  size?: number;
  url?: string;
  /** `failed`：标准错误包络。 */
  error?: { code?: string; message?: string; reason?: string };
}

export interface DownloadItemResult {
  chat_id?: number;
  message_id?: number;
  task_id?: number;
  url?: string;
  error?: string;
}

/** SSE 事件载荷（app/events.py，SDD §1.4）。 */
export interface TaskProgressEvent {
  task_id: number;
  progress_bytes: number;
  total_bytes: number | null;
  speed: number | null;
  eta: number | null;
}

export interface TaskStatusEvent {
  task_id: number;
  status: TaskStatus;
  error: string | null;
}

export interface LogErrorEvent {
  message: string;
  ts: number;
  logger?: string;
}

/** 运行日志（GET /api/logs，app/web/routes/logs.py）。 */
export interface LogEntry {
  ts: number;
  level: string;
  logger: string;
  message: string;
}

export interface LogFile {
  name: string;
  size: number;
  mtime: number;
}

export interface LogsResponse {
  entries: LogEntry[];
  files: LogFile[];
  active_file: string;
  file_size: number;
  truncated: boolean;
}
