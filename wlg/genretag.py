#!/usr/bin/env python
'''whatlastgenre genretag'''

from __future__ import division, print_function

import ConfigParser
import StringIO
from _collections import defaultdict
import difflib
import itertools
import logging
import pkgutil
import re


LOG = logging.getLogger('whatlastgenre')


class GenreTags(object):
    '''Class for managing the genre tags.'''

    def __init__(self, conf):
        from wlg.whatlastgenre import get_conf_list
        self.conf = conf
        self.tags = None
        # list activated filters
        filters = ['badtags', 'generic']
        filters += get_conf_list(conf, 'genres', 'filters')
        # get and validate tagsfile
        self.tagsfile = self.get_tagsfile(filters)
        # fill matchlist
        self.matchlist = self.tagsfile.options('basictags')
        self.matchlist += get_conf_list(conf, 'genres', 'love')
        self.matchlist += get_conf_list(conf, 'genres', 'hate')
        self.matchlist += get_conf_list(conf, 'genres', 'blacklist')
        # fill replaces dict
        self.replaces = {}
        for pattern, repl in self.tagsfile.items('replaceme', True):
            self.replaces.update({pattern: repl})
        # build filter
        filter_ = get_conf_list(conf, 'genres', 'blacklist')
        for sec in [s for s in self.tagsfile.sections()
                    if s.startswith('filter_')]:
            if sec[7:] in filters:
                filter_ += self.tagsfile.options(sec)
            elif sec.endswith('_fuzzy') and sec[7:-6] in filters:
                for tag in self.tagsfile.options(sec):
                    filter_.append('.*%s.*' % tag)
        # set up regex
        self.regex = {}
        # compile some config options and tagsfile sections
        for sec, pats in ([(s, get_conf_list(conf, 'genres', s))
                           for s in ['love', 'hate']] +
                          [(s, self.tagsfile.options(s)) for s in
                           ['uppercase', 'dontsplit', 'replaceme']]):
            self.regex[sec] = re.compile('(%s)$' % '|'.join(pats), re.I)
        # compile filter in chunks
        self.regex['filter'] = []
        for i in range(0, len(filter_), 384):
            pat = '(%s)$' % '|'.join(filter_[i:i + 384])
            self.regex['filter'].append(re.compile(pat, re.I))

    def _add(self, group, name, score):
        '''Adds a genre tag after some filter, replace, match, split, score.
        Returns True if the tag was added, False otherwise.'''
        if not score:
            return False
        name = name.encode('ascii', 'ignore').lower()
        name = self._replace(name)
        if self._filter(name):
            return False
        name = self._match(name)
        score = self._split(group, name, score)
        if not score:
            return False
        self.tags[group][name] += score
        return True

    def _filter(self, name):
        '''Filters a tag by name, returns True if tag got filtered.'''
        if len(name) < 3 or len(name) > 19:
            return True
        if re.search(r'[^a-z0-9&\-_/\\,;\.\+\* ]', name, re.I):
            return True
        if self.regex['filter_album'].match(name):
            return True
        if any(f.match(name) for f in self.regex['filter']):
            return True
        return False

    def _replace(self, name):
        '''Applies all the replaces to a tag name.'''
        if self.regex['replaceme'].match(name):
            for pattern, repl in self.replaces.items():
                name = re.sub(pattern, repl, name, 0, re.I)
        return re.sub('(_| +)', ' ', name).strip()

    def _match(self, name):
        '''Matches a tag name with existing tag names.'''
        mli = []
        for taglist in self.tags.values():
            mli += taglist.keys()
        mli += self.matchlist
        if name in mli:
            return name
        # don't change cutoff, _add replaces instead
        match = difflib.get_close_matches(name, mli, 1, .8572)
        if match:
            return match[0].lower()
        return name

    def _split(self, group, name, score):
        '''Splits a tag and adds its parts with modified score,
        returns the remaining score for the base tag.'''
        if self.regex['dontsplit'].match(name):
            return score
        name = re.sub(r'([_/\\,;\.\+\*]| and )', '&', name, 0, re.I)
        if '&' in name:
            for part in [p for p in name.split('&') if not self._filter(p)]:
                self._add(group, part, score)
                return None
        elif ' ' in name.strip():
            rawparts = name.split(' ')
            parts = [p for p in rawparts if not self._filter(p)]
            if not parts:
                return None
            parts = itertools.combinations(parts, max(1, len(parts) - 1))
            for part in set(parts):
                self._add(group, ' '.join(part), score)
            if len(rawparts) > 2:
                return None
            return score * self.conf.getfloat('scores', 'splitup')
        return score

    def reset(self, bot):
        '''Resets the genre tags and album filter.'''
        self.tags = {'artist': defaultdict(float), 'album': defaultdict(float)}
        self.regex['filter_album'] = self.get_album_filter(bot)

    def add_tags(self, source, group, tags):
        '''Adds tags with or without counts to a given group, scores them
        while taking the source score multiplier into account.
        Returns the number of tags added.'''
        if not tags:
            return 0
        added = 0
        multi = self.conf.getfloat('scores', 'src_%s' % source)
        if isinstance(tags, dict):
            max_ = max(tags.values())
            for key, val in sorted(tags.items(), key=tags.get, reverse=1)[:99]:
                if self._add(group, key, val / max_ * multi):
                    added += 1
        elif isinstance(tags, list):
            score = .85 ** (len(tags) - 1)
            for name in tags:
                if self._add(group, name, score * multi):
                    added += 1
        return added

    def get(self, various=False):
        '''Returns the sorted and formated genre genres after merging.'''
        from wlg.whatlastgenre import tagprintstr
        for group, tags in ((k, v) for k, v in self.tags.items() if v):
            # norm tag scores
            max_ = max(tags.values())
            for tag, score in tags.items():
                tags[tag] = score / max_
            # verbose output
            tags = [(self.format(k), v) for k, v in sorted
                    (tags.items(), key=lambda (k, v): (v, k), reverse=1)
                    if v > 0.1]
            tagout = tagprintstr(tags[:12], "%5.2f %-19s")
            LOG.info("Best %6s genres (%d):\n%s", group, len(tags), tagout)
        # merge artist and album genres
        genres = defaultdict(float)
        for group, tags in self.tags.items():
            mult = 1
            if group == 'artist':
                mult = 'various' if various else 'artist'
                mult = self.conf.getfloat('scores', mult)
            for tag, score in tags.items():
                score = score * mult
                score *= 2 if self.regex['love'].match(tag) else 1
                score *= 0.5 if self.regex['hate'].match(tag) else 1
                if score > 0.1:
                    genres[tag] += score
        # format genres
        genres = {self.format(k): v for k, v in genres.items()}
        # sort and return keys
        return sorted(genres, key=genres.get, reverse=1)

    def format(self, name):
        '''Formats a tag to correct case.'''
        split = name.split(' ')
        for i in range(len(split)):
            if len(split[i]) < 3 and split[i] != 'nu' or \
                    self.regex['uppercase'].match(split[i]):
                split[i] = split[i].upper()
            elif re.match('[0-9]{4}s', name, re.I):
                split[i] = split[i].lower()
            else:
                split[i] = split[i].title()
        return ' '.join(split)

    @classmethod
    def get_tagsfile(cls, filters):
        '''Reads and parses the tagsfile, validates the results and returns a
        SafeConfigParser object for the tagsfile'''
        tagsfilestr = pkgutil.get_data('wlg', 'tags.txt')
        parser = ConfigParser.SafeConfigParser(allow_no_value=True)
        parser.readfp(StringIO.StringIO(tagsfilestr))
        # tags file validation
        for sec in [s for s in ['basictags', 'uppercase', 'dontsplit',
                                'replaceme']
                    if not parser.has_section(s)]:
            print("Got no [%s] from tag.txt file." % sec)
            exit()
        for sec in [s for s in filters if
                    not parser.has_section('filter_%s' % s) and
                    not parser.has_section('filter_%s_fuzzy' % s)]:
            print("The configured filter '%s' doesn't has a [filter_%s[_fuzzy]]"
                  " section in the tags.txt file." % (sec, sec))
            exit()
        return parser

    @classmethod
    def get_album_filter(cls, bot):
        ''' Returns a genre tag filter based on
        the metadata of a given bunch of tracks.'''
        badtags = []
        for tag in ['albumartist', 'album']:
            val = bot.get_common_meta(tag)
            if not val:
                continue
            bts = [val]
            if tag == 'albumartist' and ' ' in bts[0]:
                bts += bts[0].split(' ')
            for badtag in bts:
                for pat in [r'\(.*\)', r'\[.*\]', '{.*}', '-.*-', "'.*'",
                            '".*"', r'vol(\.|ume)? ', ' and ', 'the ',
                            r'[\W\d]', r'(\.\*)+']:
                    badtag = re.sub(pat, '.*', badtag, 0, re.I).strip()
                badtag = re.sub(r'(^\.\*|\.\*$)', '', badtag, 0, re.I)
                if len(badtag) > 2:
                    badtags.append(badtag.strip().lower())
        return re.compile('.*(' + '|'.join(badtags) + ').*', re.I)

