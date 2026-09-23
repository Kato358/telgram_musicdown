/** API 客户端：唯一 fetch 出口（docs/编码规范.md §5）。
 *
 * 职责：同源 cookie、错误包络解包（`{"error": {"code", "message", "reason"}}`）、
 * 401 归一化为 ApiError。页面不得自行调用 fetch。
 */

interface ErrorEnvelope {
  error: { code?: string; message?: string; reason?: string };
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly reason?: string;

  constructor(code: string, message: string, status: number, reason?: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.reason = reason;
  }

  /** 面向用户的一句话：优先后端 message，附带 reason 便于定位。 */
  get detail(): string {
    return this.reason ? `${this.message}（${this.reason}）` : this.message;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (resp.status === 401) {
    throw new ApiError("unauthorized", "会话已失效，请重新登录", 401);
  }
  if (!resp.ok) {
    let envelope: ErrorEnvelope | null = null;
    try {
      envelope = (await resp.json()) as ErrorEnvelope;
    } catch {
      // 非 JSON 错误体（网关/代理返回 HTML）：退回状态码说明
    }
    const code = envelope?.error?.code ?? "http_error";
    const message = envelope?.error?.message ?? `请求失败（HTTP ${resp.status}）`;
    throw new ApiError(code, message, resp.status, envelope?.error?.reason);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

/** 统一的错误文案提取：页面 catch 后用这一句。 */
export function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}
