/** API 客户端：fetch 封装（编码规范 §5：组件内禁裸 fetch）。
 *
 * - 错误包络解包：{"error": {"code", "message"}}
 * - 401 跳登录（本版无独立登录页，仅清状态）
 */

interface ErrorEnvelope {
  error: { code: string; message: string; reason?: string };
}

export class ApiError extends Error {
  code: string;
  reason?: string;
  status: number;

  constructor(code: string, message: string, status: number, reason?: string) {
    super(message);
    this.code = code;
    this.reason = reason;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (resp.status === 401) {
    throw new ApiError("unauthorized", "需要登录", 401);
  }
  if (!resp.ok) {
    let envelope: ErrorEnvelope | null = null;
    try {
      envelope = (await resp.json()) as ErrorEnvelope;
    } catch {
      // 非 JSON 错误体
    }
    const code = envelope?.error?.code ?? "http_error";
    const message = envelope?.error?.message ?? `请求失败 (${resp.status})`;
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
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
