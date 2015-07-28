# whatlastgenre
# Improves genre metadata of audio files
# based on tags from various music sites.
#
# Copyright (c) 2012-2015 YetAnotherNerd
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

'''whatlastgenre beets plugin'''

from __future__ import absolute_import, print_function

from argparse import Namespace

from beets import config
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, decargs
from beetsplug import lastgenre

from wlg import whatlastgenre


class WhatLastGenre(BeetsPlugin):
    '''First version of the whatlastgenre plugin for beets.'''

    def __init__(self):
        super(WhatLastGenre, self).__init__()
        self.config.add({
            'auto': False,
            'force': False,
            'count': 4,
            'separator': u', ',
            'whitelist': u'wlg',  # wlg = whatlastgenre whitelist
                                  # beets = beets lastgenre plugin whitelist
                                  # or a custom path (fallback to wlg)
        })
        if self.config['auto'].get(bool):
            self.import_stages = [self.imported]
        self.wlg = None

    def lazy_setup(self):
        self.wlg = whatlastgenre.WhatLastGenre(Namespace(
            tag_limit=self.config['count'].get(int),
            update_cache=self.config['force'].get(bool),
            interactive=False, dry=False, difflib=False,
            tag_release=False, verbose=0))
        whitelist = self.config['whitelist'].get()
        if not whitelist:
            whitelist = 'wlg'
        if whitelist != 'wlg':
            if whitelist == 'beets':
                whitelist = lastgenre.WHITELIST
            self.wlg.read_whitelist(whitelist)
        self._log.debug(u'use {0} whitelist with {1} entries.',
                        whitelist, len(self.wlg.whitelist))

    def commands(self):
        cmds = Subcommand('wlg', help='get genres with whatlastgenre')
        cmds.parser.add_option('-f', '--force', dest='force',
                               action='store_true', default=False,
                               help='force cache update')
        cmds.func = self.commanded
        return [cmds]

    def commanded(self, lib, opts, args):
        write = config['import']['write'].get(bool)
        self.config.set_args(opts)
        for album in lib.albums(decargs(args)):
            album.genre = self.genres(album, opts.force)
            album.store()
            for item in album.items():
                item.genre = album.genre
                item.store()
                if write:
                    item.try_write()

    def imported(self, session, task):
        if task.is_album:
            genres = self.genres(task.album)
            task.album.genre = genres
            task.album.store()
            for item in task.album.items():
                item.genre = genres
                item.store()
        else:
            genres = self.genres(task.item.get_album())
            item.genre = genres
            item.store()

    def genres(self, album, force=False):
        if not self.wlg:
            self.lazy_setup()
        self.wlg.cache.update_cache = self.config['force'].get(bool) or force
        metadata = whatlastgenre.Metadata(
            path=album.item_dir(), type='beet',
            artists=[(t.artist, t.mb_artistid) for t in album.items()],
            albumartist=(album.albumartist, album.mb_albumartistid),
            album=album.album, mbid_album=album.mb_albumid,
            mbid_relgrp=album.mb_releasegroupid,
            year=album.year, releasetype=album.albumtype)
        genres, _ = self.wlg.query_album(metadata)
        genres = self.config['separator'].get(unicode).join(genres)
        self._log.info(u'genres for album {0}: {1}', album, genres)
        return genres
