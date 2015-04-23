# whatlastgenre
# Improves genre metadata of audio files
# based on tags from various music sites.
#
# Copyright (c) 2012-2015 YetAnotherNerd
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

'''whatlastgenre dataprovider'''

from __future__ import print_function

from collections import defaultdict
import json
import logging
import os
import time

import requests

from wlg import __version__


try:  # use optional requests_cache if available
    import requests_cache
    requests_cache.install_cache(
        os.path.expanduser('~/.whatlastgenre/reqcache'),
        expire_after=180 * 24 * 60 * 60,
        old_data_on_error=True)
except ImportError:
    pass


LOG = logging.getLogger('whatlastgenre')

HEADERS = {'User-Agent': "whatlastgenre/%s" % __version__}


def get_daprs(conf):
    '''Returns a list of DataProviders activated in the conf file.

    Since DataProvider will be called in the order they are added here,
    DataProviders that provide good spelled tags (eg. sources with a
    fixed set of possible genres) should generally be added before
    DataProviders that provide user added tags to avoid getting malformed
    tags due to the tag matching process while adding them.

    :param conf: Config object of the configuration file
    '''
    sources = conf.get_list('wlg', 'sources')
    dps = []
    if 'discogs' in sources:
        dps.append(Discogs())
    if 'echonest' in sources:
        dps.append(EchoNest())
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

    def __init__(self):
        self.name = self.__class__.__name__
        self.session = requests.session()
        self.session.headers.update(HEADERS)
        self.last_request = time.time()
        self.rate_limit = 1.0  # min. seconds between requests
        self.stats = defaultdict(float)

    def _query_jsonapi(self, url, params):
        '''Queries an api and returns the json results.'''
        self.stats['time_wait'] += max(
            0, self.rate_limit - time.time() + self.last_request)
        while time.time() - self.last_request < self.rate_limit:
            time.sleep(.1)
        time_ = time.time()
        try:
            req = self.session.get(url, params=params)
        except requests.exceptions.RequestException as err:
            raise DataProviderError("request error: %s" % err.message)
        self.stats['time_resp'] += time.time() - time_
        if not hasattr(req, 'from_cache') or not req.from_cache:
            self.last_request = time_
        if req.status_code != 200:
            LOG.debug(req.content)
            raise DataProviderError("request error: status code %s"
                                    % req.status_code)
        try:
            return req.json()
        except ValueError as err:
            LOG.debug(req.content)
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
        # http://github.com/WhatCD/Gazelle/wiki/JSON-API-Documentation
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
        tags = data.get('tags', {})
        max_ = max([0] + [t['count'] for t in tags])
        return [{'tags': {t['name'].replace('.', ' '): int(t['count'])
                          for t in tags if int(t['count']) > (max_ / 3)}}]

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from What.CD.'''
        data = self._query({'action': 'browse', 'filter_cat[1]': 1,
                            'artistname': artistname,
                            'albumname': albumname})
        return [{
            'info': "%s - %s (%s) [%s]: https://what.cd/torrents.php?id=%s"
                    % (d['artist'], d['groupName'], d['groupYear'],
                       d['releaseType'], d['groupId']),
            'releasetype': d['releaseType'],
            'tags': {tag.replace('.', ' '): 0 for tag in d['tags']},
            'year': d['groupYear']} for d in data.get('results', {})]


class LastFM(DataProvider):
    '''Last.FM DataProvider'''

    def __init__(self):
        super(LastFM, self).__init__()
        # http://lastfm.de/api/tos
        self.rate_limit = 0.25

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
            LOG.info("%-8s artist search using %s mbid.", self.name, mbid)
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
            LOG.info("%-8s album  search using %s %s mbid.",
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
    # NOTE: its possible not to use ?query=*id: when searching by mbid,
    # but directly put the mbid into the url, then add ?inc=tags

    def __init__(self):
        super(MBrainz, self).__init__()
        # http://musicbrainz.org/doc/XML_Web_Service/Rate_Limiting
        self.rate_limit = 1.0

    def _query(self, typ, query):
        '''Queries the MusicBrainz API.'''
        url = 'http://musicbrainz.org/ws/2/' + typ
        params = {'fmt': 'json'}
        params.update({'query': query})
        return self._query_jsonapi(url, params)

    def get_artist_data(self, artistname, mbid):
        '''Gets artist data from MusicBrainz.'''
        data = None
        # search by mbid
        if mbid:
            LOG.info("%-8s artist search using %s mbid.", self.name, mbid)
            data = self._query('artist', 'arid:"' + mbid + '"')
            data = (data or {}).get('artists', None)
            if not data:
                print("%-8s artist search found nothing, invalid MBID?"
                      % self.name)
        # search without mbid
        if not data:
            data = self._query('artist', 'artist:"' + artistname + '"')
            if not data or not len(data.get('artists', [])):
                return
            max_ = max(int(x['score']) for x in data['artists'])
            data = [x for x in data['artists'] if int(x['score']) > max_ - 5]
        return [{
            'info': "%s (%s) [%s]: http://musicbrainz.org/artist/%s"
                    % (x['name'], x.get('disambiguation', ''),
                       x.get('type', ''), x['id']),
            'tags': {t['name']: int(t['count']) for t in x.get('tags', [])}}
            for x in data]

    def get_album_data(self, artistname, albumname, mbids):
        '''Gets album data from MusicBrainz.'''
        data = None
        # search by release mbid (just if there is no release-group mbid)
        mbid = 'albumid'
        if not mbids.get('releasegroupid') and mbids.get(mbid):
            LOG.info("%-8s album  search using %s %s mbid.",
                     self.name, mbids[mbid], mbid)
            data = self._query('release', 'reid:"' + mbids[mbid] + '"')
            data = (data or {}).get('releases', None)
            if data:
                mbids['releasegroupid'] = data[0]['release-group'].get('id')
                # remove albumids since relgrpids are expected later
                for i in range(len(data)):
                    data[i]['id'] = None
            else:
                print("%-8s rel.   search found nothing, invalid MBID?"
                      % self.name)
        # search by release-group mbid
        mbid = 'releasegroupid'
        if not data and mbids.get(mbid):
            LOG.info("%-8s album  search using %s %s mbid.",
                     self.name, mbids[mbid], mbid)
            data = self._query('release-group', 'rgid:"' + mbids[mbid] + '"')
            data = (data or {}).get('release-groups', None)
            if not data:
                print("%-8s relgrp search found nothing, invalid MBID?"
                      % self.name)
        # search without mbids
        if not data:
            data = self._query('release-group',
                               'artist:"' + artistname
                               + '" AND releasegroup:"' + albumname + '"')
            if not data or not len(data.get('release-groups', [])):
                return
            max_ = max(int(x['score']) for x in data['release-groups'])
            data = [x for x in data['release-groups']
                    if int(x['score']) > max_ - 5]
        return [{
            'info': "%s - %s [%s]: http://musicbrainz.org/release-group/%s"
                    % (x['artist-credit'][0]['artist']['name'], x.get('title'),
                       x.get('primary-type'), x['id']),
            'tags': {t['name']: int(t['count']) for t in x.get('tags', [])}}
            for x in data]


class Discogs(DataProvider):
    '''Discogs DataProvider'''

    def __init__(self):
        super(Discogs, self).__init__()
        import rauth
        # http://www.discogs.com/developers/#header:home-rate-limiting
        self.rate_limit = 1.0

        discogs = rauth.OAuth1Service(
            consumer_key='sYGBZLljMPsYUnmGOzTX',
            consumer_secret='TtuLoHxEGvjDDOVMgmpgpXPuxudHvklk',
            request_token_url='https://api.discogs.com/oauth/request_token',
            access_token_url='https://api.discogs.com/oauth/access_token',
            authorize_url='https://www.discogs.com/oauth/authorize')
        token_file = os.path.expanduser('~/.whatlastgenre/discogs.json')
        try:
            # try load access token from file
            with open(token_file) as file_:
                data = json.load(file_)
            acc_token = data['token']
            acc_secret = data['secret']
        except (IOError, KeyError, ValueError):
            # get request token
            req_token, req_secret = discogs.get_request_token(headers=HEADERS)
            # get verifier from user
            print("\nDiscogs now requires authentication.")
            print("If you don't have an account or don't want to use it, "
                  "remove it from 'sources' in the configuration file.")
            print("To enable Discogs support visit:\n%s"
                  % discogs.get_authorize_url(req_token))
            verifier = raw_input('Verification code: ')
            # get access token
            acc_token, acc_secret = discogs.get_access_token(
                req_token, req_secret, data={'oauth_verifier': verifier},
                headers=HEADERS)
            # save access token to file
            with open(token_file, 'w') as file_:
                json.dump({'token': acc_token, 'secret': acc_secret}, file_)

        self.session = discogs.get_session((acc_token, acc_secret))
        self.session.headers.update(HEADERS)

    def get_artist_data(self, artistname, _):
        '''Gets artist data from Discogs.'''
        # no artist search support
        raise RuntimeError()

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from Discogs.'''
        params = {'release_title': albumname}
        if artistname:
            params.update({'artist': artistname})
        data = self._query_jsonapi('http://api.discogs.com/database/search',
                                   params)
        if not data or 'results' not in data or not data['results']:
            return None
        # merge releases and masters
        results = defaultdict(set)
        for res in data['results']:
            if res['type'] in ['master', 'release']:
                for key in ['genre', 'style']:
                    if key in res:
                        results[res['title']].update(res[key])
        return [{'tags': {t: 0 for t in r}} for r in results.values()]


class EchoNest(DataProvider):
    '''EchoNest DataProvider'''

    def __init__(self):
        super(EchoNest, self).__init__()
        # http://developer.echonest.com/docs/v4#rate-limits
        self.rate_limit = 3.0

    def get_artist_data(self, artistname, _):
        '''Gets artist data from EchoNest.'''
        data = self._query_jsonapi(
            'http://developer.echonest.com/api/v4/artist/search',
            {'api_key': "ZS0LNJH7V6ML8AHW3", 'format': 'json',
             'bucket': 'genre', 'results': 1, 'name': artistname})
        return [{'tags': {t['name']: 0 for t in x.get('genres', [])}}
                for x in (data or {}).get('response', {}).get('artists', {})]

    def get_album_data(self, artistname, albumname, _):
        '''Gets album data from EchoNest.'''
        # no album search support
        raise RuntimeError()
