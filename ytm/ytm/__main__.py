# Provides commands to interact with YouTube Music using
# https://github.com/sigma67/ytmusicapi.

import argparse
import csv
from getpass import getpass
import json
import operator
from pathlib import Path
import sys

from yt_dlp import YoutubeDL
from ytmusicapi import YTMusic
from ytmusicapi.auth.oauth import OAuthCredentials

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
        + f" ({song_entry['id']})"
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
            "extractor_args": {
                "youtube": {
                    "player_client": ["web_music"],
                },
            },
            "format": "141",
            "http_headers": {
                "Authorization": client.headers["authorization"],
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
            formatted_entry = format_song_entry(
                get_song_entry(library_songs[song_id]),
            )
            print(  # noqa: T201
                f'Adding "{formatted_entry}"',
            )

        client.add_playlist_items(playlist_id, to_add)

    if to_remove := playlist_songs.keys() - library_songs.keys():
        for song_id in to_remove:
            formatted_entry = format_song_entry(
                get_song_entry(playlist_songs[song_id]),
            )
            print(  # noqa: T201
                f'Removing "{formatted_entry}"',
            )

        client.remove_playlist_items(
            playlist_id,
            [playlist_songs[song_id] for song_id in to_remove],
        )


def main():
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument(
        "oauth_client_id",
        help="OAuth Client ID",
    )
    arg_parser.add_argument(
        "user_credentials",
        help="File containing user OAuth credentials",
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

    with args.user_credentials:
        user_credentials = json.loads(args.user_credentials.read())

    try:
        oauth_client_secret = (
            getpass("OAuth Client Secret: ") if sys.stdin.isatty() else input()
        )
    except EOFError:
        print(  # noqa: T201
            "OAuth Client Secret could not be read.",
            file=sys.stderr,
        )
        return 1

    client = YTMusic(
        user_credentials,
        oauth_credentials=OAuthCredentials(
            args.oauth_client_id,
            oauth_client_secret,
        ),
    )

    if args.command == "download-library":
        download_library(client)
    elif args.command == "export-library":
        export_library(client)
    elif args.command == "sync-playlist":
        sync_playlist(client, args.playlist_id)

    return 0
