import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { ProfileConfig } from "../../../shared/types";
import { useBrowserProfileSettings } from "./useBrowserProfileSettings";

const profile = {
  id: "1",
  name: "Profile 1",
  browser: {},
} as ProfileConfig;

describe("useBrowserProfileSettings", () => {
  it("updates run mode and proxy settings without changing unrelated profile data", () => {
    const { result } = renderHook(() => {
      const [profiles, setProfiles] = useState<Record<string, ProfileConfig>>({ "1": profile });
      const [, setMessage] = useState("");
      const [, setMessageError] = useState(false);
      const settings = useBrowserProfileSettings({
        profiles,
        setProfiles,
        l: (_vi, en) => en,
        setMessage,
        setMessageError,
      });
      return { profiles, ...settings };
    });

    act(() => result.current.setBrowserRunMode("1", "headless"));
    expect(result.current.profiles["1"].browser).toMatchObject({ headless: true, background: false });

    act(() => result.current.setBrowserRunMode("1", "offscreen"));
    expect(result.current.profiles["1"].browser).toMatchObject({ headless: false, background: false });

    act(() =>
      result.current.updateProxyEndpoint("1", {
        scheme: "socks5",
        host: "proxy.example",
        port: "1080",
      }),
    );
    expect(result.current.profiles["1"].browser?.proxy).toMatchObject({
      enabled: true,
      server: "socks5://proxy.example:1080",
    });
    expect(result.current.profiles["1"].name).toBe("Profile 1");
  });
});
