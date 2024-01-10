#!/usr/bin/env python
#
# Converts bookmarks HTML file to JSON.

from html.parser import HTMLParser
import json
import sys


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
                case "add_date" | "last_modified":
                    value = int(value)
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
                item["note"] = data


def main():
    parser = Parser()
    parser.feed(sys.stdin.read())

    print(  # noqa: T201
        json.dumps(
            parser.items,
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
