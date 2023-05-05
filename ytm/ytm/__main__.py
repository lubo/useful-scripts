#!/usr/bin/env python
#
# Provides commands to interact with YouTube Music using
# https://github.com/sigma67/ytmusicapi.

import argparse
import csv
from getpass import getpass
from http.cookiejar import Cookie
import operator
import os
import sys

from yt_dlp import YoutubeDL
import ytmusicapi
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


def download_library(client, cookie):
    dest_dir = os.path.join(os.path.expanduser("~"), "Music")
    downloader = YoutubeDL(
        {
            "format": "141",
            "paths": {
                "home": dest_dir,
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

    for cookie in map(str.strip, cookie.split(";")):
        name, value = cookie.split("=", 1)
        downloader.cookiejar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain="youtube.com",
                domain_specified=True,
                domain_initial_dot=True,
                path="",
                path_specified=False,
                secure=True,
                expires=None,
                discard=False,
                comment=None,
                comment_url=None,
                rest=None,
            ),
        )

    for song in map(get_song_entry, client.get_library_songs(GET_LIMIT)):
        file_name = f"{song['artist']} - {song['title']}.m4a".replace("/", "∕")
        if os.path.exists(os.path.join(dest_dir, file_name)):
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
        print("The playlist is up-to-date.")
        return

    if to_add := [s for s in library_songs if s not in playlist_songs]:
        for song_id in to_add:
            print(
                'Adding "{}"'.format(
                    format_song_entry(get_song_entry(library_songs[song_id])),
                ),
            )

        client.add_playlist_items(playlist_id, to_add)

    if to_remove := playlist_songs.keys() - library_songs.keys():
        for song_id in to_remove:
            print(
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

    try:
        cookie = getpass("Cookie: ") if sys.stdin.isatty() else input()
    except EOFError:
        print("Cookie could not be read.", file=sys.stderr)
        sys.exit(1)

    try:
        client = YTMusic(
            ytmusicapi.setup(
                headers_raw=(
                    # Authorization is only necessary to pass these checks:
                    # https://github.com/sigma67/ytmusicapi/blob/4d5e4b7116d46a3523184c8fcb445669fceedd8a/ytmusicapi/auth/browser.py#L13
                    # https://github.com/sigma67/ytmusicapi/blob/4d5e4b7116d46a3523184c8fcb445669fceedd8a/ytmusicapi/ytmusic.py#L106
                    f"Authorization: SAPISIDHASH\n"
                    f"Cookie: {cookie}\n"
                    f"X-Goog-Authuser: 0\n"
                ),
            ),
        )

        if args.command == "download-library":
            download_library(client, cookie)
        elif args.command == "export-library":
            export_library(client)
        elif args.command == "sync-playlist":
            sync_playlist(client, args.playlist_id)
    except KeyError as error:
        if error.args[0] in {"contents", "SAPISID"}:
            print("Invalid credentials provided.", file=sys.stderr)
            sys.exit(1)
        else:
            raise


if __name__ == "__main__":
    main()
