#!/usr/bin/env python
'''whatlastgenre
Improves genre metadata of audio files based on tags from various music sites.
http://github.com/YetAnotherNerd/whatlastgenre'''

from __future__ import division, print_function
from ConfigParser import SafeConfigParser
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import defaultdict
from datetime import timedelta
from difflib import get_close_matches, SequenceMatcher
from operator import itemgetter
from requests.exceptions import ConnectionError, HTTPError
from time import time, sleep
import musicbrainzngs.musicbrainz as mb
import mutagen
import os
import pickle
import re
import requests
import struct
import sys

__version__ = "0.1.19"


class GenreTags:
    '''Class for managing the genre tags'''

    def __init__(self, basetags, scores, filters):
        self.tags = defaultdict(float)
        self.basetags = basetags
        self.scores = scores
        # matchlist
        self.matchlist = []
        for taglist in ['basictags', 'filter_blacklist',
                        'userscore_up', 'userscore_down']:
            self.matchlist += self.basetags.get(taglist, [])
        self.regex = {}
        # filters and regex
        self.filters = ['generic', 'generic_fuzzy', 'blacklist'] + filters
        for fil in self.filters + ['dontsplit', 'splitpart']:
            if fil == 'year':
                pat = '([0-9]{2}){1,2}s?'
            elif fil == 'generic_fuzzy':
                pat = '.*(' + '|'.join(self.basetags['filter_' + fil]) + ').*'
            elif 'filter_' + fil in self.basetags:
                pat = '(' + '|'.join(self.basetags['filter_' + fil]) + ')'
            else:
                pat = '(' + '|'.join(self.basetags[fil]) + ')'
            self.regex[fil] = re.compile(pat, re.I)
        self.filters.append('album')

    def reset(self, albumfilter):
        '''Resets the genre tags.'''
        self.tags = defaultdict(float)
        self.regex['album'] = albumfilter

    def add_tags(self, tags, source):
        '''Adds tags with counts and scoring based on count-ratio
        or without count and scoring based on amount.'''
        if not tags:
            return
        # calculate multiplier
        if source in self.scores:
            multi = self.scores[source]
        else:
            multi = 1
        # add them
        if isinstance(tags, dict):
            top = max(1, max(tags.iteritems(), key=itemgetter(1))[1])
            for name, count in tags.iteritems():
                if count > top * .1:
                    self.add(name, count / top * multi)
        elif isinstance(tags, list):
            for name in tags:
                self.add(name, .85 ** (len(tags) - 1) * multi)

    def add(self, name, score):
        '''Adds a genre tag with a given score.'''
        name = name.encode('ascii', 'ignore').lower().strip()
        # filter by length
        if len(name) not in range(3, 21):
            return
        # replace
        name = re.sub(r'([_/,;\.\+\*]| and )', '&', name, 0, re.I)
        name = re.sub('-', ' ', name, 0, re.I).strip()
        for pat, rep in self.basetags['replace'].iteritems():
            name = re.sub(pat, rep, name, 0, re.I).strip()
        # split
        tags, keep = self.split(name)
        if len(tags) > 1:
            for tag in tags:
                self.add(tag, score)
            if not keep:
                return
            score *= self.scores['splitup']
        # filter
        for fil in self.filters:
            if self.regex[fil].match(name):
                return
        # matching existing tag (don't change cutoff, add replaces instead)
        match = get_close_matches(name, self.matchlist + self.tags.keys(),
                                  1, .8572)
        if match:
            name = match[0]
        # score bonus
        if name in self.basetags['userscore_up'] or \
                (name in self.basetags['userscore_down'] and score < 0):
            score *= 1 + self.scores['userset']
        elif name in self.basetags['userscore_down']:
            score *= 1 - self.scores['userset']
        # finally add it
        self.tags[name] += score

    def split(self, name):
        '''Split tag and return it parts and wheter to keep the base'''
        name = name.strip()
        if self.regex['dontsplit'].match(name):
            pass
        elif '&' in name:
            return name.split('&'), False
        elif ' ' in name:
            split = name.split(' ')
            for part in split:
                if (self.regex['splitpart'].match(part) or
                        self.regex['location'].match(part) or
                        self.regex['instrument'].match(part)):
                    for part in split:
                        if self.regex['generic'].match(part):
                            return [name], None
                    return split, True
        return [name], None

    def get(self, minscore=0, limit=0, scores=True):
        '''Gets the tags with minscore, limit and with or without scores'''
        tags = {self.format(k): v for k, v in self.tags.iteritems()
                if v > minscore}
        if scores:
            tags = sorted(tags.iteritems(), key=itemgetter(1), reverse=True)
        else:
            tags = sorted(tags, key=tags.get, reverse=True)
        return tags[:limit] if limit else tags

    def format(self, name):
        '''Formats a tag to correct case.'''
        split = name.split(' ')
        for i in range(len(split)):
            if len(split[i]) < 3 and split[i] != 'nu' or \
                    split[i] in self.basetags['uppercase']:
                split[i] = split[i].upper()
            elif re.match('[0-9]{4}s', name, re.I):
                split[i] = split[i].lower()
            elif split[i][0] in ['j', 'k'] and \
                    split[i][1:] in self.basetags['basictags']:
                split[i] = split[i][0].upper() + split[i][1:].title()
            else:
                split[i] = split[i].title()
        return ' '.join(split)


class Album:
    '''Class for managing albums.'''

    def __init__(self, path, ext, tracks):
        self.path = path
        self.ext = ext
        self.tracks = tracks
        self.filter = None
        self.meta = {}
        self.meta['is_va'] = False

    def load_metadata(self):
        '''Loads the album metadata.'''
        metadata = defaultdict(list)
        # load metadata from tracks
        for track in self.tracks:
            self.__load_track_metadata(track, metadata)
        # handle, understand and validate metadata
        self.__handle_metadata(metadata)
        # album metadata filter
        badtags = []
        for tag in ['artist', 'aartist', 'album']:
            if tag in self.meta:
                bts = [self.meta[tag].lower()]
                if tag in ['artist', 'aartist'] and ' ' in bts[0]:
                    bts += bts[0].split(' ')
                for badtag in bts:
                    for pat in [r'[\(\[{].*[\)\]}]', r'[^\w]', ' +']:
                        badtag = re.sub(pat, ' ', badtag, 0, re.I)
                    badtags.append(badtag)
        self.filter = re.compile('.*(' + '|'.join(badtags) + ').*', re.I)
        VPRINT("Metadata: %s" % self.meta)

    def __load_track_metadata(self, track, metadata):
        '''Loads metadata from a track.'''
        try:
            meta = mutagen.File(os.path.join(self.path, track), easy=True)
        except IOError as err:
            raise AlbumError("Error loading album: %s" % err.message)
        except struct.error:
            raise AlbumError("Error with mutagen while loading album!"
                             "Maybe upgrading mutagen helps.")
        for tag, tagname in \
                {'album': 'album', 'artist': 'artist',
                 'aartist': 'albumartist', 'year': 'date',
                 'mbidartist': 'musicbrainz_artistid',
                 'mbidaartist': 'musicbrainz_albumartistid',
                 'mbidrelgrp': 'musicbrainz_releasegroupid',
                 'mbidrelease': 'musicbrainz_albumid'}.iteritems():
            if self.ext in ['flac', 'ogg']:
                tagname = tagname.upper()
            elif self.ext == 'mp3' and tag == 'aartist':
                tagname = 'performer'
            try:
                value = meta[tagname][0]
                if tag == 'year':
                    value = int(value[:4])
            except (KeyError, ValueError, UnicodeEncodeError):
                continue
            metadata[tag].append(value)

    def __handle_metadata(self, metadata):
        '''Understands, validates and saves the metadata.'''
        for tag, tlist in metadata.iteritems():
            num = len(set(tlist))
            if num > 1 and tag in ['album', 'mbidrelease', 'mbidrelgrp']:
                raise AlbumError("Not all tracks have an equal %s-tag." % tag)
            if tag in ['artist', 'aartist']:
                if num > 1:
                    lcs = self.longest_common_substr(tlist)
                    if len(lcs) > 2 and lcs == tlist[0][:len(lcs)]:
                        tlist[0] = lcs
                        num = 1
                if num > 1 or VAREGEX.match(tlist[0]):
                    self.meta['is_va'] = True
                    self.meta['mbidartist'] = \
                        "89ad4ac3-39f7-470e-963a-56509c546377"
                    continue
            if num == 1:
                self.meta[tag] = tlist[0]
        if 'album' not in self.meta:
            raise AlbumError("No album tag - untagged?")
        if 'artist' in self.meta and 'aartist' in self.meta and \
                self.meta['artist'].lower() == self.meta['aartist'].lower():
            del self.meta['aartist']

    @classmethod
    def longest_common_substr(cls, data):
        '''Returns the longest common substr for a list of strings.'''
        substr = ''
        if len(data) > 1 and len(data[0]) > 0:
            for i in range(len(data[0])):
                for j in range(len(data[0]) - i + 1):
                    if j > len(substr) and all(data[0][i:i + j] in x
                                               for x in data):
                        substr = data[0][i:i + j]
        return substr

    def save_metadata(self, genres, args):
        '''Saves the metadata to the tracks.'''

        if args.dry:
            print("DRY-RUN! Not saving metadata.")
            return

        print("Saving metadata...")
        for track in self.tracks:
            try:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
                self.__set_meta(meta, 'genre', genres)
                if args.tag_release and 'releasetype' in self.meta:
                    self.__set_meta(meta, 'releasetype',
                                    self.meta['releasetype'])
                if args.tag_mbids:
                    for key, val in {'mbidartist': 'musicbrainz_artistid',
                            'mbidaartist': 'musicbrainz_albumartistid',
                            'mbidrelgrp': 'musicbrainz_releasegroupid',
                            'mbidrelease': 'musicbrainz_albumid'}.iteritems():
                        if key in self.meta:
                            self.__set_meta(meta, val, self.meta[key])
                meta.save()
            except IOError as err:
                raise AlbumError("Error saving album: %s" % err.message)

    def __set_meta(self, meta, key, value):
        '''Sets a given meta key to a given value.'''
        if self.ext in ['flac', 'ogg']:
            key = key.upper()
        elif self.ext in ['mp3', 'm4a']:
            if key == 'musicbrainz_releasegroupid':
                return
            elif key == 'releasetype':
                key = 'musicbrainz_albumtype'
        meta[key] = value


class AlbumError(Exception):
    '''If something went wrong while handling an Album.'''
    pass


class Cache:
    '''Class for caching of data returned by DataProviders.'''

    def __init__(self, cachefile, ignore=False, timeout=7):
        self.file = cachefile
        self.ignore = ignore
        self.timeout = 60 * 60 * 24 * timeout
        self.time = time()
        self.dirty = False
        self.cache = {}
        try:
            self.cache = pickle.load(open(self.file))
        except (IOError, EOFError):
            pickle.dump(self.cache, open(self.file, 'wb'))

    def __del__(self):
        self.save()

    @classmethod
    def __get_key(cls, dapr, what, meta):
        '''Helper method to get the key.'''
        key = dapr + '###'
        if what == 'album':
            key += (meta.get('artist') or meta.get('aartist') or 'VA') + '###'
        key += meta[what]
        for pat in [r'[^\w#]', ' +']:
            key = re.sub(pat, '', key, 0, re.I)
        return key.lower().strip()

    def get(self, dapr, what, meta):
        '''Gets cache data.'''
        if self.ignore:
            return None
        key = self.__get_key(dapr, what, meta)
        if key not in self.cache or \
                time() - self.cache[key]['time'] > self.timeout:
            return None
        return self.cache[key]

    def set(self, dapr, what, meta, data):
        '''Sets cache data.'''
        key = self.__get_key(dapr, what, meta)
        if data:
            keep = ['tags', 'mbid', 'releasetype']
            if len(data) > 1:
                keep.append('info')
            for dat in data:
                for k in [k for k in dat.keys() if k not in keep]:
                    del dat[k]
        self.cache[key] = {'time': time(), 'data': data}
        self.dirty = True

    def clean(self):
        '''Cleans up old data from the cache'''
        for key, val in self.cache.items():
            if time() - val['time'] > self.timeout:
                del self.cache[key]
                self.dirty = True
        self.save()

    def save(self):
        '''Saves the cache to disk.'''
        if self.dirty:
            print("\nSaving cache...\n")
            pickle.dump(self.cache, open(self.file, 'wb'))
            self.time = time()
            self.dirty = False


class DataProvider:
    '''Base class for DataProviders.'''

    def __init__(self, name, cache, session, interact):
        self.name = name
        self.cache = cache
        self.session = session
        self.interactive = interact

    def _jsonapiquery(self, url, params, sparams=None):
        '''Method for querying json-apis.'''
        if sparams:
            for key, val in sparams.iteritems():
                params.update({key: self.searchstr(val)})
        req = self.session.get(url, params=params)
        if (req.status_code != 200):
            raise DataProviderError("Request Error: %s" % req.content)
        return req.json()

    def _interactive(self, part, meta, data):
        '''Asks the user to choose from a list of possibilities.'''
        print("Multiple %s-results from %s, which is it?" % (part, self.name))
        VPRINT("Metadata: %s" % meta)
        for i in range(len(data)):
            print("#%2d:" % (i + 1), end=" ")
            print(data[i]['info'])
        while True:
            try:
                num = int(raw_input("Please Choose #[1-%d] (0 to skip): "
                                    % len(data)))
            except ValueError:
                num = None
            except EOFError:
                num = 0
                print()
            if num in range(len(data) + 1):
                break
        return [data[num - 1]] if num else data

    def _get_artistdata(self, meta, what):
        '''Get data from a DataProvider (this should be overridden by DPs)'''
        raise NotImplementedError()

    def _get_albumdata(self, meta):
        '''Get data from a DataProvider (this should be overridden by DPs)'''
        raise NotImplementedError()

    def get_data(self, part, meta):
        '''Getting data from DataProviders.'''
        # VPRINT("%s: %s search..." % (self.name, part))
        data = None
        cached = self.cache.get(self.name, part, meta)
        if cached:
            VPRINT("%s: %s search cached!" % (self.name, part))
            data = cached['data']
        else:
            try:
                if(part == 'album'):
                    data = self._get_albumdata(meta)
                if(part in ['artist', 'aartist']):
                    data = self._get_artistdata(meta, part)
            except (ConnectionError, HTTPError, DataProviderError,
                    mb.ResponseError, mb.NetworkError) as err:
                print("%s: %s" % (self.name, err.message or err.cause))
                return None
            if data and len(data) > 1:
                data = self.__filter_data(part, meta, data)
        if not data:
            print("%s: %s search found nothing." % (self.name, part))
            if not cached:
                self.cache.set(self.name, part, meta, None)
            return None
        if len(data) > 1 and self.interactive:
            data = self._interactive(part, meta, data)
        if len(data) > 1:
            print("%s: %s search returned too many results: %d (use "
                  "--interactive)" % (self.name, part, len(data)))
            if not cached:
                self.cache.set(self.name, part, meta, data)
            return None
        # unique data
        VPRINT("%s: %s search found %d tags"
               % (self.name, part, len(data[0]['tags'])))
        if not cached or len(cached['data']) > 1:
            self.cache.set(self.name, part, meta, data)
        return data[0]

    def __filter_data(self, what, meta, data):
        '''Filters data.'''
        # filter by title
        title = ''
        if what == 'album':
            if 'artist' in meta:
                title = meta['artist'] + ' - '
            elif 'aartist' in meta:
                title = meta['aartist'] + ' - '
            elif meta['is_va']:
                if isinstance(self, Discogs):
                    title = 'various - '
                else:
                    title = 'various artists - '
        title += meta[what]
        title = self.searchstr(title)
        for i in range(5):
            tmp = [d for d in data if SequenceMatcher(None, title,
                                d['title'].lower()).ratio() >= (10 - i) * 0.1]
            if tmp:
                data = tmp
                break
        # filter by year
        if what == 'album' and len(data) > 1 and 'year' in meta:
            for i in range(4):
                tmp = [d for d in data if not d.get('year') or
                       abs(int(d['year']) - meta['year']) <= i]
                if tmp:
                    data = tmp
                    break
        return data

    @classmethod
    def searchstr(cls, searchstr):
        '''Cleans up a string for use in searching.'''
        if not searchstr:
            return ''
        for pat in [r'\(.*\)', r'\[.*\]', '{.*}', ' - .* - ',
                    r' (vol(ume|\.)?|and) ', ': .*', ' +']:
            searchstr = re.sub(pat, ' ', searchstr, 0, re.I).strip()
        return searchstr.lower()


class DataProviderError(Exception):
    '''If something went wrong with DataProviders.'''
    pass


class WhatCD(DataProvider):
    '''Class for the DataProvider WhatCD'''

    def __init__(self, cache, session, interact, cred):
        DataProvider.__init__(self, "What.CD", cache, session, interact)
        self.cred = cred
        self.loggedin = False
        self.last_request = time()
        self.rate_limit = 2.0  # min. seconds between requests

    def __del__(self):
        # bug with new requests version
        # self.__login(out=True)
        pass

    def __login(self, out=False):
        '''Login or Logout from What'''
        if not self.loggedin and not out:
            self.session.post('https://what.cd/login.php',
                              {'username': self.cred[0],
                               'password': self.cred[1]})
            self.last_request = time()
            self.loggedin = True
        elif self.loggedin and out:
            authkey = self.__query({'action': 'index'}).get('authkey')
            self.session.get("https://what.cd/logout.php?auth=%s" % authkey)

    def __query(self, params, sparams=None):
        '''Query What.CD API'''
        self.__login()
        while time() - self.last_request < self.rate_limit:
            sleep(.1)
        data = self._jsonapiquery('https://what.cd/ajax.php', params, sparams)
        self.last_request = time()
        if not data or data.get('status') != 'success' or \
                'response' not in data:
            return None
        return data['response']

    def _get_artistdata(self, meta, what):
        '''Gets artist data.'''
        data = self.__query({'action': 'artist', 'id': 0},
                            {'artistname': meta[what]})
        if not data or not data.get('tags'):
            return None
        return [{'tags': {tag['name'].replace('.', ' '): int(tag['count'])
                          for tag in data['tags']}}]

    def _get_albumdata(self, meta):
        '''Gets album data.'''
        searchstr = meta['album'] + ' ' + (meta.get('artist') or
                                           meta.get('aartist', ''))
        data = self.__query({'action': 'browse', 'filter_cat[1]': 1},
                            {'searchstr': searchstr})
        if not data or not data.get('results'):
            return None
        return [{'info': "%s - %s (%s) [%s]: "
                 "https://what.cd/torrents.php?id=%s"
                 % (d['artist'], d['groupName'], d['groupYear'],
                    d['releaseType'], d['groupId']),
                 'title': d['artist'] + ' - ' + d['groupName'],
                 'releasetype': d['releaseType'],
                 'tags': [tag.replace('.', ' ') for tag in d['tags']],
                 'year': d['groupYear']} for d in data['results']]


class LastFM(DataProvider):
    '''Class for the DataProvider LastFM'''

    def __init__(self, cache, session, interact):
        DataProvider.__init__(self, "Last.FM", cache, session, interact)

    def __query(self, params, sparams=None):
        '''Query Last.FM API'''
        theparams = {'api_key': "54bee5593b60d0a5bf379cedcad79052",
                     'format': 'json'}
        theparams.update(params)
        data = self._jsonapiquery('http://ws.audioscrobbler.com/2.0/',
                                  theparams, sparams)
        if 'error' in data:
            return None
        return data

    def _get_artistdata(self, meta, what):
        '''Gets artist data.'''
        data = None
        if 'mbid' + what in meta:
            VPRINT("  Using %s-MBID: %s" % (what, meta['mbid' + what]))
            data = self.__query({'method': 'artist.gettoptags',
                                 'mbid': meta['mbid' + what]})
        if not data:
            data = self.__query({'method': 'artist.gettoptags'},
                                {'artist': meta[what]})
        return self.__handle_data(data)

    def _get_albumdata(self, meta):
        '''Gets album data.'''
        data = None
        for mbid in ['release', 'relgrp']:
            if 'mbid' + mbid in meta and not data:
                VPRINT("  Using %s-MBID: %s" % (mbid, meta['mbid' + mbid]))
                data = self.__query({'method': 'album.gettoptags',
                                     'mbid': meta['mbid' + mbid]}, {})
        if not data:
            data = self.__query({'method': 'album.gettoptags'},
                                {'album': meta['album'],
                                 'artist': meta.get('artist') or
                                 meta.get('aartist', 'Various Artists')})
        return self.__handle_data(data)

    @classmethod
    def __handle_data(cls, data):
        '''Handles the data from Last.FM ;)'''
        if not data or not data.get('toptags') or \
                not data['toptags'].get('tag'):
            return None
        tags = data['toptags']['tag']
        if not isinstance(tags, list):
            tags = [tags]
        return [{'tags': {tag['name']: int(tag['count'])
                          for tag in tags if int(tag['count']) > 2}}]


class MusicBrainz(DataProvider):
    '''Class for the DataProvider MusicBrainz'''

    def __init__(self, cache, interact):
        DataProvider.__init__(self, "MBrainz", cache, None, interact)
        mb.set_useragent("whatlastgenre", __version__,
                         "http://github.com/YetAnotherNerd/whatlastgenre")

    def _get_artistdata(self, meta, what):
        '''Get a/artist data from MusicBrainz'''
        data = None
        # search a/artist by mbid
        if 'mbid' + what in meta:
            VPRINT("  Using %s-MBID: %s" % (what, meta['mbid' + what]))
            req = mb.get_artist_by_id(meta['mbid' + what],
                                      includes=['tags'])
            if req and 'artist' in req:
                return [{'tags': {tag['name']: int(tag['count']) for tag in
                                  req['artist'].get('tag-list', [])},
                         'mbid': req['artist']['id']}]
            else:
                VPRINT("  %s not found, deleting invalid MBID" % what)
                del meta['mbid' + what]
        # search a/artist without mbid
        if not data:
            try:
                req = mb.search_artists(artist=self.searchstr(meta[what]))
            except ValueError:
                return None
            data = req.get('artist-list', [])
            if len(data) > 1:
                data = [d for d in data if SequenceMatcher
                        (None, meta[what].lower(),
                         d.get('name', '').lower()).ratio() > .8]
            if len(data) in range(2, 9):
                # filter by looking at artist's release-groups
                tmp = []
                for dat in data:
                    req = mb.get_artist_by_id(dat['id'],
                                        includes=['tags', 'release-groups'])
                    for rel in req['artist']['release-group-list']:
                        if SequenceMatcher(None, meta['album'].lower(),
                                        rel['title'].lower()).ratio() > .8:
                            tmp.append(dat)
                            break
                if tmp:
                    data = tmp
        return [{'info': "%s (%s) [%s] [%s-%s]: "
                 "http://musicbrainz.org/artist/%s"
                 % (x['name'], x.get('type'), x.get('country', ''),
                    x.get('life-span', {}).get('begin', '')[:4],
                    x.get('life-span', {}).get('end', '')[:4], x['id']),
                 'title': x.get('name', ''),
                 'tags': {tag['name']: int(tag['count']) for tag in
                          x.get('tag-list', [])},
                 'mbid': x['id']} for x in data]

    def _get_albumdata(self, meta):
        '''Get album data from MusicBrainz'''
        data = None
        # search release-group mbid by release mbid
        if 'mbidrelgrp' not in meta and 'mbidrelease' in meta:
            VPRINT("  Using release-MBID: %s" % meta["mbidrelease"])
            req = mb.get_release_by_id(meta["mbidrelease"],
                                       includes=['release-groups'])
            if req and 'release' in req and 'release-group' in req:
                meta['mbidrelgrp'] = req['release']['release-group']['id']
            else:
                VPRINT("  Release not found, deleting invalid MBID")
                del meta['mbidrelease']
        # search album by release-group mbid
        if 'mbidrelgrp' in meta:
            VPRINT("  Using relgrp-MBID: %s" % meta['mbidrelgrp'])
            req = mb.get_release_group_by_id(meta['mbidrelgrp'],
                                             includes=['tags'])
            if req and 'release-group' in req:
                data = [req['release-group']]
            else:
                VPRINT("  Rel-Grp not found, deleting invalid MBID")
                del meta['mbidrelgrp']
        # search album without release-group mbid
        if not data:
            try:
                req = mb.search_release_groups(
                        release=self.searchstr(meta['album']),
                        artist=self.searchstr(meta.get('artist') or
                                              meta.get('aartist', '')),
                        arid=meta.get('mbidartist') or
                        meta.get('mbidaartist', ''))
            except ValueError:
                return None
            data = req.get('release-group-list', [])
        return [{'info': "%s - %s [%s]: "
                 "http://musicbrainz.org/release-group/%s"
                 % (x.get('artist-credit-phrase'), x.get('title'),
                    x.get('type'), x['id']),
                 'title': x.get('artist-credit-phrase', '') + ' - '
                    + x.get('title', ''),
                 'tags': {tag['name']: int(tag['count']) for tag in
                          x.get('tag-list', [])},
                 'mbid': x['id']} for x in data]


class Discogs(DataProvider):
    '''Class for the DataProvider Discogs.'''

    def __init__(self, cache, session, interact):
        DataProvider.__init__(self, "Discogs", cache, session, interact)

    def _get_artistdata(self, meta, what):
        '''Gets artist data.'''
        pass

    def _get_albumdata(self, meta):
        '''Gets album data.'''
        searchstr = (meta.get('artist') or meta.get('aartist', '')) \
                        + ' ' + meta['album']
        data = self._jsonapiquery('http://api.discogs.com/database/search',
                                  {'type': 'master'}, {'q': searchstr})
        if not data or not data.get('results'):
            return None
        return [{'info': "%s (%s) [%s]: http://www.discogs.com/master/%s"
                 % (x.get('title'), x.get('year'),
                    ', '.join(x.get('format')), x['id']),
                 'title': x.get('title', ''),
                 'tags': x.get('style', []) + x.get('genre', []),
                 'year': x.get('year')} for x in data['results']]


class Idiomag(DataProvider):
    '''Class for the DataProvider Idomag.'''

    def __init__(self, cache, session, interact):
        DataProvider.__init__(self, "Idiomag", cache, session, interact)

    def __query(self, params):
        '''Query Idiomag API'''
        try:
            data = self._jsonapiquery(
                    'http://www.idiomag.com/api/artist/tags/json',
                    {'key': "77744b037d7b32a615d556aa279c26b5"}, params)
        except DataProviderError:
            return None
        if not data or not data.get('profile'):
            return None
        return data['profile']

    def _get_artistdata(self, meta, what):
        '''Gets artist data.'''
        data = self.__query({'artist': meta[what]})
        if not data:
            return None
        return [{'tags': {tag['name']: int(tag['value'] * 100)
                          for tag in data['tag']}}]

    def _get_albumdata(self, meta):
        '''Gets artist data.'''
        pass


class EchoNest(DataProvider):
    '''Class for the DataProvider EchoNest.'''

    def __init__(self, cache, session, interact):
        DataProvider.__init__(self, "EchoNest", cache, session, interact)

    def __query(self, params):
        '''Query EchoNest API'''
        data = self._jsonapiquery(
                'http://developer.echonest.com/api/v4/artist/search',
                {'api_key': "ZS0LNJH7V6ML8AHW3", 'format': 'json',
                 'bucket': 'genre', 'results': 1}, params)
        if not data or not data.get('response') or \
                'artists' not in data['response']:
            return None
        return data['response']['artists']

    def _get_artistdata(self, meta, what):
        '''Gets artist data.'''
        data = self.__query({'name': meta[what]})
        if not data:
            return None
        return [{'tags': [tag['name'] for tag in x['genres']]} for x in data]

    def _get_albumdata(self, meta):
        '''Gets artist data.'''
        pass


def get_arguments():
    '''Gets the cmdline arguments from ArgumentParser.'''
    argparse = ArgumentParser(
        formatter_class=ArgumentDefaultsHelpFormatter,
        description='Improves genre metadata of audio files based on tags '
                    'from various music sites.')
    argparse.add_argument(
        'path', nargs='+', help='folder(s) to scan for albums')
    argparse.add_argument(
        '-v', '--verbose', action='store_true', help='more detailed output')
    argparse.add_argument(
        '-n', '--dry', action='store_true', help='don\'t save metadata')
    argparse.add_argument(
        '-c', '--cacheignore', action='store_true', help='ignore cache hits')
    argparse.add_argument(
        '-i', '--interactive', action='store_true', help='interactive mode')
    argparse.add_argument(
        '-r', '--tag-release', action='store_true',
        help='tag release type (from What)')
    argparse.add_argument(
        '-m', '--tag-mbids', action='store_true', help='tag musicbrainz ids')
    argparse.add_argument(
        '-l', '--tag-limit', metavar='N', type=int, default=4,
        help='max. number of genre tags')
    argparse.add_argument(
        '--config', default=os.path.expanduser('~/.whatlastgenre/config'),
        help='location of the configuration file')
    argparse.add_argument(
        '--cache', default=os.path.expanduser('~/.whatlastgenre/cache'),
        help='location of the cache file')
    args = argparse.parse_args()
    if (args.verbose):
        global VPRINT
        VPRINT = print
    return args


def get_configuration(configfile):
    '''Reads and maintains the configuration file'''
    # [section, option, default, required]
    conf = [['wlg', 'sources',
             'whatcd, mbrainz, lastfm, discogs, idiomag, echonest', True],
            ['wlg', 'whatcduser', '', False],
            ['wlg', 'whatcdpass', '', False],
            ['wlg', 'cachetimeout', '7', True],
            ['genres', 'blacklist', 'charts, other, unknown', False],
            ['genres', 'score_up', 'soundtrack', False],
            ['genres', 'score_down', 'alternative, electronic, indie', False],
            ['genres', 'filters', 'instrument, label, location, year', False],
            ['scores', 'what.cd', '1.66', True],
            ['scores', 'last.fm', '0.66', True],
            ['scores', 'mbrainz', '1.00', True],
            ['scores', 'discogs', '1.00', True],
            ['scores', 'idiomag', '1.00', True],
            ['scores', 'echonest', '1.00', True],
            ['scores', 'splitup', '0.33', True],
            ['scores', 'userset', '0.66', True]]
    if not os.path.exists(os.path.dirname(configfile)):
        os.makedirs(os.path.dirname(configfile))
    config = SafeConfigParser()
    config.read(configfile)
    dirty = False
    # remove old options
    for sec in config.sections():
        if [x for x in conf if x[0] == sec]:
            for opt in config.options(sec):
                if not [x for x in conf if x[:2] == [sec, opt]]:
                    config.remove_option(sec, opt)
                    dirty = True
        else:
            config.remove_section(sec)
            dirty = True
    # add missing options
    for sec, opt, default, req in [x for x in conf]:
        if not config.has_option(sec, opt) or \
                req and config.get(sec, opt) == '':
            if not config.has_section(sec):
                config.add_section(sec)
            config.set(sec, opt, default)
            dirty = True
    if dirty:
        with open(configfile, 'wb') as conffile:
            config.write(conffile)
        print("Please edit your configuration file: %s" % configfile)
        exit()
    return config


def get_config_list(conf, sec, opt):
    '''Gets a list from a configuration string that should be a list.'''
    return [x.strip() for x in conf.get(sec, opt).split(',')]


def get_tagsfile(tagsfile):
    '''Reads the tagsfile and returns its contents.'''
    tagsfile = os.path.join(os.path.dirname(__file__), tagsfile)
    tags = {}
    section = None
    taglist = []
    with open(tagsfile, 'r') as tagfile:
        for line in tagfile:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            sectionmatch = re.match(r'^\[(.*)\]$', line)
            if sectionmatch:
                if section and taglist:
                    if section == 'replace':
                        replace = {}
                        for tag in taglist:
                            pat, repl, _ = tag.split('~~')
                            replace.update({pat: repl})
                        taglist = replace
                    tags.update({section: taglist})
                section = sectionmatch.group(1)
                taglist = []
            else:
                taglist.append(line)
    return tags


def validate(args, conf, tags):
    '''Validates argument and config settings and fixes them if necessary.'''
    sources = get_config_list(conf, 'wlg', 'sources')
    if not len(sources):
        print("Where do you want to get your data from?\nAt least one source "
              "must be activated (multiple sources recommended)!")
        exit()
    if 'whatcd' in sources and not (conf.get('wlg', 'whatcduser') and
                                    conf.get('wlg', 'whatcdpass')):
        print("No What.CD credentials specified. What.CD support disabled.\n")
        sources.remove('whatcd')
        conf.set('wlg', 'sources', ', '.join(sources))
    if 'whatcd' not in sources and args.tag_release:
        print("Can't tag release with What support disabled. "
              "Release tagging disabled.\n")
        args.tag_release = False
    if 'mbrainz' not in sources and args.tag_mbids:
        print("Can't tag MBIDs with MusicBrainz support disabled. "
              "MBIDs tagging disabled.\n")
        args.tag_mbids = False
    for tag in ['basictags', 'replace']:
        if tag not in tags or []:
            print("Got no [%s] from tag.txt file." % tag)
            exit()
    for filt in [f.strip() for f in conf.get('genres', 'filters').split(',')]:
        if filt != 'year' and 'filter_' + filt not in tags:
            print("The filter '%s' you set in your config doesn't have a "
                  "[filter_%s] section in the tags.txt file." % (filt, filt))
            exit()


def init_dataproviders(args, conf, cache):
    '''Initializes the DataProviders.'''
    dps = []
    session = requests.session()
    for dapr in get_config_list(conf, 'wlg', 'sources'):
        if dapr == 'mbrainz':
            dps.append(MusicBrainz(cache, args.interactive))
        elif dapr == 'whatcd':
            dps.append(WhatCD(cache, session, args.interactive,
                              [conf.get('wlg', 'whatcduser'),
                               conf.get('wlg', 'whatcdpass')]))
        elif dapr == 'lastfm':
            dps.append(LastFM(cache, session, args.interactive))
        elif dapr == 'discogs':
            dps.append(Discogs(cache, session, args.interactive))
        elif dapr == 'idiomag':
            dps.append(Idiomag(cache, session, args.interactive))
        elif dapr == 'echonest':
            dps.append(EchoNest(cache, session, args.interactive))
    return dps


def get_albums(paths):
    '''Scans paths for possible albumfolders.'''
    albums = []
    for path in paths:
        for root, _, files in os.walk(path):
            for afile in files:
                ext = os.path.splitext(afile)[1][1:].lower()
                if ext in ['flac', 'ogg', 'mp3', 'm4a']:
                    albums.append(Album(root, ext,
                                        [t for t in files if
                                         t.lower().endswith('.' + ext)]))
                    break
    print("Found %d album folders!" % len(albums))
    if len(albums) == 0:
        exit()
    return albums


def handle_album(album, dps, genretags, args):
    ''' Loads metadata, receives tags and saves an album.'''
    print("Loading metadata [%s]: %s"
          % (album.ext.upper(), album.path))
    album.load_metadata()
    genretags.reset(album.filter)
    print("Receiving tags%s: artist=%s, aartist=%s, album=%s, year=%s"
          % ((' [VA]' if album.meta['is_va'] else ''),
             album.meta.get('artist'), album.meta.get('aartist'),
             album.meta['album'], album.meta.get('year')))
    for dapr in dps:
        for part in ['artist', 'aartist', 'album']:
            if (part not in album.meta or
                part == 'album' and (isinstance(dapr, Idiomag) or
                                     isinstance(dapr, EchoNest)) or
                part in ['artist', 'aartist'] and isinstance(dapr, Discogs)):
                continue
            data = dapr.get_data(part, album.meta)
            if not data:
                continue
            if 'releasetype' in data:
                album.meta['releasetype'] = data['releasetype']
            if 'mbid' in data:
                album.meta['mbid' + ('relgrp' if part == 'album'
                                     else part)] = data['mbid']
            genretags.add_tags(data['tags'], dapr.name)
    if args.tag_mbids:
        print("MBIDs: artist=%s, aartist=%s\nMBIDs: relgrp=%s, release=%s"
              % (album.meta.get('mbidartist'), album.meta.get('mbidaartist'),
                 album.meta.get('mbidrelgrp'), album.meta.get('mbidrelease')))
    if args.tag_release:
        print("RelType: %s" % album.meta.get('releasetype'))
    genres = genretags.get(limit=args.tag_limit, scores=False)
    print("Genres (%d): %s" % (len(genretags.get()), ', '.join(genres))
          if genres else "No genres found :-(")
    album.save_metadata(genres, args)


def print_stats(data):
    '''Prints out some statistics at the end'''
    print("Time elapsed: %s\n" % timedelta(seconds=time() - data['start']))
    if len(data['stats']):
        print("Tag statistics (%d): %s\n" % (len(data['stats']), ', '.join
            (["%s: %d" % (k, v) for k, v in sorted
              (data['stats'].iteritems(), key=itemgetter(1), reverse=True)])))
    if data['genres']:
        print("%d albums with too little genres:\n%s\n" % (len(data['genres']),
            '\n'.join(sorted(data['genres']))))
    if data['errors']:
        print("%d albums with errors:\n%s\n" % (len(data['errors']),
            '\n'.join(["%s \t(%s)" % (k, v) for k, v in sorted
            (data['errors'].iteritems())])))


def main():
    '''The main()'''
    args = get_arguments()
    conf = get_configuration(args.config)
    basetags = get_tagsfile('tags.txt')
    basetags.update({"userscore_up":
                     get_config_list(conf, 'genres', 'score_up')})
    basetags.update({"userscore_down":
                     get_config_list(conf, 'genres', 'score_down')})
    basetags.update({"filter_blacklist":
                     get_config_list(conf, 'genres', 'blacklist')})
    validate(args, conf, basetags)

    data = {'start': time(), 'stats': defaultdict(int),
            'errors': {}, 'genres': []}
    cache = Cache(args.cache, args.cacheignore,
                  conf.getint('wlg', 'cachetimeout'))
    try:
        albums = get_albums(args.path)
        genretags = GenreTags(basetags, {x: conf.getfloat('scores', x)
                                         for x, _ in conf.items('scores')},
                              get_config_list(conf, 'genres', 'filters'))
        dps = init_dataproviders(args, conf, cache)

        for i, album in enumerate(albums):
            # save cache every 10 minutes
            if time() - cache.time > 600:
                cache.save()
            # print progress bar
            print("\n(%3d/%d) [" % (i + 1, len(albums)), end='')
            for j in range(60):
                print('#' if j < (i / len(albums) * 60) else '-', end='')
            print("] %.1f%%" % (i / len(albums) * 100))
            # handle album
            try:
                handle_album(album, dps, genretags, args)
            except AlbumError as err:
                print(err.message)
                data['errors'].update({album.path: err.message})
                continue
            # statistics
            genres = genretags.get(limit=args.tag_limit, scores=False)
            if genres:
                for tag in genres:
                    data['stats'][tag] += 1
            else:
                data['genres'].append(album.path)
    except KeyboardInterrupt:
        print("\n")
        cache.save()
        print_stats(data)
        return 0

    print("\n...all done!\n")
    cache.clean()
    print_stats(data)
    return 0

VPRINT = lambda *a, **k: None
VAREGEX = re.compile('^va(rious( ?artists?)?)?$', re.I)

if __name__ == "__main__":
    print("whatlastgenre v%s\n" % __version__)
    sys.exit(main())
