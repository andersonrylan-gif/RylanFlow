"""Pure Markdown export for a meeting transcript -- turns the same dict shape Store.get_meeting
returns into the `**Name** [mm:ss]: text` document the dashboard's export.md route serves.
"""


def _mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def to_markdown(meeting: dict) -> str:
    title = meeting.get("title") or f"Meeting with {meeting.get('source_app') or 'unknown app'}"
    lines = [f"# {title}", "", f"**Date:** {meeting['started_at']}"]
    if meeting.get("attendees"):
        lines.append(f"**Attendees:** {', '.join(meeting['attendees'])}")
    lines.append("")

    speakers_by_id = {s["id"]: s for s in meeting["speakers"]}
    for seg in meeting["segments"]:
        speaker = speakers_by_id.get(seg["speaker_id"])
        label = (speaker.get("display_name") or speaker["label"]) if speaker else "Unknown"
        lines.append(f"**{label}** [{_mmss(seg['start_s'])}]: {seg['text']}")

    return "\n".join(lines) + "\n"
