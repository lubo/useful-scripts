# Converts bookmarks to a more diff-friendly format with irrelevant fields
# removed.

import argparse
from html.parser import HTMLParser
from pathlib import Path
import sys

import yaml

try:
    from yaml import CSafeDumper as YamlSafeDumper
except ImportError:
    from yaml import SafeDumper as YamlSafeDumper

sys.path.append(
    str(Path(__file__).resolve().parent.parent.parent / "bookmarkmgr"),
)

from bookmarkmgr.utils.link_metadata import metadata_from_note


def parse_bookmarks(html):
    current_tag = None
    items = []

    def handle_starttag(tag, attrs):
        nonlocal current_tag

        current_tag = tag

        if tag != "a":
            return

        item = {}

        for attr_name, attr_value in attrs:
            name = attr_name.replace("-", "_")
            value = attr_value

            match name:
                case "add_date":
                    value = int(value)
                case "last_modified":
                    continue
                case "tags":
                    value = list(filter(len, value.split(",")))

            item[name] = value

        items.append(item)

    def handle_data(data):
        if not items or not (data := data.strip()):
            return

        item = items[-1]

        match current_tag:
            case "a":
                item["title"] = data
            case "dd":
                item["metadata"] = {
                    key: value
                    for key, value in metadata_from_note(data).items()
                    if key != "Last check"
                }

    html_parser = HTMLParser()
    html_parser.handle_data = handle_data
    html_parser.handle_starttag = handle_starttag

    html_parser.feed(html)

    return items


def main():
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("file", type=argparse.FileType("r"))
    args = arg_parser.parse_args()

    with args.file:
        items = parse_bookmarks(args.file.read())

    print(  # noqa: T201
        yaml.dump(
            items,
            allow_unicode=True,
            default_flow_style=False,
            Dumper=YamlSafeDumper,
            width=2147483647,  # -1 doesn't always work
        ),
    )


if __name__ == "__main__":
    main()
