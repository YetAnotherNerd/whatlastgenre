#!/usr/bin/env python
'''
TODO:
 * add tag filters for labels, countries
 * merge tags before adding (to enable bonus for multiple hits)
 * mbrainz, lastfm interactivity
'''

from __future__ import division
from ConfigParser import SafeConfigParser
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import namedtuple
from difflib import get_close_matches, SequenceMatcher
from requests.exceptions import HTTPError
import datetime
import json
import musicbrainzngs
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


class Album:
    def __init__(self, path, filetype, genretags, tagrelease=False):
        self.path = path
        self.filetype = filetype.lower()
        self.tracks = [track for track in os.listdir(path) if track.lower().endswith('.' + filetype)]
        self.tags = genretags
        self.tagrelease = tagrelease
        self.__load_metadata()
    
    def __load_metadata(self):
        self.va = False
        self.type = None
        try:
            meta = mutagen.File(os.path.join(self.path, self.tracks[0]), easy=True)
            for track in self.tracks:
                meta2 = mutagen.File(os.path.join(self.path, track), easy=True)
                if not meta2:
                    raise AlbumLoadException("Error loading metadata for %s." % track)
                if SequenceMatcher(None, meta['album'][0], meta2['album'][0]).ratio() < 0.9:
                    raise AlbumLoadException("Not all tracks have the same album-tag!")
                if SequenceMatcher(None, meta['artist'][0], meta2['artist'][0]).ratio() < 0.9:
                    self.va = True
                    
            self.artist = meta['artist'][0].decode('ascii', 'ignore') if not self.va else ''
            self.album = meta['album'][0].decode('ascii', 'ignore')
            try:
                self.year = int(meta['date'][0][:4])
            except ValueError:
                self.year = None
        except KeyError, e:
            raise AlbumLoadException("Error loading album metadata: %s" % e.message)
    
    def save(self):
        print "Saving metadata..."
        tags = self.tags.get()
        try:
            for track in self.tracks:
                meta = mutagen.File(os.path.join(self.path, track), easy=True)
                if self.tagrelease and self.type and self.filetype in ['flac', 'ogg']:
                    meta['release'] = self.type
                if tags:
                    meta['genre'] = tags
                meta.save()
        except IOError, e: # FIXME: replace with real exceptions
            raise AlbumSaveException("Error loading album metadata: " + e.message)


class AlbumLoadException(Exception): pass
class AlbumSaveException(Exception): pass


class GenreTags:
    def __init__(self, limit, score_up, score_down, whitelist, blacklist, uppercase):
        self.tags = {}
        self.limit = limit
        self.whitelist = whitelist
        self.blacklist = blacklist
        self.uppercase = uppercase
        self.score_up = score_up
        self.score_down = score_down
        # add some basic genre tags (from id3)
        tags = ['Acapella', 'Acid', 'Acid Jazz', 'Acid Punk', 'Acoustic', 'Alternative',
                'Alternative Rock', 'Ambient', 'Anime', 'Avantgarde', 'Ballad', 'Bass', 'Beats',
                'Bebob', 'Big Band', 'Black Metal', 'Bluegrass', 'Blues', 'Booty Bass', 'BritPop',
                'Cabaret', 'Celtic', 'Chamber Music', 'Chanson', 'Chorus', 'Christian',
                'Classic Rock', 'Classical', 'Club', 'Comedy', 'Country', 'Crossover', 'Cult',
                'Dance', 'Dance Hall', 'Darkwave', 'Death Metal', 'Disco', 'Dream', 'Drum & Bass',
                'Easy Listening', 'Electronic', 'Ethnic', 'Euro-House', 'Euro-Techno', 'Euro-Dance',
                'Fast Fusion', 'Folk', 'Folk-Rock', 'Freestyle', 'Funk', 'Fusion', 'Gangsta', 'Goa',
                'Gospel', 'Gothic', 'Gothic Rock', 'Grunge', 'Hard Rock', 'Hardcore', 'Heavy Metal',
                'Hip-Hop', 'House', 'Indie', 'Industrial', 'Instrumental', 'Jazz', 'Jazz+Funk',
                'Jungle', 'Latin', 'Lo-Fi', 'Meditative', 'Metal', 'Musical', 'New Age', 'New Wave',
                'Noise', 'Oldies', 'Opera', 'Other', 'Pop', 'Progressive Rock', 'Psychedelic',
                'Psychedelic Rock', 'Punk', 'Punk Rock', 'R&B', 'Rap', 'Rave', 'Reggae', 'Retro',
                'Revival', 'Rhythmic Soul', 'Rock', 'Rock & Roll', 'Salsa', 'Samba', 'Ska', 'Slow Jam',
                'Slow Rock', 'Sonata', 'Soul', 'Soundtrack', 'Southern Rock', 'Space', 'Speech',
                'Swing', 'Symphonic Rock', 'Symphony', 'Synthpop', 'Tango', 'Techno', 'Thrash Metal',
                'Trance', 'Tribal', 'Trip-Hop', 'Vocal']
        self.addlist(tags, 0.05)
        # TODO: add more
        tags = ['Chillout', 'Downtempo', 'Electro-Swing', 'Female Vocalist', 'Future Jazz',
                'German', 'German Hip-Hop', 'Jazz-Hop', 'Tech-House']
        self.addlist(tags, 0.1)
        self.addlist(score_up, 0.2)
        self.addlist(score_down, -0.2)

    def __replace_tag(self, tag):
        rplc = {'deutsch': 'german', 'dnb': 'd&b', 'france': 'french', 'hiphop': 'hip-hop',
                'hip hop': 'hip-hop', 'prog ': 'progressive', 'rnb': 'r&b', 'rhythm and blues': 'r&b',
                'rhythm & blues': 'r&b', 'triphop': 'trip-hop', 'trip hop': 'trip-hop',
                'lo fi': 'lo-fi', ' and ': ' & ' }
        for (a, b) in rplc.items():
            if a in tag.lower():
                return string.replace(tag.lower(), a, b)
        return tag
    
    def __format_tag(self, tag):
        if tag.upper() in self.uppercase:
            return string.upper(tag)
        return tag.title()

    def add(self, name, score):
        if not (len(name) in range(2, 21)
            and re.match('([0-9]{2}){1,2}s?', name) is None
            and score > 0.05):
            return
        name = self.__replace_tag(name)
        name = self.__format_tag(name)
        #out.verbose("  %s (%.3f)" % (name, score))
        found = get_close_matches(name, self.tags.keys(), 1, 0.875) # 0.858 don't change this, add replaces instead
        if found:
            if found[0] in self.score_up: score = score * 1.2
            elif found[0] in self.score_down: score = score * 0.8
            self.tags[found[0]] = (self.tags[found[0]] + score) * 1.05 # TODO: bonus only if merged before
            if out._verbose and SequenceMatcher(None, name, found[0]).ratio() < 0.9:
                out.verbose("  %s is the same tag as %s (%.3f)" % (name, found[0], self.tags[found[0]]))
        else:
            self.tags.update({name: score})
    
    def addlist(self, tags, score):
        for tag in tags:
            self.add(tag, score)
    
    def addlist_nocount(self, tags, multi):
        for tag in tags:
            self.add(tag, 0.85 ** (len(tags) - 1) * multi)

    def addlist_count(self, tags, multi):
        if tags:
            top = max(1, max(tags.iteritems(), key=operator.itemgetter(1))[1])
            for name, count in tags.iteritems():
                if count > top * 0.1:
                    self.add(name, count / top * multi)

    def get(self):
        tags = {name: score for name, score in self.tags.iteritems() if score > 0.4}
        if self.whitelist:
            tags = (tag for tag in tags if tag in self.whitelist)
        elif self.blacklist:
            tags = (tag for tag in tags if tag not in self.blacklist)
        tags = sorted(tags, key=self.tags.get, reverse=True)
        return tags[:self.limit]
    
    def listgood(self):
        tags = {name: score for name, score in self.tags.iteritems() if score > 0.4}
        tags = ["%s: %.2f" % (name, score) for name, score in sorted(tags.iteritems(), key=lambda (k, v): (v, k), reverse=True)]
        if tags:
            return "  Good tags: %s" % ', '.join(map(str, tags))


class DataProvider:
    def __init__(self, name, multi, session):
        self.name = name
        self.multi = multi
        self.session = session
    
    def _askforint(self, message, maximum):
        while True:
            try:
                c = int(raw_input(message))
            except ValueError:
                c = None
            except EOFError:
                c = 0
                print
            if c in range(maximum + 1):
                break
        return None if c is 0 else c - 1

    def _searchstr(self, s):
        return re.sub(r'\[\W\S\]+', '', s)


class DataProviderException(Exception): pass


class WhatCD(DataProvider):
    def __init__(self, multi, session, username, password, interactive):
        DataProvider.__init__(self, "What.CD", multi, session)
        self.session.post('https://what.cd/login.php', {'username': username, 'password': password})
        self.interactive = interactive
        self.last_request = time.time()
        self.rate_limit = 2.0 # min. seconds between requests
    
    def __query(self, action, **args):
        params = {'action': action}
        params.update(args)
        if time.time() - self.last_request < self.rate_limit:
            out.verbose("  Waiting %.2f sec. for What.CD request." % (time.time() - self.last_request))
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(0.1)
        try:
            r = self.session.get('https://what.cd/ajax.php', params=params)
            self.last_request = time.time()
            j = json.loads(r.content)
            if j['status'] != 'success':
                raise DataProviderException
            return j['response']
        except(ValueError, HTTPError):
            raise DataProviderException
    
    def __filter_tags(self, tags): # (waiting on getting all tags with counts)
        badtags = ['staff.picks', 'freely.available', 'vanity.house']
        if tags and isinstance(tags[0], dict):
            return {string.replace(tag['name'], '.', ' '): int(tag['count']) for tag in tags if tag['name'] not in badtags}
        return [string.replace(tag, '.', ' ') for tag in tags if tag not in badtags]
    
    def __interactive(self, a, data):
        print "Multiple releases found on What.CD, please choose the right one:"
        for i in range(len(data)):
            print "#%2d: %s - %s [%s] [%s]" % (i + 1, data[i]['artist'], data[i]['groupName'], data[i]['groupYear'], data[i]['releaseType'])
        i = self._askforint("Choose Release # (0 to skip): ", len(data))
        return [data[i]] if i else None
    
    def get_tags(self, a):
        if not a.va:
            try:
                data = self.__query('artist', id=0, artistname=self._searchstr(a.artist))
                a.tags.addlist_count(self.__filter_tags(data['tags']), self.multi)
            except (TypeError, KeyError, DataProviderException):
                out.verbose("  No tags for artist returned from What.CD!")
        
        try:
            data = self.__query('browse',
                                searchstr=self._searchstr(a.album if a.va else a.artist + ' ' + a.album),
                                 **{'filter_cat[1]':1})['results']
            if len(data) > 1:
                if a.year:
                    data = [d for d in data if abs(int(d['groupYear']) - a.year) <= 2]
                if len(data) > 1 and self.interactive:
                    data = self.__interactive(a, data)
            if len(data) == 1:
                a.tags.addlist_nocount(self.__filter_tags(data[0]['tags']), self.multi)
                a.type = data[0]['releaseType']
            else:
                raise DataProviderException
        except (KeyError, DataProviderException):
            out.verbose("  No tags for album returned from What.CD!")


class LastFM(DataProvider):
    def __init__(self, multi, session, apikey):
        DataProvider.__init__(self, "Last.FM", multi, session)
        self.apikey = apikey
    
    def __filter_tags(self, a, tags):
        # lastfm returns a list of dict for multiple tags, and a single dict for just one tag
        if not isinstance(tags, list):
            tags = [tags]
        # be aware of fuzzy matching below when adding bad tags here
        badtags = [a.artist.lower(), a.album.lower(), 'albums i own', 'amazing',
                   'awesome', 'cool', 'epic', 'favorite albums', 'favorites',
                   'good', 'love', 'seen live', 'sexy']
        return {tag['name']: int(tag['count']) for tag in tags if
                not get_close_matches(tag['name'].lower(), badtags, 1)
                and int(tag['count']) > 2}

    def __query(self, method, **args):
        params = {'api_key': self.apikey, 'format': 'json', 'method': method}
        params.update(args)
        try:
            r = self.session.get('http://ws.audioscrobbler.com/2.0/', params=params) 
            j = json.loads(r.content)
            return j
        except (HTTPError, ValueError):
            raise DataProviderException
    
    def get_tags(self, a):
        if not a.va:
            data = self.__query('artist.gettoptags', artist=self._searchstr(a.artist))
            try:
                a.tags.addlist_count(self.__filter_tags(a, data['toptags']['tag']), self.multi)
            except KeyError:
                out.verbose("  No tags for artist returned from Last.FM!")

        data = self.__query('album.gettoptags', album=self._searchstr(a.album),
                            artist=self._searchstr('Various Artists' if a.va else a.artist))
        try:
            a.tags.addlist_count(self.__filter_tags(a, data['toptags']['tag']), self.multi)
        except KeyError:
            out.verbose("  No tags for album returned from Last.FM!")


class MusicBrainz(DataProvider):
    def __init__(self, multi, session):
        DataProvider.__init__(self, "MusicBrainz", multi, session)
        musicbrainzngs.set_useragent("whatlastgenre", "0.1")
        
    def __add_tags(self, a, tags):
        thetags = {}
        for tag in tags:
            thetags.update({tag['name']: int(tag['count'])})
        a.tags.addlist_count(thetags, self.multi)
    
    def get_tags(self, a):
        if not a.va:
            r = musicbrainzngs.search_artists(artist=self._searchstr(a.artist), limit=1)
            try:
                r = musicbrainzngs.get_artist_by_id(r['artist-list'][0]['id'], includes=['tags'])
                self.__add_tags(a, r['artist']['tag-list'])
            except (IndexError, KeyError):
                out.verbose("  No tags for artist returned from Musicbrainz!")
                    
            r = musicbrainzngs.search_release_groups(artist=self._searchstr(a.artist), release=self._searchstr(a.album), limit=1)
        else:
            r = musicbrainzngs.search_release_groups(release=self._searchstr(a.album), limit=1)
        
        try:
            r = musicbrainzngs.get_release_group_by_id(r['release-group-list'][0]['id'], includes=['tags'])
            self.__add_tags(a, r['release-group']['tag-list'])
        except (IndexError, KeyError):
            out.verbose("  No tags for album returned from Musicbrainz!")


class Discogs(DataProvider):
    def __init__(self, multi, session, interactive):
        DataProvider.__init__(self, "Discogs", multi, session)
        self.interactive = interactive
        
    def __query(self, thetype, **args):
        params = {'type': thetype}
        params.update(args)
        try:
            r = self.session.get('http://api.discogs.com/database/search', params=params) 
            j = json.loads(r.content)
            return j['results']
        except (HTTPError, ValueError):
            raise DataProviderException
    
    def __interactive(self, a, data):
        print "Multiple releases found on Discogs, please choose the right one:"
        for i in range(len(data)):
            print "#%2d: %s [%s] [#%s]" % (i + 1, data[i]['title'], data[i]['year'], data[i]['id'])
        i = self._askforint("Choose Release # (0 to skip): ", len(data))
        return [data[i]] if i else None

    def get_tags(self, a):
        data = self.__query('master', release_title=self._searchstr(a.album))
        try:
            if data and len(data) > 1:
                data = [d for d in data if (a.artist in d['title']
                                            and (not a.year or (abs(int(d['year']) - a.year) <= 2)))]
                if len(data) > 1 and self.interactive:
                    data = self.__interactive(a, data)
            if data and len(data) == 1 and (data[0].has_key('style') or data[0].has_key('genre')):
                tags = []
                if data[0].has_key('style'): tags = tags + data[0]['style']
                if data[0].has_key('genre'): tags = tags + data[0]['genre']
                a.tags.addlist_nocount(tags, self.multi)
            else:
                raise DataProviderException
        except (KeyError, DataProviderException):
            out.verbose("  No tags for album returned from Discogs!")


class Stats:
    def __init__(self):
        self.tags = {}
        self.starttime = time.time()
        
    def add(self, tags):
        for tag in tags:
            if tag in self.tags:
                self.tags[tag] = self.tags[tag] + 1
            else:
                self.tags.update({tag: 1})
                
    def printstats(self):
        print "Tag statistics: ",
        for tag, num in sorted(self.tags.iteritems(), key=lambda (k, v): (v, k), reverse=True):
            print "%s: %d, " % (tag, num),
        print "\nTime elapsed: %s " % str(datetime.timedelta(seconds=time.time() - self.starttime))


class Out:
    def __init__(self, verbose=False, colors=False):
        self._verbose = verbose
        self.colors = colors
        
    def verbose(self, s):
        if self._verbose and s:
            if self.colors: print "\033[0;33m%s\033[0;m" % s
            else: print s
            
    def info(self, s):
        if self.colors: print "\033[0;36m%s\033[0;m" % s
        else: print s
        
    def warning(self, s):
        if self.colors: print "\033[1;35mWARNING:\033[0;35m %s\033[0;m" % s
        else: print "WARNING: %s" % s
        
    def error(self, s):
        if self.colors: print "\033[1;31mERROR:\033[0;31m %s\033[0;m" % s
        else: print "ERROR: %s" % s
        
    def success(self, s):
        if self.colors: print "\033[1;32mSUCCESS:\033[0;32m %s\033[0;m" % s
        else: print "SUCESS: %s" % s


def main():
    def get_arguments():
        ap = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter,
                            description='Improves genre metadata of audio files based on tags from various music-sites.')
        ap.add_argument('path', nargs='+', help='folder(s) to scan')
        ap.add_argument('-v', '--verbose', action='store_true', help='run verbose (more output)')
        ap.add_argument('-n', '--dry-run', action='store_true', help='dry-run (write nothing)')
        ap.add_argument('-r', '--tag-release', action='store_true', help='tag release type from what.cd')
        ap.add_argument('-i', '--interactive', action='store_true', help='interactive mode')
        ap.add_argument('-s', '--stats', action='store_true', help='collect statistics to found genres')
        ap.add_argument('-b', '--use-colors', action='store_true', help='enable colorful output')
        ap.add_argument('-c', '--use-cache', action='store_true', help='enable cache feature')
        ap.add_argument('-l', '--tag-limit', metavar='N', type=int, help='max. number of genre tags', default=4)
        ap.add_argument('--no-whatcd', action='store_true', help='disable lookup on What.CD')
        ap.add_argument('--no-lastfm', action='store_true', help='disable lookup on Last.FM')
        ap.add_argument('--no-mbrainz', action='store_true', help='disable lookup on MusicBrainz')
        ap.add_argument('--no-discogs', action='store_true', help='disable lookup on Discogs')
        ap.add_argument('--config', default=os.path.expanduser('~/.whatlastgenre/config'),
                        help='location of the configuration file')
        ap.add_argument('--cache', default=os.path.expanduser('~/.whatlastgenre/cache'),
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
            config.set('genres', 'score_down', 'Electronic, Alternative, Indie, Other, Other')
            config.write(open(configfile, 'w'))
            print "Please edit the configuration file: %s" % configfile
            sys.exit(2)
    
        conf = namedtuple('conf', '')
        conf.whatcd_user = config.get('whatcd', 'username')
        conf.whatcd_pass = config.get('whatcd', 'password')
        conf.genre_whitelist = config_list(config.get('genres', 'whitelist'))
        conf.genre_blacklist = config_list(config.get('genres', 'blacklist'))
        conf.genre_uppercase = config_list(config.get('genres', 'uppercase'))
        conf.genre_score_up = config_list(config.get('genres', 'score_up'))
        conf.genre_score_down = config_list(config.get('genres', 'score_down'))
        return conf
    
    def find_albums(paths):
        albums = {}
        for path in paths:
            for root, dirs, files in os.walk(path):
                for f in files:
                    ext = os.path.splitext(os.path.join(root, f))[1].lower()
                    if ext in [".flac", ".ogg", ".mp3"]:
                        albums.update({root: ext[1:]})
                        break
        return albums
    
    def get_data(dp, a):
        out.verbose(dp.name + "...")
        try:
            dp.get_tags(a)
        except DataProviderException:
            out.error("Could not get data from %s " + dp.name)
        finally:
            out.verbose(a.tags.listgood())

    args = get_arguments()
    
    # DEVEL Helper
    #args.verbose = True
    #args.dry_run = True
    #args.no_whatcd = True
    #args.np_lastfm = True
    #args.no_discogs = True
    #args.no_mbrainz = True
    #args.tag_release = True
    #args.interactive = True
    #args.stats = True
    #args.path.append('/home/foo/nobackup/test')
    #args.path.append('/media/music/Alben/')
    #import random; args.path.append(os.path.join('/media/music/Alben', random.choice(os.listdir('/media/music/Alben'))))
    
    out._verbose = args.verbose
    out.colors = args.use_colors

    if args.no_whatcd and args.no_lastfm and args.no_mbrainz and args.no_discogs:
        out.error("Where do you want to get your data from?")
        print "At least one source must be activated (multiple sources recommended)!"
        sys.exit()

    conf = get_configuration(args.config)

    if not (conf.whatcd_user and conf.whatcd_pass):
        out.warning("No What.CD credentials specified. What.CD support disabled.")
        args.no_whatcd = True
    if args.no_whatcd and args.tag_release:
        out.warning("Can't tag release with What.CD support disabled. Release tagging disabled.")
        args.tag_release = False
    
    if args.use_cache:
        try:
            cache = set()
            cache = pickle.load(open(args.cache))
        except:
            pickle.dump(cache, open(args.cache, 'wb'))
    
    whatcd = lastfm = mbrainz = discogs = stats = None
    
    session = requests.session()
    
    if not args.no_whatcd:
        whatcd = WhatCD(SM_WHATCD, session, conf.whatcd_user, conf.whatcd_pass, args.interactive)
    if not args.no_lastfm:
        lastfm = LastFM(SM_LASTFM, session, "54bee5593b60d0a5bf379cedcad79052")
    if not args.no_mbrainz:
        mbrainz = MusicBrainz(SM_MBRAIN, session)
    if not args.no_discogs:
        discogs = Discogs(SM_DISCOG, session, args.interactive)
    if args.stats:
        stats = Stats()
        
    albums = find_albums(args.path)
    errors = []

    print "Found %d folders with possible albums!" % len(albums)
    
    i = 0
    for album in albums:
        i = i + 1 
        if args.use_cache and album in cache:
            out.info("Found %s in cache, skipping..." % album)
            continue
        print

        gt = GenreTags(args.tag_limit, conf.genre_score_up, conf.genre_score_down,
                       conf.genre_whitelist, conf.genre_blacklist, conf.genre_uppercase)
        
        print "Getting metadata for album (%d/%d) in %s..." % (i, len(albums), album)
        try:
            a = Album(album, albums[album], gt, args.tag_release)
        except AlbumLoadException, e:
            errors.append(album)
            out.error("Could not get album: " + e.message)
            continue

        print "Getting tags for '%s - %s'..." % ('VA' if a.va else a.artist, a.album)
        
        if whatcd:
            get_data(whatcd, a)
        if lastfm:
            get_data(lastfm, a)
        if mbrainz:
            get_data(mbrainz, a)
        if discogs:
            get_data(discogs, a)

        if args.tag_release and a.type:
            print "Release type: %s" % a.type

        tags = a.tags.get()
        if tags:
            print "Genre tags: %s" % ', '.join(map(str, tags))
            if args.stats: stats.add(tags)
        
        if not args.dry_run:
            try:
                a.save()
            except AlbumSaveException, e:
                errors.append(album)
                out.error("Could not save album: " + e.message)
            finally:
                if args.use_cache:
                    cache.add(a.path)
                    pickle.dump(cache, open(args.cache, 'wb'))  

    print
    out.success("all done!")
    
    if args.stats:
        stats.printstats()
    
    if errors:
        print "Albums with errors: %s" % ', '.join(map(str, errors))


if __name__ == "__main__":
    out = Out()
    main()
