// React hooks over the desktop shell. In a browser they return empty/undefined
// so the same pages render with a "browser mode" fallback.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  desktop,
  isDesktop,
  onServicesReady,
  type ResourceSnapshot,
  type ServiceState,
} from "./desktop";

/** Poll the shell for local service status. */
export function useServices(intervalMs = 5000): {
  services: ServiceState[];
  refresh: () => void;
} {
  const [services, setServices] = useState<ServiceState[]>([]);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!isDesktop()) return undefined;
    let cancelled = false;
    let timer: number | undefined;

    async function run() {
      const result = await desktop.serviceStatus();
      if (!cancelled && result) setServices(result);
    }

    run();
    timer = window.setInterval(run, intervalMs);

    let unsubscribe = () => {};
    onServicesReady(() => {
      desktop.refreshServices().then((r) => {
        if (!cancelled && r) setServices(r);
      });
    }).then((fn) => {
      unsubscribe = fn;
    });

    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
      unsubscribe();
    };
  }, [intervalMs, tick]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);
  return { services, refresh };
}

/** Poll live CPU/RAM/GPU/VRAM telemetry. */
export function useResources(intervalMs = 3000): ResourceSnapshot | null {
  const [snapshot, setSnapshot] = useState<ResourceSnapshot | null>(null);
  const running = useRef(false);

  useEffect(() => {
    if (!isDesktop()) return undefined;
    let cancelled = false;
    let timer: number | undefined;

    async function run() {
      if (running.current) return;
      running.current = true;
      try {
        const result = await desktop.resourceSnapshot();
        if (!cancelled && result) setSnapshot(result);
      } finally {
        running.current = false;
      }
    }

    run();
    timer = window.setInterval(run, intervalMs);
    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, [intervalMs]);

  return snapshot;
}
