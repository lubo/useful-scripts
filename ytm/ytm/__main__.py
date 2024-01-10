# Provides commands to interact with YouTube Music using
# https://github.com/sigma67/ytmusicapi.

import argparse
import csv
import operator
from pathlib import Path
import sys

from yt_dlp import YoutubeDL
from ytmusicapi import YTMusic

GET_LIMIT = None


def get_song_entry(song):
    return {
        "album": (song.get("album") or {}).get("name") or "",
        "artist": (song.get("artist") or song.get("artists"))[0]["name"],
        "id": song["videoId"],
        "title": song["title"],
        "uploaded": "isAvailable" not in song,
    }


def format_song_entry(song_entry):
    return (
        " -- ".join(
            operator.itemgetter("title", "artist", "album")(song_entry),
        )
        + f' ({song_entry["id"]})'
    )


def get_all_library_songs(client, order=None):
    return [
        *client.get_library_songs(GET_LIMIT, order=order),
        *client.get_library_upload_songs(GET_LIMIT, order),
    ]


def library_sort_key(key):
    return [attr.lower() if isinstance(attr, str) else attr for attr in key]


def songs_by_id(songs):
    return {song["videoId"]: song for song in songs}


def download_library(client):
    dest_dir = Path.home() / "Music"
    downloader = YoutubeDL(
        {
            "format": "141",
            "http_headers": {
                "Authorization": client.headers["Authorization"],
            },
            "paths": {
                "home": str(dest_dir),
            },
            "postprocessors": [
                {
                    "key": "FFmpegMetadata",
                },
                {
                    "key": "EmbedThumbnail",
                },
            ],
            "writethumbnail": True,
        },
    )

    for song in map(get_song_entry, client.get_library_songs(GET_LIMIT)):
        file_name = f"{song['artist']} - {song['title']}.m4a".replace(
            "/",
            "∕",  # noqa: RUF001
        )
        if Path.exists(dest_dir / file_name):
            continue
        downloader.params["outtmpl"]["default"] = file_name
        downloader.download(
            [
                "https://music.youtube.com/watch?v=" + song["id"],
            ],
        )


def export_library(client):
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(["Title", "Artist", "Album", "ID", "Uploaded"])
    writer.writerows(
        sorted(
            map(
                operator.itemgetter(
                    "title",
                    "artist",
                    "album",
                    "id",
                    "uploaded",
                ),
                map(get_song_entry, get_all_library_songs(client)),
            ),
            key=library_sort_key,
        ),
    )


def sync_playlist(client, playlist_id):
    library_songs = songs_by_id(
        reversed(get_all_library_songs(client, "recently_added")),
    )
    playlist_songs = songs_by_id(
        client.get_playlist(playlist_id, GET_LIMIT)["tracks"],
    )

    if library_songs.keys() == playlist_songs.keys():
        print("The playlist is up-to-date.")  # noqa: T201
        return

    if to_add := [s for s in library_songs if s not in playlist_songs]:
        for song_id in to_add:
            print(  # noqa: T201
                'Adding "{}"'.format(
                    format_song_entry(get_song_entry(library_songs[song_id])),
                ),
            )

        client.add_playlist_items(playlist_id, to_add)

    if to_remove := playlist_songs.keys() - library_songs.keys():
        for song_id in to_remove:
            print(  # noqa: T201
                'Removing "{}"'.format(
                    format_song_entry(get_song_entry(playlist_songs[song_id])),
                ),
            )

        client.remove_playlist_items(
            playlist_id,
            [playlist_songs[song_id] for song_id in to_remove],
        )


def main():
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument(
        "credentials",
        help="File containing OAuth credentials",
        type=argparse.FileType(),
    )
    subparsers = arg_parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "download-library",
        help="Downloads library to ~/Music",
    )
    subparsers.add_parser(
        "export-library",
        help="Exports library to stdout in CSV",
    )
    sync_playlist_parser = subparsers.add_parser(
        "sync-playlist",
        help="Syncs a playlist with library",
    )
    sync_playlist_parser.add_argument(
        "playlist_id",
        help="ID of a playlist to be synced with library",
    )

    args = arg_parser.parse_args()

    # Only regular files are supported, not symlinks like /dev/stdin, etc.
    # https://github.com/sigma67/ytmusicapi/blob/56d54a2b50f242abe812cd8214b31ede98cb1d01/ytmusicapi/auth/headers.py#L14C9-L14C9
    with args.credentials:
        client = YTMusic(args.credentials.read())

    if args.command == "download-library":
        download_library(client)
    elif args.command == "export-library":
        export_library(client)
    elif args.command == "sync-playlist":
        sync_playlist(client, args.playlist_id)


if __name__ == "__main__":
    main()
