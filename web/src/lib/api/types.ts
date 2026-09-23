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

export interface SetupStatus {
  complete: boolean;
  has_api_id: boolean;
  has_api_hash: boolean;
  has_bot_token: boolean;
  proxy: boolean;
  connected: boolean;
}

export interface SetupSecretsPayload {
  api_id?: number;
  api_hash?: string;
  bot_token?: string;
  proxy?: { scheme: string; hostname: string; port: number };
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
}

export interface TaskRow {
  id: number;
  type: string;
  status: TaskStatus;
  progress_bytes: number;
  total_bytes: number | null;
  retry_count: number;
  error: string | null;
}

export type TaskStatus =
  | "queued"
  | "downloading"
  | "paused"
  | "success"
  | "failed"
  | "skipped"
  | "cancelled";

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
  save_path: string | null;
  status: string;
  error: string | null;
  created_at: string;
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
