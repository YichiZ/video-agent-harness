import { useCallback, useEffect, useState } from "react";
import { getStatus } from "../api";
import type { IngestStatus } from "../protocol";

const POLL_MS = 1000;

/** The current video's ingest status, polled. `null` before any upload. */
export function useStatus() {
  const [status, setStatus] = useState<IngestStatus | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  return { status, setStatus, error };
}
