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
import argparse
from collections import defaultdict
import datetime
import json
import logging
import os
import re
import sys
import tempfile
import time

from wlg import __version__
import wlg.dataprovider as dp
import wlg.genretag as gt
import wlg.mediafile as mf


LOG = logging.getLogger('whatlastgenre')

# Regex pattern to recognize Various Artist strings
VA_PAT = re.compile('^va(rious( ?artists?)?)?$', re.I)

# Musicbrainz ID of 'Various Artists'
VA_MBID = '89ad4ac3-39f7-470e-963a-56509c546377'


class Config(ConfigParser.SafeConfigParser):
    '''Reads, maintains and writes the configuration file.'''

    configfile = os.path.expanduser('~/.whatlastgenre/config')

    # [section, option, default, required, [min, max]]
    conf = [['wlg', 'sources', 'whatcd, mbrainz, lastfm', 1, []],
            ['wlg', 'cache_timeout', '30', 1, [14, 180]],
            ['wlg', 'whatcduser', '', 0, []],
            ['wlg', 'whatcdpass', '', 0, []],
            ['genres', 'love', 'soundtrack', 0, []],
            ['genres', 'hate',
             'alternative, electronic, indie, pop, rock', 0, []],
            ['genres', 'blacklist', 'charts, male vocalist, other', 0, []],
            ['genres', 'filters', 'instrument, label, location, year', 0, []],
            ['scores', 'artist', '1.33', 1, [0.5, 2.0]],
            ['scores', 'various', '0.66', 1, [0.1, 1.0]],
            ['scores', 'splitup', '0.33', 1, [0, 1.0]],
            ['scores', 'src_whatcd', '1.66', 1, [0.5, 2.0]],
            ['scores', 'src_lastfm', '0.66', 1, [0.5, 2.0]],
            ['scores', 'src_mbrainz', '1.00', 1, [0.5, 2.0]],
            ['scores', 'src_discogs', '1.00', 1, [0.5, 2.0]],
            ['scores', 'src_echonest', '1.00', 1, [0.5, 2.0]]]

    def __init__(self, args):
        ConfigParser.SafeConfigParser.__init__(self)
        self.args = args
        self.read(self.configfile)
        self.__maintain()
        self.__validate()

    def __maintain(self):
        '''Maintains the config file.

        Makes sure the config file only contains valid options with
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
                if [sec, opt] not in [x[:2] for x in self.conf]:
                    self.remove_option(sec, opt)
                    dirty = True
        # add and sanitize
        for sec, opt, default, req, rng in self.conf:
            if not self.has_option(sec, opt) or \
                    req and self.get(sec, opt) == '':
                if not self.has_section(sec):
                    self.add_section(sec)
                self.set(sec, opt, default)
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
            with open(self.configfile, 'w') as conffile:
                self.write(conffile)
            print("Please edit your configuration file: %s" % self.configfile)
            exit()

    def __validate(self):
        '''Validates some configuration options.'''
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
    '''Loads and saves a dict as json from/into a file for some
    speedup.
    '''

    def __init__(self, bypass, timeout):
        self.fullpath = os.path.expanduser('~/.whatlastgenre/cache')
        self.bypass = bypass
        self.timeout = timeout * 60 * 60 * 24
        self.time = time.time()
        self.dirty = False
        self.cache = {}
        try:
            with open(self.fullpath) as infile:
                self.cache = json.load(infile)
        except (IOError, ValueError):
            pass
        self.clean()

    def __del__(self):
        self.save()
        print()

    def get(self, key):
        '''Returns data for a given key.

        Since this method does't check the timestamp of the cache
        entries self.clean() is to be called on instantiation.
        '''
        if self.bypass:
            return
        return self.cache.get(key)

    def set(self, key, data):
        '''Sets data for a given key.'''
        if data:
            keep = ['tags', 'mbid', 'releasetype']
            if len(data) > 1:
                keep += ['info', 'title', 'year']
            for dat in data:
                for k in [k for k in dat.keys() if k not in keep]:
                    del dat[k]
        # just update the data if key exists
        if not self.bypass and self.cache.get(key):
            self.cache[key]['data'] = data
        else:
            self.cache[key] = {'time': time.time(), 'data': data}
        self.dirty = True

    def clean(self):
        '''Cleans up expired or invalid entries.'''
        print("\nCleaning cache... ", end='')
        size = len(self.cache)
        for key, val in self.cache.items():
            if time.time() - val.get('time', 0) > self.timeout \
                    or re.match('discogs##artist##', key) \
                    or re.match('echonest##album##', key) \
                    or re.match('.*##.*##.?$', key):
                del self.cache[key]
        diff = size - len(self.cache)
        print("done! (%d removed)" % diff)
        if diff:
            self.dirty = True
            self.save()

    def save(self):
        '''Saves the cache dict as json string to a file.

        A tempfile is used to avoid data loss on interruption.
        '''
        if not self.dirty:
            return
        print("\nSaving cache... ", end='')
        dirname, basename = os.path.split(self.fullpath)
        try:
            with tempfile.NamedTemporaryFile(prefix=basename + '.tmp_',
                                             dir=dirname,
                                             delete=False) as tmpfile:
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
            print("done! (%d entries, %.2f MB)" % (len(self.cache), size_mb))
        except KeyboardInterrupt:
            os.remove(tmpfile.name)


def get_args():
    '''Gets the cmdline arguments from ArgumentParser.'''
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
    parser.add_argument('-c', '--no-cache', action='store_true',
                        help='don\'t read from cache (just write)')
    parser.add_argument('-i', '--interactive', action='store_true',
                        help='interactive mode')
    parser.add_argument('-r', '--tag-release', action='store_true',
                        help='tag release type (from What.CD)')
    parser.add_argument('-m', '--tag-mbids', action='store_true',
                        help='tag musicbrainz ids')
    parser.add_argument('-l', '--tag-limit', metavar='N', type=int, default=4,
                        help='max. number of genre tags')
    args = parser.parse_args()

    if args.verbose == 0:
        loglvl = logging.WARN
    elif args.verbose == 1:
        loglvl = logging.INFO
    else:
        loglvl = logging.DEBUG
    hdlr = logging.StreamHandler(sys.stdout)
    hdlr.setLevel(loglvl)
    LOG.setLevel(loglvl)
    LOG.addHandler(hdlr)

    LOG.debug("args: %s\n", args)
    return args


def handle_album(args, dps, cache, genretags, album):
    '''Receives tags and saves an album.'''
    genretags.reset(album)
    albumartist = album.get_meta('albumartist') or album.get_meta('artist')
    if albumartist and VA_PAT.match(albumartist):
        albumartist = None
    albumartistmbid = album.get_meta('musicbrainz_albumartistid') \
            or album.get_meta('musicbrainz_artistid')
    if albumartistmbid and albumartistmbid == VA_MBID:
        albumartistmbid = None
    sdata = {
        'releasetype': None,
        'date': album.get_meta('date'),
        'album': searchstr(album.get_meta('album')),
        'artist': [(searchstr(albumartist), albumartistmbid)],
        'mbids': {'releasegroupid':
                  album.get_meta('musicbrainz_releasegroupid'),
                  'albumid': album.get_meta('musicbrainz_albumid')}
    }
    # search for all track artists if no albumartist
    if not albumartist:
        for track in album.tracks:
            if track.get_meta('artist') and \
                    not VA_PAT.match(track.get_meta('artist')):
                sdata['artist'].append((searchstr(track.get_meta('artist')),
                                        track.get_meta('musicbrainz_artistid')))

    # get data from dataproviders
    sdata = get_data(args, dps, cache, genretags, sdata)
    # set genres
    genres = genretags.get(len(sdata['artist']) > 1)[:args.tag_limit]
    if genres:
        print("Genres: %s" % ', '.join(genres))
        album.set_meta('genre', genres)
    # set releasetype
    if args.tag_release and sdata.get('releasetype'):
        print("RelTyp: %s" % sdata['releasetype'])
        album.set_meta('releasetype', sdata['releasetype'])
    # set mbrainz ids
    if args.tag_mbids and 'mbids' in sdata:
        LOG.info("MB-IDs: %s", ', '.join(["%s=%s" % (k, v) for k, v
                                          in sdata['mbids'].items()]))
        for key, val in sdata['mbids'].items():
            album.set_meta('musicbrainz_' + key, val)
    # save metadata
    if args.dry:
        print("DRY-RUN! Not saving metadata.")
    else:
        album.save()
    return genres


def get_data(args, dps, cache, genretags, sdata):
    '''Gets all the data from all dps or from cache.'''
    tuples = [(0, 'album')]
    tuples += [(i, 'artist') for i in range(len(sdata['artist']))]
    tuples = [(i, v, d) for (i, v) in tuples for d in dps]
    for i, variant, dapr in tuples:
        sstr = [sdata['artist'][i][0]]
        if variant == 'album':
            sstr.append(sdata['album'])
        sstr = ' '.join(sstr).strip()
        if not sstr or len(sstr) < 2:
            continue
        cachekey = '##'.join([dapr.name, variant, sstr]).lower()
        cachekey = re.sub(r'([^\w#]| +)', '', cachekey).strip()
        cached = cache.get(cachekey)
        if cached:
            cachemsg = ' (cached)'
            data = cached['data']
        else:
            cachemsg = ''
            try:
                if variant == 'artist':
                    data = dapr.get_artist_data(sdata['artist'][i][0],
                                                sdata['artist'][i][1])
                elif variant == 'album':
                    data = dapr.get_album_data(sdata['artist'][i][0],
                                               sdata['album'], sdata['mbids'])
            except RuntimeError:
                continue
            except dp.DataProviderError as err:
                print("%-8s %s" % (dapr.name, err.message))
                dapr.add_query_stats(error=True)
                continue
        if not data:
            LOG.info("%-8s %-6s search found    no results for '%s'%s",
                     dapr.name, variant, sstr, cachemsg)
            dapr.add_query_stats()
            cache.set(cachekey, None)
            continue
        # filter
        data = filter_data(dapr.name.lower(), variant, sdata, data)
        # still multiple results?
        if len(data) > 1:
            # ask user interactivly for important sources
            if dapr.name.lower() in ['whatcd', 'mbrainz']:
                if args.interactive:
                    data = interactive(dapr.name, variant, data)
            # merge all the hits for unimportant sources
            elif isinstance(data[0]['tags'], dict):
                tags = defaultdict(float)
                for dat in data:
                    for tag in dat['tags']:
                        tags[tag] += dat['tags'][tag]
                data = [{'tags': {k: v for k, v in tags.items()}}]
            elif isinstance(data[0]['tags'], list):
                tags = []
                for dat in data:
                    for tag in dat['tags']:
                        if tag not in tags:
                            tags.append(tag)
                data = [{'tags': tags}]
        # save cache
        if not cached or len(cached['data']) > len(data):
            cache.set(cachekey, data)
        # still multiple results?
        if len(data) > 1:
            print("%-8s %-6s search found    %2d results for '%s'%s (use -i)"
                  % (dapr.name, variant, len(data), sstr, cachemsg))
            dapr.add_query_stats(results=len(data))
            continue
        # unique data
        data = data[0]
        if not data.get('tags'):
            LOG.info("%-8s %-6s search found    no    tags for '%s'%s",
                     dapr.name, variant, sstr, cachemsg)
            dapr.add_query_stats(results=1)
            continue
        LOG.debug(data['tags'])
        tags = min(99, len(data['tags']))
        goodtags = genretags.add(dapr.name.lower(), variant, data['tags'])
        LOG.info("%-8s %-6s search found %2d of %2d tags for '%s'%s",
                 dapr.name, variant, goodtags, tags, sstr, cachemsg)
        dapr.add_query_stats(results=1, tags=tags, goodtags=goodtags)
        if variant == 'artist' and 'mbid' in data \
                and len(sdata['artist']) == 1:
            sdata['mbids']['albumartistid'] = data['mbid']
        elif variant == 'album':
            if 'mbid' in data:
                sdata['mbids']['releasegroupid'] = data['mbid']
            if 'releasetype' in data:
                sdata['releasetype'] = genretags.format(data['releasetype'])
    return sdata


def filter_data(source, variant, sdata, data):
    '''Prefilters data to reduce needed interactivity.'''
    if not data or len(data) == 1:
        return data
    # filter by date
    if variant == 'album' and sdata['date']:
        for i in range(4):
            tmp = [d for d in data if not d.get('year') or
                   abs(int(d['year']) - int(sdata['date'])) <= i]
            if tmp:
                data = tmp
                break
    # filter by releasetype for whatcd
    if len(data) > 1:
        if source == 'whatcd' and variant == 'album' and sdata['releasetype']:
            data = [d for d in data if 'releasetype' not in d or
                    d['releasetype'].lower() == sdata['releasetype'].lower()]
    return data


def interactive(source, variant, data):
    '''Asks the user to choose from a list of possibilities.'''
    print("%-8s %-6s search found    %2d results. Which is it?"
          % (source, variant, len(data)))
    for i, dat in enumerate(data, start=1):
        info = dat['info'].encode(sys.stdout.encoding, errors='replace')
        print("#%2d: %s" % (i, info))
    while True:
        try:
            num = int(raw_input("Please choose #[1-%d] (0 to skip): "
                                % len(data)))
        except ValueError:
            num = None
        except EOFError:
            num = 0
            print()
        if num in range(len(data) + 1):
            break
    return [data[num - 1]] if num else data


def searchstr(str_):
    '''Cleans up a string for use in searching.'''
    if not str_:
        return ''
    for pat in [r'\(.*\)$', r'\[.*\]', '{.*}', "- .* -", "'.*'", '".*"',
                ' (- )?(album|single|ep|official remix(es)?|soundtrack|ost)$',
                r'[ \(]f(ea)?t(\.|uring)? .*', r'vol(\.|ume)? ',
                '[!?/:,]', ' +']:
        str_ = re.sub(pat, ' ', str_, 0, re.I)
    return str_.strip().lower()


def print_stats(stats, dps):
    '''Prints out some statistics.'''
    print("\nTime elapsed: %s"
          % datetime.timedelta(seconds=time.time() - stats['starttime']))
    # genre tag statistics
    if stats['genres']:
        tags = sorted(stats['genres'].items(), key=lambda (k, v): (v, k),
                      reverse=1)
        tags = gt.GenreTags.tagprintstr(tags, "%5d %-19s")
        print("\n%d different tags used this often:\n%s"
              % (len(stats['genres']), tags))
    # data provider statistics
    if LOG.level <= logging.INFO:
        print('\n%-13s ' % 'Source stats', end='')
        for dapr in dps:
            dapr.stats.update({
                'time_resp_avg':
                dapr.stats['time_resp'] / max(.001, dapr.stats['realqueries']),
                'time_wait_avg':
                dapr.stats['time_wait'] / max(.001, dapr.stats['realqueries']),
                'results/query':
                dapr.stats['results'] / max(.001, dapr.stats['queries']),
                'tags/result':
                dapr.stats['tags'] / max(.001, dapr.stats['results']),
                'goodtags/tags':
                dapr.stats['goodtags'] / max(.001, dapr.stats['tags'])})
            print("| %-8s " % dapr.name, end='')
        print('\n', '-' * 14, '+----------' * len(dps), sep='')
        for key in ['errors', 'realqueries', 'queries', 'results',
                    'results/query', 'tags', 'tags/result', 'goodtags',
                    'goodtags/tags', 'time_resp_avg', 'time_wait_avg']:
            if not any(dapr.stats[key] for dapr in dps):
                continue
            print("%-13s " % key, end='')
            for dapr in dps:
                if isinstance(dapr.stats[key], float):
                    print("| %8.2f " % dapr.stats[key], end='')
                else:
                    print("| %8d " % dapr.stats[key], end='')
            print()
    # folder errors/messages
    if stats['folders']:
        print("\n%d album(s) with errors/messages:" % len(stats['folders']))
        for msg in set(stats['folders'].values()):
            fldrs = [k for k, v in stats['folders'].items() if v == msg]
            print("%s:\n%s" % (msg, '\n'.join(sorted(fldrs))))


def main():
    '''main function of whatlastgenre.

    Reads and validates arguments and configuration,
    set up the needed objects and run the main loop.
    Prints out some statistics at the end.
    '''
    print("whatlastgenre v%s\n" % __version__)

    wlgdir = os.path.expanduser('~/.whatlastgenre')
    if not os.path.exists(wlgdir):
        os.makedirs(wlgdir)

    args = get_args()
    conf = Config(args)

    stats = {'starttime': time.time(),
             'genres': defaultdict(int),
             'folders': {}}

    folders = mf.find_music_folders(args.path)
    print("Found %d music folders!" % len(folders))
    if not folders:
        return

    genretags = gt.GenreTags(conf)
    cache = Cache(args.no_cache, conf.getint('wlg', 'cache_timeout'))
    dps = dp.get_daprs(conf)

    try:  # main loop
        for i, path in enumerate(sorted(folders), start=1):
            # save cache periodically
            if time.time() - cache.time > 600:
                cache.save()
            # progress bar
            print("\n(%2d/%d) [" % (i, len(folders)),
                  '#' * int(60 * i / len(folders)),
                  '-' * int(60 * (1 - i / len(folders))),
                  "] %2.0f%%" % (100 * i / len(folders)), sep='')
            print(path)
            try:
                album = mf.Album(path)
                LOG.info("[%s] albumartist=%s, album=%s, date=%s",
                         album.type, album.get_meta('albumartist'),
                         album.get_meta('album'), album.get_meta('date'))
                genres = handle_album(args, dps, cache, genretags, album)
                if not genres:
                    raise mf.AlbumError("No genres found")
                for tag in genres:
                    stats['genres'][tag] += 1
            except mf.AlbumError as err:
                print(err)
                stats['folders'].update({path: str(err)})
        print("\n...all done!")
    except KeyboardInterrupt:
        print()
    print_stats(stats, dps)
