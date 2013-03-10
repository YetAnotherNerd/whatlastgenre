#!/usr/bin/env python
'''whatlastgenre
Improves genre metadata of audio files based on tags from various music sites.
http://github.com/YetAnotherNerd/whatlastgenre'''

from __future__ import division, print_function
from collections import defaultdict
from math import factorial as fac
from requests.exceptions import ConnectionError, HTTPError
import ConfigParser
import argparse
import datetime
import difflib
import musicbrainzngs.musicbrainz as mb
import mutagen
import os
import pickle
import re
import requests
import struct
import sys
import time


__version__ = "0.1.21"

VPRINT = lambda *a, **k: None


class GenreTags:
    '''Class for managing the genre tags.'''

    def __init__(self, basetags, scores, filters):
        self.basetags = basetags
        self.scores = scores
        self.filters = ['album', 'blacklist', 'generic'] + filters
        self.tags = None
        # fill matchlist
        self.matchlist = []
        for taglist in ['basictags', 'filter_blacklist', 'hate', 'love']:
            self.matchlist += self.basetags.get(taglist, [])
        # compile filters and other regex
        self.regex = {}
        for reg in [x for x in self.basetags if x.startswith('filter_')] \
                + ['splitpart', 'dontsplit']:
            if reg.endswith('_fuzzy'):
                pat = '.*(' + '|'.join(self.basetags[reg]) + ').*'
                reg = reg[:-6]
            else:
                pat = '(' + '|'.join(self.basetags[reg]) + ')'
            if len(pat) < 10:
                if reg.startswith('filter_') and reg[7:] in self.filters:
                    self.filters.remove(reg[7:])
                continue
            self.regex[reg] = re.compile(pat, re.I)

    def reset(self, albumfilter):
        '''Resets the genre tags and album filter.'''
        self.tags = {'artist': defaultdict(float), 'album': defaultdict(float)}
        self.regex['filter_album'] = albumfilter

    def add_tags(self, tags, source, part):
        '''Adds tags with or without counts to a given part, scores them
        while taking the source score multiplier into account.'''
        if not tags:
            return
        multi = self.scores.get('src_' + source, 1)
        if isinstance(tags, dict):
            top = max(1, max(tags.itervalues()))
            for name, count in tags.iteritems():
                if count > top * .1:
                    self.add(part, name, count / top * multi)
        elif isinstance(tags, list):
            for name in tags:
                self.add(part, name, .85 ** (len(tags) - 1) * multi)

    def add(self, part, name, score):
        '''Adds a genre tag with a given score to a given part after doing
        all the replace, split, filter, etc. magic.'''
        name = name.encode('ascii', 'ignore').strip().lower()
        # prefilter
        if self.regex['filter_badtags'].match(name):
            return
        # replace
        name = re.sub(r'([_/,;\.\+\*]| and )', '&', name, 0, re.I)
        name = re.sub('-', ' ', name).strip()
        for pat, rep in self.basetags['replaceme'].iteritems():
            name = re.sub(pat, rep, name, 0, re.I)
        name = re.sub(' +', ' ', name).strip()
        # split
        tags, pscore, keep = self.split(name, score)
        if tags:
            for tag in tags:
                self.add(part, tag, pscore)
            if not keep:
                return
            score *= self.scores['splitup']
        # filter
        if len(name) not in range(3, 19) or score < 0.1:
            return
        for fil in self.filters:
            if self.regex['filter_' + fil].match(name):
                return
        # matching existing tag (don't change cutoff, add replaces instead)
        mli = []
        for tags in self.tags.itervalues():
            mli += tags.keys()
        match = difflib.get_close_matches(name, self.matchlist + mli, 1, .8572)
        if match:
            name = match[0]
        # score bonus
        if name in self.basetags['love'] + self.basetags['hate']:
            score *= 2 if name in self.basetags['love'] else 0.5
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
                count = 0.5 * fac(count) / fac(count - 2)
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
        for part, ptags in self.tags.iteritems():
            VPRINT("Best %s tags (%d): %s" % (part, len(ptags), ', '.join([
                   "%s (%.2f)" % (self.format(k), v) for k, v in sorted(ptags.
                   iteritems(), key=lambda (k, v): (v, k), reverse=1)][:10])))
            if ptags:
                top = max(1, max(ptags.itervalues()))
                multi = self.scores['artist'] if part == 'artist' else 1
                for tag, score in ptags.iteritems():
                    tags[tag] += (score / top) * multi
        # format and sort
        tags = {self.format(k): v for k, v in tags.iteritems()}
        return sorted(tags, key=tags.get, reverse=True)

    def format(self, name):
        '''Formats a tag to correct case.'''
        split = name.split(' ')
        for i in range(len(split)):
            if len(split[i]) < 3 and split[i] != 'nu' or \
                    split[i] in self.basetags['uppercase']:
                split[i] = split[i].upper()
            elif re.match('[0-9]{4}s', name, re.I):
                split[i] = split[i].lower()
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
        self.load_metadata()
        # VPRINT("Metadata: %s" % self.meta)

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
                bts = [self.meta[tag]]
                if tag in ['artist', 'aartist'] and ' ' in bts[0]:
                    bts += bts[0].split(' ')
                for badtag in bts:
                    for pat in [r'\(.*\)', r'\[.*\]', '{.*}', '- .* -',
                                r'[\W\d]', r'(vol(ume)?|and|the|feat)',
                                r'(\.\*)+']:
                        badtag = re.sub(pat, '.*', badtag, 0, re.I).strip()
                    badtag = re.sub(r'(^\.\*|\.\*$)', '', badtag, 0, re.I)
                    if len(badtag) > 2:
                        badtags.append(badtag.strip().lower())
        self.filter = re.compile('.*(' + '|'.join(badtags) + ').*', re.I)

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
                    value = int(value.encode('ascii', 'ignore')[:4])
            except (KeyError, ValueError, UnicodeEncodeError):
                continue
            metadata[tag].append(value)

    def __handle_metadata(self, metadata):
        '''Understands, validates and stores the metadata.'''
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
                if num > 1 or re.match('^va(rious( ?artists?)?)?$',
                                       tlist[0], re.I):
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
        '''Saves the metadata to all tracks.'''
        if args.dry:
            print("DRY-RUN! Not saving metadata.")
            return
        print("Saving metadata... ", end='')
        gdirty = False
        for track in self.tracks:
            dirty = False
            try:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
                dirty = self.__set_meta(meta, 'genre', genres, dirty)
                if args.tag_release and 'releasetype' in self.meta:
                    dirty = self.__set_meta(meta, 'releasetype',
                                            self.meta['releasetype'], dirty)
                if args.tag_mbids:
                    for key, val in {
                            'mbidartist': 'musicbrainz_artistid',
                            'mbidaartist': 'musicbrainz_albumartistid',
                            'mbidrelgrp': 'musicbrainz_releasegroupid',
                            'mbidrelease': 'musicbrainz_albumid'}.iteritems():
                        if key in self.meta:
                            dirty = self.__set_meta(meta, val, self.meta[key],
                                                    dirty)
                if dirty:
                    gdirty = True
                    meta.save()
            except IOError as err:
                raise AlbumError("Error saving album: %s" % err.message)
        print("done!" if gdirty else "(no changes)")

    def __set_meta(self, meta, key, value, dirty):
        '''Sets a given meta key to a given value if needed.'''
        if self.ext in ['flac', 'ogg']:
            key = key.upper()
        elif self.ext in ['mp3', 'm4a']:
            if key == 'musicbrainz_releasegroupid':
                return
            elif key == 'releasetype':
                key = 'musicbrainz_albumtype'
        if meta.get(key) == (value if isinstance(value, list) else [value]):
            return dirty
        meta[key] = value
        return True


class AlbumError(Exception):
    '''If something went wrong while handling an Album.'''
    pass


class Cache:
    '''Class for caching of data returned by DataProviders.'''

    def __init__(self, cachefile, ignore=False, timeout=7):
        self.file = cachefile
        self.ignore = ignore
        self.timeout = 60 * 60 * 24 * timeout
        self.time = time.time()
        self.dirty = False
        self.cache = {}
        try:
            self.cache = pickle.load(open(self.file))
        except (IOError, EOFError):
            pickle.dump(self.cache, open(self.file, 'wb'))

    def __del__(self):
        self.save()

    @classmethod
    def __get_key(cls, dapr, part, meta):
        '''Helper method to get the cache key.'''
        key = dapr + '###'
        if part == 'album':
            key += (meta.get('artist') or meta.get('aartist') or 'VA') + '###'
        key += meta[part]
        for pat in [r'[^\w#]', ' +']:
            key = re.sub(pat, '', key, 0, re.I)
        return key.lower().strip()

    def get(self, dapr, part, meta):
        '''Gets cache data for a given DataProvider and part.'''
        if self.ignore:
            return None
        key = self.__get_key(dapr, part, meta)
        if key not in self.cache or \
                time.time() - self.cache[key]['time'] > self.timeout:
            return None
        return self.cache[key]

    def set(self, dapr, part, meta, data):
        '''Sets cache data for a given DataProvider and part.'''
        key = self.__get_key(dapr, part, meta)
        if data:
            keep = ['tags', 'mbid', 'releasetype']
            if len(data) > 1:
                keep.append('info')
            for dat in data:
                for k in [k for k in dat.keys() if k not in keep]:
                    del dat[k]
        self.cache[key] = {'time': time.time(), 'data': data}
        self.dirty = True

    def clean(self):
        '''Cleans up old data from the cache'''
        for key, val in self.cache.items():
            if time.time() - val['time'] > self.timeout:
                del self.cache[key]
                self.dirty = True
        self.save()

    def save(self):
        '''Saves the cache to disk.'''
        if self.dirty:
            print("\nSaving cache...\n")
            pickle.dump(self.cache, open(self.file, 'wb'))
            self.time = time.time()
            self.dirty = False


class DataProvider:
    '''Base class for DataProviders.'''

    def __init__(self, caps, cache, session, interact):
        self.caps = caps
        self.cache = cache
        self.session = session
        self.interactive = interact
        self.name = self.__class__.__name__

    def _jsonapiquery(self, url, params, sparams=None):
        '''Queries json-APIs.'''
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
            print("#%2d:" % (i + 1), end=' ')
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
        '''Get artist data from a DataProvider.'''
        raise NotImplementedError()

    def _get_albumdata(self, meta):
        '''Get album data from a DataProvider.'''
        raise NotImplementedError()

    def get_data(self, part, meta):
        '''Getting data from DataProviders.'''
        if part not in meta or part == 'aartist' and \
                'artist' not in self.caps or part not in self.caps:
            return None
        # VPRINT("%s: %s search..." % (self.name, part))
        data = None
        cached = self.cache.get(self.name.lower(), part, meta)
        if cached:
            data = cached['data']
            cmsg = ' (cached)'
        else:
            cmsg = ''
            try:
                if(part == 'album'):
                    data = self._get_albumdata(meta)
                if(part in ['artist', 'aartist']):
                    data = self._get_artistdata(meta, part)
            except (ConnectionError, HTTPError, DataProviderError,
                    mb.ResponseError, mb.NetworkError) as err:
                print("%s: %s" % (self.name, err.message or err.cause))
                return None
            data = self.__filter_data(part, meta, data)
        if not data:
            print("%s: %s search found nothing.%s" % (self.name, part, cmsg))
            if not cached:
                self.cache.set(self.name, part, meta, None)
            return None
        if len(data) > 1 and self.interactive:
            data = self._interactive(part, meta, data)
        if len(data) > 1:
            print("%s: %s search got too many results: %d (use -i)%s"
                  % (self.name, part, len(data), cmsg))
            if not cached:
                self.cache.set(self.name, part, meta, data)
            return None
        # unique data
        VPRINT("%s: %s search found %d tags%s"
               % (self.name, part, len(data[0]['tags']), cmsg))
        if not cached or len(cached['data']) > 1:
            self.cache.set(self.name, part, meta, data)
        return data[0]

    def __filter_data(self, what, meta, data):
        '''Prefilters data to reduce needed interactivity.'''
        if not data or len(data) == 1:
            return data
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
            tmp = [d for d in data if difflib.SequenceMatcher
                   (None, title, d['title'].lower()).ratio() >= (10 - i) * 0.1]
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
                    r'(vol(ume|\.)?|and|the)', ': .*', ' +']:
            searchstr = re.sub(pat, ' ', searchstr, 0, re.I)
        return searchstr.strip().lower()


class DataProviderError(Exception):
    '''If something went wrong with DataProviders.'''
    pass


class WhatCD(DataProvider):
    '''Class for the DataProvider WhatCD.'''

    def __init__(self, cache, session, interact, cred):
        DataProvider.__init__(self, ['artist', 'album'], cache, session,
                              interact)
        self.cred = cred
        self.loggedin = False
        self.last_request = time.time()
        self.rate_limit = 2.0  # min. seconds between requests

    def __del__(self):
        # bug with new requests version
        # self.__login(out=True)
        pass

    def __login(self, out=False):
        '''Login or -out from WhatCD.'''
        if not self.loggedin and not out:
            self.session.post('https://what.cd/login.php',
                              {'username': self.cred[0],
                               'password': self.cred[1]})
            self.last_request = time.time()
            self.loggedin = True
        elif self.loggedin and out:
            authkey = self.__query({'action': 'index'}).get('authkey')
            self.session.get("https://what.cd/logout.php?auth=%s" % authkey)

    def __query(self, params, sparams=None):
        '''Queries the WhatCD json-API.'''
        self.__login()
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(.1)
        data = self._jsonapiquery('https://what.cd/ajax.php', params, sparams)
        self.last_request = time.time()
        if not data or data.get('status') != 'success' or \
                'response' not in data:
            return None
        return data['response']

    def _get_artistdata(self, meta, what):
        '''Gets a/artist data from WhatCD.'''
        data = self.__query({'action': 'artist', 'id': 0},
                            {'artistname': meta[what]})
        if not data or not data.get('tags'):
            return None
        return [{'tags': {tag['name'].replace('.', ' '): int(tag['count'])
                          for tag in data['tags']}}]

    def _get_albumdata(self, meta):
        '''Gets album data from WhatCD.'''
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
    '''Class for the DataProvider LastFM.'''

    def __init__(self, cache, session, interact):
        DataProvider.__init__(self, ['artist', 'album'], cache, session,
                              interact)

    def __query(self, params, sparams=None):
        '''Queries the LastFM json-API.'''
        theparams = {'api_key': "54bee5593b60d0a5bf379cedcad79052",
                     'format': 'json'}
        theparams.update(params)
        data = self._jsonapiquery('http://ws.audioscrobbler.com/2.0/',
                                  theparams, sparams)
        if 'error' in data:
            return None
        return data

    def _get_artistdata(self, meta, what):
        '''Gets a/artist data from LastFM.'''
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
        '''Gets album data from LastFM.'''
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
        '''Shared datahandling for artist and album data from LastFM.'''
        if not data or not data.get('toptags') or \
                not data['toptags'].get('tag'):
            return None
        tags = data['toptags']['tag']
        if not isinstance(tags, list):
            tags = [tags]
        return [{'tags': {tag['name']: int(tag['count'])
                          for tag in tags if int(tag['count']) > 2}}]


class MBrainz(DataProvider):
    '''Class for the DataProvider MusicBrainz.'''

    def __init__(self, cache, interact):
        DataProvider.__init__(self, ['artist', 'album'], cache, None, interact)
        mb.set_useragent('whatlastgenre', __version__,
                         'http://github.com/YetAnotherNerd/whatlastgenre')

    def _get_artistdata(self, meta, what):
        '''Get a/artist data from MusicBrainz.'''
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
                data = [d for d in data if difflib.SequenceMatcher
                        (None, meta[what].lower(),
                         d.get('name', '').lower()).ratio() > .8]
            if len(data) in range(2, 9):
                # filter by looking at artist's release-groups
                album = meta['album'].lower()
                tmp = []
                for dat in data:
                    req = mb.get_artist_by_id(dat['id'],
                                              includes=['tags',
                                                        'release-groups'])
                    for rel in req['artist']['release-group-list']:
                        if difflib.SequenceMatcher(None, rel['title'].lower(),
                                                   album).ratio() > .8:
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
        '''Get album data from MusicBrainz.'''
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
                 'title': (x.get('artist-credit-phrase', '') + ' - ' +
                           x.get('title', '')),
                 'tags': {tag['name']: int(tag['count']) for tag in
                          x.get('tag-list', [])},
                 'mbid': x['id']} for x in data]


class Discogs(DataProvider):
    '''Class for the DataProvider Discogs.'''

    def __init__(self, cache, session, interact):
        DataProvider.__init__(self, ['album'], cache, session, interact)

    def _get_artistdata(self, meta, what):
        '''Gets a/artist data from Discogs.'''
        pass

    def _get_albumdata(self, meta):
        '''Gets album data from Discogs.'''
        searchstr = ((meta.get('artist') or meta.get('aartist', '')) + ' ' +
                     meta['album'])
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
    '''Class for the DataProvider Idiomag.'''

    def __init__(self, cache, session, interact):
        DataProvider.__init__(self, ['artist'], cache, session, interact)

    def __query(self, params):
        '''Queries the Idiomag json-API.'''
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
        '''Gets a/artist data from Idiomag.'''
        data = self.__query({'artist': meta[what]})
        if not data:
            return None
        return [{'tags': {tag['name']: int(tag['value'] * 100)
                          for tag in data['tag']}}]

    def _get_albumdata(self, meta):
        '''Gets album data from Idiomag.'''
        pass


class EchoNest(DataProvider):
    '''Class for the DataProvider EchoNest.'''

    def __init__(self, cache, session, interact):
        DataProvider.__init__(self, ['artist'], cache, session, interact)

    def __query(self, params):
        '''Queries the EchoNest json-API.'''
        data = self._jsonapiquery(
            'http://developer.echonest.com/api/v4/artist/search',
            {'api_key': "ZS0LNJH7V6ML8AHW3", 'format': 'json',
             'bucket': 'genre', 'results': 1}, params)
        if not data or not data.get('response') or \
                'artists' not in data['response']:
            return None
        return data['response']['artists']

    def _get_artistdata(self, meta, what):
        '''Gets artist data from EchoNest.'''
        data = self.__query({'name': meta[what]})
        if not data:
            return None
        return [{'tags': [tag['name'] for tag in x['genres']]} for x in data]

    def _get_albumdata(self, meta):
        '''Gets album data from EchoNest.'''
        pass


def get_args():
    '''Gets the cmdline arguments from ArgumentParser.'''
    args = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description='Improves genre metadata of audio files '
                    'based on tags from various music sites.')
    args.add_argument(
        'path', nargs='+', help='folder(s) to scan for albums')
    args.add_argument(
        '-v', '--verbose', action='store_true', help='more detailed output')
    args.add_argument(
        '-n', '--dry', action='store_true', help='don\'t save metadata')
    args.add_argument(
        '-c', '--cacheignore', action='store_true', help='ignore cache hits')
    args.add_argument(
        '-i', '--interactive', action='store_true', help='interactive mode')
    args.add_argument(
        '-r', '--tag-release', action='store_true',
        help='tag release type (from What)')
    args.add_argument(
        '-m', '--tag-mbids', action='store_true', help='tag musicbrainz ids')
    args.add_argument(
        '-l', '--tag-limit', metavar='N', type=int, default=4,
        help='max. number of genre tags')
    args.add_argument(
        '--config', default=os.path.expanduser('~/.whatlastgenre/config'),
        help='location of the configuration file')
    args.add_argument(
        '--cache', default=os.path.expanduser('~/.whatlastgenre/cache'),
        help='location of the cache file')
    args = args.parse_args()
    if args.verbose:
        global VPRINT
        VPRINT = print
    return args


def get_conf(configfile):
    '''Reads, maintains and writes the configuration file.'''
    # [section, option, default, required, [min, max]]
    conf = [['wlg', 'sources',
             'whatcd, mbrainz, discogs, echonest, lastfm, idiomag', 1, []],
            ['wlg', 'tagsfile', 'tags.txt', 1, []],
            ['wlg', 'cache_timeout', '7', 1, [3, 90]],
            ['wlg', 'cache_saveint', '10', 1, [5, 60]],
            ['wlg', 'whatcduser', '', 0, []],
            ['wlg', 'whatcdpass', '', 0, []],
            ['genres', 'love', 'soundtrack', 0, []],
            ['genres', 'hate',
             'alternative, electronic, indie, pop, rock', 0, []],
            ['genres', 'blacklist', 'charts, male vocalists, other', 0, []],
            ['genres', 'filters',
             'instrument, label, location, name, year', 0, []],
            ['scores', 'src_whatcd', '1.66', 1, [0.3, 2.0]],
            ['scores', 'src_lastfm', '0.66', 1, [0.3, 2.0]],
            ['scores', 'src_mbrainz', '1.00', 1, [0.3, 2.0]],
            ['scores', 'src_discogs', '1.00', 1, [0.3, 2.0]],
            ['scores', 'src_idiomag', '1.00', 1, [0.3, 2.0]],
            ['scores', 'src_echonest', '1.00', 1, [0.3, 2.0]],
            ['scores', 'artist', '1.33', 1, [0.5, 2.0]],
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
    # add and validate options
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
    with open(configfile, 'wb') as conffile:
        config.write(conffile)
    print("Please edit your configuration file: %s" % configfile)
    exit()


def get_conf_list(conf, sec, opt):
    '''Gets a configuration string as list.'''
    return [x.strip() for x in conf.get(sec, opt).lower().split(',')
            if x.strip() != '']


def get_tags(tagsfile):
    '''Parses the tagsfile.'''
    if '/' not in tagsfile and '\\' not in tagsfile:
        tagsfile = os.path.join(os.path.dirname(__file__), tagsfile)
    tags = {}
    section = None
    taglist = []
    with open(tagsfile, 'r') as tagfile:
        for line in tagfile:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            sectionmatch = re.match(r'^\[(.*)\]( +#.*)?$', line)
            if sectionmatch:
                if section and taglist:
                    if section == 'replaceme':
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
    '''Validates args, conf and tags.'''
    # tags file
    for tag in ['basictags', 'splitpart', 'dontsplit', 'replaceme']:
        if tag not in tags:
            print("Got no [%s] from tag.txt file." % tag)
            exit()
    for fil in ['filter_' + f for f in
                get_conf_list(conf, 'genres', 'filters')]:
        if fil not in tags and fil + '_fuzzy' not in tags:
            print("The filter '%s' you set in your config doesn't have a [filt"
                  "er_%s[_fuzzy]] section in the tags.txt file." % (fil, fil))
            exit()
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
    if not len(sources):
        print("Where do you want to get your data from?\nAt least one source "
              "must be activated (multiple sources recommended)!")
        exit()
    # options
    if args.tag_release and 'whatcd' not in sources:
        print("Can't tag release with What support disabled. "
              "Release tagging disabled.\n")
        args.tag_release = False
    if args.tag_mbids and 'mbrainz' not in sources:
        print("Can't tag MBIDs with MusicBrainz support disabled. "
              "MBIDs tagging disabled.\n")
        args.tag_mbids = False


def get_albums(paths):
    '''Scans paths for albums.'''
    albums = []
    for path in paths:
        for root, _, files in os.walk(path):
            for afile in files:
                ext = os.path.splitext(afile)[1].lower()
                if ext in ['.flac', '.ogg', '.mp3', '.m4a']:
                    albums.append([root, ext[1:], [t for t in files if
                                                   t.lower().endswith(ext)]])
                    break
    print("Found %d album folders!" % len(albums))
    return albums


def get_daprs(args, conf, cache):
    '''Initializes the DataProviders.'''
    dps = []
    session = requests.session()
    for dapr in get_conf_list(conf, 'wlg', 'sources'):
        if dapr == 'mbrainz':
            dps.append(MBrainz(cache, args.interactive))
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


def handle_album(album, dps, genretags, args):
    '''Loads metadata, receives tags and saves an album.'''
    print("Loading metadata [%s]: %s" % (album[1].upper(), album[0]))
    album = Album(album[0], album[1], album[2])
    genretags.reset(album.filter)
    print("Receiving tags%s: artist=%s, aartist=%s, album=%s, year=%s"
          % (' [VA]' if album.meta['is_va'] else '',
             album.meta.get('artist'), album.meta.get('aartist'),
             album.meta['album'], album.meta.get('year')))
    for dapr in dps:
        for part in ['artist', 'aartist', 'album']:
            data = dapr.get_data(part, album.meta)
            if not data:
                continue
            if 'releasetype' in data:
                album.meta['releasetype'] = \
                    genretags.format(data['releasetype'])
            if 'mbid' in data:
                album.meta['mbid' + ('relgrp' if part == 'album'
                                     else part)] = data['mbid']
            genretags.add_tags(data['tags'], dapr.__class__.__name__.lower(),
                               'artist' if part == 'aartist' else part)
    genres = genretags.get()
    if args.tag_mbids:
        print("MBIDs: artist=%s, aartist=%s\nMBIDs: relgrp=%s, release=%s"
              % (album.meta.get('mbidartist'), album.meta.get('mbidaartist'),
                 album.meta.get('mbidrelgrp'), album.meta.get('mbidrelease')))
    if args.tag_release:
        print("RelType: %s" % album.meta.get('releasetype'))
    print("Genres (%d): %s" % (len(genres), ', '.join(genres[:args.tag_limit]))
          if genres else "No genres found :-(")
    album.save_metadata(genres[:args.tag_limit], args)
    return genres[:args.tag_limit]


def print_stats(data):
    '''Prints out some statistics.'''
    print("Time elapsed: %s\n"
          % datetime.timedelta(seconds=time.time() - data['start']))
    if len(data['stats']):
        stats = sorted(data['stats'].iteritems(), key=lambda (k, v): (v, k),
                       reverse=True)
        print("Tag statistics (%d): %s\n"
              % (len(stats), ', '.join(["%s: %d" % (k, v) for k, v in stats])))
    if data['genres']:
        print("%d albums with too little genres:\n%s\n"
              % (len(data['genres']), '\n'.join(sorted(data['genres']))))
    if data['errors']:
        print("%d albums with errors:\n%s\n"
              % (len(data['errors']), '\n'.join(["%s \t(%s)"
              % (k, v) for k, v in sorted(data['errors'].iteritems())])))


def main():
    '''Hi codereader! As you might have guessed, this is the main()
    and a good place to start ;) Feedback welcome.'''
    args = get_args()
    conf = get_conf(args.config)
    tags = get_tags(conf.get('wlg', 'tagsfile'))
    tags.update({"love": get_conf_list(conf, 'genres', 'love')})
    tags.update({"hate": get_conf_list(conf, 'genres', 'hate')})
    tags.update({"filter_blacklist":
                 get_conf_list(conf, 'genres', 'blacklist')})
    validate(args, conf, tags)

    data = {'start': time.time(), 'stats': defaultdict(int),
            'errors': {}, 'genres': []}
    albums = get_albums(args.path)
    if len(albums) == 0:
        exit()
    genretags = GenreTags(tags, {x: conf.getfloat('scores', x)
                                 for x, _ in conf.items('scores')},
                          get_conf_list(conf, 'genres', 'filters'))
    cache = Cache(args.cache, args.cacheignore,
                  conf.getint('wlg', 'cache_timeout'))
    dps = get_daprs(args, conf, cache)

    try:
        for i, album in enumerate(albums):
            # save cache every x minutes
            if time.time() - cache.time > 60 * \
                    conf.get('wlg', 'cache_saveint'):
                cache.save()
            # print progress bar
            print("\n(%3d/%d) [" % (i + 1, len(albums)), end='')
            for j in range(56):
                print('#' if j < (i / len(albums) * 56) else '-', end='')
            print("] %.1f%%" % (i / len(albums) * 100))
            # handle album
            try:
                genres = handle_album(album, dps, genretags, args)
            except AlbumError as err:
                print(err.message)
                data['errors'].update({album[0]: err.message})
                continue
            # statistics
            if genres:
                for tag in genres:
                    data['stats'][tag] += 1
            else:
                data['genres'].append(album[0])
    except KeyboardInterrupt:
        print("\n")
        cache.save()
        print_stats(data)
        return 0

    print("\n...all done!\n")
    cache.clean()
    print_stats(data)
    return 0

if __name__ == "__main__":
    print("whatlastgenre v%s\n" % __version__)
    sys.exit(main())
