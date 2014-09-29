#!/usr/bin/env python
'''whatlastgenre cache'''

from __future__ import division, print_function

import json
import os
import re
import tempfile
import time


class Cache(object):
    '''Class that loads and saves a cache data dict as json from/into a file to
    speed things up.'''

    def __init__(self, fullpath, ignore, timeout):
        self.fullpath = fullpath
        self.ignore = ignore
        self.timeout = timeout * 60 * 60 * 24
        self.time = time.time()
        self.dirty = False
        self.cache = {}
        try:
            with open(self.fullpath) as infile:
                self.cache = json.load(infile)
        except (IOError, ValueError):
            pass
        self.clean()
        self.save()

    def __del__(self):
        self.save()
        print()

    @classmethod
    def _get_key(cls, dapr, variant, sstr):
        '''Helper method to get the cache key.'''
        key = '##'.join([dapr, variant, sstr])
        return re.sub(r'([^\w#]| +)', '', key, 0, re.I).lower().strip()

    def get(self, dapr, variant, sstr):
        '''Gets cache data for a given dapr, variant and sstr. Since this method
        does't check the timestamps of the cache entries, self.clean() is called
        on instantiation.'''
        if not sstr or len(sstr) < 2 or self.ignore:
            return
        key = self._get_key(dapr, variant, sstr)
        return self.cache.get(key)

    def set(self, dapr, variant, sstr, data):
        '''Sets cache data for a given DataProvider, variant and sstr.'''
        if not sstr or len(sstr) < 2:
            return
        key = self._get_key(dapr, variant, sstr)
        if data:
            keep = ['tags', 'mbid', 'releasetype']
            if len(data) > 1:
                keep += ['info', 'title', 'year']
            for dat in data:
                for k in [k for k in dat.keys() if k not in keep]:
                    del dat[k]
        # just update the data if key exists
        if self.cache.get(key) and not self.ignore:
            self.cache[key]['data'] = data
        else:
            self.cache[key] = {'time': time.time(), 'data': data}
        self.dirty = True

    def clean(self):
        '''Cleans up expired or invalid entries from the cache.'''
        print("\nCleaning cache... ", end='')
        size = len(self.cache)
        for key, val in self.cache.items():
            if time.time() - val.get('time', 0) > self.timeout \
                or re.match('discogs##artist##', key) \
                or re.match('(echonest|idiomag)##album##', key) \
                or re.match('.*##.*##.?$', key):
                del self.cache[key]
        print("done! (%d removed)" % (size - len(self.cache)))
        if size > len(self.cache):
            self.dirty = True

    def save(self):
        '''Saves the cache dict as json string to a file in a safe way to avoid
        data loss on interruption.'''
        if not self.dirty:
            return
        print("\nSaving cache... ", end='')
        dirname, basename = os.path.split(self.fullpath)
        try:
            with tempfile.NamedTemporaryFile(prefix=basename + '.tmp_',
                                             dir=dirname,
                                             delete=False) as tmpfile:
                tmpfile.write(json.dumps(self.cache))
                os.fsync(tmpfile)
            os.rename(tmpfile.name, self.fullpath)
            self.time = time.time()
            self.dirty = False
            print("done! (%d entries, %.2f MB)"
                  % (len(self.cache), os.path.getsize(self.fullpath) / 2 ** 20))
        except KeyboardInterrupt:
            pass
