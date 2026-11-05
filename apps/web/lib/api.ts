export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function detailOf(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const { detail } = body as { detail: unknown };
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return "Check those fields and try again.";
  }
  return "Something broke on our side.";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("x-rkl-client", "web");
  if (typeof init.body === "string") headers.set("content-type", "application/json");

  const response = await fetch(`/api${path}`, { ...init, headers, credentials: "same-origin" });

  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (response.ok) return body as T;

  const detail = detailOf(body);
  if (response.status === 401 && !path.startsWith("/auth/login")) {
    window.location.assign("/sign-in");
  }
  if (detail === "PASSWORD_CHANGE_REQUIRED") window.location.assign("/account");
  throw new ApiError(response.status, detail);
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });

export const postForm = <T>(path: string, body: FormData) =>
  api<T>(path, { method: "POST", body });

export const messageOf = (e: unknown) =>
  e instanceof ApiError ? e.message : "Couldn't reach the server.";
