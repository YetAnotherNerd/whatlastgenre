#!/usr/bin/env python
""""whatlastgenre
Improves genre metadata of audio files based on tags from various music-sites.
http://github.com/YetAnotherNerd/whatlastgenre
"""

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


# source score multipliers
SCORE_WHATCD = 1.5
SCORE_LASTFM = 0.7
SCORE_MBRAIN = 1.0
SCORE_DISCOG = 1.0

# score multiplier for tags based on artist
SCORE_ARTIST = 1.5

# score_{up,down} +/-x offset and 1+/-x multiplier
SCORE_USER = .25


__version__ = "0.1.9"


print_verbose = lambda *a, **k: None


class GenreTags:
    """Class for managing the genre tags"""

    replace = {  # regex replacements (lowercase)
        '(h|tr)ip ?hop': '\\1ip-hop',
        'lo ?fi': 'lo-fi',
        'nu-(.+)': 'nu \\1',
        'synth( |-)pop': 'synthpop',
        # tags with ampersand
        'd(rum)? ?(and|\'?n\'?|&) ?b(ass)?': 'drum & bass',
        'r(hythm)? ?(and|\'?n\'?|&) ?b(lues)?': 'rhythm & blues',
        'drill ?(and|\'?n\'?|&) ?bass': 'drill & bass',
        'rock ?(and|\'?n\'?|&) ?roll': 'rock & roll',
        'stage ?(and|\'?n\'?|&) ?screen': 'stage & screen',
        'hard ?(and|\'?n\'?|&) ?heavy': 'hard & heavy',
        # year related
        '^(19)?([3-9])[0-9](s|er)?$': '19\g<2>0s',
        '^(20)?([0-2])[0-9](s|er)?$': '20\g<2>0s',
        'best of ([0-9]{2}){1,2}s?': 'charts',
        'top [0-9]{2,3}': 'charts',
        # abbreviation related
        'chill$': 'chillout',
        '^prog\.?( |-)': 'progressive ',
        'goth( |-|$)': 'gothic',
        '^world$': 'world music',
        '^ost$': 'soundtrack',
        'sci(ence)?( |-)?fi(ction)?': 'science fiction',
        # country/language related
        'deutsch(er | )?': 'german',
        'liedermacher(in)?': 'singer-songwriter',
        # misc.
        'tv soundtrack': 'soundtrack',
        'rapper': 'rap',
        ' and ': ' & ',
        '_': '-'}

    def __init__(self, basetags, limit, filters):
        self.tags = defaultdict(float)
        self.basetags = basetags
        self.limit = limit
        self.filters = filters
        self.__add_list(basetags.get('_score_up'), +SCORE_USER)
        self.__add_list(basetags.get('_score_dn'), -SCORE_USER)
        for filt in self.filters:
            self.__add_list(self.basetags.get('filter_' + filt))

    def clear(self):
        """Clears the genre tags."""
        self.tags = defaultdict(float)

    def add(self, name, score):
        """Adds a genre tag with a score."""
        name = name.encode('ascii', 'ignore').lower().strip()
        # filter by length
        if len(name) not in range(2, 21):
            return
        # replace
        for pattern, repl in self.replace.items():
            name = re.sub(pattern, repl, name)
        # split and fork
        if name.lower() not in self.basetags.get('dontsplit'):
            sep = [sep for sep in ['+', '/', '&', ',', ' and '] if sep in name]
            if sep:
                self.__add_list(name.split(sep[0]), score * .75)
                return
        # format
        name = name.title()
        if len(name) < 3 or name.lower() in self.basetags.get('uppercase'):
            name = name.upper()
        elif re.match('[0-9]{4}S', name):
            name = name.lower()
        # searching for existing tag
        # don't change the cutoff, add replaces instead
        for taglist in [self.basetags.get('basictags'), self.tags.keys()]:
            match = get_close_matches(name, taglist, 1, .8572)
            if match:
                name = match[0]
        # score bonus
        if name.lower() in self.basetags.get('_score_up'):
            score *= 1 + SCORE_USER
        elif name.lower() in self.basetags.get('_score_dn'):
            score *= 1 - SCORE_USER
        # finally add it
        self.tags[name] += score

    def __add_list(self, tags, score=0):
        """Adds a list of tags with all the same score."""
        for tag in tags or []:
            self.add(tag, score)

    def add_tags(self, tags, multi=1):
        """Adds tags with counts and scoring based on count-ratio
        or without count and scoring based on amount."""
        if not tags:
            return
        if isinstance(tags, dict):
            top = max(1, max(tags.items(), key=operator.itemgetter(1))[1])
            for name, count in tags.iteritems():
                if count > top * .1:
                    self.add(name, count / top * multi)
        elif isinstance(tags, list):
            self.__add_list(tags, .85 ** (len(tags) - 1) * multi)

    def get(self, minscore=SCORE_USER,
            filtered=True, scores=True, limited=False):
        """Gets the tags by minscore, with or without scores,
        filtered and/or limited"""
        tags = {k: v for k, v in self.tags.iteritems() if v > minscore}
        if filtered:
            if 'year' in self.filters:
                tags = {k: v for k, v in tags.iteritems() if not
                        re.match('([0-9]{2}){1,2}s?', k.lower())}
            for filt in self.filters:
                tags = {k: v for k, v in tags.iteritems() if k.lower()
                        not in (self.basetags.get('filter_' + filt) or [])}
        if scores:
            tags = sorted(tags.items(), key=operator.itemgetter(1),
                          reverse=True)
        else:
            tags = sorted(tags, key=tags.get, reverse=True)
        if limited:
            return tags[:self.limit]
        return tags


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
        out += "Genres: %d - Chosen: %s" % (len(self.tags.tags), ', '.join
            (["%s (%.2f)" % (k, v) for k, v in self.tags.get(limited=True)]))
        return out

    def __load_metadata(self):
        """Loads and checks the album metadata from the tracks."""
        rva = re.compile('^v(arious)?( ?a(rtists?)?)?', re.IGNORECASE)
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
            try:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
            except mutagen.flac.FLACNoHeaderError as err:
                raise AlbumError("Error loading Metadata: %s" % err.message)
            for tag, tagname in tags.iteritems():
                if (tagname.startswith('musicbrainz') and
                        isinstance(meta, mutagen.flac.FLAC)):
                    tagname = tagname.upper()
                try:
                    value = meta[tagname][0].encode('ascii', 'ignore')
                    if tag is 'year':
                        value = int(value[:4])
                except (KeyError, ValueError, UnicodeEncodeError):
                    value = None
                if value:
                    taglist[tag].append(value)

        for tag, tlist in taglist.iteritems():
            tset = set(tlist)
            if len(tset) == 0:
                continue
            elif len(tset) == 1:
                if tag in ['artist', 'aartist'] and rva.match(tlist[0]):
                    self.meta['is_va'] = True
                else:
                    self.meta[tag] = tlist[0]
            elif tag in ['album', 'mbidrelease', 'mbidrelgrp']:
                raise AlbumError("Not all tracks have an equal %s-tag!" % tag)
            elif tag in ['artist', 'aartist']:
                self.meta['is_va'] = True

        if 'album' not in self.meta:
            raise AlbumError("There is not even an album tag (untagged?)")
        print_verbose("Metadata: %s" % self.meta)

    def save(self):
        """Saves the metadata to the tracks."""
        tags = {"mbidartist": "musicbrainz_artistid",
                "mbidaartist": "musicbrainz_albumartistid",
                "mbidrelease": "musicbrainz_albumid",
                "mbidrelgrp": "musicbrainz_releasegroupid"}
        genres = self.tags.get(scores=False, limited=True)
        error = []
        for track in self.tracks:
            try:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
                meta['genre'] = genres
                if 'release' in self.dotag and self.meta.get('releasetype') \
                        and self.filetype in ['flac', 'ogg']:
                    meta['releasetype'] = self.meta.get('releasetype')
                if 'mbids' in self.dotag:
                    for tag, tagname in tags.iteritems():
                        if self.filetype is 'mp3' and tag is 'mbidrelgrp':
                            continue
                        if self.meta.get(tag):
                            if self.filetype is 'flac':
                                tagname = tagname.upper()
                            meta[tagname] = self.meta.get(tag)
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
    """Base class for Data Providers. What all DPs have in common."""

    def __init__(self, name, multi, session, interactive):
        self.name = name
        self.multi = multi
        self.session = session
        self.interactive = interactive

    def _jsonapiquery(self, url, params, sparams=None):
        """Method for querying json-apis."""
        if sparams:
            for key, val in sparams.items():
                params.update({key: self._searchstr(val)})
        try:
            req = self.session.get(url, params=params)
            data = json.loads(req.content)
        except (ConnectionError, HTTPError, ValueError) as err:
            raise DataProviderError("Request error: %s" % err.message)
        return data

    def _interactive(self, albumpath, data):
        """Asks the user to choose from a list of possibilites."""
        print("\aMultiple results from %s, which is right one?" % self.name)
        print_verbose("Path: %s" % albumpath)
        for i in range(len(data)):
            print("#%2d:" % (i + 1), end=" ")
            print(data[i].get('info'))
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

    @classmethod
    def _searchstr(cls, searchstr):
        """Cleans up a string for use in searching."""
        if not searchstr:
            return ''
        patterns = ['[^\w\'\-\.:&]', '(volume |vol\.? | and )',
                    '\(.*\)', '\[.*\]', '{.*}', ' +']
        searchstr = searchstr.lower()
        for pattern in patterns:
            searchstr = re.sub(pattern, ' ', searchstr)
        return searchstr.strip()

    def _get_data(self, album, what):
        """Get data from a DataProvider (this should be overridden by DPs)"""
        pass

    def get_data(self, album):
        """Getting data from DataProviders."""
        for what in ['artist', 'aartist', 'album']:
            if isinstance(self, Discogs) and what in ['artist', 'aartist'] \
                    or not album.meta.get(what):
                continue
            print_verbose("%s: %s search..." % (self.name, what))
            try:
                data = self._get_data(album.meta, what)
                if not data:
                    raise DataProviderError("%s search found nothing." % what)
                if len(data) > 1 and album.meta.get('year'):
                    for i in range(4):
                        tmp = [d for d in data if not d.get('year') or abs(int
                               (d.get('year')) - album.meta.get('year')) <= i]
                        if tmp:
                            data = tmp
                            break
                if len(data) > 1 and what is 'album' and (album.meta.get
                                ('artist') or album.meta.get('aartist')):
                    for i in range(6):
                        tmp = [d for d in data if SequenceMatcher(None,
                               (album.meta.get('artist') or album.meta.get
                                ('aartist')) + ' - ' + album.meta.get('album'),
                                d.get('title')).ratio() > (10 - i) * 0.1]
                        if tmp:
                            data = tmp
                            break
                if len(data) > 1 and self.interactive:
                    data = self._interactive(album.path, data)
                if len(data) > 1:
                    raise DataProviderError("%s search returned too many "
                                            "results: %d (use --interactive)"
                                            % (what, len(data)))
                data = data[0]
                if isinstance(self, WhatCD) and 'releasetype' in data:
                    album.meta['releasetype'] = data.get('releasetype')
                elif isinstance(self, MusicBrainz):
                    for mbid in ['mbidartist', 'mbidaartist',
                                 'mbidrelgrp', 'mbidrelease']:
                        if mbid in data:
                            album.meta[mbid] = data.get(mbid)
                if 'tags' in data:
                    print_verbose("%s: %s search found %d tags."
                                  % (self.name, what, len(data.get('tags'))))
                    multi = self.multi
                    if what in ['artist', 'aartist']:
                        multi *= SCORE_ARTIST
                    album.tags.add_tags(data.get('tags'), multi)
            except DataProviderError as err:
                print("%s: %s" % (self.name, err.message))
            except (mb.ResponseError, mb.NetworkError) as err:
                print("%s: %s" % (self.name, err.cause))


class DataProviderError(Exception):
    """If something went wrong with a DataProvider."""
    pass


class WhatCD(DataProvider):
    """Class for the DataProvider WhatCD"""

    def __init__(self, session, interactive, username, password):
        DataProvider.__init__(self, "What.CD", SCORE_WHATCD,
                              session, interactive)
        self.session.post('https://what.cd/login.php',
                          {'username': username, 'password': password})
        self.last_request = time.time()
        self.rate_limit = 2.0  # min. seconds between requests

    def __query(self, params, sparams):
        """Query What.CD API"""
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(.1)
        data = self._jsonapiquery('https://what.cd/ajax.php', params, sparams)
        self.last_request = time.time()
        if data['status'] != 'success' or 'response' not in data:
            return None
        return data['response']

    @classmethod
    def __filter_tags(cls, tags):  # (waiting on getting all tags with counts)
        """Filter the tags from What.CD"""
        badtags = ['freely.available', 'staff.picks', 'vanity.house']
        if tags and isinstance(tags[0], dict):
            return {tag['name'].replace('.', ' '): int(tag['count'])
                    for tag in tags if tag['name'] not in badtags}
        return [tag.replace('.', ' ') for tag in tags if tag not in badtags]

    def _get_data(self, meta, what):
        """Get data from What.CD"""
        if what in ['artist', 'aartist']:
            data = self.__query({'action': 'artist', 'id': 0},
                                {'artistname': meta.get(what)})
            if data and data.get('tags'):
                return [{'tags': self.__filter_tags(data.get('tags'))}]
        elif what is 'album':
            searchstr = meta.get('album') + ' ' + (
                        meta.get('artist') or meta.get('aartist') or '')
            data = self.__query({'action': 'browse', 'filter_cat[1]': 1},
                                {'searchstr': searchstr})['results']
            if len(data) > 1:
                data = [d for d in data if meta.get('is_va') and
                        d.get('artist') == 'Various Artists' or
                        d.get('artist') != 'Various Artists']
            return [{'info': ("%s - %s (%s) [%s]"
                              % (d.get('artist'), d.get('groupName'),
                                 d.get('groupYear'), d.get('releaseType'))),
                     'title': d.get('artist') + ' - ' + d.get('groupName'),
                     'releasetype': d.get('releaseType'),
                     'tags': self.__filter_tags(d.get('tags')),
                     'year': d.get('groupYear')} for d in data]
        return None


class LastFM(DataProvider):
    """Class for the DataProvider LastFM"""
    def __init__(self, session, interactive, apikey):
        DataProvider.__init__(self, "Last.FM", SCORE_LASTFM,
                              session, interactive)
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

    @classmethod
    def __filter_tags(cls, meta, tags):
        """Filter the tags from Last.FM"""
        # list of dict for multiple tags; single dict for just one tag
        if not isinstance(tags, list):
            tags = [tags]
        badtags = [  # be aware of fuzzy matching below when adding here
            'albums i own', 'amazing', 'awesome', 'cool', 'drjazzmrfunkmusic',
            'epic', 'favorite albums', 'favorites', 'fettttttttttttttt',
            'good', 'love', 'owned', 'seen live', 'sexy', 'television',
            'z3po like this']
        for tag in ['artist', 'aartist', 'album']:
            if meta.get(tag):
                badtags += meta.get(tag).lower()
        return {tag['name']: int(tag['count']) for tag in tags if
                not get_close_matches(tag['name'].lower(), badtags, 1)
                and int(tag['count']) > 2}

    def _get_data(self, meta, what):
        """Get data from Last.FM"""
        data = None
        if what in ['artist', 'aartist']:
            if meta.get('mbid' + what):
                print_verbose("  Using %s-MBID: %s"
                              % (what, meta.get('mbid' + what)))
                data = self.__query({'method': 'artist.gettoptags',
                                     'mbid': meta.get('mbid' + what)})
            if not data:
                data = self.__query({'method': 'artist.gettoptags'},
                                    {'artist': meta.get(what)})
        elif what is 'album':
            for mbid in ['release', 'relgrp']:
                if meta.get('mbid' + mbid) and not data:
                    print_verbose("  Using %s-MBID: %s"
                                  % (mbid, meta.get('mbid' + mbid)))
                    data = self.__query({'method': 'album.gettoptags',
                                         'mbid': meta.get('mbid' + mbid)}, {})
            if not data:
                data = self.__query({'method': 'album.gettoptags'},
                                    {'album': meta.get('album'),
                                     'artist': 'Various Artists' if
                                     meta.get('is_va') else
                                     meta.get('artist') or
                                     meta.get('aartist') or ''})

        if data and data.get('toptags') and data.get('toptags').get('tag'):
            return [{'tags': self.__filter_tags
                     (meta, data.get('toptags').get('tag'))}]
        return None


class MusicBrainz(DataProvider):
    """There are some remedies worse than the disease."""

    def __init__(self, session, interactive):
        DataProvider.__init__(self, "MusicBrainz", SCORE_MBRAIN,
                              session, interactive)
        mb.set_useragent("whatlastgenre", __version__,
                         "http://github.com/YetAnotherNerd/whatlastgenre")

    @classmethod
    def __filter_tags(cls, tags):
        """Filter the tags from MusicBrainz"""
        return {tag['name']: int(tag['count']) for tag in tags or []}

    def _get_data(self, meta, what):
        """Get data from MusicBrainz"""
        data = None
        if what in ['artist', 'aartist']:
            # search by mbid
            if meta.get('mbid' + what):
                print_verbose("  Using %s-MBID: %s"
                              % (what, meta.get('mbid' + what)))
                req = mb.get_artist_by_id(meta.get('mbid' + what),
                                          includes=['tags'])
                if not req or not req.get('artist'):
                    print_verbose("  %s not found, deleting invalid MBID"
                                  % what)
                    meta['mbid' + what] = None
                else:
                    return [{'tags': self.__filter_tags(req.get('artist').
                                                        get('tag-list')),
                             'mbid' + what: req.get('artist').get('id')}]
            if not data:
                req = mb.search_artists(artist=self._searchstr(meta.get(what)))
                data = req.get('artist-list', [])
                if len(data) > 1:
                    data = [d for d in data if SequenceMatcher(None,
                            meta.get(what), d.get('name', '')).ratio() > .9]
                if len(data) > 1:
                    tmp = []
                    for dat in data:
                        req = mb.get_artist_by_id(dat.get('id'),
                                        includes=['tags', 'release-groups'])
                        for rel in req.get('artist').get('release-group-list'):
                            if SequenceMatcher(None, meta.get('album'),
                                               rel.get('title')).ratio() > .9:
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
                     'tags': self.__filter_tags(x.get('tag-list')),
                     'mbid' + what: x.get('id')} for x in data]

        elif what is 'album':
            # search album by release mbid
            if not meta.get('mbidrelgrp') and meta.get('mbidrelease'):
                print_verbose("  Using release-MBID: %s"
                              % meta.get("mbidrelease"))
                req = mb.get_release_by_id(meta.get("mbidrelease"),
                                           includes=['release-groups'])
                if req and req.get('release'):
                    meta['mbidrelgrp'] = req.get('release') \
                                            .get('release-group').get('id')
                else:
                    print_verbose("  Release not found, deleting invalid MBID")
                    meta['mbidrelease'] = None

            # search album by release-group mbid
            if meta.get('mbidrelgrp'):
                print_verbose("  Using relgrp-MBID: %s"
                              % meta.get('mbidrelgrp'))
                req = mb.get_release_group_by_id(meta.get('mbidrelgrp'),
                                                 includes=['tags'])
                if req and req.get('release-group'):
                    data = [req.get('release-group')]
                else:
                    print_verbose("  Rel-Grp not found, deleting invalid MBID")
                    meta['mbidrelgrp'] = None

            if not data:
                params = {'release': self._searchstr(meta.get('album'))}
                if meta.get('mbidartist') or meta.get('mbidaartist'):
                    params.update({'arid':
                                   meta.get('mbidartist') or
                                   meta.get('mbidaartist')})
                elif meta.get('artist') or meta.get('aartist'):
                    params.update({'artist':
                                   self._searchstr(meta.get('artist') or
                                                   meta.get('aartist'))})
                req = mb.search_release_groups(**params)
                data = req.get('release-group-list', [])
                if len(data) > 1:
                    data = [d for d in data if SequenceMatcher(None, meta.
                            get('album'), d.get('title', '')).ratio() > .9]

            return [{'info': "%s - %s [%s]: http://musicbrainz.org"
                     "/release-group/%s"
                     % (x.get('artist-credit-phrase'), x.get('title'),
                        x.get('type'), x.get('id')),
                     'title': x.get('artist-credit-phrase', '') + ' - '
                        + x.get('title', ''),
                     'tags': self.__filter_tags(x.get('tag-list')),
                     'mbidrelgrp': x.get('id')} for x in data]
        return None


class Discogs(DataProvider):
    """Class for the DataProvider Discogs"""

    def __init__(self, session, interactive):
        DataProvider.__init__(self, "Discogs", SCORE_DISCOG,
                              session, interactive)

    def __query(self, thetype, params):
        """Query Discogs API"""
        data = self._jsonapiquery('http://api.discogs.com/database/search',
                                  {'type': thetype}, params)
        if 'results' not in data:
            return None
        return data['results']

    def _get_data(self, meta, what):
        """Get data from Discogs"""
        if what is 'album':
            data = self.__query('master', {'q': (meta.get('artist') or
                                                 meta.get('aartist') or '')
                                           + ' ' + meta.get('album')})
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
        global print_verbose
        print_verbose = print

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
        config.set('genres', 'blacklist', 'charts, composer, live, unknown')
        config.set('genres', 'score_up', 'soundtrack')
        config.set('genres', 'score_down',
                   'electronic, alternative, indie, other, other')
        config.set('genres', 'filters', 'location, label, year')
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
    return conf


def get_tags_from_file(tagsfile):
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
            sectionmatch = re.match("\[(.*)\]", line)
            if sectionmatch:
                if section and taglist:
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
    if not tags or not tags.get('basictags'):
        print("Got no basic tags from the tag.txt file.")
        sys.exit()
    for filt in conf.filters:
        if filt != 'year' and not tags.get('filter_' + filt):
            print("The filter you specified in your config has no [filter_%s] "
                  "section with tags in the tags file.\n" % filt)


def find_albums(paths):
    """Scans all folders in paths for possible albums."""
    albums = {}
    for path in paths:
        for root, _, files in os.walk(path):
            for afile in files:
                ext = os.path.splitext(os.path.join(root, afile))[1]
                if ext.lower() in [".flac", ".ogg", ".mp3"]:
                    albums.update({root: ext[1:]})
                    break
    return albums


def main():
    """The main() ... nothing more, nothing less (shut up pylint) ;)"""
    args = get_arguments()
    conf = get_configuration(args.config)
    basetags = get_tags_from_file('tags.txt')
    basetags.update({"_score_up": conf.score_up})
    basetags.update({"_score_dn": conf.score_down})
    if conf.blacklist:
        conf.filters.append('blacklist')
        basetags.update({"filter_blacklist": conf.blacklist})
    validate(args, conf, basetags)

    albums = find_albums(args.path)
    print("Found %d folders with possible albums!" % len(albums))
    if len(albums) == 0:
        sys.exit()

    dps = []
    start = time.time()
    stats = defaultdict(int)
    cache = set()
    errors = []
    session = requests.session()
    genretags = GenreTags(basetags, args.tag_limit, conf.filters)
    if args.use_cache:
        try:
            cache = pickle.load(open(args.cache))
        except (IOError, EOFError):
            pickle.dump(cache, open(args.cache, 'wb'))
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

    for i, (albumpath, albumext) in enumerate(albums.iteritems(), 1):

        print("\n(%2d/%2d) [" % (i, len(albums)), end='')
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
            genretags.clear()
            album = Album(albumpath, albumext, genretags,
                          ['release' if args.tag_release else None,
                           'mbids' if args.tag_mbids else None])
        except AlbumError as err:
            print(err.message)
            errors.append(albumpath)
            continue

        print("Receiving tags for artist=%s, aartist=%s, album=%s%s..."
              % (album.meta.get('artist'),
                 album.meta.get('aartist'),
                 album.meta.get('album'),
                 ' (VA)' if album.meta.get('is_va') else ''))
        for dapr in dps:
            print_verbose("%s..." % dapr.name)
            dapr.get_data(album)
            print_verbose("Good Genres: %s" % ', '.join(["%s (%.2f)" % (k, v)
                                            for k, v in genretags.get()]))

        print(album)
        for tag in genretags.get(scores=False, limited=True):
            stats[tag] += 1

        if args.dry_run:
            print("DRY-RUN! Not saving metadata or cache.")
        else:
            try:
                print("Saving metadata...")
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
        print("\n%d Albums with errors:\n%s"
              % (len(errors), '\n'.join(sorted(errors))))

if __name__ == "__main__":
    print("whatlastgenre v%s\n" % __version__)
    main()
