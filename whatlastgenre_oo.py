#!/usr/bin/env python

from __future__ import division
from ConfigParser import SafeConfigParser
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import namedtuple
from difflib import get_close_matches, SequenceMatcher
from glob import glob
from inspect import stack
import json
import musicbrainzngs
import mutagen
import operator
import os
import re
import requests
import string
import sys

# Constant Score Multipliers
SM_WHATCD = 1.5
SM_LASTFM = 0.75
SM_MBRAIN = 1.0
SM_DISCOG = 1.0

class AlbumLoadException(Exception): pass
class AlbumSaveException(Exception): pass
class DataProviderException(Exception): pass

class DataProvider:
    def __init__(self, multi, session):
        self.multi = multi
        self.session = session
        
    def _add_tags_nocount(self, a, tags):
        for tag in tags:
            a.tags.add(tag, 0.85 ** (len(tags) - 1) * self.multi)
        
    def _searchstr(self, s):
        return re.sub(r'\[\W\S\]+', '', s)
    
    
class WhatCD(DataProvider):
    def __init__(self, multi, session, username, password, tag_release, interactive):
        DataProvider.__init__(self, multi, session)
        session.post('https://what.cd/login.php', {'username': username, 'password': password})
        self.tag_release = tag_release
        self.interactive = interactive
    
    def __query(self, action, **args):
        params = {'action': action}
        params.update(args)
        r = self.session.get('https://what.cd/ajax.php', params=params)
        j = json.loads(r.content)
        if j['status'] != 'success':
            raise DataProviderException("What.CD: unsuccessful response")
        return j['response']
        
    def __add_tags(self, a, tags):
        tags = list(tags)
        for tag in tags:
            if tag['name'] not in ['staff.picks', 'freely.available', 'vanity.house']:
                a.tags.add(string.replace(tag['name'], '.', ' '),
                           int(tag['count']) / (int(tags[0]['count']) + 1) * self.multi)
    
    def __interactive(self, data):
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
        out.verbose(self.__class__.__name__ + "...")
        if not a.va:
            data = self.__query('artist', id=0, artistname=DataProvider._searchstr(self, a.artist))
            if data.has_key('tags'):
                tags = sorted(data['tags'], key=operator.itemgetter('count'), reverse=True)
                self.__add_tags(a, tags)
        
        data = self.__query('browse', searchstr=DataProvider._searchstr(self, a.album if a.va else a.artist + ' ' + a.album), **{'filter_cat[1]':1})['results']
        
        if len(data) > 1 and self.interactive:
            data = self.__interactive(data)
        elif len(data) == 1:
            data = data[0]
        else:
            data = None
            
        if data:
            tags = []
            for tag in data['tags']:
                tags.append({'name': tag, 'count': (0.85 ** (len(tags) - 1))})
            self.__add_tags(a, tags)
            if self.tag_release:
                a.type = data['releaseType']
        out.verbose(a.tags.listgood())


class LastFM(DataProvider):
    def __init__(self, multi, session, apikey):
        DataProvider.__init__(self, multi, session)
        self.apikey = apikey
    
    def __add_tags(self, a, tags):
        badtags = [a.album.lower(), a.artist.lower(), 'albums i dont own yet', 'albums i own',
                   'albums i want', 'favorite album', 'favorite', 'lieblingssongs', 'own it', 'my albums'
                   'owned cds', 'seen live', 'wishlist', 'best of', 'laidback-221', 'number one']
        if tags.__class__ is not list:
            tags = [tags]
        topcount = int(tags[0]['count']) + 1
        for tag in tags:
            if not get_close_matches(tag['name'].lower(), badtags, 1):
                a.tags.add(tag['name'], int(tag['count']) / topcount * self.multi)

    def __query(self, method, **args):
        params = {'api_key': self.apikey, 'format': 'json', 'method': method}
        params.update(args)
        r = self.session.get('http://ws.audioscrobbler.com/2.0/', params=params) 
        j = json.loads(r.content)
        return j
    
    def get_tags(self, a):
        out.verbose(self.__class__.__name__ + "...")
        if not a.va:
            data = self.__query('artist.gettoptags', artist=DataProvider._searchstr(self, a.artist))
            if data.has_key('toptags') and data['toptags'].has_key('tag'):
                self.__add_tags(a, data['toptags']['tag'])
        data = self.__query('album.gettoptags', album=DataProvider._searchstr(self, a.album),
                            artist=DataProvider._searchstr(self, 'Various Artists' if a.va else a.artist))
        if data.has_key('toptags') and data['toptags'].has_key('tag'):
            self.__add_tags(a, data['toptags']['tag'])
        out.verbose(a.tags.listgood())


class MusicBrainz(DataProvider):
    def __init__(self, multi, session):
        DataProvider.__init__(self, multi, session)
        musicbrainzngs.set_useragent("whatlastgenre", "0.1")
        
    def __add_tags(self, a, tags):
        tags = list(tags)
        topcount = int(tags[0]['count']) + 1
        for tag in tags:
            a.tags.add(tag['name'], int(tag['count']) / topcount * self.multi)
    
    def get_tags(self, a):
        out.verbose(self.__class__.__name__ + "...")
        if not a.va:
            r = musicbrainzngs.search_artists(artist=DataProvider._searchstr(self, a.artist), limit=1)
            if r['artist-list']:
                artistid = r['artist-list'][0]['id']
                r = musicbrainzngs.get_artist_by_id(artistid, includes=['tags'])
                if r['artist'].has_key('tag-list'):
                    tags = sorted(r['artist']['tag-list'], key=operator.itemgetter('count'), reverse=True)
                    self.__add_tags(a, tags)
                    
            r = musicbrainzngs.search_release_groups(artist=DataProvider._searchstr(self, a.artist), release=DataProvider._searchstr(self, a.album), limit=1)
        if a.va:
            r = musicbrainzngs.search_release_groups(release=DataProvider._searchstr(self, a.album), limit=1)
            
        if r['release-group-list']:
            releasegroupid = r['release-group-list'][0]['id']
            r = musicbrainzngs.get_release_group_by_id(releasegroupid, includes=['tags'])
            if r['release-group'].has_key('tag-list'):
                tags = sorted(r['release-group']['tag-list'], key=operator.itemgetter('count'), reverse=True)
                self.__add_tags(a, tags)
        out.verbose(a.tags.listgood())


class Discogs(DataProvider):
    def __init__(self, multi, session):
        DataProvider.__init__(self, multi, session)
        
    def __query(self, thetype, **args):
        params = {'type': thetype}
        params.update(args)
        r = self.session.get('http://api.discogs.com/database/search', params=params) 
        try:
            j = json.loads(r.content)
            return j['results']
        except Exception, e:
            raise Exception(e)
    
    def get_tags(self, a):
        out.verbose(self.__class__.__name__ + "...")
        data = self.__query('master', release_title=DataProvider._searchstr(self, a.album))
        if data:
            if data[0].has_key('style'):
                self._add_tags_nocount(a, data[0]['style'])
            if data[0].has_key('genre'):
                self._add_tags_nocount(a, data[0]['genre'])
        out.verbose(a.tags.listgood())

class Album:
    def __init__(self, path, filetype, genretags, tagrelease=False):
        self.path = path
        self.filetype = filetype[1:].lower()
        self.tracks = glob(os.path.join(path, '*.' + filetype))
        self.tags = genretags
        self.tagrelease = tagrelease
        self.__load_metadata()
    
    def __load_metadata(self):
        print "Getting metadata for album in %s..." % self.path
        self.va = False
        self.type = None
        meta = mutagen.File(self.tracks[0], easy=True)
        for track in self.tracks:
            meta2 = mutagen.File(track, easy=True)
            if not meta2 or meta['album'][0] != meta2['album'][0]:
                raise AlbumLoadException("Not all tracks have the same album-tag!")
            if meta['artist'][0] != meta2['artist'][0]:
                self.va = True
        self.artist = meta['artist'][0] if not self.va else ''
        self.album = meta['album'][0]
    
    def save(self):
        print "Saving metadata..."
        tags = self.tags.get()
        for track in self.tracks:
            meta = mutagen.File(track, easy=True)
            if self.type and self.filetype in ['flac', 'ogg']:
                meta['release'] = self.type
            if tags:
                meta['genre'] = tags
            meta.save()


class GenreTags:
    def __init__(self, score_up, score_down, whitelist, blacklist, uppercase):
        self.tags = {}
        self.score_up = score_up
        self.score_down = score_down
        self.whitelist = whitelist
        self.blacklist = blacklist
        self.uppercase = uppercase
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
        for tag in tags:
            self.add(tag, 0.05)
        # TODO: add more
        tags = ['Chillout', 'Downtempo', 'Electro-Swing', 'Female Vocalist', 'Future Jazz', 'German',
                'German Hip-Hop', 'Jazz-Hop', 'Tech-House']
        for tag in tags:
            self.add(tag, 0.1)
        for tag in self.score_up:
            self.add(tag, 0.2)
        for tag in self.score_down:
            self.add(tag, -0.2)

    def __replace_tag(self, tag):
        rplc = {'deutsch': 'german', 'frensh': 'france', 'hip hop': 'hip-hop', 'hiphop': 'hip-hop',
                'prog ': 'progressive', 'rnb': 'r&b', 'trip hop': 'trip-hop', 'triphop': 'trip-hop'}
        for (a, b) in rplc.items():
            if string.find(tag.lower(), a) is not -1:
                return string.replace(tag.lower(), a, b)
        return tag;
    
    def __format_tag(self, tag):
        if tag.upper() in self.uppercase:
            return string.upper(tag)
        return tag.title()

    def add(self, name, score):
        if len(name) not in range(2, 20) \
            or re.match('([0-9]{2}){1,2}s?', name) is not None \
            or (score < 0.025 and stack()[1][3] is not '__init__'):
            return
        name = self.__replace_tag(name)
        name = self.__format_tag(name)
        #if args.verbose and inspect.stack()[1][3] is not '__init__':
        #    print "  %s (%.3f)" % (name, score)
        found = get_close_matches(name, self.tags.keys(), 1, 0.858) # don't change this, add replaces instead
        if found:
            self.tags[found[0]] = (self.tags[found[0]] + score) * 1; # FIXME: find good value for modifier
            if out._verbose and SequenceMatcher(None, name, found[0]).ratio() < 0.99:
                out.verbose("  %s is the same tag as %s (%.3f)" % (name, found[0], self.tags[found[0]]))
        else:
            self.tags.update({name: score})

    def get(self, limit):
        # only good ones
        tags = dict((name, score) for name, score in self.tags.iteritems() if score > 0.4)
        # get sorted list from it
        tags = sorted(tags, key=self.tags.get, reverse=True)
        # filter them
        return self.filter_taglist(tags)[:limit];

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
        s = " Good tags: "
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
        s = "Statistics"
        for tag, num in sorted(self.tags.iteritems(), key=lambda (tag, num): (num, tag), reverse=True):
            s = s + " %s: %d," % (tag, num)
        return s[:-1]

class Out:
    def __init__(self, verbose=False):
        self._verbose = verbose
    def verbose(self, s):
        if self._verbose:
            print "\033[0;33m%s\033[0;m" % s
    def info(self, s):
        print "\033[0;36m%s\033[0;m" % s
    def error(self, s):
        print "\033[1;31mERROR:\033[0;31m %s\033[0;m" % s
    def success(self, s):
        print "\033[1;32mSUCCESS:\033[0;32m %s\033[0;m" % s

def main():
    def get_arguments():
        ap = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter, description='Improves genre-metadata of audio-files based on tags from various music-sites.')
        ap.add_argument('path', nargs='+', help='folder(s) to scan')
        ap.add_argument('-v', '--verbose', action='store_true', help='run verbose (more output)')
        ap.add_argument('-n', '--dry-run', action='store_true', help='dry-run (write nothing)')
        ap.add_argument('-i', '--interactive', action='store_true', help='interactive mode')
        ap.add_argument('-r', '--tag-release', action='store_true', help='tag release type from what.cd')
        ap.add_argument('-s', '--stats', action='store_true', help='collect stats to written genres')
        ap.add_argument('-l', '--tag-limit', metavar='N', type=int, help='max. number of genre tags', default=4)
        ap.add_argument('--no-whatcd', action='store_true', help='disable lookup on What.CD')
        ap.add_argument('--no-lastfm', action='store_true', help='disable lookup on Last.FM')
        ap.add_argument('--no-mbrainz', action='store_true', help='disable lookup on MusicBrainz')
        ap.add_argument('--no-discogs', action='store_true', help='disable lookup on Discogs')
        ap.add_argument('--config', default=os.path.expanduser('~/.whatlastgenre/config'), help='location of the configuration file')
        #ap.add_argument('--cache', default=os.path.expanduser('~/.whatlastgenre/cache'),help='location of the cache')
        #ap.add_argument('--no-cache', action='store_true', help='disable cache feature')
        #ap.add_argument('--clear-cache', action='store_true', help='clear the cache')
        return ap.parse_args()

    def get_configuration(configfile):
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
            config.add_section('lastfm')
            config.set('lastfm', 'apikey', '54bee5593b60d0a5bf379cedcad79052')
            config.add_section('genres')
            config.set('genres', 'whitelist', '')
            config.set('genres', 'blacklist', '')
            config.set('genres', 'uppercase', 'IDM, UK, US')
            config.set('genres', 'score_up', 'Trip-Hop')
            config.set('genres', 'score_down', 'Electronic, Rock, Metal, Alternative, Indie, Other, Other, Unknown, Unknown')
            config.write(open(configfile, 'w'))
            print "Please edit the configuration file: %s" % configfile
            sys.exit(2)
    
        conf = namedtuple('conf', '')
        conf.whatcd_user = config.get('whatcd', 'username')
        conf.whatcd_pass = config.get('whatcd', 'password')
        conf.lastfm_apikey = config.get('lastfm', 'apikey')
        conf.genre_whitelist = config_list(config.get('genres', 'whitelist'))
        conf.genre_blacklist = config_list(config.get('genres', 'blacklist'))
        conf.genre_uppercase = config_list(config.get('genres', 'uppercase'))
        conf.genre_score_up = config_list(config.get('genres', 'score_up'))
        conf.genre_score_down = config_list(config.get('genres', 'score_down'))
        return conf
    
    def config_list(s):
        if s:
            return [i.strip() for i in s.split(',')]
        return []

    args = get_arguments()
    
    ''' DEVEL Helper '''
    #args.verbose = True
    #args.dry_run = True
    #args.interactive = True
    #args.tag_release = True
    #args.stats = True
    #args.path.append('/home/foo/nobackup/test')
    #args.path.append('/media/music/Alben/Boards of Canada')
    #import random; args.path.append(os.path.join('/media/music/Alben', random.choice(os.listdir('/media/music/Alben'))))
    ''' /DEVEL HELPER '''
    
    out._verbose = args.verbose

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
    if not conf.lastfm_apikey:
        print "No Last.FM apikey specified, Last.FM support disabled."
        args.no_lastfm = True
    
    whatcd, lastfm, mbrainz, discogs, stats = None, None, None, None, None
    
    session = requests.session()
    
    if not args.no_lastfm:
        lastfm = LastFM(SM_LASTFM, session, conf.lastfm_apikey)
    if not args.no_whatcd:
        whatcd = WhatCD(SM_WHATCD, session, conf.whatcd_user, conf.whatcd_pass, args.tag_release, args.interactive)
    if not args.no_mbrainz:
        mbrainz = MusicBrainz(SM_MBRAIN, session)
    if not args.no_discogs:
        discogs = Discogs(SM_DISCOG, session)
    if args.stats:
        stats = Stats()
    
    try:
        for path in args.path:
            albums = scan_folder(path)
    except Exception, e:
        out.error(e)
    
    print "Found %d folders with possible albums!\n" % len(albums)
    
    for album in albums:
        gt = GenreTags(conf.genre_score_up, conf.genre_score_down, conf.genre_whitelist, conf.genre_blacklist, conf.genre_uppercase)
        try:
            a = Album(album[0], album[1], gt)
            print "Getting tags for \"%s - %s\"..." % (('VA' if a.va else a.artist), a.album)
            
            if whatcd:
                try:
                    whatcd.get_tags(a)
                except Exception, e:
                    out.error("What.CD: " + e.message)
            if lastfm:
                try:
                    lastfm.get_tags(a)
                except Exception, e:
                    out.error("Last.FM: " + e.message)
            if mbrainz:
                try:
                    mbrainz.get_tags(a)
                except Exception, e:
                    out.error("MusicBrainz: " + e.message)
            if discogs:
                try:
                    discogs.get_tags(a)
                except Exception, e:
                    out.error("Discogs: " + e.message)

            if args.tag_release and a.type:
                print "Release type: %s" % a.type
            tags = a.tags.get(args.tag_limit)
            if tags:
                print "Genre tags: %s" % ', '.join(map(str, tags))
            if args.stats:
                stats.add(tags)
                
            if not args.dry_run:
                a.save()
        except AlbumLoadException, e:
            out.error("Could not get album:" + e.message)
        except AlbumSaveException, e:
            out.error("Could not save album:" + e.message)
        except Exception, e:
            out.error(e)
        finally:
            print

    out.success("all done!")
    
    if args.stats:
        out.info(stats.liststats())

def scan_folder(path, albums=[]):
    for item in os.listdir(path):
        item = os.path.join(path, item)
        if os.path.isdir(item):
            scan_folder(item, albums)
        elif os.path.splitext(item)[1].lower() in [".flac", ".ogg", ".mp3"]:
            albums.append((path, os.path.splitext(item)[1].lower()[1:]))
            break
    return albums

if __name__ == "__main__":
    out = Out()
    main()
