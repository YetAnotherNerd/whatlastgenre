#!/usr/bin/env python
""""whatlastgenre
Improves genre metadata of audio files based on tags from various music sites.
http://github.com/YetAnotherNerd/whatlastgenre

TODO:
* use longest common substring as artist if not all the same
    (some could have 'feat. xyz' appended)
* improve results for VA-releases (maybe search for tags of each artist)"""

from __future__ import division, print_function
from ConfigParser import SafeConfigParser
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import namedtuple, defaultdict
from difflib import get_close_matches, SequenceMatcher
from requests.exceptions import HTTPError, ConnectionError
import datetime
import json
import musicbrainzngs.musicbrainz as mb
import mutagen
import operator
import os
import pickle
import re
import requests
import sys
import time

__version__ = "0.1.13"


class GenreTags:
    """Class for managing the genre tags"""

    def __init__(self, basetags, scores, filters, limit):
        self.tags = defaultdict(float)
        self.basetags = basetags
        self.scores = scores
        self.limit = limit
        # matchlist
        self.matchlist = []
        for taglist in ['basictags', 'filter_blacklist',
                        'userscore_up', 'userscore_down']:
            if taglist in self.basetags:
                self.matchlist += self.basetags[taglist]
        # compile filters
        self.filters = ['generic', 'blacklist'] + filters
        self.filtreg = {}
        for filt in self.filters[:]:
            if filt == 'year':
                tags = ['([0-9]{2}){1,2}s?']
            elif 'filter_' + filt in self.basetags:
                tags = self.basetags['filter_' + filt]
            else:
                self.filters.remove(filt)
                continue
            self.filtreg[filt] = re.compile('^(' + '|'.join(tags) + ')$', re.I)
        self.filters.append('album')

    def reset(self, albumfilter):
        """Resets the genre tags."""
        self.tags = defaultdict(float)
        self.filtreg['album'] = albumfilter

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
            top = max(1, max(tags.iteritems(), key=operator.itemgetter(1))[1])
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
        if len(name) not in range(2, 21):
            return
        # replace
        for pat, rep in self.basetags['replace'].iteritems():
            name = re.sub(pat, rep, name)
        # filter
        for filt in self.filters:
            if self.filtreg[filt].match(name):
                return
        # split
        split = self.__split(name)
        if len(split) > 1:
            # VPRINT("Splitted %s into %s" % (name, ', '.join(split)))
            for tag in split:
                self.add(tag, score)
            score *= self.scores['splitup']
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

    def __split(self, name):
        """Split tags recursively."""

        def splitbyspace(name):
            """Decides whether name should be split by SPACE or not."""
            parts = name.split(' ')
            if parts[0] in self.basetags['splitprefix']:
                return True
            for part in parts:
                for filt in self.filtreg.itervalues():
                    if filt.match(part):
                        return True
            return False

        name = name.strip()
        if name in self.basetags['dontsplit']:
            return [name]
        for sep in ['&', ' ']:
            if sep in name and (sep != ' ' or splitbyspace(name)):
                tags = []
                for part in name.split(sep):
                    tags += self.__split(part)
                return tags
        return [name]

    def get(self, minscore=0, scores=True, limited=False):
        """Gets the tags by minscore, with or without scores,
        filtered and/or limited"""
        tags = {self.__format(k): v for k, v in self.tags.iteritems()
                if v > minscore}
        if scores:
            tags = sorted(tags.iteritems(), key=operator.itemgetter(1),
                          reverse=True)
        else:
            tags = sorted(tags, key=tags.get, reverse=True)
        if limited:
            return tags[:self.limit]
        return tags

    def __format(self, name):
        """Formats a tag to correct case."""
        split = name.split(' ')
        for i in range(len(split)):
            if len(split[i]) < 3 and split[i] != 'nu' or \
                    split[i] in self.basetags['uppercase']:
                split[i] = split[i].upper()
            elif re.match('[0-9]{4}s', name, re.IGNORECASE):
                split[i] = split[i].lower()
            elif name[0] == 'j' and name[1:] in self.basetags['basictags']:
                split[i] = "J" + split[i][1:].title()
            else:
                split[i] = split[i].title()
        return ' '.join(split)


class Album:
    """Class for managing albums."""

    def __init__(self, path, filetype, genretags, dotag):
        self.path = path
        self.filetype = filetype.lower()
        self.tracks = [track for track in os.listdir(path)
                       if track.lower().endswith('.' + filetype)]
        self.tags = genretags
        self.dotag = dotag
        self.meta = {}
        self.meta['is_va'] = False
        self.__load_metadata()
        if 'album' not in self.meta:
            raise AlbumError("There is not even an album tag (untagged?)")
        if 'artist' in self.meta and 'aartist' in self.meta and \
                self.meta['artist'].lower() == self.meta['aartist'].lower():
            del self.meta['aartist']
        VPRINT("Metadata: %s" % self.meta)
        # metadata filter
        badtags = []
        for tag in ['artist', 'aartist', 'album']:
            if tag in self.meta:
                badtag = self.meta[tag]
                for pat in [r'[\(\[{].*[\)\]}]', r'[^\w]', ' +']:
                    badtag = re.sub(pat, ' ', badtag, re.I)
                badtags.append(badtag)
                if tag in ['artist', 'aartist'] and ' ' in badtag:
                    badtags += badtag.split(' ')
        self.filter = re.compile('^(' + '|'.join(badtags) + ')$', re.I)

    def __str__(self):
        out = ""
        if 'mbids' in self.dotag:
            out += ("MBIDs: artist=%s, aartist=%s\n"
                    "MBIDs: relgrp=%s, release=%s\n"
                    % (self.meta.get('mbidartist'),
                       self.meta.get('mbidaartist'),
                       self.meta.get('mbidrelgrp'),
                       self.meta.get('mbidrelease')))
        if 'release' in self.dotag:
            out += "RelType: %s\n" % self.meta.get('releasetype')
        genres = len(self.tags.get(scores=False))
        if genres:
            out += ("Genres (%d): %s"
                    % (genres, ', '.join(["%s (%.2f)" % (k, v) for k, v in
                                          self.tags.get(limited=True)])))
        else:
            out += "No genres found :-("
        return out

    def __load_metadata(self):
        """Loads the album metadata from the tracks."""
        tags = {"album": "album",
                "artist": "artist",
                "aartist": "albumartist",
                "year": "date",
                "mbidartist": "musicbrainz_artistid",
                "mbidaartist": "musicbrainz_albumartistid",
                "mbidrelease": "musicbrainz_albumid",
                "mbidrelgrp": "musicbrainz_releasegroupid"}
        taglist = defaultdict(list)
        for track in self.tracks:
            meta = mutagen.File(os.path.join(self.path, track), easy=True)
            for tag, tagname in tags.iteritems():
                if (tagname.startswith('musicbrainz') and
                        isinstance(meta, mutagen.flac.FLAC)):
                    tagname = tagname.upper()
                try:
                    value = meta[tagname][0].encode('ascii', 'ignore')
                    if tag == 'year':
                        value = int(value[:4])
                except (KeyError, ValueError, UnicodeEncodeError):
                    continue
                taglist[tag].append(value)

        for tag, tlist in taglist.iteritems():
            tset = set(tlist)
            if len(tset) == 0:
                continue
            elif len(tset) == 1:
                if tag in ['artist', 'aartist'] and VAREGEX.match(tlist[0]):
                    self.meta['is_va'] = True
                else:
                    self.meta[tag] = tlist[0]
            elif tag in ['album', 'mbidrelease', 'mbidrelgrp']:
                raise AlbumError("Not all tracks have an equal %s-tag!" % tag)
            elif tag in ['artist', 'aartist']:
                self.meta['is_va'] = True

    def save(self):
        """Saves the metadata to the tracks."""
        print("Saving metadata...")
        tags = {'mbidartist': 'musicbrainz_artistid',
                'mbidaartist': 'musicbrainz_albumartistid',
                'mbidrelease': 'musicbrainz_albumid',
                'mbidrelgrp': 'musicbrainz_releasegroupid'}
        genres = self.tags.get(scores=False, limited=True)
        error = []
        for track in self.tracks:
            try:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
                meta['genre'] = genres
                if 'release' in self.dotag and 'releasetype' in self.meta \
                        and self.filetype in ['flac', 'ogg']:
                    meta['releasetype'] = self.meta['releasetype']
                if 'mbids' in self.dotag:
                    for tag, tagname in tags.iteritems():
                        if self.filetype == 'mp3' and tag == 'mbidrelgrp':
                            continue
                        if tag in self.meta:
                            if self.filetype == 'flac':
                                tagname = tagname.upper()
                            meta[tagname] = self.meta[tag]
                meta.save()
            except mutagen.flac.FLACNoHeaderError:
                error.append(track)
        if error:
            raise AlbumError("Error saving album metadata for tracks: %s"
                             % ', '.join(error))


class AlbumError(Exception):
    """If something wents wrong while handling an Album."""
    pass


class DataProvider:
    """Base class for DataProviders."""

    def __init__(self, name, session, interactive):
        self.name = name
        self.session = session
        self.interactive = interactive

    def _jsonapiquery(self, url, params, sparams=None):
        """Method for querying json-apis."""
        if sparams:
            for key, val in sparams.iteritems():
                params.update({key: self._searchstr(val)})
        try:
            req = self.session.get(url, params=params)
            data = json.loads(req.content)
        except (ConnectionError, HTTPError, ValueError) as err:
            raise DataProviderError("Request error: %s" % err.message)
        return data

    @classmethod
    def _searchstr(cls, searchstr):
        """Cleans up a string for use in searching."""
        if not searchstr:
            return ''
        for pat in [r'[\(\[{].*[\)\]}]', r' (vol(ume|\.)?|and) ', r' +']:
            searchstr = re.sub(pat, ' ', searchstr, re.I)
        return searchstr.strip().lower()

    def _interactive(self, albumpath, data):
        """Asks the user to choose from a list of possibilites."""
        print("\aMultiple results from %s, which is right one?" % self.name)
        VPRINT("Path: %s" % albumpath)
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

    def get_data(self, album):
        """Getting data from DataProviders."""
        VPRINT("%s..." % self.name)
        for part in ['artist', 'aartist', 'album']:
            if part not in album.meta or part in ['artist', 'aartist'] and \
                    isinstance(self, Discogs):
                continue
            # VPRINT("%s: %s search..." % (self.name, part))
            try:
                data = self._get_data(album.meta, part)
            except (mb.ResponseError, mb.NetworkError,
                    DataProviderError) as err:
                print("%s: %s" % (self.name, err.message))
                continue
            if not data:
                print("%s: %s search found nothing." % (self.name, part))
                continue
            data = self.__filter_data(album.meta, data, part)
            if len(data) > 1:
                print("%s: %s search returned too many results: %d (use "
                      "--interactive)" % (self.name, part, len(data)))
                continue
            # unique data
            data = data[0]
            if isinstance(self, WhatCD) and 'releasetype' in data:
                album.meta['releasetype'] = data['releasetype']
            elif isinstance(self, MusicBrainz):
                for key, val in data.iteritems():
                    if key in ['mbidartist', 'mbidaartist',
                               'mbidrelgrp', 'mbidrelease']:
                        album.meta[key] = val
            VPRINT("%s: %s search found %d tags (unfiltered)."
                   % (self.name, part, len(data['tags'])))
            album.tags.add_tags(data['tags'], self.name.lower(), part)

    def __filter_data(self, meta, data, part):
        """Filters the data."""
        # filter by title
        if len(data) > 1 and part == 'album':
            for i in range(6):
                title = ('Various Artist' if meta['is_va'] else
                         (meta.get('artist') or meta.get('aartist', ''))
                         + ' - ' + meta['album'])
                tmp = [d for d in data if SequenceMatcher
                       (None, title, d['title']).ratio() > (10 - i) * 0.1]
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
        # filter by user
        if len(data) > 1 and self.interactive:
            data = self._interactive(meta['album'], data)
        return data


class DataProviderError(Exception):
    """If something went wrong with a DataProvider."""
    pass


class WhatCD(DataProvider):
    """Class for the DataProvider WhatCD"""

    def __init__(self, session, interactive, username, password):
        DataProvider.__init__(self, "What.CD", session, interactive)
        self.session.post('https://what.cd/login.php',
                          {'username': username, 'password': password})
        self.last_request = time.time()
        self.rate_limit = 2.0  # min. seconds between requests
#        self.authkey = self.__query({'action': 'index'}).get('authkey')

    def __del__(self):
# bug with new requests version
#        try:
#            self.session.get("https://what.cd/logout.php?auth=%s"
#                             % self.authkey)
#        except requests.exceptions.TooManyRedirects:
#            pass
        pass

    def __query(self, params, sparams=None):
        """Query What.CD API"""
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(.1)
        data = self._jsonapiquery('https://what.cd/ajax.php', params, sparams)
        self.last_request = time.time()
        if data['status'] != 'success' or 'response' not in data:
            return None
        return data['response']

    def _get_data(self, meta, what):
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
                                {'searchstr': searchstr}).get('results')
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

    def __init__(self, session, interactive, apikey):
        DataProvider.__init__(self, "Last.FM", session, interactive)
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

    def _get_data(self, meta, what):
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

    def __init__(self, session, interactive):
        DataProvider.__init__(self, "MBrainz", session, interactive)
        mb.set_useragent("whatlastgenre", __version__,
                         "http://github.com/YetAnotherNerd/whatlastgenre")

    def _get_data(self, meta, what):
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
            req = mb.search_artists(artist=self._searchstr(meta[what]))
            data = req.get('artist-list', [])
            if len(data) > 1:
                data = [d for d in data if SequenceMatcher(None,
                        meta[what], d.get('name', '')).ratio() > .9]
            if len(data) > 1:
                # filter by looking at artist's release-groups
                tmp = []
                for dat in data:
                    req = mb.get_artist_by_id(dat['id'],
                                        includes=['tags', 'release-groups'])
                    for rel in req['artist']['release-group-list']:
                        if SequenceMatcher(None, meta['album'],
                                           rel['title']).ratio() > .9:
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
            params = {'release': self._searchstr(meta['album'])}
            if 'mbidartist' in meta:
                params.update({'arid':  meta['mbidartist']})
            elif 'mbidaartist' in meta:
                params.update({'arid':  meta['mbidaartist']})
            elif 'artist' in meta:
                params.update({'artist': self._searchstr(meta['artist'])})
            elif 'aartist' in meta:
                params.update({'artist': self._searchstr(meta['aartist'])})
            req = mb.search_release_groups(**params)
            data = req.get('release-group-list', [])
            if len(data) > 1:
                data = [d for d in data if 'title' in d and SequenceMatcher
                        (None, meta['album'], d['title']).ratio() > .9]
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

    def __init__(self, session, interactive):
        DataProvider.__init__(self, "Discogs", session, interactive)

    def __query(self, thetype, params):
        """Query Discogs API"""
        data = self._jsonapiquery('http://api.discogs.com/database/search',
                                  {'type': thetype}, params)
        if 'results' not in data:
            return None
        return data['results']

    def _get_data(self, meta, what):
        """Get data from Discogs"""
        if what == 'album':
            data = self.__query('master', {'q': (meta.get('artist') or
                                                 meta.get('aartist', ''))
                                           + ' ' + meta['album']})
            return [{'info': "%s [%s]: http://www.discogs.com/master/%s"
                     % (x.get('title'), x.get('year'), x.get('id')),
                     'tags': x.get('style', []) + x.get('genre', []),
                     'title': x.get('title', ''),
                     'year': x.get('year')}
                    for x in data]
        return None


def get_arguments():
    '''Gets the cmdline arguments from ArgumentParser.'''
    argparse = ArgumentParser(
        formatter_class=ArgumentDefaultsHelpFormatter,
        description='Improves genre metadata of audio files based on tags '
                    'from various music-sites.')
    argparse.add_argument(
        'path', nargs='+', help='folder(s) to scan for albums')
    argparse.add_argument(
        '-v', '--verbose', action='store_true', help='more detailed output')
    argparse.add_argument(
        '-n', '--dry-run', action='store_true', help='don\'t save metadata')
    argparse.add_argument(
        '-i', '--interactive', action='store_true', help='interactive mode')
    argparse.add_argument(
        '-r', '--tag-release', action='store_true',
        help='tag release type (from what.cd)')
    argparse.add_argument(
        '-m', '--tag-mbids', action='store_true', help='tag musicbrainz ids')
    argparse.add_argument(
        '-c', '--use-cache', action='store_true',
        help='cache processed albums')
    argparse.add_argument(
        '-l', '--tag-limit', metavar='N', type=int, default=4,
        help='max. number of genre tags')
    argparse.add_argument(
        '--no-whatcd', action='store_true', help='disable lookup on What.CD')
    argparse.add_argument(
        '--no-lastfm', action='store_true', help='disable lookup on Last.FM')
    argparse.add_argument(
        '--no-mbrainz', action='store_true',
        help='disable lookup on MusicBrainz')
    argparse.add_argument(
        '--no-discogs', action='store_true', help='disable lookup on Discogs')
    argparse.add_argument(
        '--config', default=os.path.expanduser('~/.whatlastgenre/config'),
        help='location of the configuration file')
    argparse.add_argument(
        '--cache', default=os.path.expanduser('~/.whatlastgenre/cache'),
        help='location of the cache')
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
        open(configfile)
        config.read(configfile)
    except:
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
        config.set('scores', 'what.cd', 1.66)
        config.set('scores', 'last.fm', 0.66)
        config.set('scores', 'mbrainz', 1.00)
        config.set('scores', 'discogs', 1.00)
        config.set('scores', 'artists', 1.33)
        config.set('scores', 'splitup', 0.33)
        config.set('scores', 'userset', 0.66)
        config.write(open(configfile, 'w'))
        print("Please edit the configuration file: %s" % configfile)
        sys.exit(2)

    conf = namedtuple('conf', '')
    conf.whatcd_user = config.get('whatcd', 'username')
    conf.whatcd_pass = config.get('whatcd', 'password')
    conf.blacklist = config_list(config.get('genres', 'blacklist'))
    conf.score_up = config_list(config.get('genres', 'score_up'))
    conf.score_down = config_list(config.get('genres', 'score_down'))
    conf.filters = config_list(config.get('genres', 'filters'))
    conf.scores = {k: config.getfloat("scores", k)
                   for k, _ in config.items("scores")}
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
    if not (conf.whatcd_user and conf.whatcd_pass):
        print("No What.CD credentials specified. What.CD support disabled.\n")
        args.no_whatcd = True
    if (args.no_whatcd and args.no_lastfm and args.no_mbrainz
            and args.no_discogs):
        print("Where do you want to get your data from?\nAt least one source "
              "must be activated (multiple sources recommended)!\n")
        sys.exit()
    if args.no_whatcd and args.tag_release:
        print("Can't tag release with What.CD support disabled. "
              "Release tagging disabled.\n")
        args.tag_release = False
    if args.no_mbrainz and args.tag_mbids:
        print("Can't tag MBIDs with MusicBrainz support disabled. "
              "MBIDs tagging disabled.\n")
        args.tag_mbids = False
    if args.dry_run and args.use_cache:
        print("Won't save cache in dry-mode.\n")
    if not tags or 'basictags' not in tags:
        print("Got no basic tags from the tag.txt file.")
        sys.exit()
    for filt in conf.filters:
        if filt != 'year' and 'filter_' + filt not in tags:
            print("The filter '%s' you set in your config doesn't have a "
                  "[filter_%s] section in the tags.txt file.\n" % (filt, filt))


def find_albums(paths):
    """Scans all folders in paths for possible albums."""
    albums = {}
    for path in paths:
        for root, _, files in os.walk(path):
            for afile in files:
                ext = os.path.splitext(afile)[1][1:].lower()
                if ext in ['flac', 'ogg', 'mp3']:
                    albums.update({root: ext})
                    break
    return albums


def main():
    """The main() ... nothing more, nothing less (shut up pylint) ;)"""
    start = time.time()
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
        sys.exit()

    cache = set()
    if args.use_cache:
        try:
            cache = pickle.load(open(args.cache))
        except (IOError, EOFError):
            pickle.dump(cache, open(args.cache, 'wb'))

    dps = []
    session = requests.session()
    if not args.no_mbrainz:
        dps.append(MusicBrainz(session, args.interactive))
    if not args.no_lastfm:
        dps.append(LastFM(session, args.interactive,
                          "54bee5593b60d0a5bf379cedcad79052"))
    if not args.no_whatcd:
        dps.append(WhatCD(session, args.interactive,
                          conf.whatcd_user, conf.whatcd_pass))
    if not args.no_discogs:
        dps.append(Discogs(session, args.interactive))

    errors = []
    stats = defaultdict(int)
    genretags = GenreTags(basetags, conf.scores, conf.filters, args.tag_limit)

    for i, (albumpath, albumext) in enumerate(albums.iteritems()):

        print("\n(%2d/%2d) [" % (i + 1, len(albums)), end='')
        for j in range(40):
            print('#' if j < (i / len(albums) * 40) else '-', end='')
        print("] %.1f%%" % (i / len(albums) * 100))

        if os.path.abspath(albumpath) in cache:
            print("Found %s-album in %s cached, skipping..."
                  % (albumext, albumpath))
            continue

        print("Loading metadata for %s-album in %s..."
              % (albumext, albumpath))
        try:
            album = Album(albumpath, albumext, genretags,
                          ['release' if args.tag_release else None,
                           'mbids' if args.tag_mbids else None])
            genretags.reset(album.filter)
        except AlbumError as err:
            print(err.message)
            errors.append(albumpath)
            continue

        print("Receiving tags for artist=%s, aartist=%s, album=%s%s..."
              % (album.meta.get('artist'),
                 album.meta.get('aartist'),
                 album.meta['album'],
                 ' (VA)' if album.meta['is_va'] else ''))
        for dapr in dps:
            dapr.get_data(album)
            genres = genretags.get()
            VPRINT("Good Genres (%s): %s" % (len(genres), ', '.join
                                (["%s (%.2f)" % (k, v) for k, v in genres])))

        print(album)
        for tag in genretags.get(scores=False, limited=True):
            stats[tag] += 1

        if args.dry_run:
            print("DRY-RUN! Not saving metadata or cache.")
            continue
        try:
            album.save()
        except AlbumError as err:
            print("Could not save album: %s" % err.message)
            errors.append(albumpath)
            continue
        if args.use_cache:
            cache.add(os.path.abspath(albumpath))
            pickle.dump(cache, open(args.cache, 'wb'))

    print("\n...all done!\n")
    print("Tag statistics: %s\n"
          % ', '.join(["%s: %d" % (tag, num) for tag, num in sorted
            (stats.iteritems(), key=operator.itemgetter(1), reverse=True)]))
    print("Time elapsed: %s"
          % datetime.timedelta(seconds=time.time() - start))
    if errors:
        print("\n%d albums with errors:\n%s"
              % (len(errors), '\n'.join(sorted(errors))))

VPRINT = lambda *a, **k: None
VAREGEX = re.compile('^va(rious ?(artists?)?)?$', re.I)

if __name__ == "__main__":
    print("whatlastgenre v%s\n" % __version__)
    main()
