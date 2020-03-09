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

"""taglib tests"""

from __future__ import absolute_import, print_function, unicode_literals

import re
import unittest
from random import randint

from wlg.whatlastgenre import Config, TagLib
from . import get_config

WHITELIST = [
    'alternative',
    'blues',
    'classical',
    'country',
    'edm',
    'electronic',
    'folk',
    'hip-hop',
    'jazz',
    'pop',
    'progressive',
    'reggae',
    'rock',
]

TAGSFILE = {
    'upper': [
        'edm',
    ],
    'alias': {
        'hip hop': 'hip-hop',
    },
    'regex': [
        (re.compile(r'\btriphop\b', re.I), 'trip-hop'),
    ],
}


class TestTagLib(unittest.TestCase):
    def setUp(self):
        conf = get_config()
        # use some default config options for these tests
        for sec, opt, val in Config.conf:
            if sec in ['genres', 'scores']:
                conf.set(sec, opt, val)
        self.taglib = TagLib(conf, WHITELIST, TAGSFILE)

    def test_add(self):
        tags = {
            'blues': 1,
            'pop': 1,
            'rock': 1,
            'shit': 1,
        }
        whitelisted = sum(x in WHITELIST for x in tags.keys())
        good = self.taglib.add(tags, 'artist')
        self.assertEqual(whitelisted, good)

    def test_score_with_counts(self):
        tags = {'tag%s' % i: randint(1, 10) for i in range(5)}
        for score_mod in [0.5, 1, 2]:
            scored_tags = self.taglib.score(tags, score_mod)
            max_score = max(scored_tags.values())
            self.assertEqual(max_score, score_mod)

    def test_score_without_counts(self):
        tags = {'tag%s' % i: 0 for i in range(5)}
        scored_tags = self.taglib.score(tags, 1)
        len_scores = len(set(scored_tags.values()))
        self.assertEqual(len_scores, 1)

    def test_score_negative(self):
        """issue #7"""
        tags = {'tag%s' % i: randint(1, 10) for i in range(5)}
        tags.update({'tag5': -1})
        scored_tags = self.taglib.score(tags, 1)
        pos_scores = [x for x in scored_tags.values() if x > 0]
        self.assertEqual(len(tags) - 1, len(pos_scores))

    def test_difflib_matching(self):
        tags = {
            'blues': 1,
            'klassikal': 1,
            'elektronik': 1,
            'shit': 1,
        }
        for key, match in self.taglib.difflib_matching(tags):
            print(key, match)
            self.assertIsNone(key)
            self.assertIsNone(match)

    def test_split(self):
        tags = [
            'pop/country',
            'jazz-blues',
            'alternative rock',
            'progressive rock',
            'punk-folk',
            'classical electronic reggae',
        ]
        tags_split = [
            'alternative',
            'blues',
            'classical',
            'country',
            'electronic',
            'folk',
            'jazz',
            'pop',
            'progressive',
            'reggae',
            'rock',
        ]
        for tag in tags:
            self.taglib.split(tag, 1, 'artist')
        for tag in tags_split:
            self.assertIn(tag, self.taglib.taggrps['artist'].keys())

    def test_normalize(self):
        tags = {
            'blues': 2.5,
            'jazz': 0.8,
        }
        tags = self.taglib.normalize(tags)
        self.assertEqual(1, max(tags.values()))

    def test_merge(self):
        self.taglib.add({'pop': 0.6}, 'artist')
        self.taglib.add({'rock': 0.3}, 'album')
        merged_tags = self.taglib.merge()
        self.assertIn('pop', merged_tags.keys())
        self.assertIn('rock', merged_tags.keys())
        self.assertEqual(2, len(merged_tags))
        # check normalized score
        self.assertEqual(1, max(merged_tags.values()))

    def test_format(self):
        test_data = [
            ('nu jazz', 'Nu Jazz'),
            ('the test tag', 'The Test Tag'),
            ('edm', 'EDM'),
        ]
        for raw, done in test_data:
            self.assertEqual(done, self.taglib.format(raw))

    def test_get_genres(self):
        self.taglib.add({'pop': 1, 'rock': 0.5}, 'artist')
        self.taglib.add({'rock': 0.8, 'jazz': 0.0}, 'album')
        genres = self.taglib.get_genres()
        self.assertEqual(['Rock', 'Pop'], genres)

    def test_get_genres_limit(self):
        limit = 1
        self.taglib.conf.args.tag_limit = limit
        self.taglib.add({'rock': 1, 'pop': 1, 'jazz': 1}, 'album')
        self.assertEqual(limit, len(self.taglib.get_genres()))
