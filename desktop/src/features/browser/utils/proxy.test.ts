import { describe, expect, it } from "vitest";
import { parseQuickProxy, proxyEndpointOf, proxyServerOf, validateProxyServer } from "./proxy";

describe("browser proxy utilities", () => {
  it("parses a proxy URL with encoded credentials", () => {
    expect(parseQuickProxy("socks5://user%20name:p%40ss@proxy.example:1080", "http")).toEqual({
      endpoint: { scheme: "socks5", host: "proxy.example", port: "1080" },
      username: "user name",
      password: "p@ss",
    });
  });

  it("parses compact proxy formats", () => {
    expect(parseQuickProxy("proxy.example:8080:alice:secret:value", "https")).toEqual({
      endpoint: { scheme: "https", host: "proxy.example", port: "8080" },
      username: "alice",
      password: "secret:value",
    });
    expect(parseQuickProxy("alice:secret@proxy.example:8080", "http")?.endpoint.host).toBe("proxy.example");
  });

  it("round-trips IPv6 endpoints", () => {
    const server = proxyServerOf({ scheme: "http", host: "2001:db8::1", port: "3128" });
    expect(server).toBe("http://[2001:db8::1]:3128");
    expect(proxyEndpointOf(server)).toEqual({ scheme: "http", host: "[2001:db8::1]", port: "3128" });
  });

  it("validates the host and port before a profile is saved", () => {
    expect(validateProxyServer("socks5://proxy.example:1080")).toEqual({ valid: true, message: "" });
    expect(validateProxyServer("socks5://proxy.example")).toMatchObject({ valid: false });
    expect(validateProxyServer("http://:8080")).toMatchObject({ valid: false });
    expect(validateProxyServer("http://proxy.example:70000")).toMatchObject({ valid: false });
  });
});
