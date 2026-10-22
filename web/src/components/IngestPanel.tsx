import type { IngestStatus } from "../protocol";

const STYLE: Record<string, string> = {
  done: "bg-emerald-900 text-emerald-200",
  running: "bg-sky-900 text-sky-200 animate-pulse",
  failed: "bg-rose-900 text-rose-200",
  blocked: "bg-neutral-800 text-neutral-500",
  cancelled: "bg-neutral-800 text-neutral-500",
  pending: "bg-neutral-800 text-neutral-400",
};

export default function IngestPanel({ status }: { status: IngestStatus }) {
  return (
    <ul className="flex flex-wrap gap-2 text-xs" aria-label="ingest steps">
      {Object.entries(status.steps).map(([name, state]) => (
        <li
          key={name}
          data-step={name}
          data-state={state}
          title={status.errors[name] ?? state}
          className={`rounded px-2 py-0.5 ${STYLE[state] ?? STYLE.pending}`}
        >
          {name}: {state}
        </li>
      ))}
    </ul>
  );
}
