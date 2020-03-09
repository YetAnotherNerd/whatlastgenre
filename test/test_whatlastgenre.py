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

"""whatlastgenre tests"""

from __future__ import absolute_import, print_function, unicode_literals

import os
import shutil
import tempfile
import time
import unittest
from tempfile import NamedTemporaryFile

from wlg import whatlastgenre
from wlg.dataprovider import DataProvider
from wlg.mediafile import Metadata
from . import get_config
from .test_mediafile import DATA_PATH


class TestWhatLastGenreClass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conf = get_config()
        conf.set('wlg', 'sources', 'lastfm')
        cls.wlg = whatlastgenre.WhatLastGenre(conf)

    def test_read_whitelist(self):
        whitelist = self.wlg.read_whitelist()
        self.assertIsNot(len(whitelist), 0)

    def test_read_whitelist_doesnt_exist(self):
        with self.assertRaises(IOError):
            self.wlg.read_whitelist('/tmp/test' + str(time.time()))

    def test_read_whitelist_empty(self):
        with self.assertRaises(RuntimeError):
            with NamedTemporaryFile() as file_:
                self.wlg.read_whitelist(file_.name)

    def test_read_tagsfile(self):
        tagsfile = self.wlg.read_tagsfile()
        self.assertIn('upper', tagsfile.keys())
        self.assertIn('alias', tagsfile.keys())
        self.assertIn('regex', tagsfile.keys())

    def test_read_tagsfile_doesnt_exist(self):
        with self.assertRaises(IOError):
            self.wlg.read_tagsfile('/tmp/test' + str(time.time()))

    def test_read_tagsfile_empty(self):
        with self.assertRaises(RuntimeError):
            with NamedTemporaryFile() as file_:
                self.wlg.read_tagsfile(file_.name)

    def test_progress_path(self):
        temp_path = os.path.join(tempfile.gettempdir(), 'wlg_tests')
        shutil.copytree(DATA_PATH, temp_path)
        self.wlg.progress_path(temp_path)
        shutil.rmtree(temp_path)

    def test_progress_path_doesnt_exist(self):
        self.wlg.progress_path(os.path.join(tempfile.gettempdir(),
                                            'wlg_test_not_found'))

    def test_query_album(self):
        metadata = Metadata(
            path='/tmp',
            type='test',
            artists=[('Artist A', None),
                     ('Artist B', None),
                     ('Artist C', None),
                     ('Artist D', None)],
            albumartist=('AlbumArtist', None),
            album='Album',
            mbid_album=None,
            mbid_relgrp=None,
            year=1987,
            releasetype=None)
        genres, release = self.wlg.query_album(metadata)
        self.assertIsNotNone(genres)
        self.assertIsNone(release)

    def test_cached_query(self):
        query = whatlastgenre.Query(
            dapr=DataProvider(),
            type='test',
            str='test',
            score=1,
            artist='test artist',
            mbid_artist='12345',
            album='test album',
            mbid_album='12345',
            mbid_relgrp='12345',
            year=None,
            releasetype=None)
        res, cached = self.wlg.cached_query(query)
        self.assertFalse(cached)
        res, cached = self.wlg.cached_query(query)
        self.assertTrue(cached)
        del self.wlg.cache.cache[str(self.wlg.cache.cachekey(query))]

    def test_create_queries_with_albumartist(self):
        metadata = Metadata(
            path='/tmp',
            type='test',
            albumartist=('AlbumArtist', None),
            artists=[('Artist A', None),
                     ('Artist B', None),
                     ('Artist C', None),
                     ('Artist D', None)],
            album='Album',
            mbid_album=None,
            mbid_relgrp=None,
            year=1987,
            releasetype=None)
        queries = self.wlg.create_queries(metadata)
        self.assertEqual(len(queries), 2 * len(self.wlg.daprs))

    def test_create_queries_without_albumartist(self):
        metadata = Metadata(
            path='/tmp',
            type='test',
            artists=[('Artist A', None),
                     ('Artist B', None),
                     ('Artist C', None),
                     ('Artist D', None)],
            albumartist=(None, None),
            album='Album',
            mbid_album=None,
            mbid_relgrp=None,
            year=1987,
            releasetype=None)
        # vaqueries enabled
        self.wlg.conf.set('scores', 'various', '1.0')
        queries = self.wlg.create_queries(metadata)
        self.assertEqual(len(queries), 5 * len(self.wlg.daprs))
        # va queries disabled
        self.wlg.conf.set('scores', 'various', '0.0')
        queries = self.wlg.create_queries(metadata)
        self.assertEqual(len(queries), 1 * len(self.wlg.daprs))

    def test_merge_results(self):
        tags = {'tag%s' % i: 0 for i in range(10)}
        test_results = [{'tags': tags,
                         'test': 'test123',
                         'alt': str(j % 2),
                         } for j in range(5)]
        merged_results = self.wlg.merge_results(test_results)
        self.assertIn('test', merged_results.keys())
        self.assertNotIn('alt', merged_results.keys())
        self.assertEqual('test123', merged_results['test'])
        self.assertEqual(tags, merged_results['tags'])


class TestWhatLastGenre(unittest.TestCase):
    def test_preprocess_tags_with_scores(self):
        tags = {
            'a': 1,
            'ABC': 1,
            'abcdefabcdefabcdefabcdefabcdefabcdef' +
            'abcdefabcdefabcdefabcdefabcde': 1,
        }
        preprocessed_tags = whatlastgenre.preprocess_tags(tags)
        for key, val in preprocessed_tags.items():
            self.assertEqual(key, key.strip().lower())
            self.assertGreater(val, 0)
        self.assertEqual(len(preprocessed_tags), 1)

    def test_preprocess_tags_many_without_scores(self):
        tags = {'tag%s' % i: 0 for i in range(100)}
        preprocessed_tags = whatlastgenre.preprocess_tags(tags)
        self.assertEqual(len(preprocessed_tags), 42)

    def test_searchstr(self):
        test_data = [
            ('Artist feat. Guest', 'artist'),
            ('Album Vol. 3', 'album 3'),
            ('album - soundtrack', 'album'),
            ('album (limited edition)', 'album'),
        ]
        for raw, done in test_data:
            self.assertEqual(done, whatlastgenre.searchstr(raw))

    def test_tag_display(self):
        res = whatlastgenre.tag_display([('a', 1), ('b', 2), ('c', 3)],
                                        '%4d %-20s')
        self.assertIsNotNone(res)

    def test_tag_display_empty(self):
        res = whatlastgenre.tag_display({}, '')
        self.assertEqual(res, '')

    def test_read_datafile_internal(self):
        res = whatlastgenre.read_datafile('data/genres.txt')
        self.assertIsNotNone(res)

    def test_read_datafile_external(self):
        with NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(b'\n aBc \n xYz\n\n')
        res = whatlastgenre.read_datafile(temp_file.name)
        self.assertEqual(res, ['abc', 'xyz'])
        os.unlink(temp_file.name)

    def test_read_datafile_empty(self):
        with NamedTemporaryFile() as temp_file:
            res = whatlastgenre.read_datafile(temp_file.name)
        self.assertEqual(res, [])

    def test_read_datafile_not_found(self):
        temp_path = os.path.join(tempfile.gettempdir(), 'wlg_test_not_found')
        with self.assertRaises(IOError):
            whatlastgenre.read_datafile(temp_path)
