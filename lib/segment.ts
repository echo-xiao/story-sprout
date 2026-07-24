import type { Segment } from "@/types";

/**
 * Pure transforms for a segment's character_actions. Extracted from the editor
 * so the "characters_in_scene stays derived from character_actions" invariant
 * is unit-tested independently of the 1400-line editor component, and so the
 * editor can call them inside functional setState updates (two quick edits in
 * one tick must not clobber each other via a stale closure).
 *
 * Every transform returns a NEW segment and never mutates its input.
 */

function withActions(seg: Segment, actions: Segment["character_actions"]): Segment {
  return {
    ...seg,
    character_actions: actions,
    // characters_in_scene is always derived from the action names (empties
    // dropped) — never edited independently.
    characters_in_scene: actions.map((a) => a.name).filter(Boolean),
  };
}

/** Set one field (name/action) of the action row at `idx`. */
export function setActionField(
  seg: Segment,
  idx: number,
  field: "name" | "action",
  value: string,
): Segment {
  const actions = [...(seg.character_actions || [])];
  if (idx < 0 || idx >= actions.length) return seg;
  actions[idx] = { ...actions[idx], [field]: value };
  return withActions(seg, actions);
}

/** Append a new action row. `name` empty → a blank row the user fills in. */
export function addAction(seg: Segment, name = ""): Segment {
  return withActions(seg, [...(seg.character_actions || []), { name, action: "" }]);
}

/** Remove the action row at `idx`. */
export function removeAction(seg: Segment, idx: number): Segment {
  return withActions(seg, (seg.character_actions || []).filter((_, i) => i !== idx));
}
