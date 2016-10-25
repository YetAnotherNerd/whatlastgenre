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

"""whatlastgenre

https://github.com/YetAnotherNerd/whatlastgenre
"""

from __future__ import division, print_function

import ConfigParser
import argparse
from collections import defaultdict, Counter, namedtuple
from datetime import timedelta
import itertools
import logging
import math
import operator
import os
import pkgutil
import re
import sys
import time

from . import __version__, cache, dataprovider, mediafile

Query = namedtuple(
    'Query', ['dapr', 'type', 'str', 'score', 'artist', 'mbid_artist',
              'album', 'mbid_album', 'mbid_relgrp', 'year', 'releasetype'])

Stats = namedtuple('Stats', ['time', 'messages', 'genres', 'reltyps'])


class WhatLastGenre(object):
    """Main class featuring a docstring that needs to be written."""

    def __init__(self, conf):
        self.log = logging.getLogger('wlg')
        self.log.setLevel(30 - 10 * conf.args.verbose)
        self.log.addHandler(logging.StreamHandler(sys.stdout))
        self.stats = Stats(time=time.time(),
                           messages=defaultdict(list),
                           genres=Counter(),
                           reltyps=Counter())
        self.conf = conf
        self.cache = cache.Cache(self.conf.path, self.conf.args.update_cache)
        self.daprs = self.init_dataproviders()
        self.whitelist = self.read_whitelist()
        self.tags = self.read_tagsfile()

    def read_whitelist(self, path=None):
        """Read the whitelist trying different paths.

        Return a set of whitelist entries.
        """
        if not path:
            if self.conf.has_option('wlg', 'whitelist') \
                    and self.conf.get('wlg', 'whitelist'):
                path = self.conf.get('wlg', 'whitelist')
            elif os.path.exists(os.path.join(self.conf.path, 'genres.txt')):
                path = os.path.join(self.conf.path, 'genres.txt')
            else:
                path = 'data/genres.txt'
        whitelist = set(read_datafile(path))
        if not whitelist:
            raise RuntimeError('empty whitelist: %s' % path)
        self.log.debug('whitelist: %s (%d items)', path, len(whitelist))
        return whitelist

    def read_tagsfile(self, path=None):
        """Read the tagsfile trying different paths.

        Return a dict of prepared data from the tagsfile.
        """
        if not path:
            if self.conf.has_option('wlg', 'tagsfile') \
                    and self.conf.get('wlg', 'tagsfile'):
                path = self.conf.get('wlg', 'tagsfile')
            elif os.path.exists(os.path.join(self.conf.path, 'tags.txt')):
                path = os.path.join(self.conf.path, 'tags.txt')
            else:
                path = 'data/tags.txt'
        tagsfile = {}
        section = None
        for line in read_datafile(path):
            line = line.strip().lower()
            if line.startswith('[') and line.endswith(']'):
                section = line[1:-1]
                tagsfile[section] = []
            elif line and not line.startswith('#') and section:
                if ' = ' in line:
                    line = tuple(line.split(' = ', 2))
                tagsfile[section].append(line)
        if any(s not in tagsfile.iterkeys()
               for s in ['upper', 'alias', 'regex']):
            raise RuntimeError('missing section in tagsfile: %s' % path)
        for key, val in tagsfile['alias']:
            if val not in self.whitelist:
                self.stat_message(logging.WARN, 'alias not whitelisted',
                                  '%s -> %s' % (key, val), 2)
        regex = []
        for pat, repl in [(r'( *[,;.:\\/&_]+ *| and )+', '/'),
                          (r'[\'"]+', ''), (r'  +', ' ')]:
            regex.append((re.compile(pat, re.I), repl))
        for pat, repl in tagsfile['regex']:
            regex.append((re.compile(r'\b%s\b' % pat, re.I), repl))
        tagsfile['regex'] = regex
        self.log.debug('tagsfile:  %s (%d items)', path,
                       sum(len(v) for v in tagsfile.values()))
        return tagsfile

    def init_dataproviders(self):
        """Initializes the DataProviders activated in the conf file."""
        daprs = []
        for dapr in self.conf.get_list('wlg', 'sources'):
            try:
                daprs.append(dataprovider.factory(dapr, self.conf))
            except dataprovider.DataProviderError as err:
                self.log.warn('%s: %s', dapr, err)
        if not daprs:
            raise RuntimeError(
                'Where do you want to get your data from? At least one source '
                'must be activated! (multiple sources recommended)')
        return daprs

    def progress_path(self, path):
        """Create an Album object for a directory given by path to read and
        write metadata from/to.  Query top genre tags by album metadata,
        update metadata with results and save the album (its tracks).
        """
        # create album object to read and write metadata
        try:
            album = mediafile.Album(path, self.conf.get('wlg', 'id3v23sep'))
        except mediafile.AlbumError as err:
            self.stat_message(logging.ERROR, str(err), path, 1)
            return
        # read album metadata
        metadata = album.get_metadata()
        # query genres (and releasetype) for album metadata
        genres, release = self.query_album(metadata)
        # update album metadata
        if genres:
            album.set_meta('genre', genres)
            print("Genres:  %s" % ', '.join(genres).encode('utf-8'))
        if release and self.conf.args.release:
            release_info = []
            for key in ['releasetype', 'date',
                        'label', 'catalognumber', 'edition', 'media']:
                if key in release and release[key]:
                    album.set_meta(key, release[key])
                    release_info.append(release[key])
            print("Release: %s" % ' / '.join(release_info))
        # save metadata to all tracks
        if self.conf.args.dry:
            print("DRY-RUN! Not saving metadata.")
        else:
            album.save()

    def query_album(self, metadata):
        """Query for top genres of an album identified by metadata
        and return them and some releaseinfo."""
        num_artists = 1
        if not metadata.albumartist[0]:
            num_artists = len(set(metadata.artists))
        self.log.info("[%s] artist=%s, album=%s, date=%s%s",
                      metadata.type, metadata.albumartist[0], metadata.album,
                      metadata.year, (" (%d artists)" % num_artists
                                      if num_artists > 1 else ''))
        taglib = TagLib(self.conf, self.whitelist, self.tags)
        release = None
        for query in self.create_queries(metadata):
            if not query.str:
                continue
            try:
                results, cached = self.cached_query(query)
            except NotImplementedError:
                continue
            except dataprovider.DataProviderError as err:
                query.dapr.stats['reqs_err'] += 1
                self.stat_message(logging.ERROR, '%-8s %-6s error: %s'
                                  % (query.dapr.name, query.type, err),
                                  metadata.path, 1)
                continue
            if not results:
                query.dapr.stats['results_none'] += 1
                if query.type == 'album' or num_artists == 1:
                    self.stat_message(logging.DEBUG, '%s: no %s results'
                                      % (query.dapr.name, query.type),
                                      metadata.path)
                self.verbose_status(query, cached, "no results")
                continue
            # ask user if appropriated
            if len(results) > 1 and not self.conf.args.dry \
                    and self.conf.args.release \
                    and query.dapr.name.lower() == 'whatcd' \
                    and query.type == 'album' \
                    and len(set(r.get('releasetype') for r in results)) > 1:
                results = ask_user(query.dapr.name, query.type, results)
                if len(results) == 1:
                    self.cache.set(self.cache.cachekey(query), results)
            # merge multiple results
            if len(results) in range(2, 6):
                results = [self.merge_results(results)]
            # too many results
            if len(results) > 1:
                query.dapr.stats['results_many'] += 1
                if query.type == 'album' or num_artists == 1:
                    self.stat_message(logging.DEBUG, '%s: too many %s results'
                                      % (query.dapr.name, query.type),
                                      metadata.path)
                self.verbose_status(query, cached,
                                    "%2d results" % len(results))
                continue
            # unique result
            query.dapr.stats['results'] += 1
            if 'tags' in results[0] and results[0]['tags']:
                tags = taglib.score(results[0]['tags'], query.score)
                good = taglib.add(tags, query.type)
                if self.conf.args.difflib:
                    matched = {}
                    for old, new in taglib.difflib_matching(tags):
                        self.stat_message(
                            logging.WARN, 'possible aliases found by difflib',
                            '%s = %s' % (old, new))
                        matched.update({new: tags[old]})
                    good += taglib.add(matched, query.type)
                query.dapr.stats['tags'] += len(tags)
                query.dapr.stats['goodtags'] += good
                status = "%2d of %2d tags" % (good, len(tags))
            else:
                status = "no    tags"
            if query.dapr.name.lower() == 'whatcd' and query.type == 'album':
                if 'releasetype' in results[0] and results[0]['releasetype']:
                    self.stats.reltyps[results[0]['releasetype']] += 1
                    release = {k: v for k, v in results[0].iteritems()
                               if k not in ['info', 'tags']}
                elif self.conf.args.release:
                    self.stat_message(logging.ERROR, 'No releaseinfo found',
                                      metadata.path, 1)
            self.verbose_status(query, cached, status)

        genres = taglib.get_genres(num_artists > 1)
        if genres:
            self.stats.genres.update(genres)
            for group in ['artist', 'album']:
                if not taglib.taggrps[group]:
                    self.stat_message(logging.INFO, 'No %s tags' % group,
                                      metadata.path, 1)
        else:
            self.stat_message(logging.ERROR, 'No genres found',
                              metadata.path, 1)
        return genres, release

    def cached_query(self, query):
        """Perform a cached DataProvider query."""
        cachekey = self.cache.cachekey(query)
        # check cache
        res = self.cache.get(cachekey)
        if res:
            query.dapr.stats['reqs_cache'] += 1
            return res[1], True
        # no cache hit
        res = self.query(query)
        self.cache.set(cachekey, res)
        # save cache periodically
        if time.time() - self.cache.time > 600:
            self.cache.save()
        return res, False

    def query(self, query):
        """Perform a real DataProvider query."""
        res = None
        if query.type == 'artist':
            try:  # query by mbid
                if query.mbid_artist:
                    res = query.dapr.query_by_mbid(query.type,
                                                   query.mbid_artist)
            except NotImplementedError:
                pass
            if not res:
                res = query.dapr.query_artist(query.artist)
        elif query.type == 'album':
            try:  # query by mbid
                if query.mbid_relgrp:
                    res = query.dapr.query_by_mbid(query.type,
                                                   query.mbid_relgrp)
                if not res and query.mbid_album:
                    res = query.dapr.query_by_mbid(query.type,
                                                   query.mbid_album)
            except NotImplementedError:
                pass
            if not res:
                res = query.dapr.query_album(query.album, query.artist,
                                             query.year, query.releasetype)
        # preprocess tags
        for result in res or []:
            result['tags'] = preprocess_tags(result['tags'])
        return res

    def create_queries(self, metadata):
        """Create queries for all DataProviders based on metadata."""
        artists = metadata.artists
        if len(set(artists)) > 42:
            self.log.warn('Too many artists for va-artist search')
            artists = []
        albumartist = searchstr(metadata.albumartist[0])
        album = searchstr(metadata.album)
        queries = []
        # album queries
        for dapr in self.daprs:
            score = self.conf.getfloat('scores', 'src_%s' % dapr.name.lower())
            queries.append(Query(
                dapr=dapr, type='album', score=score,
                str=(albumartist + ' ' + album).strip(),
                artist=albumartist, mbid_artist=metadata.albumartist[1],
                album=album, mbid_album=metadata.mbid_album,
                mbid_relgrp=metadata.mbid_relgrp,
                year=metadata.year, releasetype=metadata.releasetype))
        # albumartist queries
        if metadata.albumartist[0]:
            for dapr in self.daprs:
                score = self.conf.getfloat('scores',
                                           'src_%s' % dapr.name.lower())
                queries.append(Query(
                    dapr=dapr, type='artist', score=score,
                    str=albumartist.strip(),
                    artist=albumartist, mbid_artist=metadata.albumartist[1],
                    album='', mbid_album='', mbid_relgrp='',
                    year='', releasetype=''))
        # all artists if no albumartist and vaqueries enabled
        elif self.conf.getboolean('wlg', 'vaqueries'):
            for key, val in set(artists):
                artist = searchstr(key)
                for dapr in self.daprs:
                    score = self.conf.getfloat('scores',
                                               'src_%s' % dapr.name.lower())
                    queries.append(Query(
                        dapr=dapr, type='artist', str=artist.strip(),
                        score=artists.count((key, val)) * score,
                        artist=artist, mbid_artist=val,
                        album='', mbid_album='', mbid_relgrp='',
                        year='', releasetype=''))
        return queries

    @classmethod
    def merge_results(cls, results):
        """Merge multiple results."""
        tags = defaultdict(float)
        for tags_ in [r['tags'] for r in results if 'tags' in r]:
            for key, val in tags_.iteritems():
                tags[key] += val
        result = {'tags': tags}
        for key in set(k for r in results for k in r.keys() if k != 'tags'):
            vals = [r[key] for r in results if key in r and r[key]]
            if len(set(vals)) == 1:
                result.update({key: vals[0]})
        return result

    def verbose_status(self, query, cached, status):
        """Log a status line in verbose mode."""
        qry = query.artist
        if query.type == 'album':
            qry += ' ' + query.album
        self.log.info("%-8s %-6s got %13s for '%s'%s", query.dapr.name,
                      query.type, status, qry.strip(),
                      " (cached)" if cached else '')

    def stat_message(self, level, message, item, log=None):
        """Record a message in the stats and optionally log it."""
        self.stats.messages[(level, message)].append(item)
        if log:
            if log > 1:
                message += ': ' + item
            self.log.log(level, message)

    def print_stats(self, num_dirs):
        """Print some statistics."""
        # genres
        if self.stats.genres:
            genres = self.stats.genres.most_common()
            print("\n%d different genres used this often:" % len(genres))
            print(tag_display(genres))
        # releasetypes
        if self.conf.args.release and self.stats.reltyps:
            reltyps = self.stats.reltyps.most_common()
            print("\n%d different releasetypes used this often:"
                  % len(reltyps))
            print(tag_display(reltyps))
        # messages
        messages = sorted(self.stats.messages.iteritems(),
                          key=lambda x: (x[0][0], len(x[1])), reverse=True)
        for (lvl, msg), items in messages:
            if self.log.level <= lvl:
                items = sorted(set(items))
                print("\n%s (%d):\n  %s"
                      % (msg, len(items), '\n  '.join(items)))
        # dataprovider
        self.log.info(dataprovider.get_stats(self.daprs))
        # time
        diff = time.time() - self.stats.time
        print("\nTime elapsed: %s (%s per directory)\n"
              % (timedelta(seconds=diff), timedelta(seconds=diff / num_dirs)))


class TagLib(object):
    """Class to handle tags."""

    def __init__(self, conf, whitelist, tags):
        self.log = logging.getLogger(__name__)
        self.conf = conf
        self.whitelist = whitelist
        self.aliases = tags['alias']
        self.regexes = tags['regex']
        self.upper = tags['upper']
        self.taggrps = {'artist': defaultdict(float),
                        'album': defaultdict(float)}

    def add(self, tags, group, split=False):
        """Add scored tags to a group of tags.

        Return the number of good (used) tags.

        :param tags: dict of tag names and tag scores
        :param group: name of the tag group (artist or album)
        :param split: was split already
        """
        good = 0
        for key, val in tags.iteritems():
            # resolve if not whitelisted
            if key not in self.whitelist:
                key = self.resolve(key)
            # split if wasn't yet
            splitgood = 0
            if not split:
                splitgood, base = self.split(key, val, group)
                if splitgood:
                    good += 1
                    val = base
            # filter unscored
            if val < .001:
                self.log.debug('tag noscore %s', key)
                continue
            self.log.debug('tag score   %s %.3f', key, val)
            # filter
            if key not in self.whitelist:
                self.log.debug('tag filter  %s', key)
                continue
            # was not good for splitting, but still good for itself
            # avoid counting as good multiple times due to splitting
            if not splitgood:
                good += 1
            # add
            self.taggrps[group][key] += val
            self.log.debug('tag add     %s', key)
        return good

    def score(self, tags, scoremod):
        """Score tags taking a scoremod into account."""
        if not tags:
            return tags
        # tags with counts
        if any(max(0, x) for x in tags.itervalues()):
            max_ = max(tags.itervalues()) / scoremod
            tags = {k: max(0, v) / max_ for k, v in tags.iteritems()}
        # tags without counts
        else:
            val = max(1 / 3, .85 ** (len(tags) - 1)) * scoremod
            tags = {k: val for k in tags.iterkeys()}
        self.log.debug('tagscoring min/avg/max (num) = %.3f/%.3f/%.3f (%d)',
                       min(tags.itervalues()),
                       sum(tags.itervalues()) / len(tags),
                       max(tags.itervalues()), len(tags))
        return tags

    def resolve(self, key):
        """Try to resolve a tag to a valid whitelisted tag by using
        aliases, regex replacements and optional difflib matching.
        """

        def alias(key):
            """Return whether a key got an alias and log it if True."""
            if key in self.aliases:
                self.log.debug('tag alias   %s -> %s', key,
                               self.aliases[key])
                return True
            return False

        # alias
        if alias(key):
            return self.aliases[key]
        # regex
        if any(r[0].search(key) for r in self.regexes):
            for pat, repl in self.regexes:
                if pat.search(key):
                    key_ = key
                    key = pat.sub(repl, key)
                    self.log.debug('tag replace %s -> %s (%s)',
                                   key_, key, pat.pattern)
            # key got replaced, try alias again
            if alias(key):
                return self.aliases[key]
            return key
        return key

    def difflib_matching(self, tags):
        """Use difflib to find some whitelist matches."""
        from difflib import get_close_matches
        for key in tags.iterkeys():
            if key not in self.whitelist and key not in self.aliases:
                match = get_close_matches(key, self.whitelist, 1, .92)
                if match:
                    self.log.debug('tag match   %s -> %s', key, match[0])
                    yield key, match[0]

    def split(self, key, val, group):
        """Split a tag into its parts and add them."""

        def dont_split(key):
            """Return whether key may be split."""
            if key in ['vanity house']:
                # some exceptions (move to tagsfile if it gets longer)
                return True
            if key in self.whitelist:
                if '&' in key or key.startswith('nu '):
                    return True
            return False

        keys = []
        good = 0
        base = val
        flag = True
        if '/' in key:  # all delimiters got replaced with / earlier
            keys = [k.strip() for k in key.split('/') if len(k.strip()) > 2]
            flag = False
        elif ' ' in key and not dont_split(key):
            keys = [k.strip() for k in key.split(' ') if len(k.strip()) > 2]
            if len(keys) > 2:
                # build all combinations with length 1 to 3, requires
                # at least 3 words, permutations would be overkill
                combis = []
                for length in range(1, min(4, len(keys))):
                    for combi in itertools.combinations(keys, length):
                        combis.append(' '.join(combi))
                keys = combis
            base = val * self.conf.getfloat('scores', 'splitup')
        elif '-' in key and key not in self.whitelist:
            keys = [k.strip() for k in key.split('-') if len(k.strip()) > 2]
        # add the parts
        if keys:
            self.log.debug('tag split   %s -> %s', key, ', '.join(keys))
            good = self.add({k: val * .5 for k in keys}, group, flag)
        return good, base

    def merge(self, various):
        """Merge all tag groups using different score modifiers."""
        mergedtags = defaultdict(float)
        for group, tags in self.taggrps.iteritems():
            if not tags:
                continue
            scoremod = 1
            if group == 'artist':
                if various:
                    group = 'various'
                scoremod = self.conf.getfloat('scores', group)
            tags = {k: min(1.5, v) for k, v in tags.iteritems()}
            max_ = max(tags.itervalues())
            for key, val in tags.iteritems():
                mergedtags[key] += val / max_ * scoremod
        if mergedtags:  # normalize tag scores
            max_ = max(mergedtags.itervalues())
            mergedtags = {k: v / max_ for k, v in mergedtags.iteritems()}
        return mergedtags

    def format(self, key):
        """Format a tag to correct case."""
        words = key.split(' ')
        for i, word in enumerate(words):
            if len(word) < 3 and word != 'nu' or \
                    word in self.upper:
                words[i] = word.upper()
            else:
                words[i] = word.title()
        return ' '.join(words)

    def get_genres(self, various):
        """Return the formatted names of the limited top genres.

        Record messages in the stats if appropriated.
        """
        # merge tag groups
        tags = self.merge(various)
        # apply user score bonus
        for key in tags.iterkeys():
            if self.conf.has_option('genres', 'love') \
                    and key in self.conf.get_list('genres', 'love'):
                tags[key] *= 2.0
            elif self.conf.has_option('genres', 'hate') \
                    and key in self.conf.get_list('genres', 'hate'):
                tags[key] *= 0.5
        # filter low scored tags
        tags = {k: v for k, v in tags.iteritems()
                if v >= self.conf.getfloat('scores', 'minimum')}
        # sort, limit and format
        tags = sorted(tags.iteritems(), key=operator.itemgetter(1), reverse=1)
        tags = tags[:self.conf.args.tag_limit]
        tags = [self.format(k) for k, _ in tags]
        self.log.info(self)
        return tags

    def __str__(self):
        strs = []
        for group, tags in self.taggrps.iteritems():
            if not tags:
                continue
            max_ = max(tags.itervalues())
            tags = {self.format(k): v / max_ for k, v in tags.iteritems()
                    if v / max_ >= .01}
            tags = sorted(tags.iteritems(), key=operator.itemgetter(1),
                          reverse=1)
            strs.append('Best %-6s genres (%d):' % (group, len(tags)))
            strs.append(tag_display(tags[:9]))
        return '\n'.join(strs)


class Config(ConfigParser.SafeConfigParser):
    """Read, maintain and write the configuration file."""

    # (section, option, value)
    conf = [('wlg', 'sources', 'discogs, lastfm, whatcd'),
            ('wlg', 'whitelist', ''),
            ('wlg', 'tagsfile', ''),
            ('wlg', 'vaqueries', 'true'),
            ('wlg', 'id3v23sep', ''),
            ('genres', 'love', ''),
            ('genres', 'hate', 'alternative, electronic, indie, pop, rock'),
            ('scores', 'artist', '1.33'),
            ('scores', 'various', '0.66'),
            ('scores', 'splitup', '0.33'),
            ('scores', 'minimum', '0.10'),
            ('scores', 'src_discogs', '1.00'),
            ('scores', 'src_lastfm', '0.66'),
            ('scores', 'src_mbrainz', '0.66'),
            ('scores', 'src_whatcd', '1.50'),
            ('discogs', 'token', ''),
            ('discogs', 'secret', ''),
            ('whatcd', 'username', ''),
            ('whatcd', 'password', ''),
            ('whatcd', 'session', ''),
            ]

    def __init__(self, args):
        ConfigParser.SafeConfigParser.__init__(self)
        self.log = logging.getLogger(__name__)
        self.args = args
        self.path = os.path.expanduser('~/.whatlastgenre')
        self.fullpath = os.path.join(self.path, 'config')
        self.log.debug('args:      %s', vars(args))
        self.log.debug('conf.path: %s', self.fullpath)
        # make sure directory exists
        if not os.path.exists(self.path):
            os.makedirs(self.path)
        # create default config if necessary
        if not os.path.exists(self.fullpath):
            self.set_defaults()
            self.save()
            print('Please review your config file: %s' % self.fullpath)
            exit()
        self.read(self.fullpath)
        self.__compat()
        # validation
        if args.release and 'whatcd' not in self.get_list('wlg', 'sources'):
            self.log.warning('Can\'t tag release with What.CD support '
                             'disabled. Release tagging disabled.')
            self.args.release = False

    def __compat(self):
        """Backward compatibility code."""
        discogs_token = os.path.expanduser('~/.whatlastgenre/discogs.json')
        if os.path.exists(discogs_token):
            import json
            with open(discogs_token) as file_:
                data = json.load(file_)
            if not self.has_section('discogs'):
                self.add_section('discogs')
            self.set('discogs', 'token', data['token'])
            self.set('discogs', 'secret', data['secret'])
            os.remove(discogs_token)
            self.save()

    def set_defaults(self):
        """Create a default configuration file."""
        for sec, opt, val in self.conf:
            if not self.has_section(sec):
                self.add_section(sec)
            self.set(sec, opt, str(val))

    def save(self):
        """Write the config file but backup the existing one."""
        backup_path = self.fullpath + '~'
        if os.path.exists(self.fullpath):
            if os.name == 'nt' and os.path.isfile(backup_path):
                os.remove(backup_path)
            os.rename(self.fullpath, backup_path)
        with open(self.fullpath, 'w') as file_:
            self.write(file_)

    def get_list(self, sec, opt):
        """Gets a csv-string as list."""
        list_ = self.get(sec, opt).lower().split(',')
        return [x.strip() for x in list_ if x.strip()]


def preprocess_tags(tags):
    """Preprocess tags slightly to reduce the amount and don't
    pollute the cache with tags that obviously don't get used
    anyway.
    """
    if not tags:
        return tags
    tags = {k.strip().lower(): v for k, v in tags.iteritems()}
    tags = {k: v for k, v in tags.iteritems()
            if len(k) in range(2, 64) and v >= 0}
    # answer to the ultimate question of life, the universe,
    # the optimal number of considerable tags and everything
    limit = 42
    if len(tags) > limit:
        # tags with scores
        if any(tags.itervalues()):
            min_val = max(tags.itervalues()) / 3
            tags = {k: v for k, v in tags.iteritems() if v >= min_val}
            tags = sorted(tags.iteritems(), key=operator.itemgetter(1),
                          reverse=1)  # best tags
        # tags without scores
        else:
            tags = sorted(tags.iteritems(), key=len)  # shortest tags
        tags = {k: v for k, v in tags[:limit]}
    return tags


def searchstr(str_):
    """Clean up a string for use in searching."""
    if not str_:
        return ''
    str_ = str_.lower()
    for pat in [r'\(.*\)$', r'\[.*\]', '{.*}', "- .* -", "'.*'", '".*"',
                ' (- )?(album|single|ep|official remix(es)?|soundtrack|ost)$',
                r'[ \(]f(ea)?t(\.|uring)? .*', r'vol(\.|ume)? ',
                '[!?/:;,]', ' +']:
        sub = re.sub(pat, ' ', str_).strip()
        if sub:  # don't remove everything
            str_ = sub
    return str_


def tag_display(tags):
    """Return a string of tags formatted in columns."""
    columns = 3
    num_lines = -(-len(tags) // columns)  # math.ceil()
    # pattern should not exceed (80-2)/3=26 chars length
    if all(float(t[1]).is_integer() for t in tags):
        pattern = '%4d %-20s'
    else:
        pattern = '%4.2f %-20s'
    lines = []
    for line in range(num_lines):
        values = []
        for column in range(columns):
            index = line + columns * column
            if index < len(tags):
                values.append(pattern % tuple(reversed(tags[index])))
        lines.append(' '.join(values))
    return '\n'.join(lines)


def ask_user(dapr_name, query_type, results):
    """Ask the user to choose from a list of results."""
    print("%-8s %-6s got    %2d results. Which is it?"
          % (dapr_name, query_type, len(results)))
    for i, result in enumerate(results, start=1):
        info = result['info'].encode(sys.stdout.encoding, errors='replace')
        print("#%2d: %s" % (i, info))
    while True:
        try:
            num = int(raw_input("Please choose #[1-%d] (0 to skip): "
                                % len(results)))
        except ValueError:
            num = None
        except EOFError:
            num = 0
            print()
        if num in range(len(results) + 1):
            break
    return [results[num - 1]] if num else results


def progressbar(current, total):
    """Return a progressbar string."""
    size = 60
    prog = current / total
    done = int(size * prog)
    return '(%2d/%d) [' % (current, total) \
           + '#' * done \
           + '-' * (size - done) \
           + '] %2.0f%%' % math.floor(100 * prog)


def read_datafile(path):
    """Read a file that might be package data."""
    if path.startswith('data/'):
        lines = pkgutil.get_data('wlg', path).split('\n')
    else:
        with open(path, b'r') as file_:
            lines = file_.read().splitlines()
    return [l.strip().lower() for l in lines if l.strip()]


def get_args():
    """Get the cmdline arguments from ArgumentParser."""
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description='Improve genre metadata of audio files '
                    'based on tags from various music sites.')
    parser.add_argument('path', nargs='+',
                        help='path(s) to scan for albums')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='verbose output (-vv for debug)')
    parser.add_argument('-n', '--dry', action='store_true',
                        help='don\'t save metadata')
    parser.add_argument('-u', '--update-cache', action='store_true',
                        help='force cache update')
    parser.add_argument('-l', '--tag-limit', metavar='N', type=int, default=4,
                        help='max. number of genre tags')
    parser.add_argument('-r', '--release', action='store_true',
                        help='get release info from whatcd')
    parser.add_argument('-d', '--difflib', action='store_true',
                        help='enable difflib matching (slow)')
    return parser.parse_args()


def main():
    """main function of whatlastgenre.

    Get arguments, set up WhatLastGenre object,
    search for music directories, run the main loop on them
    and print out some statistics.
    """
    print("whatlastgenre v%s" % __version__)
    args = get_args()
    conf = Config(args)
    wlg = WhatLastGenre(conf)
    paths = mediafile.find_music_dirs(args.path)
    print("\nFound %d music directories!" % len(paths))
    if not paths:
        return
    i = 1
    try:
        for i, path in enumerate(sorted(paths), start=1):
            print('\n' + progressbar(i, len(paths)))
            print(path)
            wlg.progress_path(path)
        print('\n...all done!')
    except KeyboardInterrupt:
        print()
    wlg.print_stats(i)
