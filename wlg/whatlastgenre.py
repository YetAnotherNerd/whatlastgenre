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
from collections import defaultdict, Counter, namedtuple
from datetime import timedelta
import json
import logging
import os
import re
import sys
from tempfile import NamedTemporaryFile
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

Stats = namedtuple('Stats', ['time', 'genres', 'errors'])


class Config(ConfigParser.SafeConfigParser):
    '''Read, maintain and write the configuration file.'''

    # [section, option, default, required, [min, max]]
    conf = [('wlg', 'sources', 'whatcd, lastfm, mbrainz', 1, ()),
            ('wlg', 'whatcduser', '', 0, ()),
            ('wlg', 'whatcdpass', '', 0, ()),
            ('genres', 'love', '', 0, ()),
            ('genres', 'hate',
             'alternative, electronic, indie, pop, rock', 0, ()),
            ('genres', 'blacklist', 'charts, male vocalist, other', 0, ()),
            ('genres', 'filters', 'instrument, label, location, year', 0, ()),
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
            print("Please edit your configuration file: %s" % self.fullpath)
            exit()

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
    parser.add_argument('-u', '--update-cache', action='store_true',
                        help='force cache update')
    parser.add_argument('-i', '--interactive', action='store_true',
                        help='interactive mode')
    parser.add_argument('-r', '--tag-release', action='store_true',
                        help='tag release type (from What.CD)')
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
        cachekey = (dapr.name.lower(), variant, sstr.replace(' ', ''))
        cached = cache.get(cachekey)
        if cached:
            cachemsg = ' (cached)'
            data = cached[1]
            dapr.stats['queries_cache'] += 1
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
                dapr.stats['errors'] += 1
                continue
            dapr.stats['queries_web'] += 1
        if not data:
            LOG.info("%-8s %-6s search found    no results for '%s'%s",
                     dapr.name, variant, sstr, cachemsg)
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
        if not cached or len(cached[1]) > len(data):
            cache.set(cachekey, data)
        # still multiple results?
        if len(data) > 1:
            print("%-8s %-6s search found    %2d results for '%s'%s (use -i)"
                  % (dapr.name, variant, len(data), sstr, cachemsg))
            continue
        # unique data
        data = data[0]
        dapr.stats['results'] += 1
        if not data.get('tags'):
            LOG.info("%-8s %-6s search found    no    tags for '%s'%s",
                     dapr.name, variant, sstr, cachemsg)
            continue
        LOG.debug(data['tags'])
        tags = min(99, len(data['tags']))
        goodtags = genretags.add(dapr.name.lower(), variant, data['tags'])
        LOG.info("%-8s %-6s search found %2d of %2d tags for '%s'%s",
                 dapr.name, variant, goodtags, tags, sstr, cachemsg)
        dapr.stats['tags'] += tags
        dapr.stats['goodtags'] += goodtags
        if variant == 'album' and 'releasetype' in data:
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


def print_stats(stats, daprs, num_folders):
    '''Print some statistics.

    :param stats: dictionary with some stats
    :param daprs: list of DataProivder objects
    :param num_folders: number of processed album folders
    '''
    # genre tag statistics
    if stats.genres:
        tags = stats.genres.most_common()
        print("\n%d different tags used this often:" % len(tags))
        print(gt.tag_display(tags, "%4d %-20s"))
    # folder errors/messages
    if stats.errors:
        print("\n%d album(s) with errors:"
              % sum(len(x) for x in stats.errors.values()))
        for error, folders in stats.errors.items():
            print("%s:\n%s" % (error, '\n'.join(sorted(folders))))
    # dataprovider stats
    if LOG.level <= logging.INFO:
        print_dapr_stats(daprs)
    # time
    diff = time.time() - stats.time
    print("\nTime elapsed: %s (%s per folder)"
          % (timedelta(seconds=diff), timedelta(seconds=diff / num_folders)))


def print_dapr_stats(daprs):
    '''Print some DataProvider statistics.

    :param daprs: list of DataProvider objects
    '''
    print("\nSource stats  ", ''.join("| %-8s " % d.name for d in daprs),
          "\n", "-" * 14, "+----------" * len(daprs), sep='')
    for key in ['errors', 'queries_web', 'queries_cache', 'results',
                'results/query', 'tags', 'tags/result', 'goodtags',
                'goodtags/tag', 'time_resp_avg', 'time_wait_avg']:
        vals = []
        for dapr in daprs:
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
    conf = Config(wlgdir, args)

    folders = mf.find_music_folders(args.path)
    print("Found %d music folders!" % len(folders))
    if not folders:
        return

    stats = Stats(time.time(), Counter(), defaultdict(list))
    cache = Cache(wlgdir, args.update_cache)
    genretags = gt.GenreTags(conf)
    dps = dp.get_daprs(conf)

    i = 0
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
                stats.genres.update(genres)
            except mf.AlbumError as err:
                print(err)
                stats.errors[str(err)].append(path)
        print("\n...all done!")
    except KeyboardInterrupt:
        print()
    print_stats(stats, dps, i)
