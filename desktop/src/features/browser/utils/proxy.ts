import type { ProxyEndpoint, ProxyScheme } from "../types";

export interface ParsedProxy {
  endpoint: ProxyEndpoint;
  username: string;
  password: string;
}

export type ProxyValidation = {
  valid: boolean;
  message: string;
};

export function proxyEndpointOf(server: string | undefined): ProxyEndpoint {
  const value = String(server || "").trim();
  if (!value) return { scheme: "socks5", host: "", port: "" };
  try {
    const parsed = new URL(value.includes("://") ? value : `http://${value}`);
    const scheme = parsed.protocol.replace(":", "") as ProxyScheme;
    return {
      scheme: ["http", "https", "socks5"].includes(scheme) ? scheme : "socks5",
      host: parsed.hostname,
      port: parsed.port,
    };
  } catch {
    return { scheme: "socks5", host: "", port: "" };
  }
}

export function proxyServerOf(endpoint: ProxyEndpoint): string {
  const host = endpoint.host.trim();
  const port = endpoint.port.trim();
  if (!host && !port) return "";
  const formattedHost = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host;
  return `${endpoint.scheme}://${formattedHost}${port ? `:${port}` : ""}`;
}

export function validateProxyServer(server: string | undefined): ProxyValidation {
  const endpoint = proxyEndpointOf(server);
  const port = Number(endpoint.port);
  if (!endpoint.host) {
    return { valid: false, message: "Hãy nhập hostname hoặc địa chỉ IP của proxy." };
  }
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    return { valid: false, message: "Cổng proxy phải là số từ 1 đến 65535." };
  }
  return { valid: true, message: "" };
}

export function parseQuickProxy(value: string, fallbackScheme: ProxyScheme): ParsedProxy | null {
  const raw = value.trim();
  if (!raw) return null;
  try {
    if (raw.includes("://")) {
      const parsed = new URL(raw);
      const scheme = parsed.protocol.replace(":", "") as ProxyScheme;
      if (!["http", "https", "socks5"].includes(scheme)) return null;
      return {
        endpoint: { scheme, host: parsed.hostname, port: parsed.port },
        username: decodeURIComponent(parsed.username),
        password: decodeURIComponent(parsed.password),
      };
    }

    const atIndex = raw.lastIndexOf("@");
    if (atIndex > 0) {
      const credentials = raw.slice(0, atIndex).split(":");
      const address = raw.slice(atIndex + 1).split(":");
      if (address.length !== 2) return null;
      return {
        endpoint: { scheme: fallbackScheme, host: address[0], port: address[1] },
        username: credentials.shift() || "",
        password: credentials.join(":"),
      };
    }

    const parts = raw.split(":");
    if (parts.length < 2) return null;
    return {
      endpoint: { scheme: fallbackScheme, host: parts[0], port: parts[1] },
      username: parts[2] || "",
      password: parts.slice(3).join(":"),
    };
  } catch {
    return null;
  }
}
