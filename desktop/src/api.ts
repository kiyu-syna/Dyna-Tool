export async function request<T>(
  path: string,
  options: { method?: "GET" | "POST" | "PUT" | "DELETE"; body?: unknown } = {},
): Promise<T> {
  if (!window.dyna) {
    throw new Error("Electron bridge chưa sẵn sàng");
  }
  return window.dyna.request<T>({ path, ...options });
}

export function formatDateTime(value?: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.replace("T", " ");
  return new Intl.DateTimeFormat("vi-VN", {
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
