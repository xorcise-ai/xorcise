import { apiBaseUrl } from "./runtime-config";

/** Error thrown for non-2xx server responses; carries the HTTP status + body. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** The server's own explanation of a failure, or null when it did not give one.
 *
 * FastAPI puts it in `detail`, and it is written to be acted on ("Headscale control
 * plane 'headscale' is not reachable — run 'xorcise up'"). Showing a generic
 * "please try again" in its place is worse than showing nothing: for a 503 from a
 * missing dependency, retrying cannot ever succeed, so the advice sends people into
 * a loop instead of at the fix. `ApiError.message` is deliberately NOT a fallback —
 * it is wire trivia ("POST /runs → 503"), not an explanation. */
export function errorDetail(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.body;
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return null;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${apiBaseUrl()}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let errBody: unknown;
    try {
      errBody = await res.json();
    } catch {
      /* response had no JSON body */
    }
    throw new ApiError(`${method} ${path} → ${res.status}`, res.status, errBody);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Thin typed verbs over the server API. Paths are relative to <origin>/api. */
export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  del: <T = void>(path: string) => request<T>("DELETE", path),
};
