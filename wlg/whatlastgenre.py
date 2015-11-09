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

'''whatlastgenre

https://github.com/YetAnotherNerd/whatlastgenre
'''

from __future__ import division, print_function

import ConfigParser
import StringIO
import argparse
from collections import defaultdict, Counter, namedtuple
from datetime import timedelta
import difflib
import itertools
import logging
import math
import operator
import os
import pkgutil
import re
import sys
import time

from wlg import __version__, dataprovider, mediafile


Query = namedtuple(
    'Query', ['dapr', 'type', 'str', 'score', 'artist', 'mbid_artist',
              'album', 'mbid_album', 'mbid_relgrp', 'year', 'releasetype'])

Stats = namedtuple('Stats', ['time', 'messages', 'genres', 'reltyps'])


class WhatLastGenre(object):
    '''Main class featuring a docstring that needs to be written.'''

    def __init__(self, args, whitelist=None):
        self.log = logging.getLogger('wlg')
        self.log.setLevel(30 - 10 * args.verbose)
        self.log.addHandler(logging.StreamHandler(sys.stdout))
        self.conf = Config(args)
        self.stats = Stats(time=time.time(),
                           messages=defaultdict(list),
                           genres=Counter(),
                           reltyps=Counter())
        self.daprs = dataprovider.DataProvider.init_dataproviders(self.conf)
        self.whitelist = self.read_whitelist(whitelist)
        self.read_tagsfile()
        # validation
        if args.release \
                and 'whatcd' not in self.conf.get_list('wlg', 'sources'):
            self.log.warning('Can\'t tag release with What.CD support '
                             'disabled. Release tagging disabled.')
            self.conf.args.release = False
        # validate aliases
        for key, val in self.tags['alias'].items():
            if val not in self.whitelist:
                del self.tags['alias'][key]
                self.stat_message(logging.WARN, 'alias not whitelisted',
                                  '%s -> %s' % (key, val), 2)

    def read_whitelist(self, path=None):
        '''Read the whitelist trying different paths.

        Return a set of whitelist entries.
        '''
        paths = [(1, path)]
        if self.conf.has_option('wlg', 'whitelist'):
            paths.append((1, self.conf.get('wlg', 'whitelist')))
        for fail, path in paths:
            if path and (os.path.exists(path) or fail):
                with open(path, b'r') as file_:
                    lines = file_.read().splitlines()
                    break
        else:
            path = 'shipped data/genres.txt'
            lines = pkgutil.get_data('wlg', 'data/genres.txt').split('\n')
        whitelist = set(l.strip().lower() for l in lines
                        if l and not l.startswith('#'))
        if not whitelist:
            self.log.critical('empty whitelist: %s', path)
            exit()
        self.log.debug('whitelist: %s (%d items)', path, len(whitelist))
        return whitelist

    def read_tagsfile(self):
        '''Read tagsfile and return a dict of prepared data.'''
        parser = ConfigParser.SafeConfigParser(allow_no_value=True)
        tfstr = pkgutil.get_data('wlg', 'data/tags.txt')
        parser.readfp(StringIO.StringIO(tfstr))
        for sec in ['upper', 'alias', 'regex']:
            if not parser.has_section(sec):
                self.log.critical('Got no [%s] from tags.txt file.', sec)
                exit()
        # regex replacements
        regex = []  # list of tuples instead of dict because order matters
        for pat, repl in [(r'( *[,;.:\\/&_]+ *| and )+', '/'),
                          (r'[\'"]+', ''), (r'  +', ' ')]:
            regex.append((re.compile(pat, re.I), repl))
        for pat, repl in parser.items('regex', True):
            regex.append((re.compile(r'\b%s\b' % pat, re.I), repl))
        self.tags = {
            'upper': dict(parser.items('upper', True)).keys(),
            'alias': dict(parser.items('alias', True)),
            'regex': regex}

    def query_album(self, metadata):
        '''Query for top genres of an album identified by metadata
        and return them and some releaseinfo.'''
        num_artists = 1
        if not metadata.albumartist[0]:
            num_artists = len(set(metadata.artists))
        self.log.info("[%s] artist=%s, album=%s, date=%s%s",
                      metadata.type, metadata.albumartist[0], metadata.album,
                      metadata.year, (" (%d artists)" % num_artists
                                      if num_artists > 1 else ''))
        taglib = TagLib(self, metadata.path, num_artists > 1)
        for query in self.create_queries(metadata):
            if not query.str:
                continue
            try:
                results, cached = query.dapr.cached_query(query)
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
                results = ask_user(query, results)
                if len(results) == 1:
                    query.dapr.cache.set(query.dapr.cache.cachekey(query),
                                         results)
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
            if query.dapr.name.lower() == 'whatcd' and query.type == 'album':
                taglib.release = {k: v for k, v in results[0].iteritems()
                                  if k not in ['info', 'tags']}
            if 'tags' in results[0] and results[0]['tags']:
                tags = taglib.score(results[0]['tags'], query.score)
                good = taglib.add(tags, query.type)
                query.dapr.stats['tags'] += len(tags)
                query.dapr.stats['goodtags'] += good
                status = "%2d of %2d tags" % (good, len(tags))
            else:
                status = "no    tags"
            self.verbose_status(query, cached, status)
        if taglib.release and 'releasetype' in taglib.release \
                and taglib.release['releasetype']:
            self.stats.reltyps[taglib.release['releasetype']] += 1
        elif self.conf.args.release:
            self.stat_message(logging.ERROR, 'No releaseinfo found',
                              metadata.path, 1)
        return taglib.get_genres(), taglib.release

    def create_queries(self, metadata):
        '''Create queries for all DataProviders based on metadata.'''
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
        '''Merge multiple results.'''
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
        '''Log a status line in verbose mode.'''
        qry = query.artist
        if query.type == 'album':
            qry += ' ' + query.album
        self.log.info("%-8s %-6s got %13s for '%s'%s", query.dapr.name,
                      query.type, status, qry.strip(),
                      " (cached)" if cached else '')

    def stat_message(self, level, message, item, log=None):
        '''Record a message in the stats and optionally log it.'''
        self.stats.messages[(level, message)].append(item)
        if log:
            if log > 1:
                message += ': ' + item
            self.log.log(level, message)

    def print_stats(self, num_dirs):
        '''Print some statistics.'''
        # genres
        if self.stats.genres:
            genres = self.stats.genres.most_common()
            print("\n%d different genres used this often:" % len(genres))
            print(tag_display(genres, "%4d %-20s"))
        # releasetypes
        if self.conf.args.release and self.stats.reltyps:
            reltyps = self.stats.reltyps.most_common()
            print("\n%d different releasetypes used this often:"
                  % len(reltyps))
            print(tag_display(reltyps, "%4d %-20s"))
        # messages
        messages = sorted(self.stats.messages.iteritems(),
                          key=lambda x: (x[0][0], len(x[1])), reverse=True)
        for (lvl, msg), items in messages:
            if self.log.level <= lvl:
                items = sorted(set(items))
                print("\n%s (%d):\n  %s"
                      % (msg, len(items), '\n  '.join(items)))
        # dataprovider
        if self.log.level <= logging.INFO:
            dataprovider.DataProvider.print_stats(self.daprs)
        # time
        diff = time.time() - self.stats.time
        print("\nTime elapsed: %s (%s per directory)\n"
              % (timedelta(seconds=diff), timedelta(seconds=diff / num_dirs)))


class TagLib(object):
    '''Class to handle tags.'''

    def __init__(self, wlg, path, various):
        self.wlg = wlg
        self.path = path
        self.various = various
        self.log = logging.getLogger(__name__)
        self.taggrps = {'artist': defaultdict(float),
                        'album': defaultdict(float)}
        self.release = None

    def add(self, tags, group, split=False):
        '''Add scored tags to a group of tags.

        Return the number of good (used) tags.

        :param tags: dict of tag names and tag scores
        :param group: name of the tag group (artist or album)
        :param split: was split already
        '''
        good = 0
        for key, val in tags.iteritems():
            # resolve if not whitelisted
            if key not in self.wlg.whitelist:
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
            if key not in self.wlg.whitelist:
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
        '''Score tags taking a scoremod into account.'''
        if not tags:
            return tags
        # tags with counts
        if any(tags.itervalues()):
            max_ = max(tags.itervalues()) * scoremod
            tags = {k: v / max_ for k, v in tags.iteritems()}
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
        '''Try to resolve a tag to a valid whitelisted tag by using
        aliases, regex replacements and optional difflib matching.
        '''

        def alias(key):
            '''Return whether a key got an alias and log it if True.'''
            if key in self.wlg.tags['alias']:
                self.log.debug('tag alias   %s -> %s', key,
                               self.wlg.tags['alias'][key])
                return True
            return False

        # alias
        if alias(key):
            return self.wlg.tags['alias'][key]
        # regex
        if any(r[0].search(key) for r in self.wlg.tags['regex']):
            for pat, repl in self.wlg.tags['regex']:
                if pat.search(key):
                    key_ = key
                    key = pat.sub(repl, key)
                    self.log.debug('tag replace %s -> %s (%s)',
                                   key_, key, pat.pattern)
            # key got replaced, try alias again
            if alias(key):
                return self.wlg.tags['alias'][key]
            return key
        # match
        if self.wlg.conf.args.difflib:
            match = difflib.get_close_matches(key, self.wlg.whitelist, 1, .92)
            if match:
                self.wlg.stat_message(
                    logging.WARN, 'possible aliases found by difflib',
                    '%s = %s' % (key, match[0]))
                self.log.debug('tag match   %s -> %s', key, match[0])
                return match[0]
        return key

    def split(self, key, val, group):
        '''Split a tag into its parts and add them.'''
        keys = []
        good = 0
        base = val
        flag = True
        # some exceptions (move to tagsfile if it gets longer)
        dontsplit = ['vanity house']
        if '/' in key:  # all delimiters got replaced with / earlier
            keys = [k.strip() for k in key.split('/') if len(k.strip()) > 2]
            flag = False
        elif ' ' in key and key not in dontsplit \
                and not (key in self.wlg.whitelist
                         and ('&' in key or key.startswith('nu '))):
            keys = [k.strip() for k in key.split(' ') if len(k.strip()) > 2]
            if len(keys) > 2:
                # build all combinations with length 1 to 3, requires
                # at least 3 words, permutations would be overkill
                combis = []
                for length in range(1, min(4, len(keys))):
                    for combi in itertools.combinations(keys, length):
                        combis.append(' '.join(combi))
                keys = combis
            base = val * self.wlg.conf.getfloat('scores', 'splitup')
        elif '-' in key and key not in self.wlg.whitelist:
            keys = [k.strip() for k in key.split('-') if len(k.strip()) > 2]
        # add the parts
        if keys:
            self.log.debug('tag split   %s -> %s', key, ', '.join(keys))
            good = self.add({k: val * .5 for k in keys}, group, flag)
        return good, base

    def merge(self):
        '''Merge all tag groups using different score modifiers.'''
        mergedtags = defaultdict(float)
        for group, tags in self.taggrps.iteritems():
            if not tags:
                continue
            scoremod = 1
            if group == 'artist':
                if self.various:
                    group = 'various'
                scoremod = self.wlg.conf.getfloat('scores', group)
            tags = {k: min(1.5, v) for k, v in tags.iteritems()}
            max_ = max(tags.itervalues())
            for key, val in tags.iteritems():
                mergedtags[key] += val / max_ * scoremod
        if mergedtags:  # normalize tag scores
            max_ = max(mergedtags.itervalues())
            mergedtags = {k: v / max_ for k, v in mergedtags.iteritems()}
        return mergedtags

    def format(self, key):
        '''Format a tag to correct case.'''
        words = key.split(' ')
        for i, word in enumerate(words):
            if len(word) < 3 and word != 'nu' or \
                    word in self.wlg.tags['upper']:
                words[i] = word.upper()
            else:
                words[i] = word.title()
        return ' '.join(words)

    def get_genres(self):
        '''Return the formatted names of the limited top genres.

        Record messages in the stats if appropriated.
        '''
        for group in ['artist', 'album']:
            if not self.taggrps[group]:
                self.wlg.stat_message(
                    logging.INFO, 'No %s tags' % group, self.path, 1)
        # merge tag groups
        tags = self.merge()
        if not tags:
            self.wlg.stat_message(
                logging.ERROR, 'No genres found', self.path, 1)
            return None
        # apply user score bonus
        for key in tags.iterkeys():
            if key in self.wlg.conf.get_list('genres', 'love'):
                tags[key] *= 2.0
            elif key in self.wlg.conf.get_list('genres', 'hate'):
                tags[key] *= 0.5
        # filter low scored tags
        tags = {k: v for k, v in tags.iteritems()
                if v >= self.wlg.conf.getfloat('scores', 'minimum')}
        # sort, limit and format
        tags = sorted(tags.iteritems(), key=operator.itemgetter(1), reverse=1)
        tags = tags[:self.wlg.conf.args.tag_limit]
        tags = [self.format(k) for k, _ in tags]
        self.log.info(self)
        self.wlg.stats.genres.update(tags)
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
            strs.append(u'Best %-6s genres (%d):' % (group, len(tags)))
            strs.append(tag_display(tags[:9], u'%4.2f %-20s'))
        return u'\n'.join(strs)


class Config(ConfigParser.SafeConfigParser):
    '''Read, maintain and write the configuration file.'''

    # (section, option, value)
    conf = [('wlg', 'sources', 'discogs, echonest, lastfm, mbrainz, whatcd'),
            ('wlg', 'whitelist', ''),
            ('wlg', 'vaqueries', 'true'),
            ('wlg', 'id3v23sep', ''),
            ('genres', 'love', ''),
            ('genres', 'hate', 'alternative, electronic, indie, pop, rock'),
            ('scores', 'artist', '1.33'),
            ('scores', 'various', '0.66'),
            ('scores', 'splitup', '0.33'),
            ('scores', 'minimum', '0.10'),
            ('scores', 'src_discogs', '1.00'),
            ('scores', 'src_echonest', '1.00'),
            ('scores', 'src_lastfm', '0.66'),
            ('scores', 'src_mbrainz', '0.66'),
            ('scores', 'src_rymusic', '1.33'),
            ('scores', 'src_whatcd', '1.50'),
            ('discogs', 'token', ''),
            ('discogs', 'secret', ''),
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
        self.read(self.fullpath)
        self.__compat()

    def __compat(self):
        '''Backward compatibility code.'''
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
        '''Create a default configuration file.'''
        for sec, opt, val in self.conf:
            if not self.has_section(sec):
                self.add_section(sec)
            self.set(sec, opt, str(val))

    def save(self):
        '''Write the config file but backup the existing one.'''
        if os.path.exists(self.fullpath):
            os.rename(self.fullpath, self.fullpath + '~')
        with open(self.fullpath, 'w') as file_:
            self.write(file_)
        print('Please review your config file: %s' % self.fullpath)
        exit()

    def get_list(self, sec, opt):
        '''Gets a csv-string as list.'''
        list_ = self.get(sec, opt).lower().split(',')
        return [x.strip() for x in list_ if x.strip()]


def searchstr(str_):
    '''Clean up a string for use in searching.'''
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


def tag_display(tags, pattern):
    '''Return a string of tags formatted with pattern in 3 columns.

    :param tags: list of tuples containing tags name and count/score
    :param pattern: should not exceed (80-2)/3 = 26 chars length.
    '''
    len_ = int(math.ceil(len(tags) / 3))
    lines = [u' '.join([pattern % tuple(reversed(tags[i]))
                        for i in [l + len_ * j for j in range(3)]
                        if i < len(tags)]) for l in range(len_)]
    return u'\n'.join(lines).encode('utf-8')


def ask_user(query, results):
    '''Ask the user to choose from a list of results.'''
    print("%-8s %-6s got    %2d results. Which is it?"
          % (query.dapr.name, query.type, len(results)))
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


def work_directory(wlg, path):
    '''Create an Album object for a directory given by path to read and
    write metadata from/to.  Query top genre tags by album metadata,
    update metadata with results and save the album (its tracks).
    '''
    # create album object to read and write metadata
    try:
        album = mediafile.Album(path, wlg.conf.get('wlg', 'id3v23sep'))
    except mediafile.AlbumError as err:
        wlg.stat_message(logging.ERROR, str(err), path, 1)
        return
    # read album metadata
    metadata = album.get_metadata()
    # query genres (and releasetype) for album metadata
    genres, release = wlg.query_album(metadata)
    # update album metadata
    if genres:
        album.set_meta('genre', genres)
        print("Genres:  %s" % ', '.join(genres).encode('utf-8'))
    if release and wlg.conf.args.release:
        out = []
        for key in ['releasetype', 'date',
                    'label', 'catalog', 'edition', 'media']:
            if key in release and release[key]:
                album.set_meta(key, release[key])
                out.append(release[key])
        print("Release: %s" % ' / '.join(out))
    # save metadata to all tracks
    if wlg.conf.args.dry:
        print("DRY-RUN! Not saving metadata.")
    else:
        album.save()


def progressbar(current, total):
    '''Return a progressbar string.'''
    size = 60
    prog = current / total
    done = int(size * prog)
    return u'(%2d/%d) [' % (current, total) \
        + u'#' * done \
        + u'-' * (size - done) \
        + u'] %2.0f%%' % math.floor(100 * prog)


def get_args():
    '''Get the cmdline arguments from ArgumentParser.'''
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
    '''main function of whatlastgenre.

    Get arguments, set up WhatLastGenre object,
    search for music directories, run the main loop on them
    and print out some statistics.
    '''
    print("whatlastgenre v%s" % __version__)
    args = get_args()
    wlg = WhatLastGenre(args)
    paths = mediafile.find_music_dirs(args.path)
    print("\nFound %d music directories!" % len(paths))
    if not paths:
        return
    i = 1
    try:
        for i, path in enumerate(sorted(paths), start=1):
            print('\n' + progressbar(i, len(paths)))
            print(path)
            work_directory(wlg, path)
        print('\n...all done!')
    except KeyboardInterrupt:
        print()
    wlg.print_stats(i)
