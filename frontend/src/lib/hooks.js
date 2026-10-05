// Small data hooks: polling fetch, live SSE, and a one-shot mutation helper.
// They intentionally avoid a state library — the app is small and the network
// is local, so explicit hooks keep behaviour obvious.

import { useCallback, useEffect, useRef, useState } from "react";

export function usePolling(fetcher, deps = [], intervalMs = 8000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    let timer;

    async function run() {
      try {
        const result = await fetcherRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    run();
    if (intervalMs > 0) {
      timer = setInterval(run, intervalMs);
    }
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps]);

  return { data, error, loading, reload, setData };
}

export function useLiveEvents(onEvent, url) {
  const [connected, setConnected] = useState(false);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    if (!url) return undefined;
    let source;
    try {
      source = new EventSource(url);
    } catch {
      return undefined;
    }
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.addEventListener("activity", (event) => {
      try {
        handlerRef.current?.(JSON.parse(event.data));
      } catch {
        /* ignore malformed frames */
      }
    });
    return () => {
      source.close();
      setConnected(false);
    };
  }, [url]);

  return connected;
}

export function useMutation(fn) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const run = useCallback(
    async (...args) => {
      setBusy(true);
      setError(null);
      try {
        const value = await fn(...args);
        setResult(value);
        return value;
      } catch (err) {
        setError(err);
        throw err;
      } finally {
        setBusy(false);
      }
    },
    [fn]
  );

  return { run, busy, error, result, reset: () => setResult(null) };
}
