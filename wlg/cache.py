#!/usr/bin/env python
'''whatlastgenre cache'''

from __future__ import print_function

import json
import re
import time


class Cache(object):
    '''Caching speeds up :-)'''

    def __init__(self, args, conf):
        self.file = args.cache
        self.ignore = args.cacheignore
        self.timeout = conf.getint('wlg', 'cache_timeout') * 60 * 60 * 24
        self.time = time.time()
        self.dirty = False
        self.cache = {}
        try:
            with open(self.file) as infile:
                self.cache = json.load(infile)
            self.clean()
        except IOError:
            pass

    def __del__(self):
        self.save()
        print()

    @classmethod
    def _get_key(cls, dapr, variant, sstr):
        '''Helper method to get the cache key.'''
        key = '##'.join([dapr, variant, sstr])
        for pat in [r'[^\w#]', ' +']:
            key = re.sub(pat, '', key, 0, re.I)
        return key.lower().strip()

    def get(self, dapr, variant, sstr):
        '''Gets cache data for a given DataProvider and variant.
        Since this method does't check the timestamps of the cache entries,
        self.clean() should be run before using the cache.'''
        if self.ignore or not sstr:
            return
        key = self._get_key(dapr, variant, sstr)
        return self.cache.get(key)

    def set(self, dapr, variant, sstr, data):
        '''Sets cache data for a given DataProvider and variant.'''
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
        '''Cleans up expired data from the cache.'''
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
        with open(self.file, 'w') as outfile:
            json.dump(self.cache, outfile)
        self.time = time.time()
        self.dirty = False
