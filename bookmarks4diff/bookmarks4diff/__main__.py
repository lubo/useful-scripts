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


class Parser(HTMLParser):
    def reset(self, *args, **kwargs):
        self._tag = None
        self.items = []

        return super().reset(*args, **kwargs)

    def handle_starttag(self, tag, attrs):
        self._tag = tag

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
                    value = value.split(",")

            item[name] = value

        self.items.append(item)

    def handle_data(self, data):
        if not self.items or not (data := data.strip()):
            return

        item = self.items[-1]

        match self._tag:
            case "a":
                item["title"] = data
            case "dd":
                item["metadata"] = {
                    key: value
                    for key, value in metadata_from_note(data).items()
                    if key != "Last check"
                }


def main():
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("file", type=argparse.FileType("r"))
    args = arg_parser.parse_args()

    html_parser = Parser()
    with args.file:
        html_parser.feed(args.file.read())

    print(  # noqa: T201
        yaml.dump(
            html_parser.items,
            allow_unicode=True,
            default_flow_style=False,
            Dumper=YamlSafeDumper,
            width=2147483647,  # -1 doesn't always work
        ),
    )


if __name__ == "__main__":
    main()
