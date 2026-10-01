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
 *  不给超时的话一次悬死的连接会一直占着浏览器的每主机并发额度。
 *
 *  写操作可以自带一个更长的值（见 `api.post` 的 `timeoutMs`）；流式请求不用这里的口径，
 *  它是**空闲**超时（见 `ssePost`）。 */
const REQUEST_TIMEOUT_MS = 15_000;

async function fetchOnce(path: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  return fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
  });
}

/** 401 归一化（受保护部署的会话失效）：`request` 与 `ssePost` 共用一份文案。 */
function unauthorized(): ApiError {
  return new ApiError(
    "unauthorized",
    "Web 会话无效或缺失：这台部署设了 web_login_secret，浏览器要带有效的会话 cookie（tgm_session）",
    401,
  );
}

/** 非 2xx 的统一翻译：优先读标准错误包络（SDD §4.1），读不到就退回状态码说明。 */
async function failure(resp: Response): Promise<ApiError> {
  let envelope: ErrorEnvelope | null = null;
  try {
    envelope = (await resp.json()) as ErrorEnvelope;
  } catch {
    // 非 JSON 错误体（网关/代理返回 HTML）：退回状态码说明
  }
  const code = envelope?.error?.code ?? "http_error";
  const message = envelope?.error?.message ?? `请求失败（HTTP ${resp.status}）`;
  return new ApiError(code, message, resp.status, envelope?.error?.reason);
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const method = init?.method ?? "GET";
  let resp: Response;
  try {
    resp = await fetchOnce(path, { ...init }, timeoutMs);
  } catch (err) {
    // 超时（TimeoutError）/中断（AbortError）/连接失败（TypeError）：
    // GET 幂等，静默重试一次；写操作不自动重放，直接报错
    const retryable = err instanceof TypeError || err instanceof DOMException;
    if (method !== "GET" || !retryable) throw err;
    try {
      resp = await fetchOnce(path, { ...init }, timeoutMs);
    } catch {
      throw new ApiError("network", "请求超时或连接失败，请稍后重试", 0);
    }
  }
  if (resp.status === 401) {
    throw unauthorized();
  }
  if (!resp.ok) {
    throw await failure(resp);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  /** `timeoutMs` 给「服务端要先干完一件慢事」的写操作用；不给就走默认 15s。
   *  写操作超时不自动重放——重放等于让服务端再干一次那件慢事。（目前没有调用方用它：
   *  浏览器下载那条路已经改成流式，见 `ssePost`。） */
  post: <T>(path: string, body?: unknown, timeoutMs?: number) =>
    request<T>(
      path,
      { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) },
      timeoutMs,
    ),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** `HEAD` 只问「这份资源还在吗」（浏览器下载导航前的可用性探测）：204 / 404，不读正文。 */
  head: (path: string) => request<void>(path, { method: "HEAD" }),
};

/** SSE 流式 `POST`：服务端按 `text/event-stream` 回若干 `data: {...}` 事件。
 *
 *  为什么要有它：浏览器下载的取数可能长达几分钟，进度得**边取边推**——一次性 JSON
 *  响应只能等到最后，中途什么都给不出来。它仍留在 client.ts（全站唯一 fetch 出口，
 *  编码规范 §5），页面不许自己 fetch。
 *
 *  `signal` 供调用方取消：**取消即中止取数**——连接一断，服务端会把取数 task 一并停掉。
 *  `timeoutMs` 是**空闲**超时（默认 15s）：多久没收到新的一帧才算卡死，不是整条流的总时限
 *  ——取数跑满十分钟本身正常，调用方按自己的节奏给值。 */
export async function* ssePost<T>(
  path: string,
  body: unknown,
  options: { signal?: AbortSignal; timeoutMs?: number } = {},
): AsyncGenerator<T, void, void> {
  const { signal, timeoutMs = REQUEST_TIMEOUT_MS } = options;
  // 外部取消与超时合并成一个信号：fetch 只认一个
  const ctrl = new AbortController();
  const forward = () => ctrl.abort();
  signal?.addEventListener("abort", forward, { once: true });
  // 超时是**空闲**超时，不是一个总时限：每收到一帧就重新计时。取数跑满十分钟本身是正常的
  // （母带 + 慢源），该防的是「连接活着但再也不说话」。掐断时给一个说得清的 ApiError——
  // 否则调用方拿到的是一句 AbortError，用户看到的是「连接失败」这种误导文案。
  let timer = 0;
  const arm = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(
      () =>
        ctrl.abort(
          new ApiError("timeout", "取回超时：太久没有新进度了，请重试或改用「入队下载」", 0),
        ),
      timeoutMs,
    );
  };
  arm();
  // 读句柄提到 try 之外：调用方拿到终态事件就 `return` 了（`for await` 会就地结束这个
  // 生成器），那时必须把流关掉——不然这条连接会一直挂到服务端自己收尾。
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  try {
    const resp = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (resp.status === 401) throw unauthorized();
    if (!resp.ok) throw await failure(resp);
    if (resp.body === null) return;

    reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      arm(); // 有数据即视为「还在动」，空闲计时重新开始
      buffer += decoder.decode(value, { stream: true });
      // 事件之间以空行分隔（`data: {...}\n\n`）：一个网络分片里可能是半条、也可能是几条
      let sep = buffer.indexOf("\n\n");
      while (sep >= 0) {
        const chunk = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const raw = line.slice(5).trim();
          if (raw) yield JSON.parse(raw) as T;
        }
        sep = buffer.indexOf("\n\n");
      }
    }
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", forward);
    if (reader) void reader.cancel().catch(() => {});
  }
}

/** 统一的错误文案提取：页面 catch 后用这一句。 */
export function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}
