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

"""whatlastgenre beets plugin"""

from __future__ import absolute_import, print_function, unicode_literals

from argparse import Namespace

from beets import config
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand, decargs

from wlg import whatlastgenre
from wlg.mediafile import Metadata


class WhatLastGenre(BeetsPlugin):
    """whatlastgenre plugin for beets."""

    def __init__(self):
        super(WhatLastGenre, self).__init__()
        self.config.add({
            'auto': False,
            'force': False,
            'count': 4,
            'separator': ', ',
            'whitelist': 'wlg',  # wlg, beets or custom path
        })
        if self.config['auto'].get(bool):
            self.import_stages = [self.imported]
            self.register_listener('import', self.setdown)
        self.wlg = None

    def setup(self, update_cache=False, verbose=0):
        """Set up the WhatLastGenre object."""
        whitelist = self.config['whitelist'].get()
        if whitelist == 'wlg':
            whitelist = ''
        elif whitelist == 'beets':
            from beetsplug.lastgenre import WHITELIST
            whitelist = WHITELIST
        conf = whatlastgenre.Config(Namespace(
            tag_limit=self.config['count'].get(int),
            update_cache=update_cache,
            verbose=verbose,
            dry=False,
            difflib=False,
            release=False))
        conf.set('wlg', 'whitelist', str(whitelist))
        self.wlg = whatlastgenre.WhatLastGenre(conf)

    def setdown(self):
        """Since __del__s don't get called we need to do some stuff
        manually.
        """
        self.wlg.cache.save()

    def commands(self):
        cmds = Subcommand('wlg', help='get genres with whatlastgenre')
        cmds.parser.add_option(
            '-v', '--verbose', dest='verbose', action='count',
            default=0, help='verbose output (-vv for debug)')
        cmds.parser.add_option(
            '-f', '--force', dest='force', action='store_true',
            default=False, help='force overwrite existing genres')
        cmds.parser.add_option(
            '-u', '--update-cache', dest='cache', action='store_true',
            default=False, help='force update cache')
        cmds.func = self.commanded
        return [cmds]

    def commanded(self, lib, opts, args):
        """wlg as command"""
        if not self.wlg:
            self.setup(opts.cache, opts.verbose)

        if opts.force:
            self.config['force'] = True

        albums = lib.albums(decargs(args))
        i = 1
        try:
            for i, album in enumerate(albums, start=1):
                self._log.info(whatlastgenre.progressbar(i, len(albums)))
                genres = self.genres(album)
                if album.genre != genres:
                    album.genre = genres
                    album.store()
                    for item in album.items():
                        item.genre = genres
                        item.store()
                        if config['import']['write'].get(bool):
                            item.try_write()
        except KeyboardInterrupt:
            pass

        self.wlg.print_stats(i)
        self.setdown()

    def imported(self, _, task):
        """wlg during import"""
        if not self.wlg:
            self.setup()

        if task.is_album:
            genres = self.genres(task.album)
            if task.album.genre != genres:
                task.album.genre = genres
                task.album.store()
                for item in task.album.items():
                    item.genre = genres
                    item.store()

    def genres(self, album):
        """Return the current genres of an album if they exist and
        the force option is not set or get genres from whatlastgenre.
        """
        if album.genre and not self.config['force']:
            self._log.info('not forcing genre update for album {0}', album)
            return album.genre

        metadata = Metadata(
            path=album.item_dir().decode(),
            type='beet',
            artists=[(t.artist, t.mb_artistid) for t in album.items()],
            albumartist=(album.albumartist, album.mb_albumartistid),
            album=album.album,
            mbid_album=album.mb_albumid,
            mbid_relgrp=album.mb_releasegroupid,
            year=album.year,
            releasetype=album.albumtype)

        genres, _ = self.wlg.query_album(metadata)
        try:
            genres = self.config['separator'].get(str).join(genres)
            self._log.info('genres for album {0}: {1}', album, genres)
        except TypeError:
            self._log.info('No genres found for album {0}', album)

        return genres
