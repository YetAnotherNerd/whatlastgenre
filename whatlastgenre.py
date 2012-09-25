#!/usr/bin/env python
""""whatlastgenre"""

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

# Source Score Multipliers
SCORE_WHATCD = 1.5
SCORE_LASTFM = 0.7
SCORE_MBRAIN = 0.8
SCORE_DISCOG = 1.0

# score_{up,down} +/-x offset and 1+/-x multiplier
SCORE_USER = .25


__version__ = "0.1.3"


class GenreTags:
    """Class for managing the genre tags"""

    basic = [  # basic genre tags (anycase)
        'Acapella', 'Acid', 'Acid Jazz', 'Acid Punk', 'Acoustic',
        'Avantgarde', 'Ballad', 'Bass', 'Beats', 'Bebob', 'Big Band',
        'Black Metal', 'Bluegrass', 'Blues', 'Booty Bass', 'BritPop',
        'Cabaret', 'Celtic', 'Chamber Music', 'Chanson', 'Chillout', 'Chorus',
        'Christian', 'Classic Rock', 'Classical', 'Club', 'Comedy', 'Country',
        'Crossover', 'Cult', 'Dance', 'Dance Hall', 'Darkwave', 'Death Metal',
        'Disco', 'Downtempo', 'Dream', 'Drum & Bass', 'Easy Listening',
        'Electro-Swing', 'Electronic', 'Ethnic', 'Euro-Dance', 'Euro-House',
        'Euro-Techno', 'Fast Fusion', 'Female Vocalist', 'Folk', 'Folk-Rock',
        'Freestyle', 'Funk', 'Fusion', 'Future Jazz', 'Gangsta', 'German',
        'German Hip-Hop', 'Goa', 'Gospel', 'Gothic', 'Gothic Rock', 'Grunge',
        'Hard Rock', 'Hardcore', 'Heavy Metal', 'Hip-Hop', 'House', 'Indie',
        'Industrial', 'Instrumental', 'Jazz', 'Jazz-Hop', 'Jungle', 'Latin',
        'Lo-Fi', 'Meditative', 'Metal', 'Musical', 'New Age', 'New Wave',
        'Noise', 'Oldies', 'Opera', 'Other', 'Pop', 'Progressive Rock',
        'Psychedelic', 'Psychedelic Rock', 'Punk', 'Punk Rock',
        'Rhythm & Blues', 'Rap', 'Rave', 'Reggae', 'Retro', 'Revival',
        'Rhythmic Soul', 'Rock', 'Rock & Roll', 'Salsa', 'Samba', 'Ska',
        'Slow Jam', 'Slow Rock', 'Sonata', 'Soul', 'Soundtrack',
        'Southern Rock', 'Space', 'Speech', 'Swing', 'Symphonic Rock',
        'Symphony', 'Synthpop', 'Tango', 'Tech-House', 'Techno',
        'Thrash Metal', 'Trance', 'Tribal', 'Trip-Hop', 'Vocal']

    replace = {  # regex replacements (lowercase)
        'hip ?hop': 'hip-hop',
        'trip ?hop': 'trip-hop',
        'lo ?fi': 'lo-fi',
        'nu-(.+)': 'nu \\1',
        'synth( |-)pop': 'synthpop',
        # tags with ampersand
        'd(rum)? ?(and|\'?n\'?|&) ?b(ass)?': 'drum & bass',
        'drill ?(and|\'?n\'?|&) ?bass': 'drill & bass',
        'r(hythm)? ?(and|\'?n\'?|&) ?b(lues)?': 'rhythm & blues',
        'rock ?(and|\'?n\'?|&) ?roll': 'rock & roll',
        'stage ?(and|\'?n\'?|&) ?screen': 'stage & screen',
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
        'deutsch(er?$|$)': 'german',
        'liedermacher(in)?': 'singer-songwriter',
        'france': 'french',
        # misc.
        'tv soundtrack': 'soundtrack',
        ' and ': ' & ', '_': '-'}

    dontsplit = [  # tags that should not split up (lowercase)
        'drill & bass', 'drum & bass', 'rhythm & blues', 'rock & roll',
        'stage & screen']

    # tags len>2 that should be uppercase (lowercase)
    uppercase = ['ebm', 'idm']

    filter_country = [  # country/city/nationality filter (lowercase)
        'american', 'australia', 'australian', 'austrian', 'belgien', 'berlin',
        'bristol', 'britain', 'britannique:', 'british', 'canada', 'canadien',
        'china', 'chinese', 'england', 'english', 'france', 'french', 'german',
        'germany', 'hamburg', 'iceland', 'icelandic', 'irish', 'japan',
        'japanese', 'new york', 'new york city', 'new zealand', 'norway',
        'norwegian', 'nyc', 'roma', 'stuttgart', 'uk', 'united kingdom',
        'united states', 'us', 'usa', 'vienna']

    filter_label = [  # label filter (lowercase)
        'creative commons', 'ninja tune', 'smalltown supersound',
        'tru thoughts']

    def __init__(self, tags, limit, filters):
        self.tags = defaultdict(float)
        self.limit = limit
        self.blacklist = tags['black']
        self.score_up = tags['up']
        self.score_down = tags['down']
        self.filters = filters
        # add tags
        self.addlist(self.basic)
        self.addlist(self.blacklist)
        self.addlist(self.score_up, SCORE_USER / (1 + SCORE_USER))
        self.addlist(self.score_down, -SCORE_USER / (1 - SCORE_USER))

    def add(self, name, score):
        """Adds a genre tag with a score."""
        name = name.encode('ascii', 'ignore').lower().strip()
        # replace
        for pattern, repl in self.replace.items():
            name = re.sub(pattern, repl, name)
        # split and fork
        if name.lower() not in self.dontsplit:
            sep = [sep for sep in ['+', '/', ',', '&'] if sep in name]
            if sep:
                self.addlist(name.split(sep[0]), score * .75)
                return
        # filter by length
        if len(name) not in range(2, 21):
            return
        # format
        if len(name) < 3 or name.lower() in self.uppercase:
            name = name.upper()
        elif re.match('[0-9]{4}s', name.lower()):
            name = name.lower()
        else:
            name = name.title()
        # searching for existing tag
        # don't change the cutoff, add replaces instead
        found = get_close_matches(name, self.tags.keys(), 1, .8572)
        if found:
            if (OUT.beverbose and
                    SequenceMatcher(None, name, found[0]).ratio < 0.99):
                OUT.verbose("  %s is the same tag as %s" % (name, found[0]))
            name = found[0]
        # score bonus
        if name in self.score_up:
            score *= 1 + SCORE_USER
        elif name in self.score_down:
            score *= 1 - SCORE_USER
        # finally add it
        self.tags[name] += score

    def addlist(self, tags, score=0):
        """Adds a list of tags with all the same score."""
        for tag in tags:
            self.add(tag, score)

    def addlist_nocount(self, tags, multi=1):
        """Adds a list of countless tags with scoring based on amount."""
        self.addlist(tags, 0.85 ** (len(tags) - 1) * multi)

    def addlist_count(self, tags, multi=1):
        """Adds a list of counted tags with scoring based on count-ratio"""
        if tags:
            top = max(1, max(tags.items(), key=operator.itemgetter(1))[1])
            for name, count in tags.iteritems():
                if count > top * 0.1:
                    self.add(name, count / top * multi)

    def __getgood(self, minscore=0.4):
        """Returns tags with a score higher then minscore."""
        return {name: score for name, score in self.tags.iteritems()
                if score > minscore}

    def get(self):
        """Returns a filtered and limited list of good tags without score."""
        tags = self.__getgood(0.69)
        if self.blacklist:
            tags = [tag for tag in tags if tag not in self.blacklist]
        if 'country' in self.filters:
            tags = [tag for tag in tags
                    if tag.lower() not in self.filter_country]
        if 'label' in self.filters:
            tags = [tag for tag in tags
                    if tag.lower() not in self.filter_label]
        if 'year' in self.filters:
            tags = [tag for tag in tags
                    if re.match('([0-9]{2}){1,2}s?', tag.lower()) is None]
        tags = sorted(tags, key=self.tags.get, reverse=True)
        return tags[:self.limit]

    def listgood(self):
        """Returns a list of unfiltered good tags."""
        tags = ["%s: %.2f" % (name, score) for name, score in
                sorted(self.__getgood().items(),
                       key=operator.itemgetter(1), reverse=True)]
        if tags:
            return "Good tags: %s" % ', '.join(tags)


class Album:
    """Class for managing albums."""

    def __init__(self, path, filetype, genretags, dotag):
        self.path = path
        self.filetype = filetype.lower()
        self.tracks = [track for track in os.listdir(path)
                       if track.lower().endswith('.' + filetype)]
        self.tags = genretags
        self.dotag = dotag
        self.artist = self.aartist = self.album = None
        self.type = self.year = None
        self.is_va = False
        self.mbids = namedtuple('mbids', 'artist aartist album')
        self.__load_metadata()

    def __load_metadata(self):
        """Loads and checks the album metadata from the tracks."""
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
                if (not self.is_va and SequenceMatcher(None, self.artist,
                        self.__get_tag(meta, 'artist')).ratio() < 0.9):
                    self.artist = None
                    self.is_va = True
                if (self.aartist and SequenceMatcher(None, self.aartist,
                        self.__get_tag(meta, 'albumartist')).ratio() < 0.9):
                    self.aartist = None
        except TypeError as err:
            raise AlbumError("Error loading album metadata: %s" % err.message)

    @classmethod
    def __get_tag(cls, meta, tag):
        """Helper method to get the value of a tag from metadata."""
        if (tag.startswith('musicbrainz') and
                isinstance(meta, mutagen.flac.FLAC)):
            tag = tag.upper()
        try:
            return meta[tag][0].encode('ascii', 'ignore')
        except (KeyError, UnicodeEncodeError):
            return None

    @classmethod
    def __set_tag(cls, meta, tag, val):
        """Helper method to set the value to a tag in metadata."""
        if not tag or not val:
            return
        if (tag.startswith('musicbrainz') and
                isinstance(meta, mutagen.flac.FLAC)):
            tag = tag.upper()
        meta[tag] = val

    def get_artist(self):
        """Helper method for easily getting an artist name for searches."""
        if self.artist and not self.is_va:
            return self.artist
        elif self.aartist:
            return self.aartist
        return ''

    def save(self):
        """Saves the metadata to the tracks."""
        print("Saving metadata...")
        tags = self.tags.get()
        try:
            for track in self.tracks:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
                if ('release' in self.dotag and
                        self.filetype in ['flac', 'ogg']):
                    self.__set_tag(meta, 'releasetype', self.type)
                if 'mbids' in self.dotag:
                    self.__set_tag(meta, 'musicbrainz_artistid',
                                   self.mbids.artist)
                    self.__set_tag(meta, 'musicbrainz_albumartistid',
                                   self.mbids.aartist)
                    self.__set_tag(meta, 'musicbrainz_albumid',
                                   self.mbids.album)
                self.__set_tag(meta, 'genre', tags)
                meta.save()
        except Exception as err:
            raise AlbumError("Error saving album metadata: " + err.message)


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
        self.methods = None

    @classmethod
    def _interactive(cls, data, form, cont):
        """Asks the user to choose from a list of possibilites."""
        print("Multiple possibilities found, please choose the right one:")
        for i in range(len(data)):
            print("#%2d:" % (i + 1), end=" ")
            try:
                print(form % cont(data[i]))
            except KeyError:
                print()
        while True:
            try:
                num = int(raw_input("Please Choose # [1-%d] (0 to skip): "
                                  % len(data)))
            except ValueError:
                num = None
            except EOFError:
                num = 0
                print()
            if num in range(len(data) + 1):
                break
        return [data[num - 1]] if num else []

    @classmethod
    def _searchstr(cls, searchstr):
        """Cleans up a string for use in searching."""
        patterns = ['[^\w]', '(volume |vol | and )', '[\(\[\{\)\]\}]', ' +']
        searchstr = searchstr.lower()
        for pattern in patterns:
            searchstr = re.sub(pattern, ' ', searchstr)
        return searchstr.strip()

    def get_data(self, album):
        """Gets data by calling the datagetting-methods of DataProviders."""
        for method in self.methods:
            try:
                method(album)
            except DataProviderError as err:
                OUT.verbose("  %s" % err.message)


class DataProviderError(Exception):
    """If something wents wrong with a DataProvider."""
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
        self.methods = [self.__get_tags_artist, self.__get_tags_album]

    def __query(self, action, args):
        """Query What.CD API"""
        params = {'action': action}
        params.update(args)
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(0.1)
        try:
            req = self.session.get('https://what.cd/ajax.php', params=params)
            self.last_request = time.time()
            jsondata = json.loads(req.content)
            if jsondata['status'] != 'success':
                raise DataProviderError("unsuccessful response (maybe: " \
                                    "artist not found - api inconsistencies)")
            return jsondata['response']
        except (ConnectionError, HTTPError, KeyError, ValueError):
            raise DataProviderError("error while requesting")

    @classmethod
    def __filter_tags(cls, tags):  # (waiting on getting all tags with counts)
        """Filter the tags from What.CD"""
        badtags = ['freely.available', 'staff.picks', 'vanity.house']
        if tags and isinstance(tags[0], dict):
            return {tag['name'].replace('.', ' '): int(tag['count'])
                    for tag in tags if tag['name'] not in badtags}
        return [tag.replace('.', ' ') for tag in tags if tag not in badtags]

    def __get_tags_artist(self, album):
        """Gets the tags for the artist from What.CD"""
        if album.is_va:
            return
        try:
            data = self.__query(
                'artist', {'id': 0,
                           'artistname': self._searchstr(album.get_artist())})
            album.tags.addlist_count(
                self.__filter_tags(data['tags']), self.multi)
        except (TypeError, KeyError):
            raise DataProviderError("No tags for artist found.")

    def __get_tags_album(self, album):
        """Gets the tags for the album from What.CD"""
        try:
            data = self.__query(
                'browse', {'filter_cat[1]': 1, 'searchstr':
                    self._searchstr(album.get_artist() + ' ' + album.album)}
                )['results']
            if len(data) > 1 and not album.is_va:
                data = [d for d in data if d['artist'] != 'Various Artists']
            if len(data) > 1 and album.year:
                try:
                    data = [d for d in data if 'groupYear' in d
                            and abs(int(d['groupYear']) - album.year) <= 2]
                except ValueError:
                    pass
            if len(data) > 1:
                if self.interactive:
                    data = self._interactive(data, "%s - %s [%s] [%s]",
                                 lambda x: (x['artist'], x['groupName'],
                                            x['groupYear'], x['releaseType']))
                else:
                    raise DataProviderError("Too many (%d) album results " \
                                            "(use --interactive)." % len(data))
            if len(data) == 1:
                album.tags.addlist_nocount(self.__filter_tags(data[0]['tags']),
                                       self.multi)
                album.type = data[0]['releaseType']
            else:
                raise DataProviderError("No tags for album found.")
        except KeyError:
            raise DataProviderError("Error reading returned data.")


class LastFM(DataProvider):
    """Class for the DataProvider LastFM"""

    def __init__(self, session, interactive, apikey):
        DataProvider.__init__(self, "Last.FM", SCORE_LASTFM,
                              session, interactive)
        self.apikey = apikey
        self.methods = [self.__get_tags_artist, self.__get_tags_album]

    def __query(self, method, **args):
        """Query Last.FM API"""
        params = {'api_key': self.apikey, 'format': 'json', 'method': method}
        params.update(args)
        try:
            req = self.session.get(
                'http://ws.audioscrobbler.com/2.0/', params=params)
            j = json.loads(req.content)
            return j
        except (ConnectionError, HTTPError, KeyError, ValueError):
            raise DataProviderError("error while requesting")

    @classmethod
    def __filter_tags(cls, album, tags):
        """Filter the tags from Last.FM"""
        # list of dict for multiple tags; single dict for just one tag
        if not isinstance(tags, list):
            tags = [tags]
        # be aware of fuzzy matching below when adding bad tags here
        badtags = [album.get_artist().lower(), album.album.lower(),
            'albums i own', 'amazing', 'awesome', 'cool', 'drjazzmrfunkmusic',
            'epic', 'favorite albums', 'favorites', 'fettttttttttttttt',
            'good', 'love', 'owned', 'seen live', 'sexy', 'television',
            'z3po like this']
        return {tag['name']: int(tag['count']) for tag in tags if
                not get_close_matches(tag['name'].lower(), badtags, 1)
                and int(tag['count']) > 2}

    def __get_tags_artist(self, album):
        """Gets the tags for the artist from Last.FM"""
        if album.is_va:
            return
        try:
            data = self.__query('artist.gettoptags',
                                artist=self._searchstr(album.get_artist()))
            album.tags.addlist_count(
                self.__filter_tags(album, data['toptags']['tag']), self.multi)
        except KeyError:
            raise DataProviderError("No tags for artist found.")

    def __get_tags_album(self, album):
        """Gets the tags for the album from Last.FM"""
        try:
            data = self.__query(
                'album.gettoptags', album=self._searchstr(album.album),
                artist=self._searchstr(
                    'Various Artists' if album.is_va else album.get_artist()))
            album.tags.addlist_count(
                self.__filter_tags(album, data['toptags']['tag']), self.multi)
        except KeyError:
            raise DataProviderError("No tags for album found.")


class MusicBrainz(DataProvider):
    """There are some remedies worse than the disease."""

    def __init__(self, session, interactive):
        DataProvider.__init__(self, "MusicBrainz", SCORE_MBRAIN,
                              session, interactive)
        mb.set_useragent("whatlastgenre", __version__,
                         "http://github.com/YetAnotherNerd/whatlastgenre")
        self.methods = [self.__get_tags_artist, self.__get_tags_album]

    @classmethod
    def __filter_tags(cls, tags):
        """Filter the tags from MusicBrainz"""
        return {tag['name']: int(tag['count']) for tag in tags}

    def __get_tags_artist(self, album):
        """Gets the tags for the artist from MusicBrainz"""
        if album.is_va:
            return
        try:
            if not album.mbids.artist:
                req = mb.search_artists(
                    artist=self._searchstr(album.get_artist()))
                data = req['artist-list']
                if len(data) > 1:
                    try:
                        data = [d for d in data if album.artist == d['name']]
                    except (KeyError, ValueError):
                        pass
                if len(data) > 1:
                    if self.interactive:
                        data = self._interactive(data,
                            "%s [%s] (http://musicbrainz.org/artist/%s)",
                            lambda x: (x['name'],
                                       x['country'] if 'country' in x else '',
                                       x['id']))
                    else:
                        raise DataProviderError("Too many (%d) album " \
                                    "results (use --interactive)." % len(data))
                if len(data) == 1:
                    album.mbids.artist = data[0]['id']

            req = mb.get_artist_by_id(album.mbids.artist, includes=['tags'])
            album.tags.addlist_count(
                self.__filter_tags(req['artist']['tag-list']), self.multi)
        except mb.ResponseError as err:
            raise DataProviderError("response error: %s" % err.cause)
        except (TypeError, IndexError, KeyError):
            raise DataProviderError("No tags for artist found.")

    def __get_tags_album(self, album):
        """Gets the tags for the album from MusicBrainz"""
        try:
            req = mb.search_release_groups(
                artist=self._searchstr(album.get_artist()),
                release=self._searchstr(album.album))
            data = req['release-group-list']
            req = mb.get_release_group_by_id(data[0]['id'], includes=['tags'])
            print(req)
            album.tags.addlist_count(
                self.__filter_tags(req['release-group']['tag-list']),
                self.multi)
        except mb.ResponseError as err:
            raise DataProviderError("response error: %s" % err.cause)
        except (IndexError, KeyError):
            raise DataProviderError("No tags for album found.")


class Discogs(DataProvider):
    """Class for the DataProvider Discogs"""

    def __init__(self, session, interactive):
        DataProvider.__init__(self, "Discogs", SCORE_DISCOG,
                              session, interactive)
        self.methods = [self.__get_tags]

    def __query(self, thetype, **args):
        """Query Discogs API"""
        params = {'type': thetype}
        params.update(args)
        try:
            req = self.session.get(
                'http://api.discogs.com/database/search', params=params)
            jsondata = json.loads(req.content)
            return jsondata['results']
        except (ConnectionError, HTTPError, KeyError, ValueError):
            raise DataProviderError("error while requesting")

    def __get_tags(self, album):
        """Gets the tags from Discogs"""
        try:
            data = self.__query(
                'master', release_title=self._searchstr(album.album))
            if len(data) > 1:
                try:
                    data = [d for d in data
                            if album.get_artist() in d['title']]
                except (TypeError, KeyError, ValueError):
                    pass
            if len(data) > 1 and album.year:
                try:
                    data = [d for d in data
                            if abs(int(d['year']) - album.year) <= 2]
                except (KeyError, ValueError):
                    pass
            if len(data) > 1:
                if self.interactive:
                    data = self._interactive(data,
                                "%s [%s] (http://www.discogs.com/master/%s)",
                                lambda x: (x['title'], x['year'], x['id']))
                else:
                    raise DataProviderError("Too many (%d) album results " \
                                            "(use --interactive)." % len(data))
            if (len(data) == 1 and ('style' in data[0] or 'genre' in data[0])):
                tags = []
                if 'style' in data[0]:
                    tags = tags + data[0]['style']
                if 'genre' in data[0]:
                    tags = tags + data[0]['genre']
                album.tags.addlist_nocount(tags, self.multi)
            else:
                raise DataProviderError("No tags for album found.")
        except KeyError:
            raise DataProviderError("Error reading returned data.")


class Stats:
    """Class for collecting some statistics."""

    def __init__(self):
        self.__tags = defaultdict(int)
        self.__starttime = time.time()

    def add(self, tags):
        """Add tag or increase count for it."""
        for tag in tags:
            self.__tags[tag] += 1

    def printstats(self):
        """Print out the statistics."""
        tags = []
        for tag, num in sorted(self.__tags.iteritems(),
                               key=operator.itemgetter(1), reverse=True):
            tags.append("%s: %d" % (tag, num))
        print("Tag statistics: %s" % ', '.join(tags))
        print("Time elapsed: %s\n"
              % datetime.timedelta(seconds=time.time() - self.__starttime))


class Out:
    """Class for handling output."""

    def __init__(self, verbose=False, colors=False):
        self.beverbose = verbose
        self.usecolors = colors

    def ___print(self, level, msg):
        """Helper method to print out differnt message-levels"""
        if not msg:
            return
        if self.usecolors:
            if level is 'verbose':
                print("\033[0;33m%s\033[0;m" % msg)
            elif level is 'info':
                print("\033[0;36m%s\033[0;m" % msg)
            elif level is 'warning':
                print("\033[1;35mWARNING:\033[0;35m %s\033[0;m" % msg)
            elif level is 'error':
                print("\033[1;31mERROR:\033[0;31m %s\033[0;m" % msg)
            elif level is 'success':
                print("\n\033[1;32mSUCCESS:\033[0;32m %s\033[0;m" % msg)
            else:
                print(msg)
        else:
            if level is 'warning':
                print("WARNING: %s" % msg)
            elif level is 'error':
                print("ERROR: %s" % msg)
            elif level is 'success':
                print("\nSUCESS: %s" % msg)
            else:
                print(msg)

    def verbose(self, msg):
        """Prints verbose messages."""
        if self.beverbose:
            self.___print('verbose', msg)

    def info(self, msg):
        """Prints info messages."""
        self.___print('info', msg)

    def warning(self, msg):
        """Prints warnings."""
        self.___print('warning', msg)

    def error(self, msg):
        """Prints errors."""
        self.___print('error', msg)

    def success(self, msg):
        """Prints sucess messages."""
        self.___print('success', msg)


def get_arguments():
    '''Gets the cmdline arguments from ArgumentParser.'''
    argparse = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter,
                        description='Improves genre metadata of audio ' \
                           'files based on tags from various music-sites.')
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
        '-s', '--stats', action='store_true', help='collect statistics')
    argparse.add_argument(
        '-b', '--use-colors', action='store_true', help='colorful output')
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
    return argparse.parse_args()


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
        config.set('genres', 'blacklist', 'Charts, Live, Unknown')
        config.set('genres', 'score_up', 'Soundtrack')
        config.set('genres', 'score_down',
                   'Electronic, Alternative, Indie, Other, Other')
        config.set('genres', 'filters', 'country, label, year')
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


def validate(args, conf):
    """Validates argument and config settings and fixes them if necessary."""
    if not (conf.whatcd_user and conf.whatcd_pass):
        OUT.warning("No What.CD credentials specified. "
                    "What.CD support disabled.")
        args.no_whatcd = True
    if (args.no_whatcd and args.no_lastfm and args.no_mbrainz
            and args.no_discogs):
        OUT.error("Where do you want to get your data from?")
        OUT.warning("At least one source must be activated "
                    "(multiple sources recommended)!")
        sys.exit()
    if args.no_whatcd and args.tag_release:
        OUT.warning("Can't tag release with What.CD support disabled. "
                    "Release tagging disabled.")
        args.tag_release = False
    if args.no_mbrainz and args.tag_mbids:
        OUT.warning("Can't tag MBIDs with MusicBrainz support disabled."
                    "MBIDs tagging disabled.")
        args.tag_mbids = False
    if args.dry_run and args.use_cache:
        OUT.warning("Can't use cache in dry mode. Cache disabled.")
        args.use_cache = False


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

    # DEVEL Helper
    #args.dry_run = args.verbose = args.stats = True
    #args.interactive = args.tag_release = args.tag_mbids = True
    #args.path.append('/home/foo/nobackup/test')
    #args.path.append('/media/music/Alben/')
    #from random import choice
    #args.path.append(os.path.join(
    #    '/media/music/Alben', choice(os.listdir('/media/music/Alben'))))

    OUT.beverbose = args.verbose
    OUT.usecolors = args.use_colors
    validate(args, conf)

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
        dps.append(MusicBrainz(session, args.interactive))
    if not args.no_lastfm:
        dps.append(LastFM(session, args.interactive,
                          "54bee5593b60d0a5bf379cedcad79052"))
    if not args.no_whatcd:
        dps.append(WhatCD(session, args.interactive,
                          conf.whatcd_user, conf.whatcd_pass))
    if not args.no_discogs:
        dps.append(Discogs(session, args.interactive))

    albums = find_albums(args.path)
    print("Found %d folders with possible albums!" % len(albums))
    errors = []
    i = 0
    for album in albums:
        i = i + 1
        if args.use_cache and album in cache:
            print("Found %s (%d/%d) in cache, skipping..."
                  % (album, i, len(albums)))
            continue

        genretags = GenreTags({'black': conf.blacklist,
                               'up': conf.score_up,
                               'down': conf.score_down},
                              args.tag_limit, conf.filters)

        print("\nGetting metadata for %s-album (%d/%d) in %s..."
              % (albums[album], i, len(albums), album))
        try:
            album = Album(album, albums[album], genretags,
                          ['release' if args.tag_release else None,
                           'mbids' if args.tag_mbids else None])
        except AlbumError as err:
            OUT.error("Could not get album: %s" % err.message)
            errors.append(album)
            continue

        print("Getting tags for '%s - %s'..." % (album.get_artist()
                                if album.get_artist() else 'VA', album.album))
        for datapro in dps:
            OUT.verbose("%s..." % datapro.name)
            try:
                datapro.get_data(album)
            except DataProviderError as err:
                OUT.error("Could not get data from %s: %s"
                          % (datapro.name, err.message))
            else:
                OUT.verbose(album.tags.listgood())

        if args.tag_release and album.type:
            print("Release type: %s" % album.type)

        if args.tag_mbids and (album.mbids.artist or album.mbids.album):
            print("MBIDs: artist=%s, album=%s"
                  % (album.mbids.artist, album.mbids.album))

        tags = album.tags.get()
        if tags:
            print("Genre tags: %s" % ', '.join(tags))
            if args.stats:
                stats.add(tags)
        else:
            print("No or not good enough tags found :-(")

        if not args.dry_run:
            try:
                album.save()
            except AlbumError as err:
                OUT.error("Could not save album: %s" % err.message)
                errors.append(album)
            else:  # save every time, in case of user abort
                if args.use_cache:
                    cache.add(album.path)
                    pickle.dump(cache, open(args.cache, 'wb'))

    OUT.success("...all done!\n")
    if args.stats:
        stats.printstats()
    if errors:
        errors.sort()
        print("Albums with errors:\n%s" % '\n'.join(errors))


if __name__ == "__main__":
    print("whatlastgenre v%s\n" % __version__)
    OUT = Out()
    main()
