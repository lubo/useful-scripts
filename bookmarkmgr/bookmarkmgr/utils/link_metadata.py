def metadata_to_note(metadata):
    return "\n".join(
        [f"{key}: {value}" for key, value in sorted(metadata.items())],
    )


def metadata_from_note(note):
    metadata = {}

    for line in note.splitlines():
        segments = line.split(":", 1)

        if len(segments) == 1:
            segments.append("")

        metadata[segments[0]] = segments[1].strip()

    return metadata
