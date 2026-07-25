/// <reference types="vite/client" />

declare module "opencc-js/cn2t" {
  export function Converter(options: { from: "cn"; to: "tw" | "twp" | "hk" | "t" }): (value: string) => string;
}

type DynaRequest = {
  path: string;
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  timeoutMs?: number;
};

interface Window {
  dyna?: {
    request<T>(request: DynaRequest): Promise<T>;
    backendInfo(): Promise<Record<string, unknown>>;
    selectMedia(): Promise<string[]>;
    openExternal(url: string): Promise<{ ok: boolean }>;
    openLogWindow(): Promise<{ ok: boolean }>;
    windowControl(action: "minimize" | "maximize" | "close"): Promise<void>;
    onBackendStatus(callback: (status: Record<string, unknown>) => void): () => void;
    onBackendLog(callback: (line: string) => void): () => void;
  };
}
