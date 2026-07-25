export type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  timeoutMs?: number;
};

export const DEFAULT_REQUEST_TIMEOUT_MS = 60_000;

export class RequestTimeoutError extends Error {
  constructor(
    public readonly path: string,
    public readonly timeoutMs: number,
  ) {
    super(`Yêu cầu tới Dyna quá thời gian chờ (${Math.ceil(timeoutMs / 1000)} giây). Hãy thử lại.`);
    this.name = "RequestTimeoutError";
  }
}

export function withTimeout<T>(
  operation: Promise<T>,
  path: string,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<T> {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) return operation;
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new RequestTimeoutError(path, timeoutMs)), timeoutMs);
    operation.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        window.clearTimeout(timer);
        reject(error);
      },
    );
  });
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!window.dyna) {
    throw new Error("Electron bridge chưa sẵn sàng");
  }
  const { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, ...bridgeOptions } = options;
  return withTimeout(window.dyna.request<T>({ path, timeoutMs, ...bridgeOptions }), path, timeoutMs);
}

export function formatDateTime(value?: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.replace("T", " ");
  const locale = document.documentElement.lang || "vi";
  return new Intl.DateTimeFormat(locale, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function shortId(value?: string, visible = 12): string {
  const text = String(value || "");
  return text.length > visible ? `...${text.slice(-visible)}` : text || "-";
}
