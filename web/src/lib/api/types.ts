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
  proxy: SetupProxy | null;
  connected: boolean;
  display_name: string | null;
  username: string | null;
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
}

export interface SendCodeResponse {
  code_hash: string;
  /** 已有有效会话：没发码，直接可用。 */
  authorized: boolean;
  me: MeResponse | null;
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

export interface SourceRow {
  id: number;
  telegram_chat_id: number;
  username: string | null;
  title: string;
  type: string;
  enabled: boolean;
  auto_sync: boolean;
  sync_interval_sec: number;
  last_message_id: number | null;
  media_scope: string[];
  note: string | null;
  /** 添加源时那条一次性导入任务的 id（最近 200 条，向导第 3 步）。 */
  import_task_id?: number | null;
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
  };
  sources: {
    total: number;
    enabled: number;
  };
  uptime_sec: number;
}

export interface SearchResult {
  chat_id: number;
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
}

export interface SearchResponse {
  results: SearchResult[];
  meta: Record<string, unknown>;
}

export interface DownloadItemResult {
  chat_id?: number;
  message_id?: number;
  task_id?: number;
  url?: string;
  error?: string;
}

export interface DownloadRequest {
  urls?: string[];
  message_refs?: { chat_id: number; message_id: number; file_size?: number | null }[];
  force?: boolean;
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
}
