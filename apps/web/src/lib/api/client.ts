export type ApiError = {
  code: string;
  message: string;
  details: unknown;
};

export type ApiEnvelope<T> = {
  success: boolean;
  data: T | null;
  error: ApiError | null;
  request_id: string;
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

export class ApiClientError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const prefix = `${encodeURIComponent(name)}=`;
  const value = document.cookie
    .split("; ")
    .find((item) => item.startsWith(prefix))
    ?.slice(prefix.length);
  return value ? decodeURIComponent(value) : null;
}

function apiFetchInit(init: RequestInit): RequestInit {
  const method = (init.method ?? "GET").toUpperCase();
  const csrfToken =
    method === "GET" || method === "HEAD" ? null : readCookie("outreach_csrf");
  const headers = new Headers(init.headers);
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  if (
    init.body !== undefined &&
    !(typeof FormData !== "undefined" && init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  return { ...init, credentials: "include", headers };
}

function errorFromEnvelope(
  response: Response,
  payload: ApiEnvelope<unknown> | null,
): ApiClientError {
  const message =
    response.status === 413
      ? "上传文件超过网关允许的大小"
      : (payload?.error?.message ?? `API 请求失败（HTTP ${response.status}）`);
  return new ApiClientError(
    message,
    response.status,
    response.status === 413
      ? "FILE_TOO_LARGE"
      : (payload?.error?.code ?? "INVALID_RESPONSE"),
  );
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApiEnvelope<T>> {
  const response = await fetch(`${API_BASE_URL}${path}`, apiFetchInit(init));
  const responseText = await response.text();
  let payload: ApiEnvelope<T> | null = null;
  if (responseText) {
    try {
      payload = JSON.parse(responseText) as ApiEnvelope<T>;
    } catch {
      payload = null;
    }
  }
  if (payload === null) {
    const message =
      response.status === 413
        ? "上传文件超过网关允许的大小"
        : response.ok
          ? "API 返回了无法识别的响应"
          : `API 请求失败（HTTP ${response.status}）`;
    throw new ApiClientError(
      message,
      response.status,
      response.status === 413 ? "FILE_TOO_LARGE" : "INVALID_RESPONSE",
    );
  }
  if (!response.ok || !payload.success) {
    throw errorFromEnvelope(response, payload);
  }
  return payload;
}

export type ApiDownload = {
  blob: Blob;
  contentDisposition: string | null;
};

export async function apiDownload(
  path: string,
  init: RequestInit = {},
): Promise<ApiDownload> {
  const response = await fetch(`${API_BASE_URL}${path}`, apiFetchInit(init));
  if (!response.ok) {
    const responseText = await response.text();
    let payload: ApiEnvelope<unknown> | null = null;
    if (responseText) {
      try {
        payload = JSON.parse(responseText) as ApiEnvelope<unknown>;
      } catch {
        payload = null;
      }
    }
    throw errorFromEnvelope(response, payload);
  }
  return {
    blob: await response.blob(),
    contentDisposition: response.headers.get("Content-Disposition"),
  };
}
