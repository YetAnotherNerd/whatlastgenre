#!/usr/bin/env python
'''
TODO:
 * add tag filters for labels, countries
 * merge tags before adding (to enable bonus for multiple hits)
 * mbrainz, lastfm interactivity
 * make use of mbids (read&use, find&write)
'''

from __future__ import division
from ConfigParser import SafeConfigParser
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import namedtuple, defaultdict
from difflib import get_close_matches, SequenceMatcher
from musicbrainzngs.musicbrainz import ResponseError, set_useragent, \
    search_artists, get_artist_by_id, search_release_groups, get_release_group_by_id
from requests.exceptions import HTTPError, ConnectionError
import datetime
import json
import mutagen
import operator
import os
import pickle
import re
import requests
import string
import sys
import time

# Constant Score Multipliers
SM_WHATCD = 1.5
SM_LASTFM = 0.7
SM_MBRAIN = 0.8
SM_DISCOG = 1.0

VERSION = "0.1.1"  # just to see what was used in log files


class Album:
    def __init__(self, path, filetype, genretags, tagrelease, tagmbids):
        self.path = path
        self.filetype = filetype.lower()
        self.tracks = [track for track in os.listdir(path)
                       if track.lower().endswith('.' + filetype)]
        self.tags = genretags
        self.tagrelease = tagrelease
        self.tagmbids = tagmbids
        self.artist = self.aartist = self.album = None
        self.type = self.year = None
        self.va = False
        self.mbids = namedtuple('mbids', 'artist aartist album')
        self.__load_metadata()

    def __get_tag(self, meta, tag):
        if tag[:11] == 'musicbrainz' and isinstance(meta, mutagen.flac.FLAC):
            tag = tag.upper()
        try:
            return meta[tag][0].encode('ascii', 'ignore')
        except (KeyError, UnicodeEncodeError):
            return None

    def __load_metadata(self):
        try:
            meta = os.path.join(self.path, self.tracks[0])
            meta = mutagen.File(meta, easy=True)
            self.artist = self.__get_tag(meta, 'artist')
            self.aartist = self.__get_tag(meta, 'aartist')
            self.album = self.__get_tag(meta, 'album')
            if not self.album:
                raise AlbumError("Error loading album metadata.")
            try:
                self.year = int(self.__get_tag(meta, 'date')[:4])
            except (TypeError, ValueError):
                pass
            self.mbids.artist = self.__get_tag(meta, 'musicbrainz_artistid')
            self.mbids.aartist = self.__get_tag(meta,
                                                'musicbrainz_albumartistid')
            self.mbids.album = self.__get_tag(meta, 'musicbrainz_albumid')

            for track in self.tracks:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
                if not meta:
                    raise AlbumError("Error loading metadata for %s." % track)
                if SequenceMatcher(None, self.album,
                        self.__get_tag(meta, 'album')).ratio() < 0.9:
                    raise AlbumError("Not all tracks have the same album-tag!")
                if (not self.va and SequenceMatcher(None, self.artist,
                        self.__get_tag(meta, 'artist')).ratio() < 0.9):
                    self.artist = None
                    self.va = True
                if (self.aartist and SequenceMatcher(None, self.aartist,
                        self.__get_tag(meta, 'aartist')).ratio() < 0.9):
                    self.aartist = None
        except TypeError, e:
            raise AlbumError("Error loading album metadata: %s" % e.message)

    def get_artist(self):
        if self.artist and not self.va:
            return self.artist
        elif self.aartist:
            return self.aartist
        return ''

    def save(self):
        print "Saving metadata..."
        tags = self.tags.get()
        try:
            for track in self.tracks:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
                if (self.tagrelease and self.type
                        and self.filetype in ['flac', 'ogg']):
                    meta['releasetype'] = self.type
                if tags:
                    meta['genre'] = tags
                meta.save()
        except IOError, e:  # FIXME: replace with real exceptions
            raise AlbumError("Error saving album metadata: " + e.message)


class AlbumError(Exception):
    pass


class GenreTags:
    def __init__(self, limit, score_up, score_down, wlist, blist, ucase):
        self.tags = {}
        self.limit = limit
        self.whitelist = wlist
        self.blacklist = blist
        self.uppercase = ucase
        self.score_up = score_up
        self.score_down = score_down
        # add some basic genre tags (from id3)
        tags = ['Acapella', 'Acid', 'Acid Jazz', 'Acid Punk', 'Acoustic',
                'Alternative', 'Alternative Rock', 'Ambient', 'Anime',
                'Avantgarde', 'Ballad', 'Bass', 'Beats', 'Bebob', 'Big Band',
                'Black Metal', 'Bluegrass', 'Blues', 'Booty Bass', 'BritPop',
                'Cabaret', 'Celtic', 'Chamber Music', 'Chanson', 'Chorus',
                'Christian', 'Classic Rock', 'Classical', 'Club', 'Comedy',
                'Country', 'Crossover', 'Cult', 'Dance', 'Dance Hall',
                'Darkwave', 'Death Metal', 'Disco', 'Dream', 'Drum & Bass',
                'Easy Listening', 'Electronic', 'Ethnic', 'Euro-House',
                'Euro-Techno', 'Euro-Dance', 'Fast Fusion', 'Folk',
                'Folk-Rock', 'Freestyle', 'Funk', 'Fusion', 'Gangsta', 'Goa',
                'Gospel', 'Gothic', 'Gothic Rock', 'Grunge', 'Hard Rock',
                'Hardcore', 'Heavy Metal', 'Hip-Hop', 'House', 'Indie',
                'Industrial', 'Instrumental', 'Jazz', 'Jazz+Funk', 'Jungle',
                'Latin', 'Lo-Fi', 'Meditative', 'Metal', 'Musical', 'New Age',
                'New Wave', 'Noise', 'Oldies', 'Opera', 'Other', 'Pop',
                'Progressive Rock', 'Psychedelic', 'Psychedelic Rock', 'Punk',
                'Punk Rock', 'R&B', 'Rap', 'Rave', 'Reggae', 'Retro',
                'Revival', 'Rhythmic Soul', 'Rock', 'Rock & Roll', 'Salsa',
                'Samba', 'Ska', 'Slow Jam', 'Slow Rock', 'Sonata', 'Soul',
                'Soundtrack', 'Southern Rock', 'Space', 'Speech', 'Swing',
                'Symphonic Rock', 'Symphony', 'Synthpop', 'Tango', 'Techno',
                'Thrash Metal', 'Trance', 'Tribal', 'Trip-Hop', 'Vocal']
        self.addlist(tags, 0.05)
        # TODO: add more
        tags = ['Chillout', 'Downtempo', 'Electro-Swing', 'Female Vocalist',
                'Future Jazz', 'German', 'German Hip-Hop', 'Jazz-Hop',
                'Tech-House']
        self.addlist(tags, 0.1)
        self.addlist(score_up, 0.2)
        self.addlist(score_down, -0.2)

    def __replace_tag(self, tag):
        rplc = {'deutsch': 'german', 'dnb': 'd&b', 'france': 'french',
                'hip hop': 'hip-hop', 'hiphop': 'hip-hop', 'lo fi': 'lo-fi',
                'ost': 'soundtrack', 'prog ': 'progressive',
                'rhythm and blues': 'r&b', 'rhythm & blues': 'r&b',
                'rnb': 'r&b', 'scifi': 'science fiction',
                'triphop': 'trip-hop', 'trip hop': 'trip-hop',
                'tv soundtrack': 'soundtrack', ' and ': ' & '}
        for (a, b) in rplc.items():
            if a in tag.lower():
                return string.replace(tag.lower(), a, b)
        return tag

    def add(self, name, score):
        if not (len(name) in range(2, 21)
                and re.match('([0-9]{2}){1,2}s?', name) is None
                and score > 0.05):
            return
        name = name.encode('ascii', 'ignore')
        name = self.__replace_tag(name).title()
        if name.upper() in self.uppercase:
            name = string.upper(name)
        #out.verbose("  %s (%.3f)" % (name, score))
        # don't change the cutoff, add replaces instead
        found = get_close_matches(name, self.tags.keys(), 1, 0.875)  # 0.858
        if found:
            if found[0] in self.score_up:
                score = score * 1.2
            elif found[0] in self.score_down:
                score = score * 0.8
            # TODO: bonus only if merged before
            self.tags[found[0]] = (self.tags[found[0]] + score) * 1.05
            #out.verbose("  %s is the same tag as %s (%.3f)" %
            #            (name, found[0], self.tags[found[0]]))
        else:
            self.tags.update({name: score})

    def addlist(self, tags, score):
        for tag in tags:
            self.add(tag, score)

    def addlist_nocount(self, tags, multi):
        self.addlist(tags, 0.85 ** (len(tags) - 1) * multi)

    def addlist_count(self, tags, multi):
        if tags:
            top = max(1, max(tags.iteritems(), key=operator.itemgetter(1))[1])
            for name, count in tags.iteritems():
                if count > top * 0.1:
                    self.add(name, count / top * multi)

    def __getgood(self, minscore=0.4):
        return {name: score for name, score in self.tags.iteritems()
                if score > minscore}

    def get(self):
        tags = self.__getgood(0.69)
        if self.whitelist:
            tags = (tag for tag in tags if tag in self.whitelist)
        elif self.blacklist:
            tags = (tag for tag in tags if tag not in self.blacklist)
        tags = sorted(tags, key=self.tags.get, reverse=True)
        return tags[:self.limit]

    def listgood(self):
        tags = ["%s: %.2f" % (name, score) for name, score in
                sorted(self.__getgood().iteritems(),
                       key=lambda (k, v): (v, k), reverse=True)]
        if tags:
            return "Good tags: %s" % ', '.join(map(str, tags))


class DataProvider:
    def __init__(self, name, session, multi, interactive):
        self.name = name
        self.session = session
        self.multi = multi
        self.interactive = interactive
        self.tagmethods = None

    def _interactive(self, data, form, cont):
        print "Multiple possibilities found, please choose the right one:"
        for i in range(len(data)):
            print "#%2d:" % (i + 1),
            print form % cont(data[i])
        while True:
            try:
                c = int(raw_input("Please Choose # [1-%d] (0 to skip): "
                                  % len(data)))
            except ValueError:
                c = None
            except EOFError:
                c = 0
                print
            if c in range(len(data) + 1):
                break
        return [data[c - 1]] if c else None

    def _searchstr(self, s):
        regex = ['[^\w]', '(volume |vol | and )', '[\(\[\{\)\]\}]', ' +']
        s = s.lower()
        for r in regex:
            r = re.compile(r)
            s = r.sub(' ', s)
        return s.strip()

    def get_tags(self, a):
        for m in self.tagmethods:
            try:
                m(a)
            except DataProviderError, e:
                out.verbose("  " + e.message)


class DataProviderError(Exception):
    pass


class WhatCD(DataProvider):
    def __init__(self, session, multi, interactive, username, password):
        DataProvider.__init__(self, "What.CD", session, multi, interactive)
        self.session.post('https://what.cd/login.php',
                          {'username': username, 'password': password})
        self.last_request = time.time()
        self.rate_limit = 2.0  # min. seconds between requests
        self.tagmethods = [self.__get_tags_artist, self.__get_tags_album]

    def __query(self, action, **args):
        params = {'action': action}
        params.update(args)
        esec = time.time() - self.last_request
        if out.beverbose and esec < self.rate_limit:
            out.verbose("  Waiting %.2f seconds for next request." % esec)
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(0.1)
        try:
            r = self.session.get('https://what.cd/ajax.php', params=params)
            self.last_request = time.time()
            j = json.loads(r.content)
            if j['status'] != 'success':
                raise DataProviderError("unsuccessful response (maybe: " \
                                    "artist not found - api inconsistencies)")
            return j['response']
        except (ConnectionError, HTTPError, KeyError, ValueError):
            raise DataProviderError("error while requesting")

    def __filter_tags(self, tags):  # (waiting on getting all tags with counts)
        badtags = ['freely.available', 'staff.picks', 'vanity.house']
        if tags and isinstance(tags[0], dict):
            return {string.replace(tag['name'], '.', ' '): int(tag['count'])
                    for tag in tags if tag['name'] not in badtags}
        return [string.replace(tag, '.', ' ') for tag in tags
                if tag not in badtags]

    def __get_tags_artist(self, a):
        if a.va:
            return
        try:
            data = self.__query('artist', id=0,
                                artistname=self._searchstr(a.get_artist()))
            a.tags.addlist_count(self.__filter_tags(data['tags']), self.multi)
        except (TypeError, KeyError):
            raise DataProviderError("No tags for artist found.")

    def __get_tags_album(self, a):
        try:
            data = self.__query('browse', searchstr=self._searchstr(
                                            a.get_artist() + ' ' + a.album),
                                            **{'filter_cat[1]': 1})['results']
            if len(data) > 1 and a.year:
                try:
                    data = [d for d in data
                            if abs(int(d['groupYear']) - a.year) <= 2]
                except (KeyError, ValueError):
                    pass
            if len(data) > 1:
                if self.interactive:
                    data = self._interactive(data, "%s - %s [%s] [%s]",
                                 lambda x: (x['artist'], x['groupName'],
                                            x['groupYear'], x['releaseType']))
                else:
                    raise DataProviderError("Too many (%d) album results " \
                                            "(use --interactive)." % len(data))
            if data and len(data) == 1:
                a.tags.addlist_nocount(self.__filter_tags(data[0]['tags']),
                                       self.multi)
                a.type = data[0]['releaseType']
            else:
                raise DataProviderError("No tags for album found.")
        except KeyError:
            raise DataProviderError("Error reading returned data.")


class LastFM(DataProvider):
    def __init__(self, session, multi, interactive, apikey):
        DataProvider.__init__(self, "Last.FM", session, multi, interactive)
        self.apikey = apikey
        self.tagmethods = [self.__get_tags_artist, self.__get_tags_album]

    def __query(self, method, **args):
        params = {'api_key': self.apikey, 'format': 'json', 'method': method}
        params.update(args)
        try:
            r = self.session.get('http://ws.audioscrobbler.com/2.0/',
                                 params=params)
            j = json.loads(r.content)
            return j
        except (ConnectionError, HTTPError, KeyError, ValueError):
            raise DataProviderError("error while requesting")

    def __filter_tags(self, a, tags):
        # list of dict for multiple tags; single dict for just one tag
        if not isinstance(tags, list):
            tags = [tags]
        # be aware of fuzzy matching below when adding bad tags here
        badtags = [a.get_artist().lower(), a.album.lower(), 'albums i own',
                   'amazing', 'awesome', 'cool', 'epic', 'favorite albums',
                   'favorites', 'good', 'love', 'seen live', 'sexy']
        badtags += ['drjazzmrfunkmusic', 'television']
        return {tag['name']: int(tag['count']) for tag in tags if
                not get_close_matches(tag['name'].lower(), badtags, 1)
                and int(tag['count']) > 2}

    def __get_tags_artist(self, a):
        if a.va:
            return
        try:
            data = self.__query('artist.gettoptags',
                                artist=self._searchstr(a.get_artist()))
            a.tags.addlist_count(self.__filter_tags(a, data['toptags']['tag']),
                                 self.multi)
        except KeyError:
            raise DataProviderError("No tags for artist found.")

    def __get_tags_album(self, a):
        try:
            data = self.__query('album.gettoptags',
                        album=self._searchstr(a.album),
                        artist=self._searchstr('Various Artists'
                                               if a.va else a.get_artist()))
            a.tags.addlist_count(self.__filter_tags(a, data['toptags']['tag']),
                                 self.multi)
        except KeyError:
            raise DataProviderError("No tags for album found.")


class MusicBrainz(DataProvider):
    def __init__(self, session, multi, interactive):
        DataProvider.__init__(self, "MusicBrainz", session, multi, interactive)
        set_useragent("whatlastgenre", VERSION)
        self.tagmethods = [self.__get_tags_artist, self.__get_tags_album]

    def __add_tags(self, a, tags):
        thetags = {}
        for tag in tags:
            thetags.update({tag['name']: int(tag['count'])})
        a.tags.addlist_count(thetags, self.multi)

    def __get_tags_artist(self, a):
        if a.va:
            return
        try:
            r = search_artists(artist=self._searchstr(a.get_artist()), limit=1)
            r = get_artist_by_id(r['artist-list'][0]['id'], includes=['tags'])
            self.__add_tags(a, r['artist']['tag-list'])
        except ResponseError, e:
            raise DataProviderError("response error: %s" % e.cause)
        except (IndexError, KeyError):
            raise DataProviderError("No tags for artist found.")

    def __get_tags_album(self, a):
        try:
            if a.va:
                r = search_release_groups(limit=1,
                                        release=self._searchstr(a.album))
            else:
                r = search_release_groups(limit=1,
                                        artist=self._searchstr(a.get_artist()),
                                        release=self._searchstr(a.album))
            r = get_release_group_by_id(r['release-group-list'][0]['id'],
                                        includes=['tags'])
            self.__add_tags(a, r['release-group']['tag-list'])
        except ResponseError, e:
            raise DataProviderError("response error: %s" % e.cause)
        except (IndexError, KeyError):
            raise DataProviderError("No tags for album found.")


class Discogs(DataProvider):
    def __init__(self, session, multi, interactive):
        DataProvider.__init__(self, "Discogs", session, multi, interactive)
        self.interactive = interactive
        self.tagmethods = [self.__get_tags]

    def __query(self, thetype, **args):
        params = {'type': thetype}
        params.update(args)
        try:
            r = self.session.get('http://api.discogs.com/database/search',
                                 params=params)
            j = json.loads(r.content)
            return j['results']
        except (ConnectionError, HTTPError, KeyError, ValueError):
            raise DataProviderError("error while requesting")

    def __get_tags(self, a):
        try:
            data = self.__query('master',
                                release_title=self._searchstr(a.album))
            if len(data) > 1:
                try:
                    data = [d for d in data if a.get_artist() in d['title']]
                except (TypeError, KeyError, ValueError):
                    pass
            if len(data) > 1 and a.year:
                try:
                    data = [d for d in data
                            if abs(int(d['year']) - a.year) <= 2]
                except (KeyError, ValueError):
                    pass
            if len(data) > 1:
                if self.interactive:
                    data = self._interactive(data, "%s [%s] [#%s]",
                                    lambda x: (x['title'], x['year'], x['id']))
                else:
                    raise DataProviderError("Too many (%d) album results " \
                                            "(use --interactive)." % len(data))
            if (data and len(data) == 1
                and ('style' in data[0] or 'genre' in data[0])):
                tags = []
                if 'style' in data[0]:
                    tags = tags + data[0]['style']
                if 'genre' in data[0]:
                    tags = tags + data[0]['genre']
                a.tags.addlist_nocount(tags, self.multi)
            else:
                raise DataProviderError("No tags for album found.")
        except KeyError:
            raise DataProviderError("Error reading returned data.")


class Stats:
    def __init__(self):
        self.__tags = defaultdict(int)
        self.__starttime = time.time()

    def add(self, tags):
        for tag in tags:
            self.__tags[tag] += 1

    def printstats(self):
        tags = []
        for tag, num in sorted(self.__tags.iteritems(),
                               key=lambda (k, v): (v, k), reverse=True):
            tags.append("%s: %d" % (tag, num))
        print "Tag statistics: %s" % ', '.join(map(str, tags))
        print "Time elapsed: %s" % \
              str(datetime.timedelta(seconds=time.time() - self.__starttime))


class Out:
    def __init__(self, verbose=False, colors=False):
        self.beverbose = verbose
        self.colors = colors

    def verbose(self, s):
        if self.beverbose and s:
            if self.colors:
                print "\033[0;33m%s\033[0;m" % s
            else:
                print s

    def info(self, s):
        if self.colors:
            print "\033[0;36m%s\033[0;m" % s
        else:
            print s

    def warning(self, s):
        if self.colors:
            print "\033[1;35mWARNING:\033[0;35m %s\033[0;m" % s
        else:
            print "WARNING: %s" % s

    def error(self, s):
        if self.colors:
            print "\033[1;31mERROR:\033[0;31m %s\033[0;m" % s
        else:
            print "ERROR: %s" % s

    def success(self, s):
        if self.colors:
            print "\033[1;32mSUCCESS:\033[0;32m %s\033[0;m" % s
        else:
            print "SUCESS: %s" % s


def main():
    def get_arguments():
        ap = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter,
                            description='Improves genre metadata of audio ' \
                               'files based on tags from various music-sites.')
        ap.add_argument('path', nargs='+',
                        help='folder(s) to scan for albums')
        ap.add_argument('-v', '--verbose', action='store_true',
                        help='more detailed output')
        ap.add_argument('-n', '--dry-run', action='store_true',
                        help='don\'t save metadata')
        ap.add_argument('-i', '--interactive', action='store_true',
                        help='interactive mode')
        ap.add_argument('-r', '--tag-release', action='store_true',
                        help='tag release type (from what.cd)')
        ap.add_argument('-m', '--tag-mbids', action='store_true',
                        help='tag musicbrainz ids')
        ap.add_argument('-s', '--stats', action='store_true',
                        help='collect statistics')
        ap.add_argument('-b', '--use-colors', action='store_true',
                        help='colorful output')
        ap.add_argument('-c', '--use-cache', action='store_true',
                        help='cache processed albums')
        ap.add_argument('-l', '--tag-limit', metavar='N', type=int, default=4,
                        help='max. number of genre tags')
        ap.add_argument('--no-whatcd', action='store_true',
                        help='disable lookup on What.CD')
        ap.add_argument('--no-lastfm', action='store_true',
                        help='disable lookup on Last.FM')
        ap.add_argument('--no-mbrainz', action='store_true',
                        help='disable lookup on MusicBrainz')
        ap.add_argument('--no-discogs', action='store_true',
                        help='disable lookup on Discogs')
        ap.add_argument('--config',
                        default=os.path.expanduser('~/.whatlastgenre/config'),
                        help='location of the configuration file')
        ap.add_argument('--cache',
                        default=os.path.expanduser('~/.whatlastgenre/cache'),
                        help='location of the cache')
        return ap.parse_args()

    def get_configuration(configfile):

        def config_list(s):
            if s:
                return [i.strip() for i in s.split(',')]
            return []

        config = SafeConfigParser()
        try:
            open(configfile)
            config.read(configfile)
        except:
            if not os.path.exists(os.path.dirname(configfile)):
                os.makedirs(os.path.dirname(configfile))
            config.add_section('whatcd')
            config.set('whatcd', 'username', '')
            config.set('whatcd', 'password', '')
            config.add_section('genres')
            config.set('genres', 'whitelist', '')
            config.set('genres', 'blacklist', 'Live, Unknown')
            config.set('genres', 'uppercase', 'IDM, UK, US')
            config.set('genres', 'score_up', 'Soundtrack')
            config.set('genres', 'score_down',
                       'Electronic, Alternative, Indie, Other, Other')
            config.write(open(configfile, 'w'))
            print "Please edit the configuration file: %s" % configfile
            sys.exit(2)

        conf = namedtuple('conf', '')
        conf.whatcd_user = config.get('whatcd', 'username')
        conf.whatcd_pass = config.get('whatcd', 'password')
        conf.whitelist = config_list(config.get('genres', 'whitelist'))
        conf.blacklist = config_list(config.get('genres', 'blacklist'))
        conf.uppercase = config_list(config.get('genres', 'uppercase'))
        conf.score_up = config_list(config.get('genres', 'score_up'))
        conf.score_down = config_list(config.get('genres', 'score_down'))
        return conf

    def validate(args, conf):
        if not (conf.whatcd_user and conf.whatcd_pass):
            out.warning("No What.CD credentials specified. " \
                        "What.CD support disabled.")
            args.no_whatcd = True
        if (args.no_whatcd and args.no_lastfm and args.no_mbrainz
                and args.no_discogs):
            out.error("Where do you want to get your data from?")
            print "At least one source must be activated " \
                  "(multiple sources recommended)!"
            sys.exit()
        if args.no_whatcd and args.tag_release:
            out.warning("Can't tag release with What.CD support disabled. " \
                        "Release tagging disabled.")
            args.tag_release = False
        if args.no_mbrainz and args.tag_mbids:
            out.warning("Can't tag mbids with MusicBrainz support disabled. " \
                        "MBIDs tagging disabled.")
            args.tag_mbids = False
        if args.dry_run and args.use_cache:
            out.warning("Can't use cache in dry mode. Cache disabled.")
            args.use_cache = False

    def find_albums(paths):
        albums = {}
        for path in paths:
            for root, _, files in os.walk(path):
                for f in files:
                    ext = os.path.splitext(os.path.join(root, f))[1].lower()
                    if ext in [".flac", ".ogg", ".mp3"]:
                        albums.update({root: ext[1:]})
                        break
        return albums

    args = get_arguments()
    conf = get_configuration(args.config)

    out.beverbose = args.verbose
    out.colors = args.use_colors

    validate(args, conf)

    # DEVEL Helper
    #args.verbose = args.dry_run = args.stats = True
    #args.tag_release = args.interactive = True
    #args.path.append('/home/foo/nobackup/test')
    #args.path.append('/media/music/Alben/')
    #from random import choice
    #args.path.append(os.path.join(
    #    '/media/music/Alben', choice(os.listdir('/media/music/Alben'))))

    if args.use_cache:
        cache = set()
        try:
            cache = pickle.load(open(args.cache))
        except:
            pickle.dump(cache, open(args.cache, 'wb'))

    if args.stats:
        stats = Stats()

    dps = []
    session = requests.session()
    if not args.no_mbrainz:
        dps.append(MusicBrainz(session, SM_MBRAIN, args.interactive))
    if not args.no_lastfm:
        dps.append(LastFM(session, SM_LASTFM, args.interactive,
                          "54bee5593b60d0a5bf379cedcad79052"))
    if not args.no_whatcd:
        dps.append(WhatCD(session, SM_WHATCD, args.interactive,
                          conf.whatcd_user, conf.whatcd_pass))
    if not args.no_discogs:
        dps.append(Discogs(session, SM_DISCOG, args.interactive))

    albums = find_albums(args.path)
    print "Found %d folders with possible albums!" % len(albums)
    errors = []
    i = 0
    for album in albums:
        i = i + 1
        if args.use_cache and album in cache:
            out.info("Found %s (%d/%d) in cache, skipping..."
                     % (album, i, len(albums)))
            continue

        genretags = GenreTags(args.tag_limit, conf.score_up, conf.score_down,
                              conf.whitelist, conf.blacklist, conf.uppercase)

        print "\nGetting metadata for %s-album (%d/%d) in %s..." % \
              (albums[album], i, len(albums), album)
        try:
            album = Album(album, albums[album], genretags,
                          args.tag_release, args.tag_mbids)
        except AlbumError, e:
            out.error("Could not get album: " + e.message)
            errors.append(album)
            continue

        print "Getting tags for '%s - %s'..." % \
              (album.get_artist() if album.get_artist() else 'VA', album.album)
        for dp in dps:
            out.verbose(dp.name + "...")
            try:
                dp.get_tags(album)
            except DataProviderError:
                out.error("Could not get data from %s " + dp.name)
            else:
                out.verbose(album.tags.listgood())

        tags = album.tags.get()
        if tags:
            print "Genre tags: %s" % ', '.join(map(str, tags))
            if args.stats:
                stats.add(tags)
        else:
            print "No or not good enough tags found :-("

        if args.tag_release and album.type:
            print "Release type: %s" % album.type

        if not args.dry_run:
            try:
                album.save()
            except AlbumError, e:
                out.error("Could not save album: " + e.message)
                errors.append(album)
            else:
                if args.use_cache:
                    cache.add(album.path)  # save every time, in case of user abort
                    pickle.dump(cache, open(args.cache, 'wb'))

    print
    out.success("all done!\n")
    if args.stats:
        stats.printstats()
    if errors:
        errors.sort()
        print "\nAlbums with errors:\n%s" % '\n'.join(map(str, errors))


if __name__ == "__main__":
    print "whatlastgenre version %s\n" % VERSION
    out = Out()
    main()
