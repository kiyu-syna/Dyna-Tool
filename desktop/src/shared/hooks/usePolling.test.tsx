import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { request } from "../api/client";
import { usePolling } from "./usePolling";

vi.mock("../api/client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("usePolling", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("waits for the current request before scheduling the next one", async () => {
    const first = deferred<{ value: number }>();
    requestMock.mockReturnValueOnce(first.promise);
    requestMock.mockResolvedValue({ value: 2 });

    renderHook(() => usePolling<{ value: number }>("/slow", 1_000));
    expect(requestMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(requestMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve({ value: 1 });
      await first.promise;
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(999);
    });
    expect(requestMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(requestMock).toHaveBeenCalledTimes(2);
  });

  it("ignores an older response after the polling path changes", async () => {
    const oldResponse = deferred<{ value: string }>();
    const newResponse = deferred<{ value: string }>();
    requestMock.mockImplementation((path) => (path === "/old" ? oldResponse.promise : newResponse.promise));

    const { result, rerender } = renderHook(({ path }) => usePolling<{ value: string }>(path, 1_000), {
      initialProps: { path: "/old" },
    });
    rerender({ path: "/new" });

    await act(async () => {
      newResponse.resolve({ value: "new" });
      await newResponse.promise;
    });
    expect(result.current.data).toEqual({ value: "new" });

    await act(async () => {
      oldResponse.resolve({ value: "old" });
      await oldResponse.promise;
    });
    expect(result.current.data).toEqual({ value: "new" });
  });

  it("backs off polling while the document is hidden", async () => {
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    requestMock.mockResolvedValue({ value: 1 });

    renderHook(() => usePolling<{ value: number }>("/background", 1_000));
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_999);
    });
    expect(requestMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(requestMock).toHaveBeenCalledTimes(2);
  });

  it("shares one request and scheduler between consumers of the same endpoint", async () => {
    const first = deferred<{ value: number }>();
    requestMock.mockReturnValueOnce(first.promise);
    requestMock.mockResolvedValue({ value: 2 });

    renderHook(() => ({
      fast: usePolling<{ value: number }>("/shared", 1_000),
      slow: usePolling<{ value: number }>("/shared", 5_000),
    }));
    expect(requestMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve({ value: 1 });
      await first.promise;
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(requestMock).toHaveBeenCalledTimes(2);
  });

  it("keeps the last good data on a transient error and recovers on the next poll", async () => {
    requestMock
      .mockResolvedValueOnce({ value: 1 })
      .mockRejectedValueOnce(new Error("Temporary backend error"))
      .mockResolvedValueOnce({ value: 2 });
    const { result } = renderHook(() => usePolling<{ value: number }>("/recovering", 1_000));

    await act(async () => Promise.resolve());
    expect(result.current.data).toEqual({ value: 1 });

    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(result.current.data).toEqual({ value: 1 });
    expect(result.current.error).toBe("Temporary backend error");

    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(result.current.data).toEqual({ value: 2 });
    expect(result.current.error).toBe("");
  });
});
