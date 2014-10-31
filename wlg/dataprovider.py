#!/usr/bin/env python
'''whatlastgenre dataprovider'''

from __future__ import print_function

import json
import logging
import time

import requests
from wlg import __version__


LOG = logging.getLogger('whatlastgenre')

HEADERS = {'User-Agent': "whatlastgenre/%s" % __version__}


def get_daprs(conf):
    '''Returns a list of DataProvider objects activated in the conf file.

    The DataProviders will later be called in the order they get added here.
    Since lastfm supports search by MBIDs, mbrainz should get added before
    lastfm. DataProviders that provide good spelled tags (eg. sources with a
    fixed set of possible genres) should generally be added before DataProviders
    that provide misspelled tags (eg. lastfm user tags) to avoid getting
    malformed tags due to the tag matching process while adding them.

    :param conf: ConfigParser object of the configuration file
    '''
    sources = conf.get_list('wlg', 'sources')
    dps = []
    if 'discogs' in sources:
        dps.append(Discogs())
    if 'echonest' in sources:
        dps.append(EchoNest())
    if 'idiomag' in sources:
        dps.append(Idiomag())
    if 'whatcd' in sources:
        dps.append(WhatCD((conf.get('wlg', 'whatcduser'),
                           conf.get('wlg', 'whatcdpass'))))
    if 'mbrainz' in sources:
        dps.append(MBrainz())
    if 'lastfm' in sources:
        dps.append(LastFM())
    return dps


class DataProviderError(Exception):
    '''If something went wrong with DataProviders.'''
    pass


class DataProvider(object):
    '''Base class for DataProviders.'''

    session = requests.session()
    session.headers.update(HEADERS)

    def __init__(self):
        self.name = self.__class__.__name__
        self.last_request = time.time()
        self.rate_limit = 0.5  # min. seconds between requests

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
            raise DataProviderError("request error: %s" % err.message)

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
            return {}
        return data.get('response', {})

    def get_artist_data(self, artistname, _):
        '''Gets artist data from What.CD.'''
        data = self._query({'action': 'artist', 'artistname': artistname})
        return [{'tags': {t['name'].replace('.', ' '): int(t['count'])
                          for t in data.get('tags', {})}}]

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from What.CD.'''
        data = self._query({'action': 'browse', 'filter_cat[1]': 1,
                            'searchstr': artistname + ' ' + albumname})
        return [{
            'info': "%s - %s (%s) [%s]: https://what.cd/torrents.php?id=%s"
                    % (d['artist'], d['groupName'], d['groupYear'],
                       d['releaseType'], d['groupId']),
            'title': d['artist'] + ' - ' + d['groupName'],
            'releasetype': d['releaseType'],
            'tags': [tag.replace('.', ' ') for tag in d.get('tags', [])],
            'year': d['groupYear']} for d in data.get('results', {})]


class LastFM(DataProvider):
    '''Last.FM DataProvider'''

    def _query(self, params):
        '''Queries the Last.FM API.'''
        params.update({'api_key': "54bee5593b60d0a5bf379cedcad79052",
                       'format': 'json'})
        data = self._query_jsonapi('http://ws.audioscrobbler.com/2.0/',
                                   params)
        if not data or 'error' in data:
            return
        return data

    def get_artist_data(self, artistname, mbid):
        '''Gets artist data from Last.FM.'''
        data = None
        # search with mbid
        if mbid:
            LOG.info("%8s artist search using %s mbid.", self.name, mbid)
            data = self._query({'method': 'artist.gettoptags', 'mbid': mbid})
        # search without mbid
        if not data:
            data = self._query({'method': 'artist.gettoptags',
                                'artist': artistname})
        return self.__handle_data(data)

    def get_album_data(self, artistname, albumname, mbids):
        '''Gets album data from Last.FM.

        Last.FM seems to understand album mbids as albumid,
        not as releasegroupid.
        '''
        data = None
        # search with mbid
        mbid = 'albumid'
        if mbid in mbids and mbids[mbid]:
            LOG.info("%8s  album search using %s %s mbid.",
                     self.name, mbids[mbid], mbid)
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
        '''Helper method for data handling.'''
        if not data or 'toptags' not in data or 'tag' not in data['toptags']:
            return
        tags = data['toptags']['tag']
        tags = tags if isinstance(tags, list) else [tags]
        return [{'tags': {t['name']: int(t['count']) for t in tags
                          if t['count'] and int(t['count']) > 40}}]


class MBrainz(DataProvider):
    '''MusicBrainz DataProvider'''
    # NOTE: its possible not to use ?query=*id: when searching by mbid, but
    # directly put the mbid into the url, then don't forget to add ?inc=tags

    def __init__(self):
        super(MBrainz, self).__init__()
        self.rate_limit = 1.0

    def _query(self, typ, query):
        '''Queries the MusicBrainz API.'''
        params = {'fmt': 'json'}
        params.update({'query': query})
        return self._query_jsonapi('http://musicbrainz.org/ws/2/' + typ + '/',
                                   params)

    def get_artist_data(self, artistname, mbid):
        '''Gets artist data from MusicBrainz.'''
        data = None
        # search by mbid
        if mbid:
            LOG.info("%8s artist search using %s mbid.", self.name, mbid)
            data = self._query('artist', 'arid:"' + mbid + '"')
            if data and 'artist' in data:
                data = data['artist']
            else:
                data = None
                print("%8s artist search found nothing, invalid MBID?"
                      % self.name)
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
            LOG.info("%8s  album search using %s %s mbid.",
                     self.name, mbids[mbid], mbid)
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
                data = None
                print("%8s rel.   search found nothing, invalid MBID?"
                      % self.name)
        # search by release-group mbid
        mbid = 'releasegroupid'
        if not data and mbids.get(mbid):
            LOG.info("%8s  album search using %s %s mbid.",
                     self.name, mbids[mbid], mbid)
            data = self._query('release-group', 'rgid:"' + mbids[mbid] + '"')
            if data and 'release-groups' in data:
                data = data['release-groups']
            else:
                data = None
                print("%8s relgrp search found nothing, invalid MBID?"
                      % self.name)
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

    def __init__(self):
        import oauth2, os
        super(Discogs, self).__init__()
        consumer = oauth2.Consumer('sYGBZLljMPsYUnmGOzTX',
                                   'TtuLoHxEGvjDDOVMgmpgpXPuxudHvklk')
        token_file = os.path.expanduser('~/.whatlastgenre/discogs.json')
        try:
            # try load access token from file
            with open(token_file) as file_:
                data = json.load(file_)
            token_key = data['key']
            token_secret = data['secret']
        except (IOError, KeyError, ValueError):
            token_key, token_secret = self._authenticate(consumer)
            # save access token to file
            with open(token_file, 'w') as file_:
                json.dump({'key': token_key, 'secret': token_secret}, file_)
        token = oauth2.Token(token_key, token_secret)
        # client
        self.client = oauth2.Client(consumer, token)

    @classmethod
    def _authenticate(cls, consumer):
        '''Asks the user to log in to Discogs to get the access token.'''
        import oauth2, urlparse

        request_token_url = 'https://api.discogs.com/oauth/request_token'
        authorize_url = 'https://www.discogs.com/oauth/authorize'
        access_token_url = 'https://api.discogs.com/oauth/access_token'

        # get request token
        client = oauth2.Client(consumer)
        resp, content = client.request(request_token_url, 'POST',
                                       headers=HEADERS)
        request_token = dict(urlparse.parse_qsl(content))

        if resp['status'] != '200':
            raise DataProviderError("invalid response %s." % resp['status'])

        # get verifier from user
        print("\nDiscogs now requires authentication.")
        print("If you don't have an Discogs account or don't wont to use it, "
              "remove it from 'sources' in the configuration file.")
        print("To enable Discogs support visit:\n%s?oauth_token=%s"
              % (authorize_url, request_token['oauth_token']))
        oauth_verifier = raw_input('Verification code: ')

        # get access token
        token = oauth2.Token(request_token['oauth_token'],
                             request_token['oauth_token_secret'])
        token.set_verifier(oauth_verifier)
        client = oauth2.Client(consumer, token)
        _, content = client.request(access_token_url, 'POST', headers=HEADERS)
        access_token = dict(urlparse.parse_qsl(content))

        return access_token['oauth_token'], access_token['oauth_token_secret']

    def get_artist_data(self, artistname, _):
        '''Gets artist data from Discogs.'''
        # no artist search support
        raise RuntimeError()

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from Discogs.'''
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(.1)
        resp, content = self.client.request(
            'https://api.discogs.com/database/search?type=master&q=%s'
            % (artistname + ' ' + albumname), headers=HEADERS)
        self.last_request = time.time()
        if resp['status'] != '200':
            raise DataProviderError("request error: status %s" % resp['status'])
        try:
            data = json.loads(content)
        except ValueError as err:
            LOG.info(content)
            raise DataProviderError("request error: %s" % err.message)
        return [{
            'info': "%s (%s) [%s]: %s"
                    % (x.get('title'), x.get('year'),
                       ', '.join(x.get('format')), x['resource_url']),
            'title': x.get('title', ''),
            'tags': x.get('style', []) + x.get('genre', []),
            'year': x.get('year')} for x in (data or []).get('results', {})]


class Idiomag(DataProvider):
    '''Idiomag DataProvider'''

    def get_artist_data(self, artistname, _):
        '''Gets artist data from Idiomag.'''
        data = self._query_jsonapi(
            'http://www.idiomag.com/api/artist/tags/json',
            {'key': "77744b037d7b32a615d556aa279c26b5", 'artist': artistname})
        if not data:
            return
        return [{'tags': {t['name']: int(t['value'])
                          for t in data.get('profile', {}).get('tag', {})}}]

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from Idiomag.'''
        # no album search support
        raise RuntimeError()


class EchoNest(DataProvider):
    '''EchoNest DataProvider'''

    def __init__(self):
        super(EchoNest, self).__init__()
        self.rate_limit = 3.0

    def get_artist_data(self, artistname, _):
        '''Gets artist data from EchoNest.'''
        data = self._query_jsonapi(
            'http://developer.echonest.com/api/v4/artist/search',
            {'api_key': "ZS0LNJH7V6ML8AHW3", 'format': 'json',
             'bucket': 'genre', 'results': 1, 'name': artistname})
        if not data:
            return
        return [{'tags': [t['name'] for t in x['genres']]}
                for x in data.get('response', {}).get('artists', {})]

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from EchoNest.'''
        # no album search support
        raise RuntimeError()