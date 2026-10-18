import { useCallback, useEffect, useRef, useState } from "react";
import { getShots, getTranscript, proxyUrl, type Shot, type Transcript as TranscriptData } from "./api";
import IngestPanel from "./components/IngestPanel";
import ShotStrip from "./components/ShotStrip";
import Transcript from "./components/Transcript";
import UploadZone from "./components/UploadZone";
import { useStatus } from "./lib/useStatus";

/** Loads `load()` whenever `key` changes and is not null; `null` until then. */
function useLoaded<T>(key: string | null, load: () => Promise<T>): T | null {
  const [value, setValue] = useState<{ key: string; value: T } | null>(null);
  useEffect(() => {
    if (key === null) return;
    let live = true;
    load().then((v) => live && setValue({ key, value: v }), console.error);
    return () => {
      live = false;
    };
  }, [key]);
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
    getTranscript,
  );
  const shots = useLoaded<{ shots: Shot[] }>(
    id && done("shots") ? `${id}:${done("captions")}` : null,
    getShots,
  );

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
      <aside className="rounded-lg border border-neutral-800 p-3 text-sm text-neutral-500">
        Chat
      </aside>
    </div>
  );
}
