import { useCallback, useEffect, useMemo, useState } from "react";
import { request } from "../api/client";

type PollSnapshot<T> = {
  data: T | null;
  error: string;
  loading: boolean;
};

type PollingOptions<T> = {
  requestPath?(current: T | null): string;
  merge?(current: T | null, incoming: T): T;
};

type PollStore<T> = {
  snapshot: PollSnapshot<T>;
  listeners: Set<() => void>;
  intervals: Map<symbol, number>;
  timer?: number;
  inFlight?: Promise<void>;
  disposed: boolean;
  subscribe(token: symbol, intervalMs: number, listener: () => void): () => void;
  refresh(): Promise<void>;
  setData(data: T): void;
};

const stores = new Map<string, PollStore<unknown>>();

function createPollingStore<T>(path: string, options?: PollingOptions<T>): PollStore<T> {
  const store: PollStore<T> = {
    snapshot: { data: null, error: "", loading: true },
    listeners: new Set(),
    intervals: new Map(),
    disposed: false,
    subscribe(token, intervalMs, listener) {
      store.disposed = false;
      store.listeners.add(listener);
      store.intervals.set(token, Math.max(100, intervalMs));
      if (!store.timer && !store.inFlight) void runLoop();
      return () => {
        store.listeners.delete(listener);
        store.intervals.delete(token);
        if (store.listeners.size) {
          schedule();
          return;
        }
        store.disposed = true;
        if (store.timer !== undefined) window.clearTimeout(store.timer);
        store.timer = undefined;
        if (!store.inFlight && stores.get(path) === store) {
          stores.delete(path);
          document.removeEventListener("visibilitychange", visibilityChanged);
        }
      };
    },
    async refresh() {
      if (store.inFlight) return store.inFlight;
      const operation = (async () => {
        try {
          const incoming = await request<T>(options?.requestPath?.(store.snapshot.data) || path);
          const data = options?.merge?.(store.snapshot.data, incoming) ?? incoming;
          if (!store.disposed) update({ data, error: "", loading: false });
        } catch (caught) {
          if (!store.disposed)
            update({
              ...store.snapshot,
              error: caught instanceof Error ? caught.message : String(caught),
              loading: false,
            });
        } finally {
          store.inFlight = undefined;
          if (store.disposed && stores.get(path) === store) {
            stores.delete(path);
            document.removeEventListener("visibilitychange", visibilityChanged);
          }
        }
      })();
      store.inFlight = operation;
      return operation;
    },
    setData(data) {
      update({ data, error: "", loading: false });
    },
  };

  function update(snapshot: PollSnapshot<T>) {
    store.snapshot = snapshot;
    store.listeners.forEach((listener) => listener());
  }

  function delay() {
    const interval = Math.min(...store.intervals.values());
    return interval * (document.hidden ? 4 : 1);
  }

  function schedule() {
    if (store.disposed || !store.listeners.size) return;
    if (store.timer !== undefined) window.clearTimeout(store.timer);
    store.timer = window.setTimeout(() => {
      store.timer = undefined;
      void runLoop();
    }, delay());
  }

  async function runLoop() {
    await store.refresh();
    schedule();
  }

  function visibilityChanged() {
    if (store.disposed || store.timer === undefined) return;
    window.clearTimeout(store.timer);
    store.timer = undefined;
    if (document.hidden) schedule();
    else void runLoop();
  }

  document.addEventListener("visibilitychange", visibilityChanged);
  return store;
}

function pollingStore<T>(path: string, options?: PollingOptions<T>): PollStore<T> {
  const existing = stores.get(path);
  if (existing) return existing as PollStore<T>;
  const created = createPollingStore<T>(path, options);
  stores.set(path, created as PollStore<unknown>);
  return created;
}

export function usePolling<T>(path: string, intervalMs: number, options?: PollingOptions<T>) {
  const store = useMemo(() => pollingStore<T>(path, options), [path]);
  const [snapshot, setSnapshot] = useState(store.snapshot);

  useEffect(() => {
    const token = Symbol(path);
    setSnapshot(store.snapshot);
    return store.subscribe(token, intervalMs, () => setSnapshot(store.snapshot));
  }, [intervalMs, path, store]);

  const refresh = useCallback(() => store.refresh(), [store]);
  const setData = useCallback((data: T) => store.setData(data), [store]);
  return { ...snapshot, refresh, setData };
}
