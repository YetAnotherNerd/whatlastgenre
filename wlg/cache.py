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

"""whatlastgenre cache"""

from __future__ import print_function, unicode_literals

import json
import os
import time
from datetime import timedelta
from tempfile import NamedTemporaryFile


class Cache(object):
    """Load/save a dict as json from/to a file."""

    def __init__(self, path, update_cache):
        self.fullpath = os.path.join(path, 'cache')
        self.update_cache = update_cache
        self.expire_after = timedelta(days=180).total_seconds()
        self.time = time.time()
        self.dirty = False
        self.cache = {}
        # this new set is to avoid doing the same query multiple
        # times during the same run while using update_cache
        self.new = set()
        try:
            with open(self.fullpath) as file_:
                self.cache = json.load(file_)
        except (IOError, ValueError):
            pass

    def __del__(self):
        self.save()

    @classmethod
    def cachekey(cls, query):
        """Return the cachekey for a query."""
        cachekey = query.artist
        if query.type == 'album':
            cachekey += query.album
        return query.dapr.name.lower(), query.type, cachekey.replace(' ', '')

    def get(self, key):
        """Return a (time, value) tuple for a given key
        or None if the key wasn't found.
        """
        key = str(key)
        if key in self.cache \
                and time.time() < self.cache[key][0] + self.expire_after \
                and (not self.update_cache or key in self.new):
            return self.cache[key]
        return None

    def set(self, key, value):
        """Set value for a given key."""
        key = str(key)
        self.cache[key] = (time.time(), value)
        if self.update_cache:
            self.new.add(key)
        self.dirty = True

    def clean(self):
        """Clean up expired entries."""
        print("Cleaning cache... ", end='')
        size = len(self.cache)
        for key, val in list(self.cache.items()):
            if time.time() > val[0] + self.expire_after:
                del self.cache[key]
                self.dirty = True
        print("done! (%d entries removed)" % (size - len(self.cache)))

    def save(self):
        """Save the cache dict as json string to a file.

        Clean expired entries before saving and use a temporary
        file to avoid data loss on interruption.
        """
        if not self.dirty:
            return
        self.clean()
        print("Saving cache... ", end='')
        dirname, basename = os.path.split(self.fullpath)
        try:
            with NamedTemporaryFile(prefix=basename + '.tmp_',
                                    dir=dirname, delete=False) as tmpfile:
                tmpfile.write(json.dumps(self.cache).encode())
                os.fsync(tmpfile)
            # seems atomic rename here is not possible on windows
            # http://docs.python.org/2/library/os.html#os.rename
            if os.name == 'nt' and os.path.isfile(self.fullpath):
                os.remove(self.fullpath)
            os.rename(tmpfile.name, self.fullpath)
            self.time = time.time()
            self.dirty = False
            size_mb = os.path.getsize(self.fullpath) / 2 ** 20
            print("  done! (%d entries, %.2f MB)" % (len(self.cache), size_mb))
        except KeyboardInterrupt:
            if os.path.isfile(tmpfile.name):
                os.remove(tmpfile.name)
