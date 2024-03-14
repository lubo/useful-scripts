from collections.abc import Iterable

_HIDDEN_SECTION_END = "-->"
_HIDDEN_SECTION_START = "<!--"

_LINE_PREFIX = "- "

_VISIBLE_METADATA = {
    "Archive (AT)",
    "Archive (WM)",
}

Metadata = dict[str, str]


def _pairs_to_lines(pairs: Iterable[tuple[str, str]]) -> list[str]:
    return [f"{_LINE_PREFIX}{key}: {value}" for key, value in sorted(pairs)]


def metadata_to_note(metadata: Metadata) -> str:
    hidden: list[tuple[str, str]] = []
    visible: list[tuple[str, str]] = []

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


def metadata_from_note(note: str) -> Metadata:
    metadata = {}

    for line in note.splitlines():
        stripped_line = line.strip()

        if stripped_line in {_HIDDEN_SECTION_START, _HIDDEN_SECTION_END}:
            continue

        segments = stripped_line.removeprefix(_LINE_PREFIX).split(":", 1)

        if len(segments) == 1:
            segments.append("")

        metadata[segments[0]] = segments[1].strip()

    return metadata
