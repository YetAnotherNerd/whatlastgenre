# whatlastgenre
# Improve genre metadata of audio files based on tags from various music sites.
#
# Copyright (c) 2012-2014 YetAnotherNerd
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

'''whatlastgenre mediafile'''

from __future__ import print_function

import logging
import os.path
import re

import mutagen


LOG = logging.getLogger('whatlastgenre')

# Regex pattern to recognize Various Artist strings
VA_PAT = re.compile('^va(rious( ?artists?)?)?$', re.I)

# Musicbrainz ID of 'Various Artists'
VA_MBID = '89ad4ac3-39f7-470e-963a-56509c546377'


def find_music_folders(paths):
    '''Scans paths for folders containing music files.

    An album cannot have tracks with different extensions. Groups of tracks
    with the same extension in one folder are seen as separate Albums.
    '''
    folders = []
    for path in paths:
        for root, _, files in os.walk(path):
            for ext in ['flac', 'ogg', 'mp3', 'm4a']:
                tracks = [t for t in files if t.lower().endswith('.' + ext)]
                if tracks:
                    folders.append([root, ext, tracks])
    return folders


class AlbumError(Exception):
    '''If something went wrong while handling an Album.'''
    pass


class Album(object):
    '''Class for managing albums.'''

    def __init__(self, path, ext, tracks):
        print("[%s] %s" % (ext.upper(), path))
        self.path = path
        self.ext = ext
        self.tracks = [Track(path, t) for t in tracks]
        # validate and handle metadata
        # common album tag is necessary for now
        album = self.get_common_meta('album')
        if not album:
            raise AlbumError("Not all tracks have the same or any album-tag.")
        # put artist in empty aartist
        artist = self.get_common_meta('artist')
        if artist and not self.get_common_meta('albumartist'):
            self.set_common_meta('albumartist', artist)
        # put artist mbid in empty aartist mbid
        mbidart = self.get_common_meta('musicbrainz_artistid')
        if mbidart and not self.get_common_meta('musicbrainz_albumartistid'):
            self.set_common_meta('musicbrainz_albumartistid', mbidart)
        # handle various artists
        if artist and VA_PAT.match(artist):
            artist = None
            self.set_common_meta('artist', None)
        aartist = self.get_common_meta('albumartist')
        if aartist and VA_PAT.match(aartist):
            aartist = None
            self.set_common_meta('albumartist', None)
            self.set_common_meta("musicbrainz_albumartistid", VA_MBID)
        LOG.info("albumartist=%s, album=%s, date=%s",
                 aartist, album, self.get_common_meta('date'))

    def get_common_meta(self, key, use_lcs=True):
        '''Gets metadata that all tracks have in common.'''
        val = []
        for track in self.tracks:
            val.append(track.get_meta(key))
        # common for all tracks
        if len(set(val)) == 1:
            return val[0]
        # use longest common substring
        if use_lcs and key in ['artist', 'albumartist']:
            val = [x for x in set(val) if x]
            lcs = self._longest_common_substr(val)
            if lcs and len(lcs) > 2 and lcs == val[0][:len(lcs)]:
                return lcs
        # no common value for this key
        return None

    def set_common_meta(self, key, val):
        '''Sets metadata for all tracks.'''
        for track in self.tracks:
            track.set_meta(key, val)

    def save(self):
        '''Saves the metadata for all tracks.'''
        print("Saving metadata... ", end='')
        dirty = False
        for track in self.tracks:
            dirty = track.save() or dirty
        print("done!" if dirty else "(no changes)")

    @classmethod
    def _longest_common_substr(cls, data):
        '''Returns the longest common substring from a list of strings.'''
        substr = ''
        if len(data) > 1 and data[0]:
            for i in range(len(data[0])):
                for j in range(len(data[0]) - i + 1):
                    if j > len(substr) and all(data[0][i:i + j] in x
                                               for x in data):
                        substr = data[0][i:i + j]
        return substr


class Track(object):
    '''Class for managing tracks.'''

    def __init__(self, path, filename):
        self.filename = filename
        self.ext = os.path.splitext(filename)[1].lower()[1:]
        self.fullpath = os.path.join(path, filename)
        self.stat = os.stat(self.fullpath)
        self.dirty = False
        self.muta = None
        try:
            self.muta = mutagen.File(self.fullpath, easy=True)
        except IOError as err:
            print("Error loading track %s: %s" % (self.filename, err.strerror))

    def _translate_key(self, key):
        '''Translate the metadata key based on ext, etc.'''
        if self.ext in ['flac', 'ogg']:
            return key.upper()
        if self.ext in ['mp3', 'm4a']:
            if key == 'releasetype':
                return 'musicbrainz_albumtype'
            if key == 'musicbrainz_releasegroupid':
                return
        if self.ext == 'mp3' and key == 'albumartist':
            return 'performer'
        return key

    def get_meta(self, key):
        '''Gets metadata for a given key.'''
        key = self._translate_key(key)
        if not key or key not in self.muta:
            return
        try:
            val = self.muta[key][0].encode('utf-8')
            if key.lower() == 'date':
                return int(val.split('-')[0]) if '-' in val else int(val)
            if key.lower() == 'tracknumber':
                return int(val.split('/')[0]) if '/' in val else int(val)
            return val
        except ValueError:
            return

    def set_meta(self, key, val):
        '''Sets metadata for a given key.'''
        key = self._translate_key(key)
        if not key:
            return
        # no val, delete key if exists
        if not val:
            if key in self.muta:
                del self.muta[key]
                self.dirty = True
            return
        # get a decoded list from plain values
        val = val if isinstance(val, list) else [val]
        val = [v.decode('utf-8') for v in val]
        # check for change
        old = [o if isinstance(o, unicode) else o.decode('utf-8')
               for o in self.muta.get(key, [])]
        if not old or set(old) != set(val):
            self.muta[key] = val
            self.dirty = True

    def save(self):
        '''Saves the metadata of the track.

        Preserves the file modification time,
        returns True if changes have been saved,
        returns False if no changes were made.'''
        if not self.dirty:
            return False
        try:
            self.muta.save()
            # preserve modtime
            os.utime(self.fullpath, (self.stat.st_atime, self.stat.st_mtime))
            return True
        except IOError as err:
            print("Error saving track %s: %s" % (self.filename, err.strerror))
        return False
