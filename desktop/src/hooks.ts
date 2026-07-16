import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "./api";

export function usePolling<T>(path: string, intervalMs: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const result = await request<T>(path);
      if (mounted.current) {
        setData(result);
        setError("");
      }
    } catch (caught) {
      if (mounted.current) setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = window.setInterval(refresh, intervalMs);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
    };
  }, [intervalMs, refresh]);

  return { data, error, loading, refresh, setData };
}
