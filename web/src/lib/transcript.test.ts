import { describe, expect, it } from "vitest";
import type { Word } from "../api";
import { clock, speakerColor, wordAt } from "./transcript";

const word = (start: number): Word => ({ text: "w", start, end: start + 0.4, type: "word", speaker: null });

describe("wordAt", () => {
  const words = [word(1), word(2), word(3)];
  it("finds the last word started", () => {
    expect(wordAt(words, 0.5)).toBe(-1);
    expect(wordAt(words, 1)).toBe(0);
    expect(wordAt(words, 2.9)).toBe(1);
    expect(wordAt(words, 99)).toBe(2);
    expect(wordAt([], 1)).toBe(-1);
  });
});

describe("speakerColor", () => {
  it("is stable per speaker and neutral for unknown", () => {
    const speakers = ["a", "b"];
    expect(speakerColor(speakers, "a")).not.toBe(speakerColor(speakers, "b"));
    expect(speakerColor(speakers, null)).toBe("text-neutral-300");
  });
});

describe("clock", () => {
  it("formats mm:ss and h:mm:ss", () => {
    expect(clock(83.9)).toBe("01:23");
    expect(clock(3599)).toBe("59:59");
    expect(clock(3661)).toBe("1:01:01");
  });
});
