#!/usr/bin/env python
'''whatlastgenre
Improves genre metadata of audio files based on tags from various music sites.
http://github.com/YetAnotherNerd/whatlastgenre'''

from __future__ import division, print_function

import ConfigParser
from _collections import defaultdict
import argparse
import datetime
import difflib
import logging
import os
import re
import sys
import time

from wlg import __version__

import wlg.cache as ch
import wlg.dataprovider as dp
import wlg.genretag as gt
import wlg.mediafile as mf


LOG = logging.getLogger('whatlastgenre')


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
    args.add_argument('-c', '--cacheignore', action='store_true',
                      help='ignore cache hits')
    args.add_argument('-r', '--tag-release', action='store_true',
                      help='tag release type (from What)')
    args.add_argument('-m', '--tag-mbids', action='store_true',
                      help='tag musicbrainz ids')
    args.add_argument('-l', '--tag-limit', metavar='N', type=int, default=4,
                      help='max. number of genre tags')
    args.add_argument('--config',
                      default=os.path.expanduser('~/.whatlastgenre/config'),
                      help='location of the configuration file')
    args.add_argument('--cache',
                      default=os.path.expanduser('~/.whatlastgenre/cache'),
                      help='location of the cache file')
    args = args.parse_args()
    return args

def get_conf(configfile):
    '''Reads, maintains and writes the configuration file.'''
    # [section, option, default, required, [min, max]]
    conf = [['wlg', 'sources', 'whatcd, discogs, mbrainz, lastfm', 1, []],
            ['wlg', 'cache_timeout', '30', 1, [3, 90]],
            ['wlg', 'whatcduser', '', 0, []],
            ['wlg', 'whatcdpass', '', 0, []],
            ['genres', 'love', 'soundtrack', 0, []],
            ['genres', 'hate',
             'alternative, electronic, indie, pop, rock', 0, []],
            ['genres', 'blacklist', 'charts, male vocalist, other', 0, []],
            ['genres', 'filters',
             'instrument, label, location, name, year', 0, []],
            ['scores', 'src_whatcd', '1.66', 1, [0.3, 2.0]],
            ['scores', 'src_mbrainz', '1.00', 1, [0.3, 2.0]],
            ['scores', 'src_lastfm', '0.66', 1, [0.3, 2.0]],
            ['scores', 'src_discogs', '1.00', 1, [0.3, 2.0]],
            ['scores', 'src_idiomag', '1.00', 1, [0.3, 2.0]],
            ['scores', 'src_echonest', '1.00', 1, [0.3, 2.0]],
            ['scores', 'artist', '1.33', 1, [0.5, 2.0]],
            ['scores', 'various', '0.66', 1, [0.1, 1.0]],
            ['scores', 'splitup', '0.33', 1, [0, 1.0]]]
    if not os.path.exists(os.path.dirname(configfile)):
        os.makedirs(os.path.dirname(configfile))
    config = ConfigParser.SafeConfigParser()
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

def get_conf_list(conf, sec, opt):
    '''Gets a configuration string as list.'''
    return [x.strip() for x in conf.get(sec, opt).lower().split(',')
            if x.strip() != '']

def validate(args, conf):
    '''Validates args and conf.'''
    # sources
    sources = get_conf_list(conf, 'wlg', 'sources')
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
    album = mf.BunchOfTracks(folder[0], folder[1], folder[2])
    genretags.reset(album)
    sdata = {
        'releasetype': None,
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
        for track in [t for t in album.tracks if t.get_meta('artist')]:
            sdata['artist'].append((searchstr(track.get_meta('artist')),
                                    track.get_meta('musicbrainz_artistid')))
    # get data from dataproviders
    sdata = get_data(args, dps, cache, genretags, album, sdata)
    # set genres
    genres = genretags.get(len(sdata['artist']) > 1)[:args.tag_limit]
    if genres:
        print("Genres: %s" % ', '.join(genres))
        album.set_common_meta('genre', genres)
    else:
        print("No genres found :-(")
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
        album.save_metadata()
    return genres

def get_data(args, dps, cache, genretags, album, sdata):
    '''Gets all the data from all dps or from cache.'''
    tupels = [(0, 'album')]
    tupels += [(i, 'artist') for i in range(len(sdata['artist']))]
    tuples = [(i, v, d) for (i, v) in tupels for d in dps]
    for i, variant, dapr in tuples:
        cmsg = ''
        sstr = [sdata['artist'][i][0]]
        if variant == 'album':
            sstr.append(sdata['album'])
        sstr = ' '.join(sstr).strip()
        if not sstr:
            continue
        cached = cache.get(dapr.name, variant, sstr)
        if cached:
            cmsg = ' (cached)'
            data = cached['data']
        else:
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
                print("%8s %6s" % (dapr.name, err.message))
                continue
        if not data or (len(data) == 1 and not data[0].get('tags')):
            LOG.info("%8s %6s search found    no    tags for '%s'%s",
                     dapr.name, variant, sstr, cmsg)
            cache.set(dapr.name, variant, sstr, None)
            continue
        # filter if multiple results
        if len(data) > 1:
            data = filter_data(dapr.name, variant, data, album)
        # ask user if still multiple results
        if len(data) > 1 and args.interactive:
            data = interactive(dapr.name, variant, data)
        # save cache
        if not cached or len(cached['data']) > len(data):
            cache.set(dapr.name, variant, sstr, data)
        # still multiple results?
        if len(data) > 1:
            print("%8s %6s search found %2d ambiguous results for '%s' (use -i)"
                  "%s" % (dapr.name, variant, len(data), sstr, cmsg))
            continue
        # unique data
        data = data[0]
        tagsused = genretags.add_tags(dapr.name.lower(), variant, data['tags'])
        LOG.info("%8s %6s search found %2d of %2d tags for '%s'%s", dapr.name,
                 variant, tagsused, min(99, len(data['tags'])), sstr, cmsg)
        if variant == 'artist' and 'mbid' in data and len(sdata['artist']) == 1:
            sdata['mbids']['albumartistid'] = data['mbid']
        elif variant == 'album':
            if 'mbid' in data:
                sdata['mbids']['releasegroupid'] = data['mbid']
            if 'releasetype' in data:
                sdata['releasetype'] = genretags.format(data['releasetype'])
    return sdata

def filter_data(source, variant, data, bot):
    '''Prefilters data to reduce needed interactivity.'''
    if not data or len(data) == 1:
        return data
    source = source.lower()
    # filter by releasetype for whatcd
    releasetype = bot.get_common_meta('releasetype')
    if source == 'whatcd' and variant == 'album' and releasetype:
        data = [d for d in data if 'releasetype' not in d or
                d['releasetype'].lower() == releasetype.lower()]
    # filter by title
    title = bot.get_common_meta('albumartist')
    if variant == 'album':
        if not title:
            title = 'various' if source == 'discogs' else 'various artists'
        title += ' - ' + bot.get_common_meta('album')
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
    date = bot.get_common_meta('date')
    if variant == 'album' and date:
        for i in range(4):
            tmp = [d for d in data if not d.get('year') or
                   abs(int(d['year']) - int(date)) <= i]
            if tmp:
                data = tmp
                break
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
    genres = stats['genres']
    if genres:
        genres = ["%2d %s" % (v, k) for k, v in
                  sorted(genres.items(), key=lambda (k, v): (v, k), reverse=1)]
        print("\nTag statistics (%d): %s" % (len(genres), ', '.join(genres)))
    fldrs = stats['foldernogenres']
    if fldrs:
        print("\n%d albums with no genre tags found:\n%s"
              % (len(fldrs), '\n'.join(sorted(fldrs))))
    fldrs = stats['foldererrors']
    if fldrs:
        fldrs = ["%s \t(%s)" % (k, v) for k, v in sorted(fldrs.items())]
        print("\n%d albums with errors:\n%s" % (len(fldrs), '\n'.join(fldrs)))

def main():
    '''main function of whatlastgenre.'''
    print("whatlastgenre v%s\n" % __version__)
    args = get_args()
    conf = get_conf(args.config)
    validate(args, conf)

    hdlr = logging.StreamHandler(sys.stdout)
    hdlr.setLevel(logging.INFO if args.verbose else logging.WARN)
    LOG.setLevel(logging.INFO if args.verbose else logging.WARN)
    LOG.addHandler(hdlr)

    stats = {'starttime': time.time(),
             'genres': defaultdict(int),
             'foldererrors': {},
             'foldernogenres': []}
    genretags = gt.GenreTags(conf)
    folders = mf.find_music_folders(args.path)
    if not folders:
        return
    cache = ch.Cache(args.cache, args.cacheignore,
                     conf.getint('wlg', 'cache_timeout'))
    dps = dp.get_daprs(get_conf_list(conf, 'wlg', 'sources'),
                       [conf.get('wlg', 'whatcduser'),
                        conf.get('wlg', 'whatcdpass')])

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
            except mf.BunchOfTracksError as err:
                print(err.message)
                stats['foldererrors'].update({folder[0]: err.message})
        print("\n...all done!")
    except KeyboardInterrupt:
        pass
    print_stats(stats)
