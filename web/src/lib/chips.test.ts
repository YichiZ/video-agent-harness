import { describe, expect, it } from "vitest";
import { parseChips, seconds } from "./chips";

describe("parseChips", () => {
  it("finds single times and ranges", () => {
    expect(parseChips("Spikes [00:04] and red [01:02–01:10].")).toEqual([
      { kind: "text", text: "Spikes " },
      { kind: "chip", label: "[00:04]", start: 4 },
      { kind: "text", text: " and red " },
      { kind: "chip", label: "[01:02–01:10]", start: 62, end: 70 },
      { kind: "text", text: "." },
    ]);
  });
  it("accepts hyphens and hours, and leaves other brackets alone", () => {
    expect(parseChips("[1:02:03-1:02:09]")).toEqual([
      { kind: "chip", label: "[1:02:03-1:02:09]", start: 3723, end: 3729 },
    ]);
    expect(parseChips("see [note] and [12]")).toEqual([{ kind: "text", text: "see [note] and [12]" }]);
  });
  it("converts clocks", () => {
    expect(seconds("00:59")).toBe(59);
    expect(seconds("10:00")).toBe(600);
  });
});
