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

'''whatlastgenre'''

from __future__ import division, print_function

import ConfigParser
import StringIO
import argparse
from collections import defaultdict, Counter, namedtuple
from datetime import timedelta
import difflib
import itertools
import json
import logging
import math
import operator
import os
import pkgutil
import re
import sys
from tempfile import NamedTemporaryFile
import time

from wlg import __version__, dataprovider, mediafile


Metadata = namedtuple(
    'Metadata', ['path', 'type', 'artists', 'albumartist', 'album',
                 'mbid_album', 'mbid_relgrp', 'year', 'releasetype'])

Query = namedtuple(
    'Query', ['dapr', 'type', 'str', 'score', 'artist', 'mbid_artist',
              'album', 'mbid_album', 'mbid_relgrp', 'year', 'releasetype'])

Stats = namedtuple('Stats', ['time', 'errors', 'genres', 'difflib'])

# regex pattern for 'Various Artist'
VA_PAT = re.compile('^va(rious( ?artists?)?)?$', re.I)

# musicbrainz artist id of 'Various Artists'
VA_MBID = '89ad4ac3-39f7-470e-963a-56509c546377'


class WhatLastGenre(object):
    '''Main class featuring a docstring that needs to be written.'''

    def __init__(self, args):
        wlgdir = os.path.expanduser('~/.whatlastgenre')
        if not os.path.exists(wlgdir):
            os.makedirs(wlgdir)
        self.args = args
        self.setup_logging(args.verbose)
        self.log.debug("args: %s\n", args)
        self.stats = Stats(time=time.time(), errors=defaultdict(list),
                           genres=Counter(), difflib={})
        self.conf = Config(wlgdir, args)
        self.cache = Cache(wlgdir, args.update_cache)
        self.daprs = dataprovider.get_daprs(self.conf)
        self.read_whitelist(self.conf.get('wlg', 'whitelist'))
        self.read_tagsfile()

    def setup_logging(self, verbose):
        '''Setup up the logging.'''
        self.log = logging.getLogger('whatlastgenre')
        if verbose == 0:
            loglvl = logging.WARN
        elif verbose == 1:
            loglvl = logging.INFO
        else:
            loglvl = logging.DEBUG
        self.log.setLevel(loglvl)
        hdlr = logging.StreamHandler(sys.stdout)
        hdlr.setLevel(loglvl)
        self.log.addHandler(hdlr)

        # add null handler
        class NullHandler(logging.Handler):
            '''Do nothing handler.'''
            def emit(self, record):
                pass
        self.log.addHandler(NullHandler())

    def read_whitelist(self, path=None):
        '''Read whitelist file and store its contents as set.'''
        if not path or path == 'wlg':
            wlstr = pkgutil.get_data('wlg', 'data/genres.txt').decode('utf8')
        else:
            with open(path, b'r') as file_:
                wlstr = u'\n'.join([l.decode('utf8') for l in file_])
        self.whitelist = set()
        for line in wlstr.split(u'\n'):
            line = line.strip().lower()
            if line and not line.startswith(u'#'):
                self.whitelist.add(line)
        if not self.whitelist:
            self.log.error("error: empty whitelist: %s", path)
            exit()

    def read_tagsfile(self):
        '''Read tagsfile and return a dict of prepared data.'''
        tagsfile = ConfigParser.SafeConfigParser(allow_no_value=True)
        tfstr = pkgutil.get_data('wlg', 'data/tags.txt')
        tagsfile.readfp(StringIO.StringIO(tfstr))
        for sec in ['upper', 'alias', 'regex']:
            if not tagsfile.has_section(sec):
                print("Got no [%s] from tags.txt file." % sec)
                exit()
        # regex replacements
        regex = []  # list of tuples instead of dict because order matters
        for pat, repl in [(r'( ?[,;\\/&_]+ ?| and )+', '/'),
                          (r'[\'"]+', ''), (r'  +', ' ')]:
            regex.append((re.compile(pat, re.I), repl))
        for pat, repl in tagsfile.items('regex', True):
            regex.append((re.compile(r'\b%s\b' % pat, re.I), repl))
        self.tagsfile = {
            'upper': tagsfile.items('upper', True),
            'aliases': dict(tagsfile.items('alias', True)),
            'love': self.conf.get_list('genres', 'love'),
            'hate': self.conf.get_list('genres', 'hate'),
            'regex': regex}

    def query_album(self, metadata):
        '''Query for top genres of an album identified by metadata
        and return them and the releasetype.'''
        num_artists = len(set(metadata.artists))
        self.log.info("[%s] artist=%s, album=%s, date=%s%s",
                      metadata.type, metadata.albumartist[0], metadata.album,
                      metadata.year, (" (%d artists)" % num_artists
                                      if num_artists > 1 else ''))
        taglib = TagLib(self, num_artists > 1)
        for query in self.create_queries(metadata):
            if not query.str:
                continue
            try:
                results, cached = self.cached_query(query)
            except NotImplementedError:
                continue
            except dataprovider.DataProviderError as err:
                query.dapr.stats['errors'] += 1
                err = query.dapr.name + ' ' + str(err)
                self.stats.errors[err].append(metadata.path)
                print("%-8s %s" % (query.dapr.name, err))
                continue
            if not results:
                self.verbose_status(query, cached, "no results")
                continue
            # ask user if appropriated
            if len(results) > 1 \
                    and self.args.tag_release and self.args.interactive \
                    and query.dapr.name.lower() == 'whatcd' \
                    and query.type == 'album' \
                    and not query.releasetype:
                reltyps = [r['releasetype'] for r in results
                           if 'releasetype' in r]
                if len(set(reltyps)) != 1:  # all the same anyway
                    results = ask_user(query, results)
            # merge_results all tags from multiple results
            if len(results) in range(2, 6):
                results = self.merge_results(results)
            # too many results
            if len(results) > 1:
                self.verbose_status(query, cached,
                                    "%2d results" % len(results))
                continue
            # unique result
            res = results[0]
            query.dapr.stats['results'] += 1
            if 'releasetype' in res and res['releasetype']:
                taglib.releasetype = res['releasetype']
            if 'tags' in res and res['tags']:
                found, added = taglib.add(query, res['tags'])
                query.dapr.stats['tags'] += found
                query.dapr.stats['goodtags'] += added
                status = "%2d of %2d tags" % (added, found)
            else:
                status = "no    tags"
            self.verbose_status(query, cached, status)

        genres = taglib.top_genres(self.args.tag_limit)
        self.stats.genres.update(genres)
        return genres, taglib.releasetype

    def create_queries(self, metadata):
        '''Create queries for all DataProviders based on metadata.'''
        artists = metadata.artists
        num_artists = len(set(artists))
        if num_artists > 42:
            print("Too many artists for va-artist search")
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

    def cached_query(self, query):
        '''Query Cache before querying real DataProviders.'''
        cachekey = query.artist
        if query.type == 'album':
            cachekey += query.album
        cachekey = (query.dapr.name.lower(), query.type,
                    cachekey.replace(' ', ''))
        results = self.cache.get(cachekey)
        if results:
            query.dapr.stats['queries_cache'] += 1
            return results[1], True
        results = query.dapr.query(query)
        query.dapr.stats['queries_web'] += 1
        self.cache.set(cachekey, results)
        # save cache periodically
        if time.time() - self.cache.time > 600:
            self.cache.save()
        return results, False

    @classmethod
    def merge_results(cls, results):
        '''Merge the tags of multiple results.'''
        tags = defaultdict(float)
        for res in results:
            if 'tags' in res and res['tags']:
                for key, val in res['tags'].items():
                    tags[key] += val / len(results)
        result = {'tags': tags}
        reltyps = [r['releasetype'] for r in results if 'releasetype' in r]
        if len(set(reltyps)) == 1:
            result.update({'releasetype': reltyps[0]})
        return [result]

    def verbose_status(self, query, cached, status):
        '''Return a string for status printing.'''
        qry = query.artist
        if query.type == 'album':
            qry += ' ' + query.album
        self.log.info("%-8s %-6s got %13s for '%s'%s", query.dapr.name,
                      query.type, status, qry.strip(),
                      " (cached)" if cached else '')

    def print_stats(self, num_folders):
        '''Print some statistics.'''
        # genre tag statistics
        if self.stats.genres:
            tags = self.stats.genres.most_common()
            print("\n%d different tags used this often:" % len(tags))
            print(tag_display(tags, "%4d %-20s"))
        # errors/messages
        if self.stats.errors:
            print("\n%d album(s) with errors:"
                  % sum(len(x) for x in self.stats.errors.values()))
            for error, folders in sorted(self.stats.errors.items(),
                                         key=lambda x: len(x[1]), reverse=1):
                print("  %s:\n    %s"
                      % (error, '\n    '.join(sorted(folders))))
        # dataprovider stats
        if self.log.level <= logging.INFO:
            self.print_dapr_stats()
        # difflib stats
        if self.stats.difflib:
            print("\ndifflib found %d tags:" % len(self.stats.difflib))
            for key, val in self.stats.difflib:
                print("%s = %s" % (key, val))
            print("Add them as aliases to tags.txt to speed things up.")
        # time
        diff = time.time() - self.stats.time
        print("\nTime elapsed: %s (%s per folder)\n"
              % (timedelta(seconds=diff),
                 timedelta(seconds=diff / num_folders)))

    def print_dapr_stats(self):
        '''Print some DataProvider statistics.'''
        print("\nSource stats  ",
              ''.join("| %-8s " % d.name for d in self.daprs),
              "\n", "-" * 14, "+----------" * len(self.daprs), sep='')
        for key in ['errors', 'queries_web', 'queries_cache', 'results',
                    'results/query', 'tags', 'tags/result', 'goodtags',
                    'goodtags/tag', 'time_resp_avg', 'time_wait_avg']:
            vals = []
            for dapr in self.daprs:
                if key == 'results/query' and (dapr.stats['queries_web']
                                               or dapr.stats['queries_cache']):
                    vals.append(dapr.stats['results']
                                / (dapr.stats['queries_web']
                                   + dapr.stats['queries_cache']))
                elif key == 'time_resp_avg' and dapr.stats['queries_web']:
                    vals.append(dapr.stats['time_resp']
                                / dapr.stats['queries_web'])
                elif key == 'time_wait_avg' and dapr.stats['queries_web']:
                    vals.append(dapr.stats['time_wait']
                                / dapr.stats['queries_web'])
                elif key == 'tags/result' and dapr.stats['results']:
                    vals.append(dapr.stats['tags'] / dapr.stats['results'])
                elif key == 'goodtags/tag' and dapr.stats['tags']:
                    vals.append(dapr.stats['goodtags'] / dapr.stats['tags'])
                elif key in dapr.stats:
                    vals.append(dapr.stats[key])
                else:
                    vals.append(0.0)
            if any(v for v in vals):
                pat = "| %8d " if all(v.is_integer() for v in vals) \
                    else "| %8.2f "
                print("%-13s " % key, ''.join(pat % v for v in vals), sep='')


class TagLib(object):
    '''Class to keep tag information (tags with name and score from
    different types).
    '''

    def __init__(self, wlg, various):
        self.wlg = wlg
        self.log = wlg.log
        self.various = various
        self.taggrps = {}
        self.releasetype = None

    def add(self, query, tags, split=False):
        '''Add scored tags to a group of tags.

        Return the number of tags processed and added.

        :param query: query object
        :param tags: dict of tag names and tag counts
        :param split: was split already
        '''
        tags = self.score(tags, query.score)
        tags = sorted(tags.items(), key=operator.itemgetter(1), reverse=1)
        tags = tags[:99]
        added = 0
        for key, val in tags:
            key = key.lower().strip()
            if len(key) < 3:
                continue
            # resolve if not whitelisted
            if key not in self.wlg.whitelist:
                key = self.resolve(key)
            # split if wasn't yet
            if not split and not ('&' in key and key in self.wlg.whitelist):
                parts = self.split(key)
                if parts and len(parts) < 6 and all(len(p) > 3 for p in parts):
                    _, add = self.add(query, {p: val for p in parts}, True)
                    if add:
                        added += add
                        val *= self.wlg.conf.getfloat('scores', 'splitup')
                        self.log.debug("tag split   %s -> %s",
                                       key, ', '.join(parts))
            # filter
            if key not in self.wlg.whitelist:
                self.log.debug("tag filter  %s", key)
                continue
            # add
            if query.type not in self.taggrps:
                self.taggrps[query.type] = defaultdict(float)
            self.taggrps[query.type][key] += val
            added += 1
            self.log.debug("tag add     %s", key)
        return len(tags), added

    def score(self, tags, scoremod):
        '''Adjusts the score of a dict of tags using a scoremod.'''
        any_ = any(tags.values())
        max_ = max(tags.values())
        for key, val in tags.items():
            if any_:  # tags with counts
                tags[key] = val * max_ ** -1 * scoremod
            else:  # tags without counts
                tags[key] = max(0.1, .85 ** (len(tags) - 1) * scoremod)
            # apply user bonus
            if key in self.wlg.tagsfile['love']:
                tags[key] *= 2.0
            elif key in self.wlg.tagsfile['hate']:
                tags[key] *= 0.5
        return tags

    def resolve(self, key):
        '''Try to resolve a tag to a valid whitelisted tag by using
        aliases, regex replacements and optional difflib matching.
        '''
        if key in self.wlg.tagsfile['aliases']:
            if self.wlg.tagsfile['aliases'][key] not in self.wlg.whitelist:
                self.log.info("warning: aliased %s is not whitelisted.", key)
                return key
            self.log.debug("tag alias   %s -> %s", key,
                           self.wlg.tagsfile['aliases'][key])
            return self.wlg.tagsfile['aliases'][key]
        # regex
        if any(r[0].search(key) for r in self.wlg.tagsfile['regex']):
            for pat, repl in self.wlg.tagsfile['regex']:
                if pat.search(key):
                    key_ = key
                    key = pat.sub(repl, key)
                    self.log.debug("tag replace %s -> %s (%s)",
                                   key_, key, pat.pattern)
            return key
        # match
        if self.wlg.args.difflib:
            match = difflib.get_close_matches(key, self.wlg.whitelist, 1, .92)
            if match:
                self.log.debug("tag match   %s -> %s", key, match[0])
                return match[0]
        return key

    @classmethod
    def split(cls, key):
        '''Split a tag into all possible subtags.'''
        if len(key) < 7:
            return None
        keys = []
        if '/' in key:  # all delimiters got replaced with / earlier
            keys = key.split('/')
        elif ' ' in key:
            parts = key.split(' ')
            for i in range(1, len(parts) - 1):
                for combi in itertools.combinations(parts, len(parts) - i):
                    keys.append(' '.join(combi))
        return [k for k in keys if len(k) > 2]

    def format(self, key):
        '''Format a tag to correct case.'''
        words = key.split(' ')
        for i, word in enumerate(words):
            if len(word) < 3 and word != 'nu' or \
                    word in self.wlg.tagsfile['upper']:
                words[i] = word.upper()
            else:
                words[i] = word.title()
        return ' '.join(words)

    def merge(self):
        '''Merge all tag groups using different score modifiers.'''
        tags = defaultdict(float)
        for key, val in self.taggrps.items():
            scoremod = 1
            if key == 'artist':
                if self.various:
                    key = 'various'
                scoremod = self.wlg.conf.getfloat('scores', key)
            max_ = max(val.values())
            for key, val_ in val.items():
                tags[key] += val_ / max_ * scoremod
        return tags

    def top_genres(self, limit=4):
        '''Return the formated names of the top genre tags by score,
        limited to a set number of genres.
        '''
        self.verboseinfo()
        tags = self.merge()
        tags = sorted(tags.items(), key=operator.itemgetter(1), reverse=1)
        return [self.format(k) for k, _ in tags[:limit]]

    def verboseinfo(self):
        '''Prints out some verbose info about the TagLib.'''
        if self.log.level > logging.INFO:
            return None
        for key, val in self.taggrps.items():
            val = {self.format(k): v for k, v in val.items() if v > 0.1}
            val = sorted(val.items(), key=operator.itemgetter(1), reverse=1)
            val = val[:12 if self.log.level > logging.DEBUG else 24]
            self.log.info("Best %-6s genres (%d):", key, len(val))
            self.log.info(tag_display(val, "%4.2f %-20s"))


class Config(ConfigParser.SafeConfigParser):
    '''Read, maintain and write the configuration file.'''

    # [section, option, default, required, [min, max]]
    conf = [('wlg', 'sources', 'whatcd, lastfm, mbrainz', 1, ()),
            ('wlg', 'whatcduser', '', 0, ()),
            ('wlg', 'whatcdpass', '', 0, ()),
            ('wlg', 'whitelist', '', 0, ()),
            ('wlg', 'vaqueries', True, 1, ()),
            ('wlg', 'id3v23sep', '', 0, ()),
            ('genres', 'love', '', 0, ()),
            ('genres', 'hate',
             'alternative, electronic, indie, pop, rock', 0, ()),
            ('scores', 'artist', '1.33', 1, (0.5, 2.0)),
            ('scores', 'various', '0.66', 1, (0.1, 1.0)),
            ('scores', 'splitup', '0.33', 1, (0, 1.0)),
            ('scores', 'src_whatcd', '1.50', 1, (0.5, 2.0)),
            ('scores', 'src_lastfm', '0.66', 1, (0.5, 2.0)),
            ('scores', 'src_mbrainz', '0.66', 1, (0.5, 2.0)),
            ('scores', 'src_discogs', '1.00', 1, (0.5, 2.0)),
            ('scores', 'src_echonest', '1.00', 1, (0.5, 2.0))]

    def __init__(self, wlgdir, args):
        ConfigParser.SafeConfigParser.__init__(self)
        self.fullpath = os.path.join(wlgdir, 'config')
        self.read(self.fullpath)
        self.args = args
        self.maintain()
        self.validate()

    def maintain(self):
        '''Maintain the config file.

        Make sure the config file only contains valid options with
        reasonable values.
        '''
        dirty = False
        # clean up
        for sec in self.sections():
            if sec not in set(x[0] for x in self.conf):
                self.remove_section(sec)
                dirty = True
                continue
            for opt in self.options(sec):
                if (sec, opt) not in [(x[0], x[1]) for x in self.conf]:
                    self.remove_option(sec, opt)
                    dirty = True
        # add and sanitize
        for sec, opt, default, req, rng in self.conf:
            if not self.has_option(sec, opt) or \
                    req and self.get(sec, opt) == '':
                if not self.has_section(sec):
                    self.add_section(sec)
                self.set(sec, opt, str(default))
                dirty = True
                continue
            if rng and self.getfloat(sec, opt) < rng[0]:
                cor = ["small: setting to min", rng[0]]
            elif rng and self.getfloat(sec, opt) > rng[1]:
                cor = ["large: setting to max", rng[1]]
            else:
                continue
            print("%s option too %s value of %.2f." % (opt, cor[0], cor[1]))
            self.set(sec, opt, str(cor[1]))
            dirty = True
        # save
        if dirty:
            with open(self.fullpath, 'w') as file_:
                self.write(file_)

    def validate(self):
        '''Validate some configuration options.'''
        # sources
        sources = self.get_list('wlg', 'sources')
        for src in sources:
            if src not in ['whatcd', 'lastfm', 'mbrainz', 'discogs',
                           'echonest']:
                msg = "%s is not a valid source" % src
            elif src == 'whatcd' and not (self.get('wlg', 'whatcduser') and
                                          self.get('wlg', 'whatcdpass')):
                msg = "No What.CD credentials specified"
            else:
                continue
            print("%s. %s support disabled.\n" % (msg, src))
            sources.remove(src)
            self.set('wlg', 'sources', ', '.join(sources))
        if not sources:
            print("Where do you want to get your data from?\nAt least one "
                  "source must be activated (multiple sources recommended)!")
            exit()
        # options
        if self.args.tag_release and 'whatcd' not in sources:
            print("Can't tag release with What.CD support disabled. "
                  "Release tagging disabled.\n")
            self.args.tag_release = False

    def get_list(self, sec, opt):
        '''Gets a csv-string as list.'''
        list_ = self.get(sec, opt).lower().split(',')
        return [x.strip() for x in list_ if x.strip()]


class Cache(object):
    '''Load and save a dict as json from/into a file for some
    speedup.
    '''

    def __init__(self, wlgdir, update_cache):
        self.fullpath = os.path.join(wlgdir, 'cache')
        self.update_cache = update_cache
        self.expire_after = 180 * 24 * 60 * 60
        self.time = time.time()
        self.dirty = False
        self.cache = {}
        self.new = set()
        try:
            with open(self.fullpath) as file_:
                self.cache = json.load(file_)
        except (IOError, ValueError):
            pass

        # backward compatibility code
        for key, val in [(k, v) for k, v in self.cache.items() if '##' in k]:
            if val['data']:
                # only keep some keys
                keep = ['info', 'year'] if len(val['data']) > 1 else []
                val['data'] = [{k: v for k, v in d.items()
                                if k in ['tags', 'releasetype'] + keep}
                               for d in val['data'] if d]
                # all tag data are dicts now
                for dat in val['data']:
                    if dat and 'tags' in dat and isinstance(dat['tags'], list):
                        dat['tags'] = {t: 0 for t in dat['tags']}
            del self.cache[key]
            key = str(tuple(key.encode("utf-8").split('##')))
            val['data'] = val['data'] if val['data'] else []
            self.cache[key] = (val['time'], val['data'])
            self.dirty = True

    def __del__(self):
        self.save()
        print()

    def get(self, key):
        '''Return a (time, value) data tuple for a given key.'''
        if str(key) in self.cache \
                and time.time() < self.cache[str(key)][0] + self.expire_after \
                and (str(key) in self.new or not self.update_cache):
            return self.cache[str(key)]
        return None

    def set(self, key, value):
        '''Set value for a given key.'''
        if value:
            keep = ['info', 'year'] if len(value) > 1 else []
            value = [{k: v for k, v in val.items()
                      if k in ['tags', 'releasetype'] + keep}
                     for val in value if val]
        self.cache[str(key)] = (time.time(), value)
        if self.update_cache:
            self.new.add(str(key))
        self.dirty = True

    def clean(self):
        '''Clean up expired entries.'''
        print("Cleaning cache... ", end='')
        size = len(self.cache)
        for key, val in self.cache.items():
            if time.time() > val[0] + self.expire_after:
                del self.cache[key]
                self.dirty = True
        print("done! (%d entries removed)" % (size - len(self.cache)))

    def save(self):
        '''Save the cache dict as json string to a file.
        Clean expired entries before saving and use a tempfile to
        avoid data loss on interruption.
        '''
        if not self.dirty:
            return
        self.clean()
        print("Saving cache... ", end='')
        dirname, basename = os.path.split(self.fullpath)
        try:
            with NamedTemporaryFile(prefix=basename + '.tmp_',
                                    dir=dirname, delete=False) as tmpfile:
                tmpfile.write(json.dumps(self.cache))
                os.fsync(tmpfile)
            # seems atomic rename here is not possible on windows
            # http://docs.python.org/2/library/os.html#os.rename
            if os.name == 'nt' and os.path.isfile(self.fullpath):
                os.remove(self.fullpath)
            os.rename(tmpfile.name, self.fullpath)
            self.time = time.time()
            self.dirty = False
            size_mb = os.path.getsize(self.fullpath) / 2 ** 20
            print("  done! (%d entries, %.2f MB)" % (len(self.cache), size_mb))
        except KeyboardInterrupt:
            if os.path.isfile(tmpfile.name):
                os.remove(tmpfile.name)


def searchstr(str_):
    '''Clean up a string for use in searching.'''
    if not str_:
        return ''
    str_ = str_.lower()
    for pat in [r'\(.*\)$', r'\[.*\]', '{.*}', "- .* -", "'.*'", '".*"',
                ' (- )?(album|single|ep|official remix(es)?|soundtrack|ost)$',
                r'[ \(]f(ea)?t(\.|uring)? .*', r'vol(\.|ume)? ',
                '[!?/:,]', ' +']:
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
    lines = [' '.join([pattern % tuple(reversed(tags[i]))
                       for i in [l + len_ * j for j in range(3)]
                       if i < len(tags)]) for l in range(len_)]
    return '\n'.join(lines).encode('utf-8')


def ask_user(query, results):
    '''Ask the user to choose from a list of results.'''
    print("%-8s %-6s got    %2d results. Which is it?"
          % (query.dapr.name, query.type, len(results)))
    for i, dat in enumerate(results, start=1):
        info = dat['info'].encode(sys.stdout.encoding, errors='replace')
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


def work_folder(wlg, path):
    '''Create an Album object for a folder given by path to read and
    write metadata from/to. Query top genre tags by album metadata,
    update metadata with results and save the album and its tracks.'''
    # create album object to read and write metadata
    try:
        album = mediafile.Album(path, wlg.conf.get('wlg', 'id3v23sep'))
    except mediafile.AlbumError as err:
        print(err)
        wlg.stats.errors[str(err)].append(path)
        return
    # read album metadata
    artists = []
    for track in album.tracks:
        artist = (track.get_meta('artist'),
                  track.get_meta('musicbrainz_artistid'))
        if artist[0] and not VA_PAT.match(artist[0]):
            if artist[1] == VA_MBID:
                artists.append((artist[0], None))
            else:
                artists.append(artist)
    albumartist = (album.get_meta('albumartist'),
                   album.get_meta('musicbrainz_albumartistid'))
    if not albumartist[0]:
        albumartist = (album.get_meta('artist', lcp=True),
                       album.get_meta('musicbrainz_artistid'))
    if albumartist[0] and VA_PAT.match(albumartist[0]) \
            or albumartist[1] == VA_MBID:
        albumartist = (None, VA_MBID)
    metadata = Metadata(
        path=album.path, type=album.type,
        artists=artists, albumartist=albumartist,
        album=album.get_meta('album'),
        mbid_album=album.get_meta('musicbrainz_albumid'),
        mbid_relgrp=album.get_meta('musicbrainz_releasegroupid'),
        year=album.get_meta('date'), releasetype=album.get_meta('releasetype'))
    # query genres (and releasetype) for album metadata
    genres, releasetype = wlg.query_album(metadata)
    # update album metadata
    if genres:
        print("Genres: %s" % ', '.join(genres).encode('utf-8'))
        album.set_meta('genre', genres)
    else:
        print("No genres found")
        wlg.stats.errors["No genres found"].append(path)
    if wlg.args.tag_release and releasetype:
        print("RelTyp: %s" % releasetype)
        album.set_meta('releasetype', releasetype)
    # save metadata to all tracks
    if wlg.args.dry:
        print("DRY-RUN! Not saving metadata.")
    else:
        album.save()


def print_progressbar(current, total):
    '''Print the progressbar.'''
    print("\n(%2d/%d) [" % (current, total),
          '#' * int(60 * current / total),
          '-' * int(60 * (1 - current / total)),
          "] %2.0f%%" % (100 * current / total), sep='')


def get_args():
    '''Get the cmdline arguments from ArgumentParser.'''
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description='Improves genre metadata of audio files '
                    'based on tags from various music sites.')
    parser.add_argument('path', nargs='+',
                        help='folder(s) to scan for albums')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='verbose output (-vv for debug)')
    parser.add_argument('-n', '--dry', action='store_true',
                        help='don\'t save metadata')
    parser.add_argument('-u', '--update-cache', action='store_true',
                        help='force cache update')
    parser.add_argument('-i', '--interactive', action='store_true',
                        help='interactive mode')
    parser.add_argument('-r', '--tag-release', action='store_true',
                        help='tag release type (from What.CD)')
    parser.add_argument('-l', '--tag-limit', metavar='N', type=int, default=4,
                        help='max. number of genre tags')
    parser.add_argument('-d', '--difflib', action='store_true',
                        help='enable difflib matching (slow)')
    return parser.parse_args()


def main():
    '''main function of whatlastgenre.

    Get arguments, set up WhatLastGenre object, search for music
    folders, run the main loop on them and prints out some statistics.
    '''
    print("whatlastgenre v%s\n" % __version__)

    args = get_args()
    wlg = WhatLastGenre(args)
    folders = mediafile.find_music_folders(args.path)

    print("Found %d music folders!" % len(folders))
    if not folders:
        return

    i = 0
    try:  # main loop
        for i, path in enumerate(sorted(folders), start=1):
            print_progressbar(i, len(folders))
            print(path)
            work_folder(wlg, path)
        print("\n...all done!")
    except KeyboardInterrupt:
        print()
    wlg.print_stats(i)
