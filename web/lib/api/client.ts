export function getApiBaseUrl(): string {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (!baseUrl) {
    throw new Error("未配置 NEXT_PUBLIC_API_BASE_URL，请先设置 FastAPI 地址。");
  }
  return baseUrl.replace(/\/+$/, "");
}

function getErrorDetail(payload: unknown): string | undefined {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === "object" && item !== null && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : "",
        )
        .filter(Boolean)
        .join("；");
    }
  }
  return undefined;
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const url = `${getApiBaseUrl()}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new Error("无法连接 FastAPI 服务，请确认后端已在运行。");
  }

  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    throw new Error(
      getErrorDetail(payload) ?? `后端请求失败（HTTP ${response.status}）`,
    );
  }

  return (await response.json()) as T;
}

export async function apiUpload<T>(path: string, body: FormData): Promise<T> {
  const url = `${getApiBaseUrl()}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      body,
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new Error("无法连接 FastAPI 服务，请确认后端已在运行。");
  }

  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    throw new Error(
      getErrorDetail(payload) ?? `后端请求失败（HTTP ${response.status}）`,
    );
  }

  return (await response.json()) as T;
}

export async function apiBlobRequest(
  path: string,
  init: RequestInit = {},
): Promise<Blob> {
  const url = `${getApiBaseUrl()}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      cache: "no-store",
      headers: {
        Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new Error("无法连接 FastAPI 服务，请确认后端已在运行。");
  }

  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    throw new Error(
      getErrorDetail(payload) ?? `后端请求失败（HTTP ${response.status}）`,
    );
  }

  return response.blob();
}
