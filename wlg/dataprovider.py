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
import operator
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
        allowable_codes=(200, 404),
        allowable_methods=('GET', 'POST'))
except ImportError:
    requests_cache = None


HEADERS = {'User-Agent': "whatlastgenre/%s" % __version__}


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
        self.log = logging.getLogger(__name__)
        self.name = self.__class__.__name__
        self.rate_limit = 1.0  # min. seconds between requests
        self.last_request = 0
        self.cache = None
        self.stats = defaultdict(float)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @classmethod
    def init_dataproviders(cls, conf):
        '''Initializes the DataProviders activated in the conf file.'''
        daprs = []
        cache = Cache(conf.path, conf.args.update_cache)
        for src in conf.get_list('wlg', 'sources'):
            dapr = DataProvider.factory(src, conf)
            if dapr:
                dapr.cache = cache
                daprs.append(dapr)
        if not daprs:
            logging.getLogger(__name__).error(
                'Where do you want to get your data from?\nAt least one '
                'source must be activated! (multiple sources recommended)')
            exit()
        return daprs

    @classmethod
    def factory(cls, name, conf):
        '''Factory method for DataProvider instances.'''
        dapr = None
        if name == 'whatcd':
            cred = {'username': conf.get('wlg', 'whatcduser'),
                    'password': conf.get('wlg', 'whatcdpass')}
            if all(cred.itervalues()):
                dapr = WhatCD(cred)
            else:
                logging.getLogger(__name__).warn(
                    'No What.CD credentials specified. '
                    'What.CD support disabled.')
        elif name == 'lastfm':
            dapr = LastFM()
        elif name == 'discogs':
            dapr = Discogs()
        elif name == 'mbrainz':
            dapr = MusicBrainz()
        elif name == 'echonest':
            dapr = EchoNest()
        return dapr

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
                    vals.append(.0)
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

        if res.status_code not in [200, 404]:
            raise DataProviderError(
                'status code %d: %s' % (res.status_code, res.reason))

        return res

    def _request_json(self, url, params, method='GET'):
        '''Return a json response from a request.'''
        res = self._request(url, params, method=method)
        try:
            return res.json()
        except ValueError as err:
            self.log.debug(res.text)
            raise DataProviderError("json request: %s" % err.message)

    def _prefilter_results(self, results, name, value, func):
        '''Try to prefilter results.'''
        res = [r for r in results if func(r) == value]
        if res and len(res) < len(results):
            self.log.info('prefilter %d of %d results by %s',
                          len(results) - len(res), len(results), name)
            results = res
        return results

    @classmethod
    def _preprocess_tags(cls, tags):
        '''Preprocess tags slightly to reduce the amount and don't
        pollute the cache with tags that obviously don't get used
        anyway.
        '''
        if not tags:
            return tags

        # strip and lower case tags
        tags = {k.strip().lower(): v for k, v in tags.iteritems()}

        # filter by length
        # 64 is not valid for a single tag, but it could be a
        # composition of tags for splitting
        tags = {k: v for k, v in tags.iteritems() if len(k) in range(2, 64)}

        # answer to the ultimate question of life, the universe,
        # the optimal number of considerable tags and everything
        limit = 42

        if len(tags) > limit:

            if any(tags.itervalues()):  # tags with scores
                min_val = max(tags.itervalues()) / 3
                tags = {k: v for k, v in tags.iteritems() if v >= min_val}
                sort_key = operator.itemgetter(1)  # best tags

            else:  # tags without scores
                sort_key = len  # shortest tags

            tags = sorted(tags.iteritems(), key=sort_key, reverse=1)
            tags = {k: v for k, v in tags[:limit]}

        return tags

    def cached_query(self, query):
        '''Perform a cached DataProvider query.'''
        cachekey = query.artist
        if query.type == 'album':
            cachekey += query.album
        cachekey = (self.name.lower(), query.type, cachekey.replace(' ', ''))

        # check cache
        res = self.cache.get(cachekey)
        if res:
            self.stats['reqs_cache'] += 1
            return res[1], True

        # no cache hit
        res = self.query(query)
        self.cache.set(cachekey, res)

        # save cache periodically
        if time.time() - self.cache.time > 600:
            self.cache.save()

        return res, False

    def query(self, query):
        '''Perform a real DataProvider query.'''
        res = None
        if query.type == 'artist':
            try:  # query by mbid
                if query.mbid_artist:
                    res = self.query_by_mbid(query.type, query.mbid_artist)
            except NotImplementedError:
                pass
            if not res:
                res = self.query_artist(query.artist)

        elif query.type == 'album':
            try:  # query by mbid
                if query.mbid_relgrp:
                    res = self.query_by_mbid(query.type, query.mbid_relgrp)
                if not res and query.mbid_album:
                    res = self.query_by_mbid(query.type, query.mbid_album)
            except NotImplementedError:
                pass
            if not res:
                res = self.query_album(query.album, query.artist,
                                       query.year, query.releasetype)

        # preprocess tags
        for result in res or []:
            result['tags'] = self._preprocess_tags(result['tags'])

        return res

    def query_artist(self, artist):
        '''Query for artist data.'''
        raise NotImplementedError()

    def query_album(self, album, artist=None, year=None, reltyp=None):
        '''Query for album data.'''
        raise NotImplementedError()

    def query_by_mbid(self, entity, mbid):
        '''Query by mbid.'''
        raise NotImplementedError()


class WhatCD(DataProvider):
    '''What.CD DataProvider'''

    def __init__(self, cred):
        super(WhatCD, self).__init__()
        # http://github.com/WhatCD/Gazelle/wiki/JSON-API-Documentation
        self.rate_limit = 2.0
        self.cred = cred
        self.authkey = None  # also a logged-in-flag

    def __del__(self):
        if self.authkey:
            self.logout()

    def login(self):
        '''Login to What.CD without using requests_cache.'''

        self.log.debug('WhatCD login...')

        def login():
            '''Login to What.CD.'''
            try:
                self._request('https://what.cd/login.php', self.cred, 'POST')
                res = self._request_json(
                    'https://what.cd/ajax.php', {'action': 'index'})
                self.authkey = res['response']['authkey']
            except (DataProviderError, KeyError):
                raise DataProviderError("login failed")

        if requests_cache:
            with self.session.cache_disabled():
                login()
        else:
            login()

    def logout(self):
        '''Logout from What.CD without using requests_cache.'''

        self.log.debug('WhatCD logout...')

        def logout():
            '''Logout from What.CD.'''
            if self.authkey:
                self._request('https://what.cd/logout.php',
                              {'auth': self.authkey})
                self.authkey = None

        if requests_cache:
            with self.session.cache_disabled():
                logout()
        else:
            logout()

    def _query(self, params):
        '''Query What.CD API.'''
        if not self.authkey:
            self.login()
        result = self._request_json('https://what.cd/ajax.php', params)
        try:
            return result['response']
        except KeyError:
            raise DataProviderError('request failure')

    def query_artist(self, artist):
        '''Query for artist data.'''
        result = self._query({'action': 'artist', 'artistname': artist})
        if not result:
            return None
        tags = {tag['name'].replace('.', ' '): tag.get('count', 0)
                for tag in result['tags']}
        return [{'tags': tags}]

    def query_album(self, album, artist=None, year=None, reltyp=None):
        '''Query for album data.'''
        res = self._query({'action': 'browse', 'filter_cat[1]': 1,
                           'artistname': artist, 'groupname': album})

        if not res['results']:
            return None

        res = res['results']

        # prefilter by snatched
        # make sure to enable "Enable snatched torrents indicator" in
        # your whatcd profile settings
        if len(res) > 1:
            res = self._prefilter_results(
                res, 'snatched', True, lambda x: any(t['hasSnatched']
                                                     for t in x['torrents']))

        # prefilter by reltyp
        if len(res) > 1 and reltyp:
            res = self._prefilter_results(
                res, 'releasetype', reltyp.lower(),
                lambda x: x.get('releaseType', '').lower())

        # prefilter by year
        if len(res) > 1 and year:
            res = self._prefilter_results(
                res, 'year', int(year), lambda x: int(x.get('groupYear', 0)))

        results = []
        for res_ in res:
            tags = {t.replace('.', ' '): 0 for t in res_['tags']}
            result = {'tags': tags, 'releasetype': res_['releaseType']}
            if len(res) > 1:
                info = \
                    '%s - %s (%s) [%s]: https://what.cd/torrents.php?id=%s' \
                    % (res_['artist'], res_['groupName'], res_['groupYear'],
                       res_['releaseType'], res_['groupId'])
                result.update({'info': info})
            results.append(result)

        return results

    def query_by_mbid(self, entity, mbid):
        '''Query by mbid.'''
        raise NotImplementedError()


class LastFM(DataProvider):
    '''Last.FM DataProvider'''

    def __init__(self):
        super(LastFM, self).__init__()
        # http://lastfm.de/api/tos
        self.rate_limit = .25

    def _query(self, params):
        '''Query Last.FM API.'''
        params.update({'format': 'json',
                       'api_key': '54bee5593b60d0a5bf379cedcad79052'})
        result = self._request_json(
            'http://ws.audioscrobbler.com/2.0/', params)

        if 'error' in result:
            self.log.debug('%-8s error: %s', self.name, result['message'])
            return None

        tags = result['toptags'].get('tag')
        if tags:
            if not isinstance(tags, list):
                tags = [tags]
            tags = {t['name']: int(t.get('count', 0)) for t in tags}

        return [{'tags': tags}]

    def query_artist(self, artist):
        '''Query for artist data.'''
        return self._query({'method': 'artist.gettoptags', 'artist': artist})

    def query_album(self, album, artist=None, year=None, reltyp=None):
        '''Query for album data.'''
        return self._query({'method': 'album.gettoptags', 'album': album,
                            'artist': artist or 'Various Artists'})

    def query_by_mbid(self, entity, mbid):
        '''Query by mbid.'''
        if entity == 'album':
            # FIXME: seems broken at the moment,
            # resolve later when lastfm finished migration
            raise NotImplementedError()
        self.log.debug("%-8s %-6s use mbid '%s'.", self.name, entity, mbid)
        return self._query({'method': entity + '.gettoptags', 'mbid': mbid})


class Discogs(DataProvider):
    '''Discogs DataProvider

    rauth requests can't be cached by requests_cache at this time,
    see https://github.com/reclosedev/requests-cache/pull/52
    '''

    def __init__(self):
        super(Discogs, self).__init__()
        # http://www.discogs.com/developers/#header:home-rate-limiting
        self.rate_limit = 3.0

        # OAuth1 authentication
        import rauth
        discogs = rauth.OAuth1Service(
            consumer_key='sYGBZLljMPsYUnmGOzTX',
            consumer_secret='TtuLoHxEGvjDDOVMgmpgpXPuxudHvklk',
            request_token_url='https://api.discogs.com/oauth/request_token',
            access_token_url='https://api.discogs.com/oauth/access_token',
            authorize_url='https://www.discogs.com/oauth/authorize')

        # load access token from file
        token_file = os.path.expanduser('~/.whatlastgenre/discogs.json')
        try:
            with open(token_file) as file_:
                data = json.load(file_)
            acc_token = data['token']
            acc_secret = data['secret']
        except (IOError, KeyError, ValueError):
            # get request token
            req_token, req_secret = discogs.get_request_token(headers=HEADERS)

            # get verifier from user
            print('Discogs requires authentication with your own account.\n'
                  'Disable discogs in the config file or use this link to '
                  'authenticate:\n%s' % discogs.get_authorize_url(req_token))
            verifier = raw_input('Verification code: ')

            # get access token
            try:
                acc_token, acc_secret = discogs.get_access_token(
                    req_token, req_secret, data={'oauth_verifier': verifier},
                    headers=HEADERS)
            except KeyError as err:
                self.log.fatal(err.message)
                exit()

            # save access token to file
            with open(token_file, 'w') as file_:
                json.dump({'token': acc_token, 'secret': acc_secret}, file_)

        self.session = discogs.get_session((acc_token, acc_secret))
        self.session.headers.update(HEADERS)

    def query_artist(self, artist):
        '''Query for artist data.'''
        raise NotImplementedError()

    def query_album(self, album, artist=None, year=None, reltyp=None):
        '''Query for album data.'''
        params = {'release_title': album}
        if artist:
            params.update({'artist': artist})
        result = self._request_json(
            'https://api.discogs.com/database/search', params)

        if not result['results']:
            return None

        # merge all releases and masters
        tags = set()
        for res in result['results']:
            if res['type'] in ['master', 'release']:
                for key in ['genre', 'style']:
                    tags.update(res.get(key))

        return [{'tags': {tag: 0 for tag in tags}}]

    def query_by_mbid(self, entity, mbid):
        '''Query by mbid.'''
        raise NotImplementedError()


class MusicBrainz(DataProvider):
    '''MusicBrainz DataProvider'''

    def __init__(self):
        super(MusicBrainz, self).__init__()
        # http://musicbrainz.org/doc/XML_Web_Service/Rate_Limiting
        self.rate_limit = 1.0
        self.name = 'MBrainz'

    def _query(self, path, params):
        '''Query MusicBrainz.'''
        params.update({'fmt': 'json'})
        result = self._request_json(
            'http://musicbrainz.org/ws/2/' + path, params)

        if 'error' in result:
            self.log.debug('%-8s error: %s', self.name, result['error'])
            return None

        if 'query' in params:
            result = result[path + 's']
            # prefilter by score
            if len(result) > 1:
                min_ = max(int(r['score']) for r in result) - 5
                result = [r for r in result if int(r['score']) > min_]

        else:  # by mbid
            result = [result]

        return [{'tags': {t['name']: int(t.get('count', 0))
                          for t in r.get('tags', {})}} for r in result]

    def query_artist(self, artist):
        '''Query for artist data.'''
        return self._query('artist', {'query': 'artist: ' + artist})

    def query_album(self, album, artist=None, year=None, reltyp=None):
        '''Query for album data.'''
        qry = 'releasegroup: %s' % album
        if artist:
            qry += ' AND artist: %s' % artist
        return self._query('release-group', {'query': qry})

    def query_by_mbid(self, entity, mbid):
        '''Query by mbid.'''
        self.log.debug("%-8s %-6s use mbid '%s'.", self.name, entity, mbid)
        if entity == 'album':
            entity = 'release-group'
        return self._query(entity + '/' + mbid, {'inc': 'tags'})


class EchoNest(DataProvider):
    '''EchoNest DataProvider'''

    def __init__(self):
        super(EchoNest, self).__init__()
        # http://developer.echonest.com/docs/v4#rate-limits
        self.rate_limit = 3.0

    def query_artist(self, artist):
        '''Query for artist data.'''
        result = self._request_json(
            'http://developer.echonest.com/api/v4/artist/search',
            [('api_key', 'ZS0LNJH7V6ML8AHW3'), ('format', 'json'),
             ('results', 1), ('bucket', 'terms'), ('bucket', 'genre'),
             ('name', artist)])

        if not result['response']['artists']:
            return None

        result = result['response']['artists'][0]
        terms = {tag['name']: float(tag['weight'] + tag['frequency']) * .5
                 for tag in result['terms']}
        genres = {tag['name']: 0 for tag in result['genres']}
        # TODO: merge them instead of using just one, can't just be handled as
        # separate results at this time since unscored tags with and without
        # counts can't be auto-merged later
        return [{'tags': terms or genres}]

    def query_album(self, album, artist=None, year=None, reltyp=None):
        '''Query for album data.'''
        raise NotImplementedError()

    def query_by_mbid(self, entity, mbid):
        '''Query by mbid.'''
        raise NotImplementedError()
