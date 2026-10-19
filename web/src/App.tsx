import { useCallback, useEffect, useRef, useState } from "react";
import { getShots, getTranscript, proxyUrl, type Shot, type Transcript as TranscriptData } from "./api";
import Chat from "./components/Chat";
import IngestPanel from "./components/IngestPanel";
import ShotStrip from "./components/ShotStrip";
import Transcript from "./components/Transcript";
import UploadZone from "./components/UploadZone";
import { useStatus } from "./lib/useStatus";

const RETRY_MS = 2000;

/**
 * Loads `load()` whenever `key` or `version` changes (and `key` is not null),
 * retrying on failure. `null` until the first load for `key`; while a newer
 * `version` loads, the previous value stays.
 */
function useLoaded<T>(key: string | null, version: string, load: () => Promise<T>): T | null {
  const [value, setValue] = useState<{ key: string; value: T } | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (key === null) return;
    let live = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    load().then(
      (v) => live && setValue({ key, value: v }),
      (e) => {
        console.error(e);
        if (live) retry = setTimeout(() => setAttempt((n) => n + 1), RETRY_MS);
      },
    );
    return () => {
      live = false;
      clearTimeout(retry);
    };
  }, [key, version, attempt]);
  return value && value.key === key ? value.value : null;
}

export default function App() {
  const { status, setStatus, error } = useStatus();
  const video = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);

  const id = status?.video_id ?? null;
  const done = (step: string) => status?.steps[step] === "done";
  const transcript = useLoaded<TranscriptData>(
    id && done("transcribe") ? id : null,
    "",
    getTranscript,
  );
  const shots = useLoaded<{ shots: Shot[] }>(
    id && done("shots") ? id : null,
    String(done("captions")), // refetch with captions, keeping the strip meanwhile
    getShots,
  );
  useEffect(() => {
    setTime(0);
  }, [id]);

  const seek = useCallback((t: number) => {
    if (video.current) video.current.currentTime = t;
    setTime(t);
  }, []);

  return (
    <div className="grid h-screen grid-cols-[minmax(0,1fr)_380px] gap-4 p-4">
      <main className="flex min-h-0 flex-col gap-3">
        <UploadZone hasVideo={!!id} onUploaded={setStatus} />
        {error && <p className="text-sm text-rose-400">Server unreachable: {error}</p>}
        {status && (
          <>
            <div className="flex items-baseline justify-between gap-3">
              <h1 className="truncate font-medium">{status.name}</h1>
              <IngestPanel status={status} />
            </div>
            <div className="aspect-video max-h-[50vh] bg-black">
              {done("proxy") ? (
                <video
                  key={id}
                  ref={video}
                  src={proxyUrl(id!)}
                  controls
                  preload="auto"
                  className="h-full w-full"
                  onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
                  onSeeked={(e) => setTime(e.currentTarget.currentTime)}
                />
              ) : (
                <p className="p-4 text-sm text-neutral-500">Preparing the player…</p>
              )}
            </div>
            {shots && <ShotStrip videoId={id!} shots={shots.shots} onSeek={seek} />}
            <section className="min-h-0 flex-1">
              {transcript ? (
                <Transcript transcript={transcript} time={time} onSeek={seek} />
              ) : (
                <p className="text-sm text-neutral-500">Transcribing…</p>
              )}
            </section>
          </>
        )}
      </main>
      <Chat videoId={id} chatReady={!!status?.chat_ready} onSeek={seek} />
    </div>
  );
}
