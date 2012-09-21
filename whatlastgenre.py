#!/usr/bin/env python

from __future__ import division
from ConfigParser import SafeConfigParser
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import namedtuple
from difflib import get_close_matches, SequenceMatcher
from requests.exceptions import HTTPError
import json
import musicbrainzngs
import mutagen
import operator
import os
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

class DataProvider:
    def __init__(self, name, multi, session):
        self.name = name
        self.multi = multi
        self.session = session
        
    def _searchstr(self, s):
        return re.sub(r'\[\W\S\]+', '', s)

class DataProviderException(Exception):
    def __init__(self, name, message):
        Exception.__init__(self)
        self.name = name
        self.message = message

    
class WhatCD(DataProvider):
    def __init__(self, multi, session, username, password, tag_release, interactive):
        DataProvider.__init__(self, "What.CD", multi, session)
        self.session.post('https://what.cd/login.php', {'username': username, 'password': password})
        self.tag_release = tag_release
        self.interactive = interactive
        self.last_request = time.time()
        self.rate_limit = 2.0 # seconds between requests
    
    def __query(self, action, **args):
        params = {'action': action}
        params.update(args)
        if out._verbose and time.time() - self.last_request < self.rate_limit:
            out.verbose("  Waiting %.2f sec for What.CD request." % (time.time() - self.last_request))
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(0.1)
        try:
            r = self.session.get('https://what.cd/ajax.php', params=params)
            self.last_request = time.time()
            j = json.loads(r.content)
            if j['status'] != 'success':
                raise DataProviderException("What.CD", "unsuccessful response")
            return j['response']
        except (ValueError, HTTPError), e:
            raise DataProviderException("What.CD", e.message)
    
    def __filter_tags(self, tags): # Improve this shit
        badtags = ['staff.picks', 'freely.available', 'vanity.house']
        if tags.__class__.__name__ == 'str':
            tags = [tags]
        thetags = []
        if tags[0].__class__.__name__ == 'dict':
            thetags = {}
        for tag in tags:
            name = tag
            if tag.__class__.__name__ == 'dict':
                name = tag['name']
            if name not in badtags:
                name = string.replace(name, '.', ' ')
                if thetags.__class__.__name__ == 'list':
                    thetags.append(name)
                elif thetags.__class__.__name__ == 'dict':
                    thetags.update({name: tag['count']})
        return thetags
    
    def __interactive(self, a, data):
        print "Multiple releases found on What.CD, please choose the right one (0 to skip):"
        for i in range(len(data)):
            print "#%d: %s - %s [%d] [%s]" % (i + 1, data[i]['artist'], data[i]['groupName'], data[i]['groupYear'], data[i]['releaseType'])
        while True:
            try: c = int(raw_input("Choose Release #: "))
            except: c = None
            if c in range(len(data) + 1):
                break
        return None if c == 0 else data[c - 1]
    
    def get_tags(self, a):
        if not a.va:
            data = self.__query('artist', id=0, artistname=DataProvider._searchstr(self, a.artist))
            if data.has_key('tags') and data['tags']:
                a.tags.addlist_count(self.__filter_tags(data['tags']), self.multi)
        
        data = self.__query('browse',
                            searchstr=DataProvider._searchstr(self, a.album if a.va else a.artist + ' ' + a.album),
                             **{'filter_cat[1]':1})['results']
        if len(data) > 10 and a.year:
            data = [d for d in data if int(d['groupYear']) in range(int(a.year) - 3, int(a.year) + 3)]
        if len(data) > 1 and self.interactive:
            data = self.__interactive(a, data)
        elif len(data) == 1:
            data = data[0]
        else:
            data = None
            
        if data:
            if data.has_key('tags') and data['tags']:
                a.tags.addlist_nocount(self.__filter_tags(data['tags']), self.multi)
            if self.tag_release and data.has_key('releaseType'):
                a.type = data['releaseType']


class LastFM(DataProvider):
    def __init__(self, multi, session, apikey):
        DataProvider.__init__(self, "Last.FM", multi, session)
        self.apikey = apikey
    
    def __filter_tags(self, a, tags):
        if tags.__class__.__name__ == 'str':
            tags = [tags]
        badtags = [a.album.lower(), a.artist.lower(), 'albums i dont own yet', 'albums i own', 'tagme'
                   'albums i want', 'favorite album', 'favorite', 'lieblingssongs', 'own it', 'my albums'
                   'owned cds', 'seen live', 'wishlist', 'best of', 'laidback-221', 'number one', 'fettttttttttttttt']
        thetags = {}
        for tag in tags:
            if (tag.__class__.__name__ == 'dict' and tag.has_key('name')
                and not get_close_matches(tag['name'].lower(), badtags, 1)):
                thetags.update({tag['name']: int(tag['count'])})
        return thetags
        

    def __query(self, method, **args):
        params = {'api_key': self.apikey, 'format': 'json', 'method': method}
        params.update(args)
        try:
            r = self.session.get('http://ws.audioscrobbler.com/2.0/', params=params) 
            j = json.loads(r.content)
            return j
        except (ValueError, HTTPError), e:
            raise DataProviderException("Last.FM", e.message)
    
    def get_tags(self, a):
        if not a.va:
            data = self.__query('artist.gettoptags', artist=DataProvider._searchstr(self, a.artist))
            if data.has_key('toptags') and data['toptags'].has_key('tag'):
                a.tags.addlist_count(self.__filter_tags(a, data['toptags']['tag']), self.multi)
        data = self.__query('album.gettoptags', album=DataProvider._searchstr(self, a.album),
                            artist=DataProvider._searchstr(self, 'Various Artists' if a.va else a.artist))
        if data.has_key('toptags') and data['toptags'].has_key('tag'):
            a.tags.addlist_count(self.__filter_tags(a, data['toptags']['tag']), self.multi)


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
        try:
            if not a.va:
                r = musicbrainzngs.search_artists(artist=DataProvider._searchstr(self, a.artist), limit=1)
                if r['artist-list']:
                    artistid = r['artist-list'][0]['id']
                    r = musicbrainzngs.get_artist_by_id(artistid, includes=['tags'])
                    if r['artist'].has_key('tag-list'):
                        self.__add_tags(a, r['artist']['tag-list'])
                        
                r = musicbrainzngs.search_release_groups(artist=DataProvider._searchstr(self, a.artist),
                                                         release=DataProvider._searchstr(self, a.album),
                                                         limit=1)
            if a.va:
                r = musicbrainzngs.search_release_groups(release=DataProvider._searchstr(self, a.album), limit=1)
                
            if r['release-group-list']:
                releasegroupid = r['release-group-list'][0]['id']
                r = musicbrainzngs.get_release_group_by_id(releasegroupid, includes=['tags'])
                if r['release-group'].has_key('tag-list'):
                    self.__add_tags(a, r['release-group']['tag-list'])
        except musicbrainzngs.musicbrainz.ResponseError, e:
            raise DataProviderException("MusicBrainz", e.cause)
        except HTTPError, e:
            raise DataProviderException("MusicBrainz", e.args)


class Discogs(DataProvider):
    def __init__(self, multi, session):
        DataProvider.__init__(self, "Discogs", multi, session)
        
    def __query(self, thetype, **args):
        params = {'type': thetype}
        params.update(args)
        try:
            r = self.session.get('http://api.discogs.com/database/search', params=params) 
            j = json.loads(r.content)
            return j['results']
        except (ValueError, HTTPError), e:
            raise DataProviderException("Discogs", e.message)
    
    def get_tags(self, a):
        data = self.__query('master', release_title=DataProvider._searchstr(self, a.album))
        if data:
            if data[0].has_key('style'):
                a.tags.addlist_nocount(data[0]['style'], self.multi)
            if data[0].has_key('genre'):
                a.tags.addlist_nocount(data[0]['genre'], self.multi)


class Album:
    def __init__(self, path, filetype, genretags):
        self.path = path
        self.filetype = filetype.lower()
        self.tracks = [track for track in os.listdir(path) if track.lower().endswith('.' + filetype)]
        self.tags = genretags
        self.__load_metadata()
    
    def __load_metadata(self):
        print "Getting metadata for album in %s..." % self.path
        self.va = False
        self.type = None
        try:
            meta = mutagen.File(os.path.join(self.path, self.tracks[0]), easy=True)
            for track in self.tracks:
                meta2 = mutagen.File(os.path.join(self.path, track), easy=True)
                if not meta2 or meta['album'][0] != meta2['album'][0]:
                    raise AlbumLoadException("Not all tracks have the same album-tag!")
                if meta['artist'][0] != meta2['artist'][0]:
                    self.va = True
            self.artist = meta['artist'][0] if not self.va else ''
            self.album = meta['album'][0]
            try:
                self.year = int(meta['date'][0][:4])
            except:
                pass
        except Exception, e:
            raise AlbumLoadException("Could not load album metadata.")
    
    def save(self):
        print "Saving metadata..."
        tags = self.tags.get()
        for track in self.tracks:
            meta = mutagen.File(os.path.join(self.path, track), easy=True)
            if self.type and self.filetype in ['flac', 'ogg']:
                meta['release'] = self.type
            if tags:
                meta['genre'] = tags
            meta.save()

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
                'Alternative Rock', 'Ambient', 'Anime', 'Avantgarde', 'Ballad', 'Bass', 'Beat',
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
        self.addlist(tags, 0.05, True)
        # TODO: add more
        tags = ['Chillout', 'Downtempo', 'Electro-Swing', 'Female Vocalist', 'Future Jazz', 'German',
                'German Hip-Hop', 'Jazz-Hop', 'Tech-House']
        self.addlist(tags, 0.1, True)
        self.addlist(score_up, 0.2, True)
        self.addlist(score_down, -0.2, True)

    def __replace_tag(self, tag):
        rplc = {'deutsch': 'german', 'frensh': 'france', 'hip hop': 'hip-hop', 'hiphop': 'hip-hop',
                'prog ': 'progressive', 'rnb': 'r&b', 'rhythm and blues': 'r&b', 'rhythm & blues': 'r&b',
                'trip hop': 'trip-hop', 'triphop': 'trip-hop'}
        for (a, b) in rplc.items():
            if a in tag.lower():
                return string.replace(tag.lower(), a, b)
        return tag
    
    def __format_tag(self, tag):
        if tag.upper() in self.uppercase:
            return string.upper(tag)
        return tag.title()

    def add(self, name, score, init=False):
        if (len(name) not in range(2, 20)
            or re.match('([0-9]{2}){1,2}s?', name) is not None
            or (not init and score < 0.01)):
            return
        name = self.__replace_tag(name)
        name = self.__format_tag(name)
        #if not init: out.verbose("  %s (%.3f)" % (name, score))
        found = get_close_matches(name, self.tags.keys(), 1, 0.858) # don't change this, add replaces instead
        if found:
            if found[0] in self.score_up: score = score * 1.2
            elif found[0] in self.score_down: score = score * 0.8
            self.tags[found[0]] = (self.tags[found[0]] + score) * 1.2 # FIXME: find good value for modifier
            if out._verbose and SequenceMatcher(None, name, found[0]).ratio() < 0.92:
                out.verbose("  %s is the same tag as %s (%.3f)" % (name, found[0], self.tags[found[0]]))
        else:
            self.tags.update({name: score})
    
    def addlist(self, tags, score, init=False):
        for tag in tags:
            self.add(tag, score, init)
    
    def addlist_nocount(self, tags, multi):
        for tag in tags:
            self.add(tag, 0.85 ** (len(tags) - 1) * multi)

    def addlist_count(self, tags, multi):
        if tags:
            top = max(tags.iteritems(), key=operator.itemgetter(1))[1]
            if top == 0: top = 1
            for name, count in tags.iteritems():
                self.add(name, count / top * multi)

    def get(self):
        # only good ones
        tags = dict((name, score) for name, score in self.tags.iteritems() if score > 0.4)
        # get sorted list from it
        tags = sorted(tags, key=self.tags.get, reverse=True)
        # filter them
        return self.filter_taglist(tags)[:self.limit]

    def filter_taglist(self, tags):
        # apply whitelist
        if self.whitelist:
            tags = (tag for tag in tags if tag in self.whitelist)
        # or apply blacklist
        elif self.blacklist:
            tags = (tag for tag in tags if tag not in self.blacklist)
        return list(tags)
    
    def listgood(self):
        tags = dict((name, score) for name, score in self.tags.iteritems() if score > 0.4)
        s = "  Good tags:"
        for name, score in sorted(tags.iteritems(), key=lambda (k, v): (v, k), reverse=True):
            s = s + " %s: %.2f," % (name, score)
        return s[:-1]
        

class Stats:
    def __init__(self):
        self.tags = {}
        
    def add(self, tags):
        for tag in tags:
            if self.tags.has_key(tag):
                self.tags[tag] = self.tags[tag] + 1
            else:
                self.tags.update({tag: 1})
                
    def liststats(self):
        s = "Statistics:"
        for tag, num in sorted(self.tags.iteritems(), key=lambda (tag, num): (num, tag), reverse=True):
            s = s + " %s: %d," % (tag, num)
        return s[:-1]

class Out:
    def __init__(self, verbose=False, colors=True):
        self._verbose = verbose
        self.colors = colors
    def verbose(self, s):
        if self._verbose:
            print "\033[0;33m%s\033[0;m" % s if self.colors else s
    def info(self, s):
        print "\033[0;36m%s\033[0;m" % s if self.colors else s
    def error(self, s):
        print "\033[1;31mERROR:\033[0;31m %s\033[0;m" % s if self.colors else s
    def success(self, s):
        print "\033[1;32mSUCCESS:\033[0;32m %s\033[0;m" % s if self.colors else s

def main():
    def get_arguments():
        ap = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter,
                            description='Improves genre metadata of audio files based on tags from various music-sites.')
        ap.add_argument('path', nargs='+', help='folder(s) to scan')
        ap.add_argument('-v', '--verbose', action='store_true', help='run verbose (more output)')
        ap.add_argument('-n', '--dry-run', action='store_true', help='dry-run (write nothing)')
        ap.add_argument('-i', '--interactive', action='store_true', help='interactive mode')
        ap.add_argument('-r', '--tag-release', action='store_true', help='tag release type from what.cd')
        ap.add_argument('-s', '--stats', action='store_true', help='collect statistics to found genres')
        ap.add_argument('-l', '--tag-limit', metavar='N', type=int, help='max. number of genre tags', default=4)
        ap.add_argument('--no-colors', action='store_true', help='dont use colors')
        ap.add_argument('--no-whatcd', action='store_true', help='disable lookup on What.CD')
        ap.add_argument('--no-lastfm', action='store_true', help='disable lookup on Last.FM')
        ap.add_argument('--no-mbrainz', action='store_true', help='disable lookup on MusicBrainz')
        ap.add_argument('--no-discogs', action='store_true', help='disable lookup on Discogs')
        ap.add_argument('--config', default=os.path.expanduser('~/.whatlastgenre/config'),
                        help='location of the configuration file')
        #ap.add_argument('--cache', default=os.path.expanduser('~/.whatlastgenre/cache'),
        #                help='location of the cache')
        #ap.add_argument('--no-cache', action='store_true', help='disable cache feature')
        #ap.add_argument('--clear-cache', action='store_true', help='clear the cache')
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
            config.set('genres', 'blacklist', 'Unknown')
            config.set('genres', 'uppercase', 'IDM, UK, US')
            config.set('genres', 'score_up', 'Soundtrack')
            config.set('genres', 'score_down', 'Electronic, Rock, Metal, Alternative, Indie, Other, Other')
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
        except DataProviderException, e:
            out.error("Could not get data from " + e.name + ": " + e.message)
            return
        out.verbose(a.tags.listgood())

    args = get_arguments()
    
    ''' DEVEL Helper '''
    #args.verbose = True
    #args.dry_run = True
    #args.tag_release = True
    #args.no_colors = True
    #args.stats = True
    #args.path.append('/home/foo/nobackup/test')
    #args.path.append('/media/music/Alben/Mogwai')
    #import random; args.path.append(os.path.join('/media/music/Alben', random.choice(os.listdir('/media/music/Alben'))))
    ''' /DEVEL HELPER '''
    
    out._verbose = args.verbose
    out.colors = not args.no_colors

    if args.no_whatcd and args.no_lastfm and args.no_mbrainz and args.no_discogs:
        print "Where do you want to get your data from? At least one source must be activated!"
        sys.exit()

    conf = get_configuration(args.config)

    if not (conf.whatcd_user and conf.whatcd_pass):
        print "No What.CD credentials specified. What.CD support disabled."
        args.no_whatcd = True
    if args.no_whatcd and args.tag_release:
        print "Can't tag release with What.CD support disabled. Release tagging disabled."
        args.tag_release = False
    
    whatcd, lastfm, mbrainz, discogs, stats = None, None, None, None, None
    
    session = requests.session()
    
    if not args.no_lastfm:
        lastfm = LastFM(SM_LASTFM, session, "54bee5593b60d0a5bf379cedcad79052")
    if not args.no_whatcd:
        whatcd = WhatCD(SM_WHATCD, session, conf.whatcd_user, conf.whatcd_pass, args.tag_release, args.interactive)
    if not args.no_mbrainz:
        mbrainz = MusicBrainz(SM_MBRAIN, session)
    if not args.no_discogs:
        discogs = Discogs(SM_DISCOG, session)
    if args.stats:
        stats = Stats()
        
    albums = find_albums(args.path)

    print "Found %d folders with possible albums!" % len(albums)
    
    for album in albums:
        print
        gt = GenreTags(args.tag_limit, conf.genre_score_up, conf.genre_score_down,
                       conf.genre_whitelist, conf.genre_blacklist, conf.genre_uppercase)
        
        try:
            a = Album(album, albums[album], gt)
        except AlbumLoadException, e:
            out.error("Could not get album: " + e.message)
            continue

        print 'Getting tags for "%s - %s"...' % ('VA' if a.va else a.artist, a.album)
        
        if whatcd:
            get_data(whatcd, a);
        if lastfm:
            get_data(lastfm, a);
        if mbrainz:
            get_data(mbrainz, a);
        if discogs:
            get_data(discogs, a);

        if args.tag_release and a.type:
            print "Release type: %s" % a.type

        tags = a.tags.get()
        if tags:
            print "Genre tags: %s" % ', '.join(map(str, tags))
            if args.stats:
                stats.add(tags)
        
        try:
            if not args.dry_run:
                a.save()
        except AlbumSaveException, e:
            out.error("Could not save album: " + e.message)

    print
    out.success("all done!")
    
    if args.stats:
        out.info(stats.liststats())


if __name__ == "__main__":
    out = Out()
    main()
