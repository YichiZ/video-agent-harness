import type { Word } from "../api";

/** Index of the last word starting at or before `t`, or -1 before the first word. */
export function wordAt(words: readonly Word[], t: number): number {
  let lo = 0;
  let hi = words.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].start <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

const SPEAKER_COLORS = ["text-sky-300", "text-amber-300", "text-emerald-300", "text-rose-300"];

export function speakerColor(speakers: readonly string[], speaker: string | null): string {
  const i = speaker === null ? -1 : speakers.indexOf(speaker);
  return i < 0 ? "text-neutral-300" : SPEAKER_COLORS[i % SPEAKER_COLORS.length];
}

export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const pad = (n: number) => String(n).padStart(2, "0");
  const h = Math.floor(s / 3600);
  return h ? `${h}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}` : `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
}
