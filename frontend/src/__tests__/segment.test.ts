import { describe, it, expect } from "vitest";
import { setActionField, addAction, removeAction } from "@/lib/segment";
import type { Segment } from "@/types";

function seg(partial: Partial<Segment> = {}): Segment {
  return {
    id: 1,
    chapter_idx: 0,
    text: "",
    characters_in_scene: [],
    character_actions: [],
    scene_background: "",
    scene_summary: "",
    sentiment: "neutral",
    is_key_event: false,
    ...partial,
  };
}

describe("segment action transforms", () => {
  it("renaming an action keeps characters_in_scene in sync", () => {
    const s = seg({
      character_actions: [{ name: "Tom", action: "runs" }],
      characters_in_scene: ["Tom"],
    });
    const next = setActionField(s, 0, "name", "Nick");
    expect(next.character_actions[0].name).toBe("Nick");
    expect(next.characters_in_scene).toEqual(["Nick"]);
  });

  it("editing the action text does not touch characters_in_scene", () => {
    const s = seg({
      character_actions: [{ name: "Tom", action: "runs" }],
      characters_in_scene: ["Tom"],
    });
    const next = setActionField(s, 0, "action", "sits");
    expect(next.character_actions[0].action).toBe("sits");
    expect(next.characters_in_scene).toEqual(["Tom"]);
  });

  it("removing an action drops it from BOTH lists", () => {
    const s = seg({
      character_actions: [
        { name: "Tom", action: "runs" },
        { name: "Nick", action: "watches" },
      ],
      characters_in_scene: ["Tom", "Nick"],
    });
    const next = removeAction(s, 0);
    expect(next.character_actions).toHaveLength(1);
    expect(next.character_actions[0].name).toBe("Nick");
    expect(next.characters_in_scene).toEqual(["Nick"]);
  });

  it("adding a named action adds it to both lists", () => {
    const next = addAction(seg(), "Daisy");
    expect(next.character_actions).toEqual([{ name: "Daisy", action: "" }]);
    expect(next.characters_in_scene).toEqual(["Daisy"]);
  });

  it("adding a blank row leaves characters_in_scene empty until named", () => {
    const next = addAction(seg());
    expect(next.character_actions).toEqual([{ name: "", action: "" }]);
    expect(next.characters_in_scene).toEqual([]);
  });

  it("transforms never mutate the input segment", () => {
    const s = seg({
      character_actions: [{ name: "Tom", action: "runs" }],
      characters_in_scene: ["Tom"],
    });
    const snapshot = JSON.parse(JSON.stringify(s));
    setActionField(s, 0, "name", "Nick");
    addAction(s, "Daisy");
    removeAction(s, 0);
    expect(s).toEqual(snapshot);
  });

  it("out-of-range index is a no-op", () => {
    const s = seg({ character_actions: [{ name: "Tom", action: "runs" }] });
    expect(setActionField(s, 5, "name", "X")).toBe(s);
  });
});
