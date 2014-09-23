#!/usr/bin/env python
'''whatlastgenre
Improves genre metadata of audio files based on tags from various music sites.
http://github.com/YetAnotherNerd/whatlastgenre'''

from __future__ import division, print_function

import ConfigParser
import StringIO
import argparse
from collections import defaultdict
import datetime
import difflib
import itertools
import logging
from math import floor, factorial
import os
import pkgutil
import re
import sys
import time

from wlg import __version__

import wlg.cache as ch
import wlg.dataprovider as dp
import wlg.mediafile as mf


LOG = logging.getLogger('whatlastgenre')


class GenreTags(object):
    '''Class for managing the genre tags.'''
    # TODO: rewrite this

    def __init__(self, conf):
        self.conf = conf
        self.tags = None
        self.filters = ['badtags', 'generic', 'album'] + \
                       get_conf_list(conf, 'genres', 'filters')
        self.conftags = {
            'blacklist': get_conf_list(conf, 'genres', 'blacklist'),
            'love': get_conf_list(conf, 'genres', 'love'),
            'hate': get_conf_list(conf, 'genres', 'hate')}
        # tags file parsing
        self.parser = ConfigParser.SafeConfigParser(allow_no_value=True)
        tagfp = StringIO.StringIO(pkgutil.get_data('wlg', 'tags.txt'))
        self.parser.readfp(tagfp)
        # tags file validation
        for sec in ['basictags', 'uppercase', 'splitpart', 'dontsplit',
                    'replaceme']:
            if not self.parser.has_section(sec):
                print("Got no [%s] from tag.txt file." % sec)
                exit()
        for sec in get_conf_list(conf, 'genres', 'filters') + ['badtags',
                                                               'generic']:
            if not (self.parser.has_section('filter_%s' % sec) or
                    self.parser.has_section('filter_%s_fuzzy' % sec)):
                print("The configured filter '%s' doesn't have a "
                      "[filter_%s[_fuzzy]] section in the tags.txt file."
                      % (sec, sec))
                exit()
        # set up matchlist
        self.matchlist = [self.parser.options('basictags') +
                          self.conftags['blacklist'] +
                          self.conftags['love'] +
                          self.conftags['hate']]
        # set up replaces
        self.replaces = {}
        for pattern, repl in self.parser.items("replaceme", True):
            self.replaces.update({pattern: repl})
        # compile filters and other regex
        self.regex = {}
        for sec in self.parser.sections():
            if not (sec.startswith('filter_') or
                    sec in ['splitpart', 'dontsplit', 'replaceme']):
                continue
            pat = '(%s)' % '|'.join(self.parser.options(sec))
            if sec.endswith('_fuzzy'):
                pat = '.*%s.*' % pat
                sec = sec[:-6]
            self.regex[sec] = re.compile(pat, re.I)

    def reset(self, bot):
        '''Resets the genre tags and album filter.'''
        self.tags = {'artist': defaultdict(float), 'album': defaultdict(float)}
        self.regex['filter_album'] = self.get_album_filter(bot)

    def add_tags(self, tags, source, part):
        '''Adds tags with or without counts to a given part, scores them
        while taking the source score multiplier into account.'''
        if not tags:
            return
        multi = self.conf.getfloat('scores', 'src_%s' % source)
        if isinstance(tags, dict):
            top = max(1, max(tags.values()))
            for name, count in tags.items():
                if count > top * .1:
                    self.add(part, name, count / top * multi)
        elif isinstance(tags, list):
            for name in tags:
                self.add(part, name, .85 ** (len(tags) - 1) * multi)

    def add(self, part, name, score):
        '''Adds a genre tag with a given score to a given part after doing
        all the replace, split, filter, etc. magic.'''
        name = name.encode('ascii', 'ignore').lower()
        # replace
        name = re.sub(r'([_/,;\.\+\*]| and )', '&', name, 0, re.I)
        name = re.sub(r'-', ' ', name)
        name = re.sub(r'[^a-z0-9 ]', '', name, 0, re.I)
        if self.regex['replaceme'].match(name):
            for pattern, repl in self.replaces.items():
                name = re.sub(pattern, repl, name, 0, re.I)
        name = re.sub(' +', ' ', name).strip()
        # split
        tags, pscore, keep = self.split(name, score)
        if tags:
            for tag in tags:
                self.add(part, tag, pscore)
            if not keep:
                return
            score *= self.conf.getfloat('scores', 'splitup')
        if len(name) not in range(3, 19) or score < 0.1:
            return
        # matching existing tag (don't change cutoff, add replaces instead)
        mli = [t.keys() for t in self.tags.values()] + self.matchlist
        match = difflib.get_close_matches(name, mli, 1, .8572)
        if match:
            name = match[0]
        # filter
        if name in self.conftags['blacklist']:
            return
        for fil in self.filters:
            if self.regex['filter_%s' % fil].match(name):
                return
        # score bonus
        if name in self.conftags['love']:
            score *= 2
        elif name in self.conftags['hate']:
            score *= 0.5
        # finally add it
        self.tags[part][name] += score

    def split(self, name, score):
        '''Splits a tag, modifies the score of the parts if appropriate
        and decided whether to keep the base tag or not.'''
        if self.regex['dontsplit'].match(name):
            return None, None, True
        if '&' in name:
            return name.split('&'), score, False
        if ' ' in name:
            split = name.split(' ')
            if len(split) > 2:  # length>2: split into all parts of length 2
                tags = []
                count = len(split)
                for i in range(count):
                    for j in range(i + 1, count):
                        tags.append(split[i].strip() + ' ' + split[j].strip())
                count = 0.5 * factorial(count) / factorial(count - 2)
                return tags, score / count, False
            elif any([self.regex['filter_instrument'].match(x) or
                      self.regex['filter_location'].match(x) or
                      self.regex['splitpart'].match(x) for x in split]):
                return split, score, any([self.regex['filter_generic'].match(x)
                                          for x in split])
        return None, None, True

    def get(self):
        '''Gets the tags after merging artist and album tags and formatting.'''
        tags = defaultdict(float)
        # merge artist and album tags
        for part, ptags in self.tags.items():
            toptags = ', '.join(["%s (%.2f)" % (self.format(k), v) for k, v in
                                 sorted(ptags.items(), key=lambda (k, v):
                                        (v, k), reverse=1)][:10])
            LOG.info("Best %s tags (%d): %s", part, len(ptags), toptags)
            if ptags:
                if part == 'artist':
                    mult = self.conf.getfloat('scores', 'artist')
                else:
                    mult = 1
                for tag, score in ptags.items():
                    tags[tag] += score * mult / max(ptags.values())
        # format and sort
        tags = {self.format(k): v for k, v in tags.items()}
        return sorted(tags, key=tags.get, reverse=True)

    def format(self, name):
        '''Formats a tag to correct case.'''
        split = name.split(' ')
        for i in range(len(split)):
            if len(split[i]) < 3 and split[i] != 'nu' or \
                    split[i] in self.parser.options('uppercase'):
                split[i] = split[i].upper()
            elif re.match('[0-9]{4}s', name, re.I):
                split[i] = split[i].lower()
            else:
                split[i] = split[i].title()
        return ' '.join(split)

    @classmethod
    def get_album_filter(cls, bot):
        ''' Returns a genre tag filter based on
        the metadata of a given bunch of tracks.'''
        badtags = []
        for tag in ['albumartist', 'album']:
            val = bot.get_common_meta(tag)
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
    conf = [['wlg', 'sources', 'whatcd, mbrainz, discogs, lastfm', 1, []],
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


def handle_folder(args, dps, cache, genretags, bot):
    '''Loads metadata, receives tags and saves an album.'''
    # TODO: shrink this method
    genretags.reset(bot)
    artistname = searchstr(bot.get_common_meta('albumartist'))
    albumname = searchstr(bot.get_common_meta('album'))
    mbids = {'artistid':
             bot.get_common_meta('musicbrainz_artistid'),
             'albumartistid':
             bot.get_common_meta('musicbrainz_albumartistid'),
             'releasegroupid':
             bot.get_common_meta('musicbrainz_releasegroupid'),
             'albumid':
             bot.get_common_meta('musicbrainz_albumid')}
    releasetype = None
    for variant, dapr in itertools.product(['artist', 'album'], dps):
        data = None
        cmsg = ''
        sstr = artistname + (albumname if variant == 'album' else '')
        cached = cache.get(dapr.name, variant, sstr)
        if cached:
            cmsg = ' (cached)'
            data = cached['data']
        else:
            try:
                if variant == 'artist':
                    # TODO: query for track artists if no common artist
                    if not artistname:
                        continue
                    data = dapr.get_artist_data(artistname, mbids)
                elif variant == 'album':
                    data = dapr.get_album_data(artistname, albumname, mbids)
            except RuntimeError:
                continue
            except dp.DataProviderError as err:
                print("%s %s" % (dapr.name, err.message))
                continue
        if not data:
            LOG.info("%7s %6s search found nothing for '%s'.%s",
                     dapr.name, variant, sstr, cmsg)
            cache.set(dapr.name, variant, sstr, None)
            continue
        # filter if multiple results
        if len(data) > 1:
            data = filter_data(dapr.name, variant, data, bot)
        # ask user if still multiple results
        if len(data) > 1 and args.interactive:
            data = interactive(dapr.name, variant, data)
        # save cache
        if not cached or len(cached['data']) > len(data):
            cache.set(dapr.name, variant, sstr, data)
        # still multiple results?
        if len(data) > 1:
            print("%7s %6s search found %2d (too many) results for '%s'. "
                  "(use -i)%s" % (dapr.name, variant, len(data), sstr, cmsg))
            continue
        # unique data
        data = data[0]
        LOG.info("%s %s search found %d tags.%s",
                 dapr.name, variant, len(data['tags']), cmsg)
        if 'tags' in data:
            genretags.add_tags(data['tags'], dapr.name.lower(), variant)
        if 'mbid' in data:
            if variant == 'artist':
                mbids['albumartistid'] = data['mbid']
            elif variant == 'album':
                mbids['releasegroupid'] = data['mbid']
        if variant == 'album' and 'releasetype' in data:
            releasetype = genretags.format(data['releasetype'])

    # set genres
    genres = genretags.get()[:args.tag_limit]
    if genres:
        print("Genres: %s" % ', '.join(genres))
        bot.set_common_meta('genre', genres)
    else:
        print("No genres found :-(")
    # set releasetype
    if args.tag_release and releasetype:
        print("RelType: %s" % releasetype)
        bot.set_common_meta('releasetype', releasetype)
    # set mbrainz ids
    if args.tag_mbids and mbids:
        LOG.info("MBIDs: %s", ', '.join(["%s=%s" % (k, v)
                                         for k, v in mbids.items()]))
        for mbid in mbids:
            bot.set_common_meta('musicbrainz_' + mbid, mbids[mbid])
    # save metadata
    if args.dry:
        print("DRY-RUN! Not saving metadata.")
    else:
        bot.save_metadata()
    return genres


def filter_data(source, variant, data, bot):
    '''Prefilters data to reduce needed interactivity.'''
    if not data or len(data) == 1:
        return data
    # filter by title
    title = bot.get_common_meta('albumartist')
    if variant == 'album':
        if not title:
            if source.lower() == 'discogs':
                title = 'various'
            else:
                title = 'various artists'
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
    if len(stats['genres']):
        genres = sorted(stats['genres'].items(), key=lambda (k, v): (v, k),
                        reverse=True)
        print("\nTag statistics (%d): %s"
              % (len(genres), ', '.join
                 (["%d %s" % (v, k) for k, v in genres])))
    if stats['foldernogenres']:
        print("\n%d albums with no genre tags found:\n%s"
              % (len(stats['foldernogenres']), '\n'.join
                 (sorted(stats['foldernogenres']))))
    if stats['foldererrors']:
        print("\n%d albums with errors:\n%s"
              % (len(stats['foldererrors']), '\n'.join
                 (["%s \t(%s)" % (k, v) for k, v in
                   sorted(stats['foldererrors'].items())])))

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
    genretags = GenreTags(conf)
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
                bot = mf.BunchOfTracks(folder[0], folder[1], folder[2])
                genres = handle_folder(args, dps, cache, genretags, bot)
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
