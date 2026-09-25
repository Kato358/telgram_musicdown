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

/** API 请求超时：局域网正常请求毫秒级；卡死的请求到此为止并按 GET 重试一次，
 *  不给超时的话一次悬死的连接会一直占着浏览器的每主机并发额度。 */
const REQUEST_TIMEOUT_MS = 15_000;

async function fetchOnce(path: string, init: RequestInit): Promise<Response> {
  return fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  let resp: Response;
  try {
    resp = await fetchOnce(path, { ...init });
  } catch (err) {
    // 超时（TimeoutError）/中断（AbortError）/连接失败（TypeError）：
    // GET 幂等，静默重试一次；写操作不自动重放，直接报错
    const retryable = err instanceof TypeError || err instanceof DOMException;
    if (method !== "GET" || !retryable) throw err;
    try {
      resp = await fetchOnce(path, { ...init });
    } catch {
      throw new ApiError("network", "请求超时或连接失败，请稍后重试", 0);
    }
  }
  if (resp.status === 401) {
    throw new ApiError(
      "unauthorized",
      "Web 会话无效或缺失：这台部署设了 web_login_secret，浏览器要带有效的会话 cookie（tgm_session）",
      401,
    );
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
