import { useCallback, useEffect, useRef, useState } from "react";
import { getStatus } from "../api";
import type { IngestStatus } from "../protocol";

const POLL_MS = 1000;

/** The current video's ingest status, polled. `null` before any upload. */
export function useStatus() {
  const [status, setStatus] = useState<IngestStatus | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0); // polls overlap: only the newest request may set the status

  const refresh = useCallback(async () => {
    const request = ++latest.current;
    try {
      const next = await getStatus();
      if (request === latest.current) {
        setStatus(next);
        setError(null);
      }
    } catch (e) {
      if (request === latest.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  /** Set the status from elsewhere (an upload's response), dropping polls in flight. */
  const override = useCallback((next: IngestStatus) => {
    latest.current++;
    setStatus(next);
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  return { status, setStatus: override, error };
}
