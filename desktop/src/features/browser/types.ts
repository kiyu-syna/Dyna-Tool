export type BrowserProvider = "gemlogin" | "local_chromium";
export type BrowserRunMode = "headless" | "offscreen";
export type ProxyScheme = "http" | "https" | "socks5";

export interface ProxyEndpoint {
  scheme: ProxyScheme;
  host: string;
  port: string;
}
