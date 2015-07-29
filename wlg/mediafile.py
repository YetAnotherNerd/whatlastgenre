# whatlastgenre
# Improves genre metadata of audio files
# based on tags from various music sites.
#
# Copyright (c) 2012-2015 YetAnotherNerd
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

'''whatlastgenre mediafile'''

from __future__ import print_function

import logging
import os.path

import mutagen


LOG = logging.getLogger('whatlastgenre')

# supported extensions
EXTENSIONS = ['.mp3', '.flac', '.ogg', '.m4a']


def find_music_folders(paths):
    '''Scan paths for folders containing music files.'''
    folders = []
    for path in paths:
        for root, _, files in os.walk(path):
            if any(os.path.splitext(f)[1].lower() in EXTENSIONS
                   for f in files):
                folders.append(root)
    return folders


class AlbumError(Exception):
    '''If something went wrong while handling an Album.'''
    pass


class Album(object):
    '''Class for managing albums.'''

    def __init__(self, path):
        if not os.path.exists(path):
            raise AlbumError("Folder vanished")
        self.path = path
        self.tracks = []
        for file_ in os.listdir(path):
            if os.path.splitext(file_)[1].lower() in EXTENSIONS:
                try:
                    self.tracks.append(Track(path, file_))
                except TrackError as err:
                    print("Error loading track '%s': %s" % (file_, err))
        if not self.tracks:
            raise AlbumError("Could not load any tracks")
        if not self.get_meta('album'):
            raise AlbumError("Not all tracks have the same or any album-tag")
        self.type = ','.join(set(t.ext for t in self.tracks)).upper()

    def get_meta(self, key, lcp=True):
        '''Get metadata that all tracks have in common.

        Return the common value (if any) for the given metadata key.

        :param key: metadata key
        :param lcp: use longest common prefix for some keys
        '''
        vals = set(t.get_meta(key) for t in self.tracks)
        # common for all tracks
        if len(vals) == 1:
            return vals.pop()
        # use longest common prefix
        if lcp and key in ['artist', 'albumartist', 'album']:
            vals.discard(None)
            val = os.path.commonprefix(vals)
            if len(val) > 2:
                return val
        # no common value for this key
        return None

    def set_meta(self, key, val):
        '''Set metadata for all tracks.'''
        for track in self.tracks:
            track.set_meta(key, val)

    def save(self):
        '''Save all tracks.'''
        print("Saving metadata... ", end='')
        dirty = False
        for track in self.tracks:
            try:
                dirty = track.save() or dirty
            except TrackError as err:
                print("Error saving track '%s': %s" % (track.filename, err))
        print("done!" if dirty else "(no changes)")


class TrackError(Exception):
    '''If something went wrong while handling a Track.'''
    pass


class Track(object):
    '''Class for managing tracks.'''

    def __init__(self, path, filename):
        self.filename = filename
        self.ext = os.path.splitext(filename)[1].lower()[1:]
        self.fullpath = os.path.join(path, filename)
        self.dirty = False
        try:
            self.stat = os.stat(self.fullpath)
            self.muta = mutagen.File(self.fullpath, easy=True)
        except (IOError, OSError) as err:
            raise TrackError(err)
        if not self.muta:
            raise TrackError('unknown mutagen error')

    def map_key(self, key):
        '''Map a general metadata key to an ext-specific metadata key.

        :param key: metadata key name string
        '''
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
        '''Get metadata for a given key.'''
        key = self.map_key(key)
        if not key or key not in self.muta:
            return None
        try:
            val = self.muta[key][0].encode('utf-8')
            if key.lower() in ['date', 'tracknumber', 'discnumber']:
                for sep in ['/', '-']:
                    if sep in val:
                        val = val.split(sep)[0].strip()
                val = int(val)
            return val
        except ValueError:
            pass
        return None

    def set_meta(self, key, val):
        '''Set metadata of a given key to a given val.'''
        key = self.map_key(key)
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
        '''Save the track.

        Preserve the file modification time,
        return True if changes have been saved,
        return False if no changes were made.
        '''
        if not self.dirty:
            return False
        try:
            self.muta.save()
            # preserve modtime
            os.utime(self.fullpath, (self.stat.st_atime, self.stat.st_mtime))
        except IOError as err:
            raise TrackError(err)
        return True
