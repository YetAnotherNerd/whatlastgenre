#!/usr/bin/env python
'''whatlastgenre

Improves genre metadata of audio files based on tags from various music sites.
http://github.com/YetAnotherNerd/whatlastgenre
'''

from __future__ import division, print_function

import ConfigParser
import argparse
from collections import defaultdict
import datetime
import difflib
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


class MySafeConfigParser(ConfigParser.SafeConfigParser):
    '''Little addition to SafeConfigParser.'''
    def get_list(self, sec, opt):
        '''Gets a csv-string as list.'''
        list_ = self.get(sec, opt).lower().split(',')
        return [x.strip() for x in list_ if x.strip()]


class Cache(object):
    '''Loads and saves a dict as json from/into a file for some speedup.'''

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

        Since this method does't check the timestamp of the cache entries
        self.clean() is to be called on instantiation.
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
                or re.match('(echonest|idiomag)##album##', key) \
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
            os.rename(tmpfile.name, self.fullpath)
            self.time = time.time()
            self.dirty = False
            print("done! (%d entries, %.2f MB)"
                  % (len(self.cache), os.path.getsize(self.fullpath) / 2 ** 20))
        except KeyboardInterrupt:
            pass


def get_args():
    '''Gets the cmdline arguments from ArgumentParser.'''
    args = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description='Improves genre metadata of audio files '
                    'based on tags from various music sites.')
    args.add_argument('path', nargs='+',
                      help='folder(s) to scan for albums')
    args.add_argument('-v', '--verbose', action='store_true',
                      help='more detailed output')
    args.add_argument('-n', '--dry', action='store_true',
                      help='don\'t save metadata')
    args.add_argument('-i', '--interactive', action='store_true',
                      help='interactive mode')
    args.add_argument('-c', '--no-cache', action='store_true',
                      help='bypass cache hits')
    args.add_argument('-r', '--tag-release', action='store_true',
                      help='tag release type (from What)')
    args.add_argument('-m', '--tag-mbids', action='store_true',
                      help='tag musicbrainz ids')
    args.add_argument('-l', '--tag-limit', metavar='N', type=int, default=4,
                      help='max. number of genre tags')
    args = args.parse_args()
    return args


def get_conf():
    '''Reads, maintains and writes the configuration file.'''
    configfile = os.path.expanduser('~/.whatlastgenre/config')
    # [section, option, default, required, [min, max]]
    conf = [['wlg', 'sources', 'whatcd, mbrainz, lastfm', 1, []],
            ['wlg', 'cache_timeout', '60', 1, [14, 180]],
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
            ['scores', 'src_whatcd', '1.66', 1, [0.3, 2.0]],
            ['scores', 'src_lastfm', '0.66', 1, [0.3, 2.0]],
            ['scores', 'src_mbrainz', '1.00', 1, [0.3, 2.0]],
            ['scores', 'src_discogs', '1.00', 1, [0.3, 2.0]],
            ['scores', 'src_idiomag', '1.00', 1, [0.3, 2.0]],
            ['scores', 'src_echonest', '1.00', 1, [0.3, 2.0]]]
    config = MySafeConfigParser()
    config.read(configfile)
    dirty = False
    # remove old options
    for sec in config.sections():
        if not [x for x in conf if x[0] == sec]:
            config.remove_section(sec)
            dirty = True
            continue
        for opt in config.options(sec):
            if not [x for x in conf if x[:2] == [sec, opt]]:
                config.remove_option(sec, opt)
                dirty = True
    # add and correct options
    for sec, opt, default, req, rng in [x for x in conf]:
        if not config.has_option(sec, opt) or \
                req and config.get(sec, opt) == '':
            if not config.has_section(sec):
                config.add_section(sec)
            config.set(sec, opt, default)
            dirty = True
            continue
        if rng and config.getfloat(sec, opt) < rng[0]:
            cor = [rng[0], "small: setting to min"]
        elif rng and config.getfloat(sec, opt) > rng[1]:
            cor = [rng[1], "large: setting to max"]
        else:
            continue
        print("%s option too %s value of %.2f." % (opt, cor[1], cor[0]))
        config.set(sec, opt, cor[0])
        dirty = True
    if not dirty:
        return config
    with open(configfile, 'w') as conffile:
        config.write(conffile)
    print("Please edit your configuration file: %s" % configfile)
    exit()

def validate(args, conf):
    '''Validates args and conf.'''
    # sources
    sources = conf.get_list('wlg', 'sources')
    for src in sources:
        if src not in ['whatcd', 'lastfm', 'mbrainz', 'discogs',
                       'idiomag', 'echonest']:
            msg = "%s is not a valid source" % src
        elif src == 'whatcd' and not (conf.get('wlg', 'whatcduser') and
                                      conf.get('wlg', 'whatcdpass')):
            msg = "No WhatCD credentials specified"
        else:
            continue
        print("%s. %s support disabled.\n" % (msg, src))
        sources.remove(src)
        conf.set('wlg', 'sources', ', '.join(sources))
    if not sources:
        print("Where do you want to get your data from?\nAt least one "
              "source must be activated (multiple sources recommended)!")
        exit()
    # options
    if args.tag_release and 'whatcd' not in sources:
        print("Can't tag release with WhatCD support disabled. "
              "Release tagging disabled.\n")
        args.tag_release = False
    if args.tag_mbids and 'mbrainz' not in sources:
        print("Can't tag MBIDs with MusicBrainz support disabled. "
              "MBIDs tagging disabled.\n")
        args.tag_mbids = False

def handle_folder(args, dps, cache, genretags, folder):
    '''Loads metadata, receives tags and saves an album.'''
    album = mf.Album(folder[0], folder[1], folder[2])
    genretags.reset(compile_album_filter(album))
    sdata = {
        'releasetype': None,
        'date' : album.get_common_meta('date'),
        'album': searchstr(album.get_common_meta('album')),
        'artist': [(searchstr(album.get_common_meta('albumartist')),
                    album.get_common_meta('musicbrainz_albumartistid'))],
        'mbids': {'albumartistid':
                  album.get_common_meta('musicbrainz_albumartistid'),
                  'releasegroupid':
                  album.get_common_meta('musicbrainz_releasegroupid'),
                  'albumid': album.get_common_meta('musicbrainz_albumid')}
    }
    # search for all track artists if no albumartist
    if not album.get_common_meta('albumartist'):
        for track in [t for t in album.tracks if t.get_meta('artist')
                      and not mf.VAPAT.match(t.get_meta('artist'))]:
            sdata['artist'].append((searchstr(track.get_meta('artist')),
                                    track.get_meta('musicbrainz_artistid')))
    # get data from dataproviders
    sdata = get_data(args, dps, cache, genretags, sdata)
    # set genres
    genres = genretags.get(len(sdata['artist']) > 1)[:args.tag_limit]
    if genres:
        print("Genres: %s" % ', '.join(genres))
        album.set_common_meta('genre', genres)
    else:
        print("Genres: None found :-(")
    # set releasetype
    if args.tag_release and sdata.get('releasetype'):
        print("RelTyp: %s" % sdata['releasetype'])
        album.set_common_meta('releasetype', sdata['releasetype'])
    # set mbrainz ids
    if args.tag_mbids and 'mbids' in sdata:
        LOG.info("MB-IDs: %s", ', '.join(["%s=%s" % (k, v) for k, v
                                          in sdata['mbids'].items()]))
        for key, val in sdata['mbids'].items():
            album.set_common_meta('musicbrainz_' + key, val)
    # save metadata
    if args.dry:
        print("DRY-RUN! Not saving metadata.")
    else:
        album.save()
    return genres

def compile_album_filter(album):
    '''Returns a filter pattern object based on the metadata of an album.'''
    badtags = []
    for tag in ['albumartist', 'album']:
        val = album.get_common_meta(tag)
        if not val:
            continue
        bts = [val]
        if tag == 'albumartist' and ' ' in bts[0]:
            bts += bts[0].split(' ')
        for badtag in bts:
            for pat in [r'\(.*\)', r'\[.*\]', '{.*}', '-.*-', "'.*'",
                        '".*"', r'vol(\.|ume)? ', ' and ', 'the ',
                        r'[\W\d]', r'(\.\*)+']:
                badtag = re.sub(pat, '.*', badtag, 0, re.I).strip()
            badtag = re.sub(r'(^\.\*|\.\*$)', '', badtag, 0, re.I)
            if len(badtag) > 2:
                badtags.append(badtag.strip().lower())
    return re.compile('.*(' + '|'.join(badtags) + ').*', re.I)

def get_data(args, dps, cache, genretags, sdata):
    '''Gets all the data from all dps or from cache.'''
    tupels = [(0, 'album')]
    tupels += [(i, 'artist') for i in range(len(sdata['artist']))]
    tuples = [(i, v, d) for (i, v) in tupels for d in dps]
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
                print("%8s %s" % (dapr.name, err.message))
                continue
        if not data or (len(data) == 1 and not data[0].get('tags')):
            LOG.info("%8s %6s search found    no    tags for '%s'%s",
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
                for tag, score in [d['tags'] for d in data]:
                    tags[tag] += score
                data = [{'tags': {k: v for k, v in tags.items()}}]
            elif isinstance(data[0]['tags'], list):
                tags = []
                for dat in data:
                    for tag in [t for t in dat['tags'] if t not in tags]:
                        tags.append(tag)
                data = [{'tags': tags}]
        # save cache
        if not cached or len(cached['data']) > len(data):
            cache.set(cachekey, data)
        # still multiple results?
        if len(data) > 1:
            print("%8s %6s search found %d ambiguous results for '%s' (use -i)"
                  "%s" % (dapr.name, variant, len(data), sstr, cachemsg))
            continue
        # unique data
        data = data[0]
        tagsused = genretags.add(dapr.name.lower(), variant, data['tags'])
        LOG.info("%8s %6s search found %2d of %2d tags for '%s'%s", dapr.name,
                 variant, tagsused, min(99, len(data['tags'])), sstr, cachemsg)
        if variant == 'artist' and 'mbid' in data and len(sdata['artist']) == 1:
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
    # filter by title
    title = sdata['artist'][0][0]
    if variant == 'album':
        if not title:
            title = 'various' if source == 'discogs' else 'various artists'
        title += ' - ' + sdata['album']
    title = searchstr(title)
    for i in range(5):
        tmp = [d for d in data if 'title' not in d or difflib.
               SequenceMatcher(None, title, d['title'].lower())
               .ratio() >= (10 - i) * 0.1]
        if tmp:
            data = tmp
            break
    if len(data) == 1:
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
    print("Multiple %s results from %s, which is it?" % (variant, source))
    for i in range(len(data)):
        print("#%2d: %s" % (i + 1, data[i]['info']))
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
    for pat in [r'\(.*\)', r'\[.*\]', '{.*}', "- .* -", "'.*'", '".*"',
                ' (- )?(album|single|ep|(official )?remix(es)?|soundtrack)$',
                r'(ft|feat(\.|uring)?) .*', r'vol(\.|ume)? ', ' and ', 'the ',
                ' ost', '[!?/&:,.]', ' +']:
        str_ = re.sub(pat, ' ', str_, 0, re.I)
    return str_.strip().lower()

def print_stats(stats):
    '''Prints out some statistics.'''
    print("\nTime elapsed: %s"
          % datetime.timedelta(seconds=time.time() - stats['starttime']))
    tags = stats['genres']
    if tags:
        tagout = sorted(tags.items(), key=lambda (k, v): (v, k), reverse=1)
        tagout = gt.GenreTags.tagprintstr(tagout, "%5d %-19s")
        print("\n%d different tags used this often:\n%s" % (len(tags), tagout))
    fldrs = stats['foldernogenres']
    if fldrs:
        print("\n%d albums with no genre tags found:\n%s"
              % (len(fldrs), '\n'.join(sorted(fldrs))))
    fldrs = stats['foldererrors']
    if fldrs:
        print("\n%d albums with errors:" % len(fldrs))
        for error in set(fldrs.values()):
            fldrs = ["%s" % k for k, v in sorted(fldrs.items()) if v == error]
            print("'%s':\n%s" % (error, '\n'.join(fldrs)))

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
    conf = get_conf()
    validate(args, conf)

    hdlr = logging.StreamHandler(sys.stdout)
    hdlr.setLevel(logging.INFO if args.verbose else logging.WARN)
    LOG.setLevel(logging.INFO if args.verbose else logging.WARN)
    LOG.addHandler(hdlr)

    stats = {'starttime': time.time(),
             'genres': defaultdict(int),
             'foldererrors': {},
             'foldernogenres': []}

    folders = mf.find_music_folders(args.path)
    if not folders:
        return

    genretags = gt.GenreTags(conf)
    cache = Cache(args.no_cache, conf.getint('wlg', 'cache_timeout'))
    dps = dp.get_daprs(conf)

    try:  # main loop
        for i, folder in enumerate(folders, start=1):
            # save cache periodically
            if time.time() - cache.time > 600:
                cache.save()
            # print progress bar
            print("\n(%2d/%d) [" % (i, len(folders)), end='')
            for j in range(60):
                print('#' if j < int(i / len(folders) * 60) else '-', end='')
            print("] %2.0f%%" % int(i / len(folders) * 100))
            # handle folders
            try:
                genres = handle_folder(args, dps, cache, genretags, folder)
                if not genres:
                    stats['foldernogenres'].append(folder[0])
                    continue
                # add genres to stats
                for tag in genres:
                    stats['genres'][tag] += 1
            except mf.AlbumError as err:
                print(err.message)
                stats['foldererrors'].update({folder[0]: err.message})
        print("\n...all done!")
    except KeyboardInterrupt:
        print()
    print_stats(stats)
