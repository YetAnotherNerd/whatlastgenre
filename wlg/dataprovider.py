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

from __future__ import division, print_function

from collections import defaultdict
import json
import logging
import os.path
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


HEADERS = {'User-Agent': "whatlastgenre/%s" % __version__}


def get_daprs(conf):
    '''Return a list of DataProviders activated in the conf file.'''
    sources = conf.get_list('wlg', 'sources')
    daprs = []
    if 'discogs' in sources:
        daprs.append(Discogs())
    if 'echonest' in sources:
        daprs.append(EchoNest())
    if 'whatcd' in sources:
        daprs.append(WhatCD((conf.get('wlg', 'whatcduser'),
                             conf.get('wlg', 'whatcdpass'))))
    if 'mbrainz' in sources:
        daprs.append(MBrainz())
    if 'lastfm' in sources:
        daprs.append(LastFM())
    return daprs


class DataProviderError(Exception):
    '''If something went wrong with DataProviders.'''
    pass


class DataProvider(object):
    '''Base class for DataProviders.'''

    def __init__(self):
        self.name = self.__class__.__name__
        self.rate_limit = 1.0  # min. seconds between requests
        self.last_request = 0
        self.stats = defaultdict(float)
        self.session = requests.session()
        self.session.headers.update(HEADERS)
        self.log = logging.getLogger('whatlastgenre')

    def _query_jsonapi(self, url, params):
        '''Query a json-api by url and params.

        Honor rate limits and record some timings for stats.
        Return the json results.

        :param url: url str of the api
        :param params: dict of call parameters
        '''
        # rate limit
        while time.time() - self.last_request < self.rate_limit:
            self.stats['time_wait'] += .1
            time.sleep(.1)
        time_ = time.time()
        try:
            req = self.session.get(url, params=params)
        except requests.exceptions.RequestException as err:
            self.log.debug(err)
            raise DataProviderError("request error: %s" % err.message)
        self.stats['time_resp'] += time.time() - time_
        if not hasattr(req, 'from_cache') or not req.from_cache:
            self.last_request = time_
        if req.status_code != 200:
            self.log.debug(req.content)
            raise DataProviderError("request error: status code %s"
                                    % req.status_code)
        try:
            return req.json()
        except ValueError as err:
            self.log.debug(req.content)
            raise DataProviderError("request error: %s" % err.message)

    def query(self, query):
        '''Get results for a given query from this DataProvider.'''
        if query.type == 'artist':
            results = self.artist_query(query)
        elif query.type == 'album':
            results = self.album_query(query)
        # filter results
        if results:
            results = self.filter_results(query, results)
        return results

    def artist_query(self, query):
        '''Get artist data from a DataProvider.'''
        raise NotImplementedError()

    def album_query(self, query):
        '''Get album data from a DataProvider.'''
        raise NotImplementedError()

    def filter_results(self, query, results):
        '''Filter results from a DataProvider.'''
        # filter by album year
        if len(results) > 1 and query.type == 'album' and query.year:
            year = int(query.year)
            for i in range(4):
                tmp = [d for d in results if 'year' in d
                       and abs(int(d['year']) - year) <= i]
                if tmp and len(tmp) < len(results):
                    self.log.debug(
                        "prefiltered results '%d' -> '%d' (by year)",
                        len(results), len(tmp))
                    results = tmp
                    break
        return results


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
        data = self._query({'action': 'index'})
        if 'authkey' in data:
            self.session.get("https://what.cd/logout.php?auth=%s"
                             % data['authkey'])
            self.loggedin = False

    def _query(self, params):
        '''Query What.CD API.'''
        if not self.loggedin:
            self.__login()
        data = self._query_jsonapi('https://what.cd/ajax.php', params)
        if data and 'status' in data and data['status'] == 'success' \
                and 'response' in data:
            return data['response']
        return None

    def artist_query(self, query):
        '''Get artist data from What.CD.'''
        results = self._query({'action': 'artist',
                               'artistname': query.artist})
        if results and 'tags' in results and results['tags']:
            tags = {t['name'].replace('.', ' '): t['count']
                    for t in results['tags']}
            max_ = max(v for v in tags.values())
            return [{'tags': {k: v for k, v in tags.items() if v > max_ / 3}}]
        return None

    def album_query(self, query):
        '''Get album data from What.CD.'''
        results = self._query({'action': 'browse', 'filter_cat[1]': 1,
                               'artistname': query.artist,
                               'groupname': query.album})
        if results and 'results' in results and results['results']:
            results_ = []
            for res in results['results']:
                res_ = {'tags': {t.replace('.', ' '): 0 for t in res['tags']},
                        'releasetype': res['releaseType']}
                if len(results['results']) > 1:
                    res_.update({
                        'info': "%s - %s (%s) [%s]: "
                                "https://what.cd/torrents.php?id=%s"
                                % (res['artist'], res['groupName'],
                                   res['groupYear'], res['releaseType'],
                                   res['groupId']),
                        'year': res['groupYear']})
                results_.append(res_)
            return results_
        return None

    def filter_results(self, query, results):
        # filter by releasetype
        if len(results) > 1 and query.type == 'album' and query.releasetype:
            tmp = [d for d in results if 'releasetype' in d and
                   d['releasetype'].lower() == query.releasetype.lower()]
            if tmp and len(tmp) < len(results):
                self.log.debug("prefiltered results '%d' -> '%d' (by reltype)",
                               len(results), len(tmp))
                results = tmp
        super(WhatCD, self).filter_results(query, results)


class LastFM(DataProvider):
    '''Last.FM DataProvider'''

    def __init__(self):
        super(LastFM, self).__init__()
        # http://lastfm.de/api/tos
        self.rate_limit = 0.25

    def _query(self, params):
        '''Query Last.FM API.'''
        params.update({'api_key': "54bee5593b60d0a5bf379cedcad79052",
                       'format': 'json'})
        data = self._query_jsonapi('http://ws.audioscrobbler.com/2.0/', params)
        if data and 'error' not in data:
            return data
        return None

    def artist_query(self, query):
        '''Get artist data from Last.FM.'''
        results = None
        # search by mbid
        if query.mbid_artist:
            self.log.info("%-8s artist use   artist mbid     '%s'.",
                          self.name, query.mbid_artist)
            results = self._query({'method': 'artist.gettoptags',
                                   'mbid': query.mbid_artist})
        # search by name
        if not results:
            results = self._query({'method': 'artist.gettoptags',
                                   'artist': query.artist})
        return results

    def album_query(self, query):
        '''Get album data from Last.FM.'''
        results = None
        # search by mbid_album
        if query.mbid_album:
            self.log.info("%-8s album  use   album  mbid     '%s'.",
                          self.name, query.mbid_album)
            results = self._query({'method': 'album.gettoptags',
                                   'mbid': query.mbid_album})
        # search by mbid_relgrp
        if query.mbid_relgrp and not results:
            self.log.info("%-8s album  use   relgrp mbid     '%s'.",
                          self.name, query.mbid_relgrp)
            results = self._query({'method': 'album.gettoptags',
                                   'mbid': query.mbid_relgrp})
        # search by name
        if not results:
            artist = query.artist or 'Various Artists'
            results = self._query({'method': 'album.gettoptags',
                                   'album': query.album, 'artist': artist})
        return results

    def filter_results(self, query, results):
        # resolve results
        if results and 'toptags' in results and 'tag' in results['toptags']:
            tags = results['toptags']['tag']
            tags = tags if isinstance(tags, list) else [tags]
            tags = {t['name']: int(t['count']) for t in tags}
            max_ = max(v for v in tags.values())
            results = [{'tags': {k: v for k, v in tags.items()
                                 if v > max_ / 3}}]
            return super(LastFM, self).filter_results(query, results)
        return None


class MBrainz(DataProvider):
    '''MusicBrainz DataProvider'''

    def __init__(self):
        super(MBrainz, self).__init__()
        # http://musicbrainz.org/doc/XML_Web_Service/Rate_Limiting
        self.rate_limit = 1.0

    def _query(self, entity, query=None):
        '''Query MusicBrainz API.'''
        params = {'fmt': 'json'}
        if query:
            params.update({'query': query})
        else:
            params.update({'inc': 'tags'})
        return self._query_jsonapi(
            'http://musicbrainz.org/ws/2/' + entity, params)

    def artist_query(self, query):
        '''Get artist data from MusicBrainz.'''
        # search by mbid
        if query.mbid_artist:
            self.log.info("%-8s %-6s use   artist mbid     '%s'.",
                          self.name, query.type, query.mbid_artist)
            results = self._query('artist/%s' % query.mbid_artist)
            if results and 'error' not in results:
                return [results]
            print("%-8s %-6s got nothing, invalid MBID?"
                  % (self.name, query.type))
        # search by name
        results = self._query('artist', 'artist:"%s"' % query.artist)
        if results and 'artists' in results:
            return results['artists']
        return None

    def album_query(self, query):
        '''Get album data from MusicBrainz.'''
        # search by mbid_relgrp
        if query.mbid_relgrp:
            self.log.info("%-8s %-6s use   relgrp mbid     '%s'.",
                          self.name, query.type, query.mbid_relgrp)
            results = self._query('release-group/%s' % query.mbid_relgrp)
            if results and 'error' not in results:
                return [results]
            print("%-8s %-6s got nothing, invalid MBID?"
                  % (self.name, query.type))
        # search by mbid_album
        if query.mbid_album:
            self.log.info("%-8s %-6s use   album  mbid     '%s'.",
                          self.name, query.type, query.mbid_album)
            results = self._query('release/%s' % query.mbid_album)
            if results and 'error' not in results:
                return [results]
            print("%-8s %-6s got nothing, invalid MBID?"
                  % (self.name, query.type))
        # search by name
        qry = " AND artist: %s" % query.artist if query.artist else ''
        results = self._query('release-group',
                              "releasegroup: %s" % query.album + qry)
        if results and 'release-groups' in results:
            return results['release-groups']
        return None

    def filter_results(self, query, results):
        if results:
            if len(results) > 1 and all('score' in d for d in results):
                max_ = max(int(d['score']) for d in results)
                results = [d for d in results if int(d['score']) > max_ - 5]
            results = [{'tags': {t['name']: int(t['count'])
                                 for t in d['tags']}}
                       for d in results if 'tags' in d]
        return super(MBrainz, self).filter_results(query, results)


class Discogs(DataProvider):
    '''Discogs DataProvider'''

    def __init__(self):
        super(Discogs, self).__init__()
        import rauth
        # http://www.discogs.com/developers/#header:home-rate-limiting
        self.rate_limit = 3.0
        # OAuth1 authentication
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
            print("\nDiscogs requires authentication by an own account.\n"
                  "Update the configuration file to disable Discogs support "
                  "or use this link to authenticate:\n%s"
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

    def artist_query(self, query):
        '''Get artist data from Discogs.'''
        raise NotImplementedError()

    def album_query(self, query):
        '''Get album data from Discogs.'''
        params = {'release_title': query.album}
        if query.artist:
            params.update({'artist': query.artist})
        results = self._query_jsonapi(
            'http://api.discogs.com/database/search', params)
        if results and 'results' in results and results['results']:
            # merge releases and masters
            res_ = defaultdict(set)
            for res in results['results']:
                if res['type'] in ['master', 'release']:
                    for key in ['genre', 'style']:
                        if key in res:
                            res_[res['title']].update(res[key])
            return [{'tags': {t: 0 for t in r}} for r in res_.values()]
        return None


class EchoNest(DataProvider):
    '''EchoNest DataProvider'''

    def __init__(self):
        super(EchoNest, self).__init__()
        # http://developer.echonest.com/docs/v4#rate-limits
        self.rate_limit = 3.0

    def artist_query(self, query):
        '''Get artist data from EchoNest.'''
        results = self._query_jsonapi(
            'http://developer.echonest.com/api/v4/artist/search',
            [('api_key', 'ZS0LNJH7V6ML8AHW3'), ('format', 'json'),
             ('results', 1), ('bucket', 'genre'), ('bucket', 'terms'),
             ('name', query.artist)])
        if results and 'response' in results and results['response'] \
                and 'artists' in results['response'] \
                and results['response']['artists'] \
                and 'terms' in results['response']['artists'][0] \
                and results['response']['artists'][0]['terms']:
            tags = {t['name']: float(t['weight'])
                    for t in results['response']['artists'][0]['terms']}
            max_ = max(v for v in tags.values())
            return [{'tags': {k: v for k, v in tags.items() if v > max_ / 3}}]
        return None

    def album_query(self, query):
        '''Get album data from EchoNest.'''
        raise NotImplementedError()
