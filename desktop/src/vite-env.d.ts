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
    localMediaUrl(projectId: string): Promise<string>;
    localDubbingUrl(projectId: string): Promise<string>;
    localTtsPreviewUrl(previewId: string): Promise<string>;
    revealFile(filePath: string): Promise<{ ok: boolean }>;
    openExternal(url: string): Promise<{ ok: boolean }>;
    openLogWindow(): Promise<{ ok: boolean }>;
    windowControl(action: "minimize" | "maximize" | "close" | "focus"): Promise<void>;
    onBackendStatus(callback: (status: Record<string, unknown>) => void): () => void;
    onBackendLog(callback: (line: string) => void): () => void;
    onNavigate(callback: (page: string) => void): () => void;
  };
}
