import { describe, expect, it, vi } from "vitest";
import { withTimeout } from "./client";

describe("API request timeout", () => {
  it("returns a completed operation and clears its timer", async () => {
    vi.useFakeTimers();
    await expect(withTimeout(Promise.resolve({ ok: true }), "/api/health", 1_000)).resolves.toEqual({ ok: true });
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
  });

  it("rejects a stalled operation with an actionable timeout error", async () => {
    vi.useFakeTimers();
    const result = withTimeout(new Promise<never>(() => undefined), "/api/profiles", 2_000);
    const assertion = expect(result).rejects.toMatchObject({
      name: "RequestTimeoutError",
      path: "/api/profiles",
      timeoutMs: 2_000,
    });
    await vi.advanceTimersByTimeAsync(2_000);
    await assertion;
    vi.useRealTimers();
  });
});
