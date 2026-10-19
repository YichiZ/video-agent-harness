/** Split agent text into plain text and `[mm:ss]` / `[mm:ss–mm:ss]` timestamp chips. */

export type Part = { kind: "text"; text: string } | { kind: "chip"; label: string; start: number; end?: number };

const TIME = String.raw`\d{1,2}:\d{2}(?::\d{2})?`;
const CHIP = new RegExp(String.raw`\[(${TIME})(?:\s*[–—-]\s*(${TIME}))?\]`, "g");

export function seconds(clock: string): number {
  return clock.split(":").reduce((total, part) => total * 60 + Number(part), 0);
}

export function parseChips(text: string): Part[] {
  const parts: Part[] = [];
  let last = 0;
  for (const match of text.matchAll(CHIP)) {
    const at = match.index ?? 0;
    if (at > last) parts.push({ kind: "text", text: text.slice(last, at) });
    const [label, start, end] = match;
    parts.push({ kind: "chip", label, start: seconds(start), ...(end ? { end: seconds(end) } : {}) });
    last = at + label.length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}
