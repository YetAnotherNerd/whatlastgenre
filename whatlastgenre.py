#!/usr/bin/env python
""""whatlastgenre
Improves genre metadata of audio files based on tags from various music sites.
http://github.com/YetAnotherNerd/whatlastgenre"""

from __future__ import division, print_function
from ConfigParser import SafeConfigParser, NoSectionError, NoOptionError
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import namedtuple, defaultdict
from datetime import timedelta
from difflib import get_close_matches, SequenceMatcher
from operator import itemgetter
from requests.exceptions import ConnectionError, HTTPError
from time import time, sleep
import json
import musicbrainzngs.musicbrainz as mb
import mutagen
import os
import pickle
import re
import requests
import struct
import sys

__version__ = "0.1.17"


class GenreTags:
    """Class for managing the genre tags"""

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
        """Resets the genre tags."""
        self.tags = defaultdict(float)
        self.regex['album'] = albumfilter

    def add_tags(self, tags, source, search):
        """Adds tags with counts and scoring based on count-ratio
        or without count and scoring based on amount."""
        if not tags:
            return
        # calculate multiplier
        multi = 1
        if source in self.scores:
            multi *= self.scores[source]
        if search in ['artist', 'aartist']:
            multi *= self.scores['artists']
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
        """Adds a genre tag with a given score."""
        name = name.encode('ascii', 'ignore').lower().strip()
        # filter by length
        if len(name) not in range(3, 21):
            return
        # replace
        name = re.sub(r'([_/,;\.\+\*]| and )', '&', name, flags=re.I)
        name = re.sub('-', ' ', name, flags=re.I).strip()
        for pat, rep in self.basetags['replace'].iteritems():
            name = re.sub(pat, rep, name, flags=re.I).strip()
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
        """Split tag and return it parts and wheter to keep the base"""
        name = name.strip()
        if self.regex['dontsplit'].match(name):
            pass
        elif '&' in name:
            return name.split('&'), False
        elif ' ' in name:
            split = name.split(' ')
            # TODO: improve this
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
        """Gets the tags with minscore, limit and with or without scores"""
        tags = {self.format(k): v for k, v in self.tags.iteritems()
                if v > minscore}
        if scores:
            tags = sorted(tags.iteritems(), key=itemgetter(1), reverse=True)
        else:
            tags = sorted(tags, key=tags.get, reverse=True)
        return tags[:limit] if limit else tags

    def format(self, name):
        """Formats a tag to correct case."""
        split = name.split(' ')
        for i in range(len(split)):
            if len(split[i]) < 3 and split[i] != 'nu' or \
                    split[i] in self.basetags['uppercase']:
                split[i] = split[i].upper()
            elif re.match('[0-9]{4}s', name, flags=re.I):
                split[i] = split[i].lower()
            elif split[i][0] in ['j', 'k'] and \
                    split[i][1:] in self.basetags['basictags']:
                split[i] = split[i][0].upper() + split[i][1:].title()
            else:
                split[i] = split[i].title()
        return ' '.join(split)


class Album:
    """Class for managing albums."""

    def __init__(self, path, filetype):
        self.path = path
        self.filetype = filetype.lower()
        self.metadata = defaultdict(list)
        self.load_metadata()
        self.meta = {}
        self.handle_meta()
        if 'album' not in self.meta:
            raise AlbumError("There is not even an album tag (untagged?)")
        if 'artist' in self.meta and 'aartist' in self.meta and \
                self.meta['artist'].lower() == self.meta['aartist'].lower():
            del self.meta['aartist']
        VPRINT("Metadata: %s" % self.meta)
        # album metadata filter
        badtags = []
        for tag in ['artist', 'aartist', 'album']:
            if tag in self.meta:
                badtag = self.meta[tag].lower()
                if tag in ['artist', 'aartist'] and ' ' in badtag:
                    badtags += badtag.split(' ')
                badtags.append(badtag)
        for i in range(len(badtags)):
            for pat in [r'[\(\[{].*[\)\]}]', r'[^\w]', ' +']:
                badtags[i] = re.sub(pat, ' ', badtags[i], flags=re.I)
        self.filter = re.compile('.*(' + '|'.join(badtags) + ').*', re.I)

    def load_metadata(self):
        """Loads the album metadata from the tracks."""
        tags = {'album': 'album',
                'artist': 'artist',
                'aartist': {'flac': 'albumartist',
                            'ogg': 'albumartist',
                            'mp3': 'performer'},
                'year': 'date',
                'mbidartist': 'musicbrainz_artistid',
                'mbidaartist': 'musicbrainz_albumartistid',
                'mbidrelease': 'musicbrainz_albumid',
                'mbidrelgrp': 'musicbrainz_releasegroupid'}
        self.metadata = defaultdict(list)
        for track in [t for t in os.listdir(self.path) if
                      t.lower().endswith('.' + self.filetype)]:
            try:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
            except IOError as err:
                raise AlbumError("Error loading album metadata: %s"
                                 % err.message)
            except struct.error:
                raise AlbumError("Error with mutagen while loading album "
                                 "metadata! Maybe upgrading mutagen helps.")
            for tag, tagname in tags.iteritems():
                if isinstance(tagname, dict):
                    tagname = tagname[self.filetype]
                if (tagname.startswith('musicbrainz') and
                        isinstance(meta, mutagen.flac.FLAC)):
                    tagname = tagname.upper()
                try:
                    value = meta[tagname][0]
                    if tag == 'year':
                        value = int(value[:4])
                except (KeyError, ValueError, UnicodeEncodeError):
                    continue
                self.metadata[tag].append(value)

    def handle_meta(self):
        """Handles the metadata and prepares it later use."""

        def longest_common_substr(data):
            """Returns the longest common substr for a list of strings."""
            substr = ''
            if len(data) > 1 and len(data[0]) > 0:
                for i in range(len(data[0])):
                    for j in range(len(data[0]) - i + 1):
                        if j > len(substr) and all(data[0][i:i + j] in x
                                                   for x in data):
                            substr = data[0][i:i + j]
            return substr

        self.meta['is_va'] = False
        for tag, tlist in self.metadata.iteritems():
            tset = set(tlist)
            if len(tset) == 0:
                continue
            elif len(tset) == 1:
                if tag in ['artist', 'aartist'] and VAREGEX.match(tlist[0]):
                    self.meta['is_va'] = True
                    continue
                self.meta[tag] = tlist[0]
            elif tag in ['album', 'mbidrelease', 'mbidrelgrp']:
                raise AlbumError("Not all tracks have an equal %s-tag!" % tag)
            elif tag in ['artist', 'aartist']:
                lcs = longest_common_substr(tlist)
                if len(lcs) > 2 and lcs == tlist[0][:len(lcs)]:
                    self.meta[tag] = lcs
                    continue
                self.meta['is_va'] = True

    def save(self, genres, args):
        """Saves the metadata to the tracks."""
        if args.dry:
            print("DRY-RUN! Not saving metadata.")
            return
        print("Saving metadata...")
        tags = {'mbidartist': 'musicbrainz_artistid',
                'mbidaartist': 'musicbrainz_albumartistid',
                'mbidrelease': 'musicbrainz_albumid',
                'mbidrelgrp': 'musicbrainz_releasegroupid'}
        error = []
        for track in [t for t in os.listdir(self.path) if
                      t.lower().endswith('.' + self.filetype)]:
            try:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
                meta['genre'] = genres
                if args.tag_release and 'releasetype' in self.meta \
                        and self.filetype in ['flac', 'ogg']:
                    meta['releasetype'] = self.meta['releasetype']
                if args.tag_mbids:
                    for tag, tagname in tags.iteritems():
                        if self.filetype == 'mp3' and tag == 'mbidrelgrp':
                            continue
                        if tag in self.meta:
                            if self.filetype == 'flac':
                                tagname = tagname.upper()
                            meta[tagname] = self.meta[tag]
                meta.save()
            except IOError:
                error.append(track)
        if error:
            raise AlbumError("Error saving album metadata for tracks: %s"
                             % ', '.join(error))


class AlbumError(Exception):
    """If something went wrong while handling an Album."""
    pass


class Cache:
    """Class for caching of data returned by DataProviders."""

    def __init__(self, cachefile):
        self.file = cachefile
        self.cache = {}
        self.time = time()
        self.timeout = 60 * 60 * 24 * 7
        self.dirty = False
        try:
            self.cache = pickle.load(open(self.file))
        except (IOError, EOFError):
            pickle.dump(self.cache, open(self.file, 'wb'))

    def __del__(self):
        self.save()

    @classmethod
    def __get_key(cls, dapr, what, meta):
        """Helper method to get the key."""
        key = dapr + '###'
        if what == 'album':
            key += (meta.get('artist') or meta.get('aartist') or 'VA') + '###'
        key += meta[what]
        for pat in [r'[^\w#]', ' +']:
            key = re.sub(pat, '', key, flags=re.I)
        return key.lower().strip()

    def get(self, dapr, what, meta):
        """Gets cache data."""
        key = self.__get_key(dapr, what, meta)
        if key not in self.cache or \
                time() - self.cache[key]['time'] > self.timeout:
            return None
        return self.cache[key]

    def set(self, dapr, what, meta, data):
        """Sets cache data."""
        key = self.__get_key(dapr, what, meta)
        if data:
            keep = ['tags', 'mbid', 'rele']
            if len(data) > 1:
                keep.append('info')
            for dat in data:
                for k in dat.keys():
                    if k[:4] not in keep:
                        del dat[k]
        self.cache[key] = {'time': time(), 'data': data}
        self.dirty = True

    def clean(self):
        """Cleans up old data from the cache"""
        for key, val in self.cache.items():
            if time() - val['time'] > self.timeout:
                del self.cache[key]
                self.dirty = True
        self.save()

    def save(self):
        """Saves the cache to disk."""
        if self.dirty:
            print("\nSaving cache...\n")
            pickle.dump(self.cache, open(self.file, 'wb'))
            self.time = time()
            self.dirty = False


class DataProvider:
    """Base class for DataProviders."""

    def __init__(self, name, cache, session, interact):
        self.name = name
        self.cache = cache
        self.session = session
        self.interactive = interact

    def _jsonapiquery(self, url, params, sparams=None):
        """Method for querying json-apis."""
        if sparams:
            for key, val in sparams.iteritems():
                params.update({key: self.searchstr(val)})
        req = self.session.get(url, params=params)
        return json.loads(req.content)

    def _interactive(self, meta, data):
        """Asks the user to choose from a list of possibilites."""
        print("\aMultiple results from %s, which is right one?" % self.name)
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

    def _get_data(self, meta, part):
        """Get data from a DataProvider (this should be overridden by DPs)"""
        pass

    def get_data(self, part, meta):
        """Getting data from DataProviders."""
        if part not in meta or \
                part in ['artist', 'aartist'] and isinstance(self, Discogs):
            return None
        # VPRINT("%s: %s search..." % (self.name, part))
        data = None
        cached = self.cache.get(self.name, part, meta)
        if cached:
            VPRINT("%s: %s search cached!" % (self.name, part))
            data = cached['data']
        else:
            try:
                data = self._get_data(part, meta)
            except (ConnectionError, HTTPError, ValueError,
                    mb.ResponseError, mb.NetworkError) as err:
                print("%s: %s" % (self.name, err.message or err.cause))
                return None
            if part == 'album' and data and len(data) > 1:
                data = self.__filter_albums(meta, data)
        if not data:
            print("%s: %s search found nothing." % (self.name, part))
            if not cached:
                self.cache.set(self.name, part, meta, None)
            return None
        if len(data) > 1 and self.interactive:
            data = self._interactive(meta, data)
        if len(data) > 1:
            print("%s: %s search returned too many results: %d (use "
                  "--interactive)" % (self.name, part, len(data)))
            if not cached:
                self.cache.set(self.name, part, meta, data)
            return None
        # unique data
        VPRINT("%s: %s search found %d tags (unfiltered)."
               % (self.name, part, len(data[0]['tags'])))
        if not cached or len(cached['data']) > 1:
            self.cache.set(self.name, part, meta, data)
        return data[0]

    @classmethod
    def __filter_albums(cls, meta, data):
        """Filters albums."""
        # filter by title
        if len(data) > 1:
            for i in range(6):
                title = ('Various Artist' if meta['is_va'] else
                         (meta.get('artist') or meta.get('aartist', ''))
                         + ' - ' + meta['album']).lower()
                tmp = [d for d in data if SequenceMatcher(None, title,
                                d['title'].lower()).ratio() > (10 - i) * 0.1]
                if tmp:
                    data = tmp
                    break
        # filter by year
        if len(data) > 1 and 'year' in meta:
            for i in range(4):
                tmp = [d for d in data if not d.get('year') or
                       abs(int(d['year']) - meta['year']) <= i]
                if tmp:
                    data = tmp
                    break
        return data

    @classmethod
    def searchstr(cls, searchstr):
        """Cleans up a string for use in searching."""
        if not searchstr:
            return ''
        for pat in [r'[\(\[{].*[\)\]}]', r' (vol(ume|\.)?|and) ',
                    r'(:| -) .*', r' +']:
            searchstr = re.sub(pat, ' ', searchstr, flags=re.I).strip()
        return searchstr


class WhatCD(DataProvider):
    """Class for the DataProvider WhatCD"""

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
        """Login or Logout from What"""
        if not self.loggedin and not out:
            self.session.post('https://what.cd/login.php',
                              {'username': self.cred['user'],
                               'password': self.cred['pass']})
            self.last_request = time()
            self.loggedin = True
        elif self.loggedin and out:
            authkey = self.__query({'action': 'index'}).get('authkey')
            self.session.get("https://what.cd/logout.php?auth=%s" % authkey)

    def __query(self, params, sparams=None):
        """Query What.CD API"""
        self.__login()
        while time() - self.last_request < self.rate_limit:
            sleep(.1)
        data = self._jsonapiquery('https://what.cd/ajax.php', params, sparams)
        self.last_request = time()
        if data['status'] != 'success' or 'response' not in data:
            return None
        return data['response']

    def _get_data(self, what, meta):
        """Get data from What.CD"""
        if what in ['artist', 'aartist']:
            data = self.__query({'action': 'artist', 'id': 0},
                                {'artistname': meta[what]})
            if data and 'tags' in data:
                return [{'tags': {tag['name'].replace('.', ' '):
                                  int(tag['count'])
                                  for tag in data['tags']}}]
        elif what == 'album':
            searchstr = meta['album'] + ' ' + (meta.get('artist') or
                                               meta.get('aartist', ''))
            data = self.__query({'action': 'browse', 'filter_cat[1]': 1},
                                {'searchstr': searchstr})
            if data:
                data = data.get('results')
            if not data:
                return None
            if len(data) > 1:
                data = [d for d in data if meta['is_va'] and
                        (VAREGEX.match(d['artist']) or
                         'aartist' in meta and d['artist'] == meta['aartist'])
                        or not VAREGEX.match(d['artist'])]
            return [{'info': ("%s - %s (%s) [%s]"
                              % (d.get('artist'), d.get('groupName'),
                                 d.get('groupYear'), d.get('releaseType'))),
                     'title': d.get('artist') + ' - ' + d.get('groupName'),
                     'releasetype': d.get('releaseType'),
                     'tags': [tag.replace('.', ' ') for tag in d.get('tags')],
                     'year': d.get('groupYear')} for d in data]
        return None


class LastFM(DataProvider):
    """Class for the DataProvider LastFM"""

    def __init__(self, cache, session, interact, apikey):
        DataProvider.__init__(self, "Last.FM", cache, session, interact)
        self.apikey = apikey

    def __query(self, params, sparams=None):
        """Query Last.FM API"""
        theparams = {'api_key': self.apikey, 'format': 'json'}
        theparams.update(params)
        data = self._jsonapiquery('http://ws.audioscrobbler.com/2.0/',
                                  theparams, sparams)
        if 'error' in data:
            return None
        return data

    def _get_data(self, what, meta):
        """Get data from Last.FM"""
        data = None
        if what in ['artist', 'aartist']:
            if 'mbid' + what in meta:
                VPRINT("  Using %s-MBID: %s" % (what, meta['mbid' + what]))
                data = self.__query({'method': 'artist.gettoptags',
                                     'mbid': meta['mbid' + what]})
            if not data:
                data = self.__query({'method': 'artist.gettoptags'},
                                    {'artist': meta[what]})
        elif what == 'album':
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

        if data and 'toptags' in data and 'tag' in data['toptags']:
            tags = data['toptags']['tag']
            if not isinstance(tags, list):
                tags = [tags]
            return [{'tags': {tag['name']: int(tag['count']) for tag in tags
                              if int(tag['count']) > 2}}]
        return None


class MusicBrainz(DataProvider):
    """Class for the DataProvider MusicBrainz"""

    def __init__(self, cache, interact):
        DataProvider.__init__(self, "MBrainz", cache, None, interact)
        mb.set_useragent("whatlastgenre", __version__,
                         "http://github.com/YetAnotherNerd/whatlastgenre")

    def _get_data(self, what, meta):
        """Get data from MusicBrainz"""
        if what in ['artist', 'aartist']:
            return self.__get_data_artist(meta, what)
        if what == 'album':
            return self.__get_data_album(meta)
        return None

    def __get_data_artist(self, meta, what):
        """Get a/artist data from MusicBrainz"""
        data = None
        # search a/artist by mbid
        if 'mbid' + what in meta:
            VPRINT("  Using %s-MBID: %s" % (what, meta['mbid' + what]))
            req = mb.get_artist_by_id(meta['mbid' + what],
                                      includes=['tags'])
            if req and 'artist' in req:
                return [{'tags': {tag['name']: int(tag['count']) for tag in
                                  req['artist'].get('tag-list', [])},
                         'mbid' + what: req['artist']['id']}]
            else:
                VPRINT("  %s not found, deleting invalid MBID" % what)
                del meta['mbid' + what]
        # search a/artist without mbid
        if not data:
            req = mb.search_artists(artist=self.searchstr(meta[what]))
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
        return [{'info': "%s (%s) [%s] [%s-%s]: http://musicbrainz.org"
                 "/artist/%s"
                 % (x.get('name'), x.get('type'), x.get('country', ''),
                    x.get('life-span', {}).get('begin', '')[:4],
                    x.get('life-span', {}).get('end', '')[:4],
                    x.get('id')),
                 'title': x.get('name', ''),
                 'tags': {tag['name']: int(tag['count']) for tag in
                          x.get('tag-list', [])},
                 'mbid' + what: x.get('id')} for x in data]

    def __get_data_album(self, meta):
        """Get album data from MusicBrainz"""
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
            req = mb.search_release_groups(
                    release=self.searchstr(meta['album']),
                    artist=self.searchstr(meta.get('artist') or
                                          meta.get('aartist', '')),
                    arid=meta.get('mbidartist') or meta.get('mbidaartist', ''))
            data = req.get('release-group-list', [])
            if len(data) > 1:
                data = [d for d in data if 'title' in d and SequenceMatcher
                        (None, meta['album'].lower(),
                         d['title'].lower()).ratio() > .8]
        return [{'info': "%s - %s [%s]: http://musicbrainz.org"
                 "/release-group/%s"
                 % (x.get('artist-credit-phrase'), x.get('title'),
                    x.get('type'), x.get('id')),
                 'title': x.get('artist-credit-phrase', '') + ' - '
                    + x.get('title', ''),
                 'tags': {tag['name']: int(tag['count']) for tag in
                          x.get('tag-list', [])},
                 'mbidrelgrp': x.get('id')} for x in data]


class Discogs(DataProvider):
    """Class for the DataProvider Discogs."""

    def __init__(self, cache, session, interact):
        DataProvider.__init__(self, "Discogs", cache, session, interact)

    def _get_data(self, what, meta):
        """Get data from Discogs"""
        if what != 'album':
            return None
        searchstr = (meta.get('artist') or meta.get('aartist', '')) \
                        + ' ' + meta['album']
        data = self._jsonapiquery('http://api.discogs.com/database/search',
                                  {'type': 'master'}, {'q': searchstr})
        if 'results' not in data:
            return None
        return [{'info': "%s [%s]: http://www.discogs.com/master/%s"
                 % (x.get('title'), x.get('year'), x.get('id')),
                 'tags': x.get('style', []) + x.get('genre', []),
                 'title': x.get('title', ''),
                 'year': x.get('year')} for x in data['results']]


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
        '--no-whatcd', action='store_true', help='disable lookup on What.CD')
    argparse.add_argument(
        '--no-lastfm', action='store_true', help='disable lookup on Last.FM')
    argparse.add_argument(
        '--no-mbrainz', action='store_true', help='disable lookup on MBrainz')
    argparse.add_argument(
        '--no-discogs', action='store_true', help='disable lookup on Discogs')
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
    '''Reads the configuration file. Creates it if not exists.'''

    def config_list(strlist):
        '''Gets a list from a configuration string that should be a list.'''
        if strlist:
            return [i.strip() for i in strlist.split(',')]
        return []

    config = SafeConfigParser()
    try:
        config.read(configfile)
    except IOError:
        if not os.path.exists(os.path.dirname(configfile)):
            os.makedirs(os.path.dirname(configfile))
        config.add_section('whatcd')
        config.set('whatcd', 'username', '')
        config.set('whatcd', 'password', '')
        config.add_section('genres')
        config.set('genres', 'blacklist', 'charts, other, unknown')
        config.set('genres', 'score_up', 'soundtrack')
        config.set('genres', 'score_down', 'alternative, electronic, indie')
        config.set('genres', 'filters', 'instrument, label, location, year')
        config.add_section('scores')
        config.set('scores', 'what.cd', '1.66')
        config.set('scores', 'last.fm', '0.66')
        config.set('scores', 'mbrainz', '1.00')
        config.set('scores', 'discogs', '1.00')
        config.set('scores', 'artists', '1.33')
        config.set('scores', 'splitup', '0.33')
        config.set('scores', 'userset', '0.66')
        with open(configfile, 'wb') as conffile:
            config.write(conffile)
        print("Please edit the configuration file: %s" % configfile)
        exit()

    conf = namedtuple('conf', '')
    try:
        conf.whatcd_user = config.get('whatcd', 'username')
        conf.whatcd_pass = config.get('whatcd', 'password')
        conf.blacklist = config_list(config.get('genres', 'blacklist'))
        conf.score_up = config_list(config.get('genres', 'score_up'))
        conf.score_down = config_list(config.get('genres', 'score_down'))
        conf.filters = config_list(config.get('genres', 'filters'))
        conf.scores = {k: config.getfloat("scores", k)
                       for k, _ in config.items("scores")}
    except (NoSectionError, NoOptionError):
        print("Seems you are using an old config file. Please update it "
              "manually or delete it to have it recreated.")
        exit()
    return conf


def read_tagsfile(tagsfile):
    """Reads the tagsfile and returns its contents."""
    tagsfile = os.path.join(
        os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__))),
        tagsfile)
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
    """Validates argument and config settings and fixes them if necessary."""
    if args.no_whatcd and args.no_lastfm and args.no_mbrainz and \
            args.no_discogs:
        print("Where do you want to get your data from?\nAt least one source "
              "must be activated (multiple sources recommended)!")
        exit()
    if not args.no_whatcd and (not conf.whatcd_user or not conf.whatcd_pass):
        print("No What.CD credentials specified. What.CD support disabled.\n")
        args.no_whatcd = True
    if args.no_whatcd and args.tag_release:
        print("Can't tag release with What support disabled. "
              "Release tagging disabled.\n")
        args.tag_release = False
    if args.no_mbrainz and args.tag_mbids:
        print("Can't tag MBIDs with MusicBrainz support disabled. "
              "MBIDs tagging disabled.\n")
        args.tag_mbids = False
    for tag in ['basictags', 'replace']:
        if tag not in tags or []:
            print("FATAL: Got no [%s] from tag.txt file." % tag)
            exit()
    for filt in conf.filters:
        if filt != 'year' and 'filter_' + filt not in tags:
            print("The filter '%s' you set in your config doesn't have a "
                  "[filter_%s] section in the tags.txt file." % (filt, filt))
            exit()


def find_albums(paths):
    """Scans paths for possible albumfolders."""
    albums = {}
    for path in paths:
        for root, _, files in os.walk(path):
            for afile in files:
                ext = os.path.splitext(afile)[1][1:].lower()
                if ext in ['flac', 'ogg', 'mp3']:
                    albums.update({root: ext})
                    break
    return albums


def handle_album(albumpath, albumext, dps, genretags, args):
    """ Loads metadata, receives tags and saves an album."""
    print("Loading metadata for %s-album in %s..." % (albumext, albumpath))
    album = Album(albumpath, albumext)
    genretags.reset(album.filter)
    print("Receiving tags for artist=%s, aartist=%s, album=%s%s..."
          % (album.meta.get('artist'), album.meta.get('aartist'),
             album.meta['album'], ' (VA)' if album.meta['is_va'] else ''))
    for dapr in dps:
        for part in ['artist', 'aartist', 'album']:
            data = dapr.get_data(part, album.meta)
            if not data:
                continue
            if isinstance(dapr, WhatCD) and \
                    part == 'album' and 'releasetype' in data:
                album.meta['releasetype'] = data['releasetype']
            elif isinstance(dapr, MusicBrainz):
                for key in ['mbidartist', 'mbidaartist',
                            'mbidrelgrp', 'mbidrelease']:
                    if key in data:
                        album.meta[key] = data[key]
            genretags.add_tags(data['tags'], dapr.name, part)
        genres = genretags.get()
        VPRINT("Good Genres (%s): %s" % (len(genres), ', '.join(["%s (%.2f)"
                                                % (k, v) for k, v in genres])))
    if args.tag_mbids:
        print("MBIDs: artist=%s, aartist=%s\nMBIDs: relgrp=%s, release=%s"
              % (album.meta.get('mbidartist'), album.meta.get('mbidaartist'),
                 album.meta.get('mbidrelgrp'), album.meta.get('mbidrelease')))
    if args.tag_release:
        print("RelType: %s" % album.meta.get('releasetype'))
    genres = genretags.get(limit=args.tag_limit, scores=False)
    if genres:
        print("Genres (%d): %s" % (len(genretags.get()), ', '.join(genres)))
    else:
        print("No genres found :-(")
    album.save(genres, args)


def main():
    """The main() ... nothing more, nothing less (shut up pylint) ;)"""
    start = time()
    args = get_arguments()
    conf = get_configuration(args.config)
    basetags = read_tagsfile('tags.txt')
    basetags.update({"userscore_up": conf.score_up})
    basetags.update({"userscore_down": conf.score_down})
    basetags.update({"filter_blacklist": conf.blacklist})
    validate(args, conf, basetags)

    albums = find_albums(args.path)
    print("Found %d album folders!" % len(albums))
    if len(albums) == 0:
        exit()

    cache = Cache(args.cache)
    session = requests.session()
    dps = []
    if not args.no_mbrainz:
        dps.append(MusicBrainz(cache, args.interactive))
    if not args.no_lastfm:
        dps.append(LastFM(cache, session, args.interactive,
                          "54bee5593b60d0a5bf379cedcad79052"))
    if not args.no_whatcd:
        dps.append(WhatCD(cache, session, args.interactive,
                          {'user': conf.whatcd_user,
                           'pass': conf.whatcd_pass}))
    if not args.no_discogs:
        dps.append(Discogs(cache, session, args.interactive))

    genretags = GenreTags(basetags, conf.scores, conf.filters)

    errors = []
    stats = defaultdict(int)
    for i, (albumpath, albumext) in enumerate(albums.iteritems()):
        # print progress bar
        print("\n(%2d/%2d) [" % (i + 1, len(albums)), end='')
        for j in range(40):
            print('#' if j < (i / len(albums) * 40) else '-', end='')
        print("] %.1f%%" % (i / len(albums) * 100))
        # save cache every 10 minutes
        if time() - cache.time > 600:
            cache.save()
        # handle album
        try:
            handle_album(albumpath, albumext, dps, genretags, args)
        except AlbumError as err:
            print(err.message)
            errors.append(albumpath)
            continue
        # statistics
        for tag in genretags.get(limit=args.tag_limit, scores=False):
            stats[tag] += 1

    cache.clean()
    print("\n...all done!\n")
    print("Time elapsed: %s\n" % timedelta(seconds=time() - start))
    print("Tag statistics (%d): %s\n" % (len(stats), ', '.join
            (["%s: %d" % (k, v) for k, v in sorted
            (stats.iteritems(), key=itemgetter(1), reverse=True)])))
    if errors:
        print("\n%d albums with errors:\n%s"
              % (len(errors), '\n'.join(sorted(errors))))

VPRINT = lambda *a, **k: None
VAREGEX = re.compile('^va(rious ?(artists?)?)?$', re.I)

if __name__ == "__main__":
    print("whatlastgenre v%s\n" % __version__)
    main()
