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

"""dataprovider tests"""

from __future__ import absolute_import, print_function, unicode_literals

import unittest

from wlg.dataprovider import LASTFM_API_KEY, requests_cache, factory, \
    DataProvider, DataProviderError
from . import get_config

if requests_cache:
    requests_cache.core.uninstall_cache()

HTTPBIN_URL = 'http://httpbin.org/'


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


class TestDiscogsDataProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conf = get_config()
        if conf.has_section('discogs') and \
                conf.get('discogs', 'token') and \
                conf.get('discogs', 'secret'):
            cls.dapr = factory('discogs', conf)
        else:
            raise unittest.SkipTest('no discogs auth')

    def test_discogs(self):
        result = self.dapr._request_json('https://api.discogs.com/', {})
        self.assertIn('api_version', result)


class TestLastFMDataProvider(unittest.TestCase):
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


class TestMusicBrainzDataProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dapr = factory('mbrainz', None)

    def test_api(self):
        mbid = '067102ea-9519-4622-9077-57ca4164cfbb'
        res = self.dapr._request_json(
            'http://musicbrainz.org/ws/2/artist/%s' % mbid,
            {'fmt': 'json', 'limit': 1})
        self.assertEqual(res['id'], mbid)


class TestRedactedDataProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conf = get_config()
        if conf.has_section('redacted') and (
                conf.get('redacted', 'session') or (
                conf.get('redacted', 'username') and
                conf.get('redacted', 'password'))):
            cls.dapr = factory('redacted', conf)
        else:
            raise unittest.SkipTest('no redacted auth')

    def test_api(self):
        res = self.dapr._query({'action': 'index'})
        self.assertIsNotNone(res['id'])
