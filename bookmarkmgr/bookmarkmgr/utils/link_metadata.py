_HIDDEN_SECTION_END = "-->"
_HIDDEN_SECTION_START = "<!--"

_VISIBLE_METADATA = {
    "Archive (AT)",
    "Archive (WM)",
}


def _pairs_to_lines(pairs):
    return [f"{key}: {value}" for key, value in sorted(pairs)]


def metadata_to_note(metadata):
    hidden = []
    visible = []

    for key, value in metadata.items():
        group = visible if key in _VISIBLE_METADATA else hidden
        group.append((key, value))

    lines = _pairs_to_lines(visible)

    if hidden:
        lines += [
            _HIDDEN_SECTION_START,
            *_pairs_to_lines(hidden),
            _HIDDEN_SECTION_END,
        ]

    return "\n".join(lines)


def metadata_from_note(note):
    metadata = {}

    for line in note.splitlines():
        stripped_line = line.strip()

        if stripped_line in {_HIDDEN_SECTION_START, _HIDDEN_SECTION_END}:
            continue

        segments = stripped_line.split(":", 1)

        if len(segments) == 1:
            segments.append("")

        metadata[segments[0]] = segments[1].strip()

    return metadata