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

"""mediafile tests"""

from __future__ import absolute_import, print_function, unicode_literals

import os.path
import shutil
import tempfile
import time
import unittest

from wlg.mediafile import Album, find_music_dirs, VA_MBID

DATA_PATH = os.path.abspath(os.path.join('test', 'data'))


class TestMediafile(unittest.TestCase):
    temp_path = os.path.join(tempfile.gettempdir(), 'wlg_tests')

    @classmethod
    def setUpClass(cls):
        """copy test files to temporary location"""
        shutil.copytree(DATA_PATH, cls.temp_path)

    @classmethod
    def tearDownClass(cls):
        """delete test files from temporary location"""
        shutil.rmtree(cls.temp_path)

    def get_album(self):
        return Album(self.temp_path)

    def get_track(self, ext=None):
        for track in self.get_album().tracks:
            if not ext or track.ext == ext:
                return track
        return None

    def test_find_music_dirs(self):
        paths = find_music_dirs([self.temp_path])
        self.assertIn(self.temp_path, paths)

    def test_album_get_metadata(self):
        album = self.get_album()

        albumname = 'Test Album'
        releasetype = 'Test'
        date = '2015'
        mbid_album = 'Test Album MBID'
        mbid_relgrp = 'Test RelGrp MBID'
        albumartist = ('Test Artist', None)
        artists = [('Test Artist', None),
                   ('Test Artist feat. Alice', None),
                   ('Test Artist & Bob', None)]

        album.set_meta('album', albumname)
        album.set_meta('date', date)
        album.set_meta('releasetype', releasetype)
        album.set_meta('albumartist', albumartist[0])
        album.set_meta('musicbrainz_albumartistid', albumartist[1])
        album.set_meta('musicbrainz_albumid', mbid_album)
        album.set_meta('musicbrainz_releasegroupid', mbid_relgrp)
        for i, track in enumerate(album.tracks):
            artist = artists[i % len(artists)]
            track.set_meta('artist', artist[0])
            track.set_meta('musicbrainz_artistid', artist[1])

        metadata = album.get_metadata()
        self.assertEqual(metadata.album, albumname)
        self.assertEqual(metadata.releasetype, releasetype)
        self.assertEqual(metadata.year, date)
        self.assertEqual(metadata.mbid_album, mbid_album)
        self.assertEqual(metadata.mbid_relgrp, mbid_relgrp)
        self.assertEqual(metadata.albumartist, albumartist)
        # self.assertIsNone(metadata.artists)
        self.assertTrue(all(a in artists for a in metadata.artists))

        # without album artist (va artist) but common artist
        album.set_meta('albumartist', 'Various Artists')
        album.set_meta('musicbrainz_albumartistid', VA_MBID)
        metadata = album.get_metadata()
        self.assertEqual(metadata.albumartist, albumartist)

        # without album artist (va artist) and no common artist
        for i, track in enumerate(album.tracks):
            track.set_meta('artist', '%s Artist' % i)
            track.set_meta('musicbrainz_artistid', '%s MBID' % i)
        metadata = album.get_metadata()
        self.assertIsNone(metadata.albumartist[0])
        self.assertEqual(len(metadata.artists), len(album.tracks))

    def test_album_get_and_set_meta(self):
        album = self.get_album()
        val = 'test %s' % time.time()
        album.set_meta('album', val)
        self.assertEqual(album.get_meta('album'), val)
        val = [val, val, val * 2]
        album.set_meta('album', val)
        self.assertEqual(album.get_meta('album'), val[0])

    def test_track_get_and_set_meta(self):
        track = self.get_track()
        val = 'test %s' % time.time()
        track.set_meta('album', val)
        self.assertEqual(track.get_meta('album'), [val])
        val = [val, val, val * 2]
        track.set_meta('album', val)
        self.assertEqual(track.get_meta('album'), val)

    def test_get_and_set_csv_tags(self):
        album = self.get_album()
        values = ['Artist A', 'Artist B', 'Artist C']
        album.set_meta('artist', '; '.join(values))
        self.assertEqual(album.get_meta('artist'), values[0])

    def test_get_and_set_date_tag(self):
        album = self.get_album()
        for val in ['2015', '2015-01-01', '2015/01/01']:
            album.set_meta('date', val)
            self.assertEqual(album.get_meta('date'), '2015')

    def test_get_and_set_releaseinfo(self):
        album = self.get_album()
        for key, val in [('releasetype', 'Album'),
                         ('date', '2015'),
                         ('label', 'Music Records'),
                         ('catalognumber', 'MR001'),
                         ('edition', 'Limited Edition'),
                         ('media', 'CD')]:
            album.set_meta(key, val)
            self.assertEqual(album.get_meta(key), val)

    def test_id3v23_separator(self):
        track = self.get_track('mp3')
        genre = ['Test', 'Music', str(time.time())]
        track.set_meta('genre', genre)
        track.save()
        self.assertEqual(track.get_meta('genre'), genre)
        track.v23sep = ';'
        track.set_meta('genre', genre)
        track.save()
        self.assertEqual(track.get_meta('genre'), genre)

    def test_album_save(self):
        self.get_album().save()
