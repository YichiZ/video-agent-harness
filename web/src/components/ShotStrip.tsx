import { type Shot, thumbUrl } from "../api";
import { clock } from "../lib/transcript";

interface Props {
  videoId: string;
  shots: Shot[];
  onSeek: (t: number) => void;
}

export default function ShotStrip({ videoId, shots, onSeek }: Props) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-2" aria-label="shots">
      {shots.map((shot) => (
        <button
          key={shot.index}
          type="button"
          data-shot={shot.index}
          data-start={shot.start_s}
          title={shot.caption ?? clock(shot.start_s)}
          onClick={() => onSeek(shot.start_s)}
          className="shrink-0 overflow-hidden rounded border border-neutral-800 hover:border-sky-400"
        >
          <img src={thumbUrl(videoId, shot.index)} alt={`shot at ${clock(shot.start_s)}`} loading="lazy" className="h-16" />
          <span className="block text-[10px] text-neutral-400">{clock(shot.start_s)}</span>
        </button>
      ))}
    </div>
  );
}
