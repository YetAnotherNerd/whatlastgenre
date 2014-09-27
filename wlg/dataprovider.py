#!/usr/bin/env python
'''whatlastgenre dataprovider'''

from __future__ import print_function

import logging
import time

import requests
from wlg import __version__


LOG = logging.getLogger('whatlastgenre')


def get_daprs(sources, wcdcred):
    '''Returns a list of initialized DataProviders that are mentioned as source
    in the config file. The loop is used to maintain the given order.'''
    dps = []
    for dapr in sources:
        if dapr == 'whatcd':
            dps.append(WhatCD(wcdcred))
        elif dapr == 'mbrainz':
            dps.append(MBrainz())
        elif dapr == 'lastfm':
            dps.append(LastFM())
        elif dapr == 'discogs':
            dps.append(Discogs())
        elif dapr == 'idiomag':
            dps.append(Idiomag())
        elif dapr == 'echonest':
            dps.append(EchoNest())
    return dps


class DataProviderError(Exception):
    '''If something went wrong with DataProviders.'''
    pass


class DataProvider(object):
    '''Base class for DataProviders.'''

    session = requests.session()
    session.headers.update({'User-Agent': "whatlastgenre/%s" % __version__})

    def __init__(self):
        self.name = self.__class__.__name__
        self.last_request = time.time()
        self.rate_limit = 1  # min. seconds between requests

    def _query_jsonapi(self, url, params):
        '''Queries an api by url and params and returns the json results.'''
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(.1)
        try:
            req = self.session.get(url, params=params)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError) as err:
            raise DataProviderError("connection error: %s" % err.message)
        self.last_request = time.time()
        if req.status_code != 200:
            if req.status_code == 400 and isinstance(self, Idiomag):
                return
            raise DataProviderError("request error: status code %s"
                                    % req.status_code)
        try:
            return req.json()
        except ValueError as err:
            LOG.info(req.content)
            raise DataProviderError("Request error: %s" % err.message)

    def get_artist_data(self, artistname, mbid):
        '''Gets artist data from a DataProvider.'''
        raise NotImplementedError()

    def get_album_data(self, artistname, albumname, mbids):
        '''Gets album data from a DataProvider.'''
        raise NotImplementedError()


class WhatCD(DataProvider):
    '''What.CD DataProvider'''

    def __init__(self, cred):
        super(WhatCD, self).__init__()
        self.cred = cred
        self.loggedin = False
        self.rate_limit = 2.0

    def __del__(self):
        if self.loggedin:
            self.__logout()

    def __login(self):
        '''Login to What.CD.'''
        self.session.post('https://what.cd/login.php',
                          {'username': self.cred[0], 'password': self.cred[1]})
        self.loggedin = True

    def __logout(self):
        '''Logout from What.CD.'''
        self.session.get("https://what.cd/logout.php?auth=%s"
                         % self._query({'action': 'index'}).get('authkey'))
        self.loggedin = False

    def _query(self, params):
        '''Queries the What.CD API.'''
        if not self.loggedin:
            self.__login()
        data = self._query_jsonapi('https://what.cd/ajax.php', params)
        if not data or data.get('status') != 'success':
            return
        return data.get('response')

    def get_artist_data(self, artistname, _):
        '''Gets artist data from What.CD.'''
        data = self._query({'action': 'artist', 'artistname': artistname})
        if not data or 'tags' not in data:
            return
        return [{'tags': {t['name'].replace('.', ' '): int(t['count'])
                          for t in data['tags']}}]

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from What.CD.'''
        data = self._query({'action': 'browse', 'filter_cat[1]': 1,
                            'searchstr': artistname + ' ' + albumname})
        if not data or 'results' not in data:
            return
        return [{
            'info': "%s - %s (%s) [%s]: https://what.cd/torrents.php?id=%s"
                    % (d['artist'], d['groupName'], d['groupYear'],
                       d['releaseType'], d['groupId']),
            'title': d['artist'] + ' - ' + d['groupName'],
            'releasetype': d['releaseType'],
            'tags': [tag.replace('.', ' ') for tag in d['tags']],
            'year': d['groupYear']} for d in data['results']]


class LastFM(DataProvider):
    '''Last.FM DataProvider'''

    def _query(self, params):
        '''Queries the Last.FM API.'''
        params.update({'api_key': "54bee5593b60d0a5bf379cedcad79052",
                       'format': 'json'})
        data = self._query_jsonapi('http://ws.audioscrobbler.com/2.0/',
                                   params)
        if 'error' in data:
            return
        return data

    def get_artist_data(self, artistname, mbid):
        '''Gets artist data from Last.FM.'''
        data = None
        # search with mbid
        if mbid:
            LOG.info("%8s using artist mbid: %s", self.name, mbid)
            data = self._query({'method': 'artist.gettoptags', 'mbid': mbid})
        # search without mbid
        if not data:
            data = self._query({'method': 'artist.gettoptags',
                                'artist': artistname})
        return self.__handle_data(data)

    def get_album_data(self, artistname, albumname, mbids):
        '''Gets album data from Last.FM.
        Last.FM seems to understand album mbids als albumid,
        not as releasegroupid.'''
        data = None
        # search with mbid
        mbid = 'albumid'
        if mbid in mbids and mbids[mbid]:
            LOG.info("%8s using mbid %s: %s", self.name, mbid, mbids[mbid])
            data = self._query({'method': 'album.gettoptags',
                                'mbid': mbids[mbid]})
        # search without mbid
        if not data:
            data = self._query({'method': 'album.gettoptags',
                                'album': albumname,
                                'artist': artistname or 'Various Artists'})
        return self.__handle_data(data)

    @classmethod
    def __handle_data(cls, data):
        '''Shared datahandling for artist and album data from Last.FM.'''
        if not data or 'toptags' not in data or 'tag' not in data['toptags']:
            return
        tags = data['toptags']['tag']
        if not isinstance(tags, list):
            tags = [tags]
        return [{'tags': {t['name']: int(t['count']) for t in tags
                          if t['count'] and int(t['count']) > 40}}]


class MBrainz(DataProvider):
    '''MusicBrainz DataProvider'''
    # TODO: its possible not to use ?query=*id: when searching by mbid, but
    # directly put the mbid into the url, then don't forget to add ?inc=tags

    def __init__(self):
        super(MBrainz, self).__init__()
        self.rate_limit = 1.0

    def _query(self, typ, query):
        '''Queries the MusicBrainz API.'''
        params = {'fmt': 'json'}
        params.update({'query': query})
        data = self._query_jsonapi(
            'http://musicbrainz.org/ws/2/' + typ + '/', params)
        return data

    def get_artist_data(self, artistname, mbid):
        '''Gets artist data from MusicBrainz.'''
        data = None
        # search by mbid
        if mbid:
            LOG.info("%8s using artist mbid: %s", self.name, mbid)
            data = self._query('artist', 'arid:"' + mbid + '"')
            if data and 'artist' in data:
                data = data['artist']
            else:
                print("%8s: artist not found, invalid MBID %s?"
                      % (self.name, mbid))
        # search without mbid
        if not data:
            data = self._query('artist', 'artist:"' + artistname + '"')
            if not data or 'artist' not in data:
                return
            data = [x for x in data['artist'] if int(x['score']) > 90]

        return [{
            'info': "%s (%s) [%s] [%s-%s]: http://musicbrainz.org/artist/%s"
                    % (x['name'], x.get('type', ''), x.get('country', ''),
                       x.get('life-span', {}).get('begin', ''),
                       x.get('life-span', {}).get('ended', ''), x['id']),
            'title': x.get('name', ''),
            'tags': {t['name']: int(t['count']) for t in x.get('tags', [])},
            'mbid': x['id']} for x in data]

    def get_album_data(self, artistname, albumname, mbids):
        '''Gets album data from MusicBrainz.'''
        data = None
        # search by release mbid (just if there is no release-group mbid)
        mbid = 'albumid'
        if not mbids.get('releasegroupid') and mbids.get(mbid):
            LOG.info("%8s using mbid %s: %s", self.name, mbid, mbids[mbid])
            data = self._query('release', 'reid:"' + mbids[mbid] + '"')
            if data and 'releases' in data:
                data = data['releases']
                if data:
                    mbids['releasegroupid'] = \
                        data[0]['release-group'].get('id')
                    # remove albumids since relgrpids are expected later
                    for i in range(len(data)):
                        data[i]['id'] = None
            else:
                print("%8s: release not found, invalid MBID %s?"
                      % (self.name, mbids[mbid]))
        # search by release-group mbid
        mbid = 'releasegroupid'
        if not data and mbids.get(mbid):
            LOG.info("%8s using mbid %s: %s", self.name, mbid, mbids[mbid])
            data = self._query('release-group', 'rgid:"' + mbids[mbid] + '"')
            if data and 'release-groups' in data:
                data = data['release-groups']
            else:
                print("%8s: release-group not found, invalid MBID %s?"
                      % (self.name, mbids[mbid]))
        # search without mbids
        if not data:
            data = self._query('release-group',
                               'artist:"' + artistname
                               + '" AND releasegroup:"' + albumname + '"')
            if not data or 'release-groups' not in data:
                return
            data = [x for x in data['release-groups'] if int(x['score']) > 90]
        return [{
            'info': "%s - %s [%s]: http://musicbrainz.org/release-group/%s"
                    % (x['artist-credit'][0]['artist']['name'], x.get('title'),
                       x.get('primary-type'), x['id']),
            'title': (x['artist-credit'][0]['artist']['name'] + ' - '
                      + x.get('title', '')),
            'tags': {t['name']: int(t['count']) for t in x.get('tags', [])},
            'mbid': x['id']} for x in data]


class Discogs(DataProvider):
    '''Discogs DataProvider'''

    def get_artist_data(self, artistname, _):
        '''Gets artist data from Discogs.'''
        # no artist search support
        raise RuntimeError()

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from Discogs.'''
        data = self._query_jsonapi(
            'http://api.discogs.com/database/search',
            {'type': 'master', 'q': artistname + ' ' + albumname})
        if not data or 'results' not in data:
            return
        return [{
            'info': "%s (%s) [%s]: http://www.discogs.com/master/%s"
                    % (x.get('title'), x.get('year'),
                       ', '.join(x.get('format')), x['id']),
            'title': x.get('title', ''),
            'tags': x.get('style', []) + x.get('genre', []),
            'year': x.get('year')} for x in data['results']]


class Idiomag(DataProvider):
    '''Idiomag DataProvider'''

    def get_artist_data(self, artistname, _):
        '''Gets artist data from Idiomag.'''
        data = self._query_jsonapi(
            'http://www.idiomag.com/api/artist/tags/json',
            {'key': "77744b037d7b32a615d556aa279c26b5", 'artist': artistname})
        if not data or 'profile' not in data:
            return
        return [{'tags': {t['name']: int(t['value'] * 100)
                          for t in data['profile']['tag']}}]

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from Idiomag.'''
        # no album search support
        raise RuntimeError()


class EchoNest(DataProvider):
    '''EchoNest DataProvider'''

    def __init__(self):
        super(EchoNest, self).__init__()
        self.rate_limit = 3

    def get_artist_data(self, artistname, _):
        '''Gets artist data from EchoNest.'''
        data = self._query_jsonapi(
            'http://developer.echonest.com/api/v4/artist/search',
            {'api_key': "ZS0LNJH7V6ML8AHW3", 'format': 'json',
             'bucket': 'genre', 'results': 1, 'name': artistname})
        if not data or 'response' not in data or \
        'artists' not in data['response']:
            return
        return [{'tags': [t['name'] for t in x['genres']]}
                for x in data['response']['artists']]

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from EchoNest.'''
        # no album search support
        raise RuntimeError()
