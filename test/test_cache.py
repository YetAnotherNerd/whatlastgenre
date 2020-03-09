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

"""cache tests"""

from __future__ import absolute_import, print_function, unicode_literals

import os
import shutil
import tempfile
import time
import unittest

from wlg.cache import Cache

CACHE_PATH = os.path.join(tempfile.gettempdir(), 'wlg_test_cache')


class TestCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CACHE_PATH):
            os.mkdir(CACHE_PATH)
        cls.cache = Cache(CACHE_PATH, True)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(CACHE_PATH):
            shutil.rmtree(CACHE_PATH)

    def test_get_and_set(self):
        key = 'test' + str(time.time())
        val = [{'tags': {'test': 0}}]
        self.cache.set(key, val)
        self.assertEqual(self.cache.get(key)[1], val)

    def test_get_unknown_key(self):
        key = 'unknown' + str(time.time())
        self.assertIsNone(self.cache.get(key))

    def test_clean(self):
        key = 'testclean' + str(time.time())
        self.cache.set(key, [])
        newtime = self.cache.cache[key][0] - self.cache.expire_after - 1
        self.cache.cache[key] = (newtime, [])
        size = len(self.cache.cache)
        self.cache.clean()
        self.assertEqual(size - 1, len(self.cache.cache))

    def test_save(self):
        self.assertFalse(os.path.exists(self.cache.fullpath))
        self.cache.save()
        self.assertTrue(os.path.exists(self.cache.fullpath))
