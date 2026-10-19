import { memo, useEffect, useMemo, useRef } from "react";
import type { Transcript as TranscriptData, Word } from "../api";
import { clock, speakerColor, wordAt } from "../lib/transcript";

interface Props {
  transcript: TranscriptData;
  time: number;
  onSeek: (t: number) => void;
}

interface Line {
  speaker: string | null;
  first: number; // index of its first word in transcript.words
  words: Word[];
}

/** Lines of words, split where the speaker changes or a segment starts. */
function lines(transcript: TranscriptData): Line[] {
  const starts = new Set(transcript.segments.map((s) => s.start));
  const out: Line[] = [];
  transcript.words.forEach((w, i) => {
    const last = out[out.length - 1];
    if (!last || last.speaker !== w.speaker || starts.has(w.start)) {
      out.push({ speaker: w.speaker, first: i, words: [w] });
    } else last.words.push(w);
  });
  return out;
}

const TranscriptLine = memo(function TranscriptLine({
  line, color, active, onSeek,
}: { line: Line; color: string; active: number; onSeek: (t: number) => void }) {
  return (
    <p className={`mb-2 ${color}`}>
      <span className="mr-2 text-xs text-neutral-500">{clock(line.words[0].start)}</span>
      {line.words.map((w, j) => (
        <span
          key={j}
          data-word={line.first + j}
          data-start={w.start}
          onClick={() => onSeek(w.start)}
          className={`cursor-pointer rounded hover:bg-neutral-700 ${
            line.first + j === active ? "bg-sky-800 text-white" : ""
          }`}
        >
          {w.text}{" "}
        </span>
      ))}
    </p>
  );
});

export default function Transcript({ transcript, time, onSeek }: Props) {
  const grouped = useMemo(() => lines(transcript), [transcript]);
  const active = wordAt(transcript.words, time);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = box.current?.querySelector(`[data-word="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (transcript.words.length === 0) {
    return <p className="text-sm text-neutral-500">No speech in this video.</p>;
  }
  return (
    <div ref={box} className="h-full overflow-y-auto pr-2 text-sm leading-relaxed" aria-label="transcript">
      {grouped.map((line) => {
        const inLine = active >= line.first && active < line.first + line.words.length;
        return (
          <TranscriptLine
            key={line.first}
            line={line}
            color={speakerColor(transcript.speakers, line.speaker)}
            active={inLine ? active : -1}
            onSeek={onSeek}
          />
        );
      })}
    </div>
  );
}
