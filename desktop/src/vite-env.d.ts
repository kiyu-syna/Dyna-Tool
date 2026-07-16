/// <reference types="vite/client" />

type DynaRequest = {
  path: string;
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
};

interface Window {
  dyna?: {
    request<T>(request: DynaRequest): Promise<T>;
    backendInfo(): Promise<Record<string, unknown>>;
    onBackendStatus(callback: (status: Record<string, unknown>) => void): () => void;
    onBackendLog(callback: (line: string) => void): () => void;
  };
}
