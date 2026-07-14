"""Prompt for revising a character sketch after a completed trip."""


def _bullet_list(names: list[str]) -> str:
    if not names:
        return "- (nothing)"
    lines = []
    for name in names:
        lines.append("- " + name)
    return "\n".join(lines)


def build_update_prompt(current_sketch: str, trip_summary: str, picked: list[str], skipped: list[str]) -> str:
    """Revise the sketch using what the traveler picked vs skipped this trip."""
    return f"""Here is a traveler's current character sketch:

{current_sketch}

They just planned a trip: {trip_summary}

From the recommendations they were shown, they PICKED these:
{_bullet_list(picked)}

And SKIPPED these:
{_bullet_list(skipped)}

Revise the sketch. Rules:
- Keep stable traits as they are. Only adjust or add where this trip gives real evidence.
- One or two picks are weak evidence; a clear pattern is strong evidence. Don't overreact.
- Update the keywords line if their tastes have visibly shifted or grown.
- The sketch contains a ```json taste block (likes/dislikes with weights 1-3, diet, pace). Adjust the WEIGHTS on evidence too: a like they keep picking grows toward 3, a like they keep skipping shrinks toward 1 or gets dropped. Add new concepts this trip clearly revealed. Keep the same JSON shape.
- Respond in EXACTLY the same format (# Character Sketch, keywords line, the ```json taste block, then prose), MAXIMUM 350 words of prose, nothing before or after."""
