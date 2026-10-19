import { useRef, useState } from "react";
import { uploadVideo } from "../api";
import type { IngestStatus } from "../protocol";

interface Props {
  hasVideo: boolean;
  onUploaded: (status: IngestStatus) => void;
}

/** One file at a time, by drop or picker. Replacing the current video asks first. */
export default function UploadZone({ hasVideo, onUploaded }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);

  async function start(fileList: FileList | null) {
    const files = fileList ? [...fileList] : [];
    if (input.current) input.current.value = ""; // so picking the same file again fires
    if (files.length === 0 || progress !== null) return;
    if (files.length > 1) {
      setError("Drop one video at a time.");
      return;
    }
    if (hasVideo && !window.confirm("Replace the current video? Its chat will be cleared.")) return;
    setError(null);
    setProgress(0);
    try {
      onUploaded(await uploadVideo(files[0], setProgress));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setProgress(null);
    }
  }

  return (
    <div
      data-testid="drop-zone"
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        start(e.dataTransfer.files);
      }}
      className={`flex items-center justify-between gap-3 rounded-lg border border-dashed px-4 py-3 text-sm ${
        over ? "border-sky-400 bg-sky-950/40" : "border-neutral-700"
      }`}
    >
      <span className="text-neutral-400">
        {progress !== null
          ? `Uploading… ${Math.round(progress * 100)}%`
          : hasVideo
            ? "Drop a video here to replace this one"
            : "Drop one video here (up to 60 minutes)"}
      </span>
      {progress !== null && (
        <progress className="h-2 flex-1" value={progress} max={1} aria-label="upload progress" />
      )}
      {error && <span className="text-rose-400" role="alert">{error}</span>}
      <button
        type="button"
        disabled={progress !== null}
        onClick={() => input.current?.click()}
        className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700 disabled:opacity-50"
      >
        {hasVideo ? "Replace" : "Choose file"}
      </button>
      <input
        ref={input}
        type="file"
        accept="video/*"
        className="hidden"
        data-testid="file-input"
        onChange={(e) => start(e.target.files)}
      />
    </div>
  );
}
