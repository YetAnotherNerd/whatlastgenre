#!/usr/bin/env python
'''whatlastgenre cache'''

from __future__ import print_function

import json
import os
import re
import tempfile
import time


class Cache(object):
    '''Caching speeds up :-)'''

    def __init__(self, filename, ignore, timeout):
        self.filename = filename
        self.ignore = ignore
        self.timeout = timeout * 60 * 60 * 24
        self.time = time.time()
        self.dirty = False
        self.cache = {}
        try:
            with open(self.filename) as infile:
                self.cache = json.load(infile)
            self.clean()
        except (IOError, ValueError):
            pass

    def __del__(self):
        self.save()
        print()

    @classmethod
    def _get_key(cls, dapr, variant, sstr):
        '''Helper method to get the cache key.'''
        key = '##'.join([dapr, variant, sstr])
        return re.sub(r'([^\w#]| +)', '', key, 0, re.I).lower().strip()

    def get(self, dapr, variant, sstr):
        '''Gets cache data for a given DataProvider and variant.
        Since this method does't check the timestamps of the cache entries,
        self.clean() is be run before using the cache.'''
        if not sstr or self.ignore:
            return
        key = self._get_key(dapr, variant, sstr)
        return self.cache.get(key)

    def set(self, dapr, variant, sstr, data):
        '''Sets cache data for a given DataProvider, variant and sstr.'''
        if not sstr:
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
        '''Cleans up expired entries from the cache.'''
        print("\nCleaning cache...")
        for key, val in self.cache.items():
            if not val.get('time') or time.time() - val['time'] > self.timeout:
                del self.cache[key]
                self.dirty = True
        self.save()

    def save(self):
        '''Saves the cache to disk.'''
        if not self.dirty:
            return
        print("\nSaving cache...")
        dirname, basename = os.path.split(self.filename)
        try:
            with tempfile.NamedTemporaryFile(prefix=basename + '.tmp_',
                                             dir=dirname,
                                             delete=False) as tmpfile:
                tmpfile.write(json.dumps(self.cache))
                os.fsync(tmpfile)
            os.rename(tmpfile.name, self.filename)
            self.time = time.time()
            self.dirty = False
        except KeyboardInterrupt:
            pass
