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

Stats = namedtuple('Stats', ['time', 'errors', 'genres', 'reltyps', 'difflib'])


class WhatLastGenre(object):
    '''Main class featuring a docstring that needs to be written.'''

    def __init__(self, args, whitelist=None):
        self.log = logging.getLogger('wlg')
        self.log.setLevel(30 - 10 * args.verbose)
        self.log.addHandler(logging.StreamHandler(sys.stdout))
        self.log.debug("args: %s\n", args)

        self.stats = Stats(time=time.time(), errors=defaultdict(list),
                           genres=Counter(), reltyps=Counter(),
                           difflib=defaultdict())
        self.conf = Config(args)

        # dataproviders
        self.daprs = dataprovider.DataProvider.init_dataproviders(self.conf)

        # whitelist and tagsfile
        self.read_whitelist(whitelist)
        self.read_tagsfile()

        # validate tag_release arg
        if self.conf.args.tag_release \
                and 'whatcd' not in self.conf.get_list('wlg', 'sources'):
            self.log.warn('Can\'t tag release with What.CD support disabled. '
                          'Release tagging disabled.\n')
            self.conf.args.tag_release = False

        # validate aliases
        for key, val in self.tagsfile['aliases'].items():
            if val not in self.whitelist:
                del self.tagsfile['aliases'][key]
                self.log.info('warning: alias not whitelisted: %s -> %s',
                              key, val)

    def read_whitelist(self, path=None):
        '''Read whitelist file and store its contents as set.'''
        path = path or self.conf.get('wlg', 'whitelist')
        if path:
            with open(path, b'r') as file_:
                wlstr = u'\n'.join([l.decode('utf8') for l in file_])
        else:
            wlstr = pkgutil.get_data('wlg', 'data/genres.txt').decode('utf8')

        self.whitelist = set()
        for line in wlstr.split(u'\n'):
            line = line.strip().lower()
            if line and not line.startswith(u'#'):
                self.whitelist.add(line)

        if not self.whitelist:
            self.log.critical('empty whitelist: %s', path)
            exit()

    def read_tagsfile(self):
        '''Read tagsfile and return a dict of prepared data.'''
        tagsfile = ConfigParser.SafeConfigParser(allow_no_value=True)
        tfstr = pkgutil.get_data('wlg', 'data/tags.txt')
        tagsfile.readfp(StringIO.StringIO(tfstr))
        for sec in ['upper', 'alias', 'regex']:
            if not tagsfile.has_section(sec):
                self.log.critical('Got no [%s] from tags.txt file.', sec)
                exit()
        # regex replacements
        regex = []  # list of tuples instead of dict because order matters
        for pat, repl in [(r'( *[,;.:\\/&_]+ *| and )+', '/'),
                          (r'[\'"]+', ''), (r'  +', ' ')]:
            regex.append((re.compile(pat, re.I), repl))
        for pat, repl in tagsfile.items('regex', True):
            regex.append((re.compile(r'\b%s\b' % pat, re.I), repl))
        self.tagsfile = {
            'upper': dict(tagsfile.items('upper', True)).keys(),
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
                results, cached = query.dapr.cached_query(query)
            except NotImplementedError:
                continue
            except dataprovider.DataProviderError as err:
                query.dapr.stats['reqs_err'] += 1
                msg = query.dapr.name + ': ' + str(err)
                self.stats.errors[msg].append(metadata.path)
                self.log.error(msg)
                continue

            if not results:
                query.dapr.stats['results_none'] += 1
                self.verbose_status(query, cached, "no results")
                continue

            # ask user if appropriated
            if len(results) > 1 and self.conf.args.interactive \
                    and self.conf.args.tag_release \
                    and query.dapr.name.lower() == 'whatcd' \
                    and query.type == 'album' \
                    and not query.releasetype:
                reltyps = [r['releasetype'] for r in results
                           if 'releasetype' in r]
                if len(set(reltyps)) != 1:  # all the same anyway
                    results = ask_user(query, results)

            # merge multiple results
            if len(results) in range(2, 6):
                results = self.merge_results(results)

            # too many results
            if len(results) > 1:
                query.dapr.stats['results_many'] += 1
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

        # genres
        genres = taglib.top_genres(self.conf.args.tag_limit)
        self.stats.genres.update(genres)

        # releasetype
        if taglib.releasetype:
            taglib.releasetype = taglib.format(taglib.releasetype)
            self.stats.reltyps[taglib.releasetype] += 1

        return genres, taglib.releasetype

    def create_queries(self, metadata):
        '''Create queries for all DataProviders based on metadata.'''
        artists = metadata.artists
        num_artists = len(set(artists))
        if num_artists > 42:
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
        for res in results:
            if 'tags' in res and res['tags']:
                for key, val in res['tags'].iteritems():
                    tags[key] += val
        result = {'tags': tags}
        reltyps = [r['releasetype'] for r in results if 'releasetype' in r]
        if len(set(reltyps)) == 1:
            result.update({'releasetype': reltyps[0]})
        return [result]

    def verbose_status(self, query, cached, status):
        '''Log a status line in verbose mode.'''
        qry = query.artist
        if query.type == 'album':
            qry += ' ' + query.album
        self.log.info("%-8s %-6s got %13s for '%s'%s", query.dapr.name,
                      query.type, status, qry.strip(),
                      " (cached)" if cached else '')

    def print_stats(self, num_folders):
        '''Print some statistics.'''
        # genres
        if self.stats.genres:
            tags = self.stats.genres.most_common()
            print("\n%d different tags used this often:" % len(tags))
            print(tag_display(tags, "%4d %-20s"))
        # releasetypes
        if self.conf.args.tag_release and self.stats.reltyps:
            reltyps = self.stats.reltyps.most_common()
            print("\n%d different releasetypes used this often:"
                  % len(reltyps))
            print(tag_display(reltyps, "%4d %-20s"))
        # errors
        if self.stats.errors:
            print("\n%d album(s) with errors:"
                  % sum(len(x) for x in self.stats.errors.itervalues()))
            for error, folders in sorted(self.stats.errors.iteritems(),
                                         key=lambda x: len(x[1]), reverse=1):
                print("  %s:\n    %s"
                      % (error, '\n    '.join(sorted(folders))))
        # difflib
        if self.stats.difflib:
            print("\ndifflib found %d tags:" % len(self.stats.difflib))
            for key, val in self.stats.difflib.iteritems():
                print("%s = %s" % (key, val))
            print("You should add them as aliases (if correct) to tags.txt.")
        # dataprovider
        if self.log.level <= logging.INFO:
            dataprovider.DataProvider.print_stats(self.daprs)
        # time
        diff = time.time() - self.stats.time
        print("\nTime elapsed: %s (%s per folder)\n"
              % (timedelta(seconds=diff),
                 timedelta(seconds=diff / num_folders)))


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
        tags = sorted(tags.iteritems(), key=operator.itemgetter(1), reverse=1)
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
        any_ = any(tags.itervalues())
        max_ = max(tags.itervalues())
        for key, val in tags.iteritems():
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
        # alias
        if key in self.wlg.tagsfile['aliases']:
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
        if self.wlg.conf.args.difflib:
            match = difflib.get_close_matches(key, self.wlg.whitelist, 1, .92)
            if match:
                self.log.debug("tag match   %s -> %s", key, match[0])
                self.wlg.stats.difflib[key] = match[0]
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
        for key, val in self.taggrps.iteritems():
            scoremod = 1
            if key == 'artist':
                if self.various:
                    key = 'various'
                scoremod = self.wlg.conf.getfloat('scores', key)
            max_ = max(val.itervalues())
            for key, val_ in val.iteritems():
                tags[key] += val_ / max_ * scoremod
        return tags

    def top_genres(self, limit=4):
        '''Return the formated names of the top genre tags by score,
        limited to a set number of genres.
        '''
        self.verboseinfo()
        tags = self.merge()
        tags = sorted(tags.iteritems(), key=operator.itemgetter(1), reverse=1)
        return [self.format(k) for k, _ in tags[:limit]]

    def verboseinfo(self):
        '''Prints out some verbose info about the TagLib.'''
        if self.log.level > logging.INFO:
            return None
        for key, val in self.taggrps.iteritems():
            val = {self.format(k): v for k, v in val.iteritems() if v > 0.1}
            val = sorted(val.iteritems(), key=operator.itemgetter(1),
                         reverse=1)
            val = val[:12 if self.log.level > logging.DEBUG else 24]
            self.log.info("Best %-6s genres (%d):", key, len(val))
            self.log.info(tag_display(val, "%4.2f %-20s"))


class Config(ConfigParser.SafeConfigParser):
    '''Read, maintain and write the configuration file.'''

    # (section, option, default, required, (min, max))
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
            ('scores', 'src_discogs', '1.00', 1, (0.5, 2.0)),
            ('scores', 'src_mbrainz', '0.66', 1, (0.5, 2.0)),
            ('scores', 'src_echonest', '1.00', 1, (0.5, 2.0))]

    def __init__(self, args):
        ConfigParser.SafeConfigParser.__init__(self)
        self.args = args
        self.path = os.path.expanduser('~/.whatlastgenre')
        if not os.path.exists(self.path):
            os.makedirs(self.path)
        self.fullpath = os.path.join(self.path, 'config')
        self.read(self.fullpath)
        self.maintain()

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
            logging.getLogger(__name__).warn(
                '%s option too %s value of %.2f.', opt, cor[0], cor[1])
            self.set(sec, opt, str(cor[1]))
            dirty = True
        # save
        if dirty:
            with open(self.fullpath, 'w') as file_:
                self.write(file_)

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


def work_folder(wlg, path):
    '''Create an Album object for a folder given by path to read and
    write metadata from/to.  Query top genre tags by album metadata,
    update metadata with results and save the album (its tracks).
    '''
    # create album object to read and write metadata
    try:
        album = mediafile.Album(path, wlg.conf.get('wlg', 'id3v23sep'))
    except mediafile.AlbumError as err:
        print(err)
        wlg.stats.errors[str(err)].append(path)
        return

    # read album metadata
    metadata = album.get_metadata()

    # query genres (and releasetype) for album metadata
    genres, releasetype = wlg.query_album(metadata)

    # update album metadata
    if genres:
        print("Genres: %s" % ', '.join(genres).encode('utf-8'))
        album.set_meta('genre', genres)
    else:
        err = "No genres found"
        print(err)
        wlg.stats.errors[err].append(path)
    if wlg.conf.args.tag_release:
        if releasetype:
            print("RelTyp: %s" % releasetype)
            album.set_meta('releasetype', releasetype)
        else:
            err = "No releasetype found"
            print(err)
            wlg.stats.errors[err].append(path)

    # save metadata to all tracks
    if wlg.conf.args.dry:
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
        description='Improve genre metadata of audio files '
                    'based on tags from various music sites.')
    parser.add_argument('path', nargs='+',
                        help='folder(s) to scan for albums')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='verbose output (-vv for debug)')
    parser.add_argument('-n', '--dry', action='store_true',
                        help='don\'t save metadata')
    parser.add_argument('-u', '--update-cache', action='store_true',
                        help='force cache update')
    parser.add_argument('-l', '--tag-limit', metavar='N', type=int, default=4,
                        help='max. number of genre tags')
    parser.add_argument('-r', '--tag-release', action='store_true',
                        help='tag release type (from What.CD)')
    parser.add_argument('-i', '--interactive', action='store_true',
                        help='interactive mode')
    parser.add_argument('-d', '--difflib', action='store_true',
                        help='enable difflib matching (slow)')
    return parser.parse_args()


def main():
    '''main function of whatlastgenre.

    Get arguments, set up WhatLastGenre object, search for music
    folders, run the main loop on them and print out some statistics.
    '''
    print("whatlastgenre v%s\n" % __version__)

    args = get_args()
    wlg = WhatLastGenre(args)
    folders = mediafile.find_music_folders(args.path)

    print("Found %d music folders!" % len(folders))
    if not folders:
        return

    i = len(folders)
    try:  # main loop
        for i, path in enumerate(sorted(folders), start=1):
            print_progressbar(i, len(folders))
            print(path)
            work_folder(wlg, path)
        print('\n...all done!')
    except KeyboardInterrupt:
        print()
    wlg.print_stats(i)
