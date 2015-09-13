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
from tempfile import NamedTemporaryFile
import time

import requests

from wlg import __version__


try:  # use optional requests_cache if available
    import requests_cache
    requests_cache.install_cache(
        os.path.expanduser('~/.whatlastgenre/reqcache'),
        expire_after=180 * 24 * 60 * 60,
        allowable_methods=('GET', 'POST'),
        old_data_on_error=True)
except ImportError:
    requests_cache = None


HEADERS = {'User-Agent': "whatlastgenre/%s" % __version__}


def get_daprs(conf):
    '''Return a list of DataProviders activated in the conf file.'''
    sources = conf.get_list('wlg', 'sources')
    daprs = []
    if 'whatcd' in sources:
        cred = {'username': conf.get('wlg', 'whatcduser'),
                'password': conf.get('wlg', 'whatcdpass')}
        if all(cred.itervalues()):
            daprs.append(WhatCD(cred))
        else:
            print("No What.CD credentials specified. "
                  "What.CD support disabled.\n")
    if 'lastfm' in sources:
        daprs.append(LastFM())
    if 'discogs' in sources:
        daprs.append(Discogs())
    if 'mbrainz' in sources:
        daprs.append(MusicBrainz())
    if 'echonest' in sources:
        daprs.append(EchoNest())
    if not daprs:
        print("Where do you want to get your data from?\nAt least one "
              "source must be activated (multiple sources recommended)!")
        exit()
    return daprs


class Cache(object):
    '''Load and save a dict as json from/into a file for some
    speedup.
    '''

    def __init__(self, path, update_cache):
        self.fullpath = os.path.join(path, 'cache')
        self.update_cache = update_cache
        self.expire_after = 180 * 24 * 60 * 60
        self.time = time.time()
        self.dirty = False
        self.cache = {}
        self.new = set()
        try:
            with open(self.fullpath) as file_:
                self.cache = json.load(file_)
        except (IOError, ValueError):
            pass

    def __del__(self):
        self.save()

    def get(self, key):
        '''Return a (time, value) data tuple for a given key.'''
        key = str(key)
        if key in self.cache \
                and time.time() < self.cache[key][0] + self.expire_after \
                and (not self.update_cache or key in self.new):
            return self.cache[key]
        return None

    def set(self, key, value):
        '''Set value for a given key.'''
        key = str(key)
        if value:
            keep = ['tags', 'releasetype']
            if len(value) > 1:
                keep.append('info')
            value = [{k: v for k, v in val.iteritems() if k in keep}
                     for val in value if val]
        self.cache[key] = (time.time(), value)
        if self.update_cache:
            self.new.add(key)
        self.dirty = True

    def clean(self):
        '''Clean up expired entries.'''
        print("Cleaning cache... ", end='')
        size = len(self.cache)
        for key, val in self.cache.items():
            if time.time() > val[0] + self.expire_after:
                del self.cache[key]
                self.dirty = True
        print("done! (%d entries removed)" % (size - len(self.cache)))

    def save(self):
        '''Save the cache dict as json string to a file.
        Clean expired entries before saving and use a tempfile to
        avoid data loss on interruption.
        '''
        if not self.dirty:
            return
        self.clean()
        print("Saving cache... ", end='')
        dirname, basename = os.path.split(self.fullpath)
        try:
            with NamedTemporaryFile(prefix=basename + '.tmp_',
                                    dir=dirname, delete=False) as tmpfile:
                tmpfile.write(json.dumps(self.cache))
                os.fsync(tmpfile)
            # seems atomic rename here is not possible on windows
            # http://docs.python.org/2/library/os.html#os.rename
            if os.name == 'nt' and os.path.isfile(self.fullpath):
                os.remove(self.fullpath)
            os.rename(tmpfile.name, self.fullpath)
            self.time = time.time()
            self.dirty = False
            size_mb = os.path.getsize(self.fullpath) / 2 ** 20
            print("  done! (%d entries, %.2f MB)" % (len(self.cache), size_mb))
        except KeyboardInterrupt:
            if os.path.isfile(tmpfile.name):
                os.remove(tmpfile.name)


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
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.log = logging.getLogger('whatlastgenre')

    @classmethod
    def print_stats(cls, daprs):
        '''Print some DataProvider statistics.'''
        print("\nSource stats  ", ''.join("| %-8s " % d.name for d in daprs),
              "\n", "-" * 14, "+----------" * len(daprs), sep='')
        for key in ['reqs_err', 'reqs_web', 'reqs_cache', 'reqs_lowcache',
                    'results', 'results_none', 'results_many', 'results/req',
                    'tags', 'tags/result', 'goodtags', 'goodtags/tag',
                    'time_resp_avg', 'time_wait_avg']:
            vals = []
            for dapr in daprs:
                num_req_web = dapr.stats['reqs_web']
                num_req = sum((num_req_web, dapr.stats['reqs_cache'],
                               dapr.stats['reqs_lowcache']))
                if key == 'results/req' and num_req > 0:
                    vals.append(dapr.stats['results'] / num_req)
                elif key == 'time_resp_avg' and num_req_web:
                    vals.append(dapr.stats['time_resp'] / num_req_web)
                elif key == 'time_wait_avg' and num_req_web:
                    vals.append(dapr.stats['time_wait'] / num_req_web)
                elif key == 'tags/result' and dapr.stats['results']:
                    vals.append(dapr.stats['tags'] / dapr.stats['results'])
                elif key == 'goodtags/tag' and dapr.stats['tags']:
                    vals.append(dapr.stats['goodtags'] / dapr.stats['tags'])
                elif key in dapr.stats:
                    vals.append(dapr.stats[key])
                else:
                    vals.append(0.0)
            if any(v for v in vals):
                pat = "| %8d " if all(v.is_integer() for v in vals) \
                    else "| %8.2f "
                print("%-13s " % key, ''.join(pat % v for v in vals), sep='')

    def _wait_rate_limit(self):
        '''Wait for the rate limit.'''
        while time.time() - self.last_request < self.rate_limit:
            self.stats['time_wait'] += .1
            time.sleep(.1)

    def _request(self, url, params, method='GET'):
        '''Send a request.

        Honor rate limits and record some timings for stats.

        :param url: url string
        :param params: dict of call parameters
        :param method: request method
        '''
        self._wait_rate_limit()
        time_ = time.time()
        try:
            if method == 'POST':
                res = self.session.post(url, data=params)
            else:
                res = self.session.get(url, params=params)
        except requests.exceptions.RequestException as err:
            self.log.debug(err)
            raise DataProviderError("request: %s" % err.message)
        if not getattr(res, 'from_cache', False):
            self.stats['reqs_web'] += 1
            self.stats['time_resp'] += time.time() - time_
            self.last_request = time_
        else:
            self.stats['reqs_lowcache'] += 1
        return res

    def _request_json(self, url, params, method='GET'):
        '''Return a json response from a request.'''
        res = self._request(url, params, method=method)
        if res.status_code != 200:
            self.log.debug(res.text)
            raise DataProviderError('status code: %s' % res.status_code)
        try:
            return res.json()
        except ValueError as err:
            self.log.debug(res.text)
            raise DataProviderError("json request: %s" % err.message)

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
        '''Login to What.CD without using requests_cache.'''

        def login():
            '''Login to What.CD.'''
            self._request('https://what.cd/login.php', self.cred, 'POST')

        if requests_cache:
            with self.session.cache_disabled():
                login()
        else:
            login()
        self.loggedin = True

    def __logout(self):
        '''Logout from What.CD without using requests_cache.'''

        def logout():
            '''Logout from What.CD.'''
            res = self._query({'action': 'index'})
            if 'authkey' in res:
                self._request('https://what.cd/logout.php',
                              {'auth': res['authkey']})

        if requests_cache:
            with self.session.cache_disabled():
                logout()
        else:
            logout()
        self.loggedin = False

    def _query(self, params):
        '''Query What.CD API.'''
        if not self.loggedin:
            self.__login()
        res = self._request_json('https://what.cd/ajax.php', params)
        if res and 'status' in res and res['status'] == 'success' \
                and 'response' in res:
            return res['response']
        return None

    def artist_query(self, query):
        '''Get artist data from What.CD.'''
        results = self._query({'action': 'artist', 'artistname': query.artist})
        if results and 'tags' in results and results['tags']:
            tags = {t['name'].replace('.', ' '): t['count']
                    for t in results['tags']}
            max_ = max(v for v in tags.itervalues())
            return [{'tags': {k: v for k, v in tags.iteritems()
                              if v > max_ / 3}}]
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
                        'snatched': any(t['hasSnatched']
                                        for t in res['torrents']),
                        'year': res['groupYear']})
                results_.append(res_)
            return results_
        return None

    def filter_results(self, query, results):
        if len(results) > 1 and query.type == 'album':
            # filter by snatched
            tmp = [r for r in results if r.get('snatched', False)]
            if tmp and len(tmp) < len(results):
                self.log.info("prefiltered %d of %d results by snatched",
                              len(results) - len(tmp), len(results))
                results = tmp
            # filter by releasetype
            if len(results) > 1 and query.releasetype:
                tmp = [r for r in results if r.get('releasetype', '').lower()
                       == query.releasetype.lower()]
                if tmp and len(tmp) < len(results):
                    self.log.info("prefiltered %d of %d results by reltyp",
                                  len(results) - len(tmp), len(results))
                    results = tmp
        return super(WhatCD, self).filter_results(query, results)


class LastFM(DataProvider):
    '''Last.FM DataProvider'''

    def __init__(self):
        super(LastFM, self).__init__()
        # http://lastfm.de/api/tos
        self.rate_limit = 0.25

    def _query(self, params):
        '''Query Last.FM API.'''
        params.update({'format': 'json',
                       'api_key': '54bee5593b60d0a5bf379cedcad79052'})
        res = self._request_json('http://ws.audioscrobbler.com/2.0/', params)
        if res and 'error' not in res:
            return res
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
            max_ = max(v for v in tags.itervalues())
            results = [{'tags': {k: v for k, v in tags.iteritems()
                                 if v > max_ / 3}}]
            return super(LastFM, self).filter_results(query, results)
        return None


class Discogs(DataProvider):
    '''Discogs DataProvider

    Known issues:
    * rauth requests can't be cached by requests_cache at this time
    '''

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
        results = self._request_json(
            'http://api.discogs.com/database/search', params)
        if results and 'results' in results and results['results']:
            # merge releases and masters
            res_ = defaultdict(set)
            for res in results['results']:
                if res['type'] in ['master', 'release']:
                    for key in ['genre', 'style']:
                        if key in res:
                            res_[res['title']].update(res[key])
            return [{'tags': {t: 0 for t in r}} for r in res_.itervalues()]
        return None


class MusicBrainz(DataProvider):
    '''MusicBrainz DataProvider'''

    def __init__(self):
        super(MusicBrainz, self).__init__()
        # http://musicbrainz.org/doc/XML_Web_Service/Rate_Limiting
        self.rate_limit = 1.0
        self.name = 'MBrainz'

    def _query(self, entity, query=None):
        '''Query MusicBrainz API.'''
        params = {'fmt': 'json'}
        if query:
            params.update({'query': query})
        else:
            params.update({'inc': 'tags'})
        return self._request_json(
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
        return super(MusicBrainz, self).filter_results(query, results)


class EchoNest(DataProvider):
    '''EchoNest DataProvider'''

    def __init__(self):
        super(EchoNest, self).__init__()
        # http://developer.echonest.com/docs/v4#rate-limits
        self.rate_limit = 3.0

    def artist_query(self, query):
        '''Get artist data from EchoNest.'''
        results = self._request_json(
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
            max_ = max(v for v in tags.itervalues())
            return [{'tags': {k: v for k, v in tags.iteritems()
                              if v > max_ / 3}}]
        return None

    def album_query(self, query):
        '''Get album data from EchoNest.'''
        raise NotImplementedError()
