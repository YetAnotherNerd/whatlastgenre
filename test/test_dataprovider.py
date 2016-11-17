# whatlastgenre
# Improves genre metadata of audio files
# based on tags from various music sites.
#
# Copyright (c) 2012-2016 YetAnotherNerd
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

"""dataprovider tests

I hope these tests make sense since they compare to data from remote
APIs which obviously could change any time.  Since the data is
somewhat constant they appear helpful anyway.  Make sure the failure
is not caused by changed remote data.
"""

from __future__ import print_function

import unittest

import pytest
from wlg.dataprovider import LASTFM_API_KEY, requests_cache, factory, \
    DataProvider, DataProviderError

from . import get_config

if requests_cache:
    requests_cache.core.uninstall_cache()

HTTPBIN_URL = 'http://httpbin.org/'

TEST_DATA = {
    'artist': [
        ('nirvana',),
    ],
    'album': [
        ('who can you trust', 'morcheeba', None, None),
        ('the virgin suicides', 'air', 2000, 'soundtrack'),
    ],
    'mbid': [
        # pink floyd the dark side of the moon (album mbid)
        ('album', 'f5093c06-23e3-404f-aeaa-40f72885ee3a'),
        # portishead dummy (relgrp mbid)
        ('album', '76df3287-6cda-33eb-8e9a-044b5e15ffdd'),
        # bonobo (artist mbid)
        ('artist', '9a709693-b4f8-4da9-8cc1-038c911a61be'),
    ],
}


class TestDataProvider(unittest.TestCase):
    def test_factory(self):
        conf = get_config()
        for name in ['lastfm', 'mbrainz']:
            factory(name, conf)

    def test_factory_fail(self):
        with self.assertRaises(DataProviderError):
            factory('fail', None)


class TestDataProviderClass(unittest.TestCase):
    params = {'test': '123', 'foo': 'bar'}

    @classmethod
    def setUpClass(cls):
        cls.dapr = DataProvider()
        cls.dapr.rate_limit = 0

    def test_request_get(self):
        res = self.dapr._request(HTTPBIN_URL + 'get', self.params, 'GET')
        args = {str(k): str(v) for k, v in res.json()['args'].items()}
        self.assertEqual(self.params, args)

    def test_request_post(self):
        res = self.dapr._request(HTTPBIN_URL + 'post', self.params, 'POST')
        args = {str(k): str(v) for k, v in res.json()['form'].items()}
        self.assertEqual(self.params, args)

    def test_request_bad_status(self):
        with self.assertRaises(DataProviderError):
            self.dapr._request(HTTPBIN_URL + 'status/418', None)

    def test_request_json(self):
        res = self.dapr._request_json(HTTPBIN_URL + 'get', self.params, 'GET')
        args = {str(k): str(v) for k, v in res['args'].items()}
        self.assertEqual(self.params, args)

    def test_prefilter_results(self):
        res = [0, 0, 1, 1, 2, 2]
        filtered = self.dapr._prefilter_results(res, 'test', 1, lambda x: x)
        self.assertEqual(len(filtered), 2)


class DataProviderTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if cls is DataProviderTestCase:
            raise unittest.SkipTest('base class')
        super(DataProviderTestCase, cls).setUpClass()

    def dapr_test(self, test_func, test_data, expected_results):
        if not expected_results:
            with self.assertRaises(NotImplementedError):
                test_func(*test_data[0])
        else:
            for test, exp_res in zip(test_data, expected_results):
                res = test_func(*test)
                if res:
                    res = (len(res), sum(len(r['tags']) for r in res))
                self.assertEqual(exp_res, res)

    @pytest.mark.long
    def test_query_artist(self):
        self.dapr_test(self.dapr.query_artist,
                       TEST_DATA['artist'],
                       self.results.get('artist'))

    @pytest.mark.long
    def test_query_album(self):
        self.dapr_test(self.dapr.query_album,
                       TEST_DATA['album'],
                       self.results.get('album'))

    @pytest.mark.long
    def test_query_by_mbid(self):
        self.dapr_test(self.dapr.query_by_mbid,
                       TEST_DATA['mbid'],
                       self.results.get('mbid'))


class TestDiscogsDataProvider(DataProviderTestCase):
    results = {
        'album': [
            (1, 5),
            (1, 16),
        ],
    }

    @classmethod
    def setUpClass(cls):
        conf = get_config()
        if conf.get('discogs', 'token') and conf.get('discogs', 'secret'):
            cls.dapr = factory('discogs', conf)
        else:
            raise unittest.SkipTest('no discogs auth')

    def test_discogs(self):
        result = self.dapr._request_json('https://api.discogs.com/', {})
        self.assertIn('api_version', result)


class TestLastFMDataProvider(DataProviderTestCase):
    results = {
        'artist': [
            (1, 20),
        ],
        'album': [
            (1, 8),
            (1, 100),
        ],
    }

    @classmethod
    def setUpClass(cls):
        cls.dapr = factory('lastfm', None)

    def test_api(self):
        res = self.dapr._request_json(
            'http://ws.audioscrobbler.com/2.0/',
            {'format': 'json',
             'api_key': LASTFM_API_KEY,
             'method': 'tag.gettoptags'})
        self.assertIn('toptags', res)


class TestMusicBrainzDataProvider(DataProviderTestCase):
    results = {
        'artist': [
            (1, 20),
        ],
        'album': [
            (1, 10),
            (1, 12),
        ],
        'mbid': [
            (1, 24),
            None,
            (1, 4),
        ],
    }

    @classmethod
    def setUpClass(cls):
        cls.dapr = factory('mbrainz', None)

    def test_api(self):
        mbid = '067102ea-9519-4622-9077-57ca4164cfbb'
        res = self.dapr._request_json(
            'http://musicbrainz.org/ws/2/artist/%s' % mbid,
            {'fmt': 'json', 'limit': 1})
        self.assertEqual(res['id'], mbid)


class TestRedactedDataProvider(DataProviderTestCase):
    results = {
        'artist': [
            (1, 97),
        ],
        'album': [
            (1, 1),
            (1, 9),
        ],
    }

    @classmethod
    def setUpClass(cls):
        conf = get_config()
        if conf.get('redacted', 'session') or (
                    conf.get('redacted', 'username') and
                    conf.get('redacted', 'password')):
            cls.dapr = factory('redacted', conf)
        else:
            raise unittest.SkipTest('no redacted auth')

    def test_api(self):
        res = self.dapr._query({'action': 'index'})
        self.assertIsNotNone(res['id'])
