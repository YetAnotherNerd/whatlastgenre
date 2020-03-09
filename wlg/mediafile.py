# whatlastgenre
# Improves genre metadata of audio files
# based on tags from various music sites.
#
# Copyright (c) 2012-2020 YetAnotherNerd
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

"""whatlastgenre mediafile

Read and write metadata of audio files using mutagen.
"""

from __future__ import print_function, unicode_literals

import os.path
import re
from collections import namedtuple

import mutagen

# supported extensions
EXTENSIONS = ['.flac', '.ogg', '.mp3', '.m4a']

# regex pattern for 'Various Artist'
VA_PAT = re.compile('^va(rious( ?artists?)?)?$', re.I)

# musicbrainz artist id of 'Various Artists'
VA_MBID = '89ad4ac3-39f7-470e-963a-56509c546377'

# metadata key mapping {ext: {old: new}}
MAPPING = {
    'mp3': {
        'albumartist': 'performer',
        'edition': 'version',
        'label': 'organization',
        'releasetype': 'musicbrainz_albumtype',
    },
    'm4a': {
        'catalognumber': None,
        'edition': None,
        'label': None,
        'media': None,
        'musicbrainz_releasegroupid': None,
        'releasetype': 'musicbrainz_albumtype',
    },
}

Metadata = namedtuple(
    'Metadata', ['path', 'type', 'artists', 'albumartist', 'album',
                 'mbid_album', 'mbid_relgrp', 'year', 'releasetype'])


def find_music_dirs(paths):
    """Scan paths for directories containing supported music files."""
    dirs = []
    for path in paths:
        for root, _, files in os.walk(path):
            if any(os.path.splitext(f)[1].lower() in EXTENSIONS
                   for f in files):
                dirs.append(root)
    return dirs


def is_various_artists(name, mbid):
    """Check if given name or mbid represents 'Various Artists'."""
    return name and VA_PAT.match(name) or mbid == VA_MBID


def map_key(ext, key):
    """Map metadata key."""
    if ext in MAPPING and key in MAPPING[ext]:
        key = MAPPING[ext][key]
    if ext in ['flac', 'ogg']:
        key = key.upper()
    return key


def get_first(iterable, default=None):
    """Get the first not None item from an iterable or default."""
    if iterable:
        for item in iterable:
            if item:
                return item
    return default


class AlbumError(Exception):
    """If something went wrong while handling an Album."""
    pass


class Album(object):
    """Class for managing albums."""

    def __init__(self, path, v23sep=None):
        if not os.path.exists(path):
            raise AlbumError("Directory vanished")
        self.path = path
        self.tracks = []
        for file_ in os.listdir(path):
            if os.path.splitext(file_)[1].lower() in EXTENSIONS:
                try:
                    self.tracks.append(Track(path, file_, v23sep))
                except TrackError as err:
                    print("Error loading track '%s': %s" % (file_, err))
        if not self.tracks:
            raise AlbumError("Could not load any tracks")
        if not self.get_meta('album'):
            raise AlbumError("Not all tracks have the same or any album-tag")
        self.type = ','.join(set(t.ext for t in self.tracks)).upper()

    def get_metadata(self):
        """Return a Metadata namedtuple."""
        # artists
        artists = []
        for track in self.tracks:
            artist = (get_first(track.get_meta('artist')),
                      get_first(track.get_meta('musicbrainz_artistid')))
            if artist[0] and not is_various_artists(*artist):
                artists.append(artist)
        # album artist
        albumartist = (self.get_meta('albumartist'),
                       self.get_meta('musicbrainz_albumartistid'))
        if not albumartist[0] or is_various_artists(*albumartist):
            albumartist = (self.get_meta('artist'),
                           self.get_meta('musicbrainz_artistid'))
        if not albumartist[0] or is_various_artists(*albumartist):
            albumartist = (None, None)
        return Metadata(
            path=self.path, type=self.type,
            artists=artists, albumartist=albumartist,
            album=self.get_meta('album'),
            mbid_album=self.get_meta('musicbrainz_albumid'),
            mbid_relgrp=self.get_meta('musicbrainz_releasegroupid'),
            year=self.get_meta('date'),
            releasetype=self.get_meta('releasetype'))

    def get_meta(self, key, lcp=True):
        """Get metadata that all tracks have in common.

        Return the common value (if any) for the given metadata key.

        :param key: metadata key
        :param lcp: use longest common prefix for some keys
        """
        values = [get_first(t.get_meta(key)) for t in self.tracks]
        values = [v for v in values if v]
        # common for all tracks
        if len(set(values)) == 1:
            return values[0]
        # use longest common prefix
        if values and lcp and key in ['artist', 'albumartist', 'album']:
            val = os.path.commonprefix(values).strip()
            if len(val) > 2:
                return val
        # no common value for this key
        return None

    def set_meta(self, key, val):
        """Set metadata for all tracks."""
        for track in self.tracks:
            track.set_meta(key, val)

    def save(self):
        """Save all tracks."""
        print("Saving metadata... ", end='')
        dirty = False
        for track in self.tracks:
            try:
                dirty = track.save() or dirty
            except TrackError as err:
                print("Error saving track '%s': %s" % (track.filename, err))
        print("done!" if dirty else "(no changes)")


class TrackError(Exception):
    """If something went wrong while handling a Track."""
    pass


class Track(object):
    """Class for managing tracks."""

    def __init__(self, path, filename, v23sep=None):
        self.fullpath = os.path.join(path, filename)
        self.filename = filename
        self.v23sep = v23sep
        self.ext = os.path.splitext(filename)[1].lower()[1:]
        self.dirty = False
        try:
            self.stat = os.stat(self.fullpath)
            self.muta = mutagen.File(self.fullpath, easy=True)
        except (IOError, OSError) as err:
            raise TrackError(err)
        if not self.muta:
            raise TrackError('unknown mutagen error')

    def get_meta(self, key):
        """Get metadata for a given key."""

        def split(value, separators):
            """Split value by some separators."""
            for sep in separators:
                if sep in value:
                    return [v.strip() for v in value.split(sep)]
            return [value]

        key = map_key(self.ext, key)
        if not key or key not in self.muta or not self.muta[key]:
            return None
        values = self.muta[key]
        # CSV tags
        if len(set(values)) == 1:
            sep = [self.v23sep] if self.v23sep else [';', '\n', '\\']
            values = split(values[0], sep)
        # date tags
        if key.lower() in ['date']:
            values = [split(v, ['/', '-'])[0] for v in values]
        return values

    def set_meta(self, key, val):
        """Set metadata of a given key to a given val."""
        key = map_key(self.ext, key)
        if not key:
            return
        # no val, delete key if exists
        if not val:
            if key in self.muta:
                del self.muta[key]
                self.dirty = True
            return
        if not isinstance(val, list):
            val = [val]
        # check for change
        if val != self.get_meta(key):
            self.muta[key] = val
            self.dirty = True

    def save(self):
        """Save the track.

        Preserve the file modification time,
        return True if changes have been saved,
        return False if no changes were made.
        """
        if not self.dirty:
            return False
        try:
            self.muta.save()
            # downgrade id3 v2.4 tags to v2.3 if separator is set
            if self.ext == 'mp3' and self.v23sep:
                from mutagen.id3 import ID3
                audio = ID3(self.fullpath, v2_version=3)
                audio.save(v2_version=3, v23_sep=self.v23sep + ' ')
            # preserve modtime
            os.utime(self.fullpath, (self.stat.st_atime, self.stat.st_mtime))
        except IOError as err:
            raise TrackError(err)
        return True
