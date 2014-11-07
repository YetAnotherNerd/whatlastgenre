#!/usr/bin/env python

# Copyright (c) 2012-2014 YetAnotherNerd
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

'''whatlastgenre util: cachecleaner

Removes specific entries from cache.


Examples:

- Remove empty cache entries:
    ./cachecleaner.py -e

- Remove all artist data from whatcd and mbrainz:
    ./cachecleaner.py "(whatcd|mbrainz)##artist##.*"

- Remove all whatcd data:
    ./cachecleaner.py "whatcd##.*"

- Remove some other stuff:
    ./cachecleaner.py -e "whatcd##artist##.*" "discogs##album##.*"


Make sure whatlastgenre is not running while running this util script.
'''

from __future__ import division, print_function

import argparse
import json
import os
import re


def main():
    '''cachecleaner main function'''

    parser = argparse.ArgumentParser(
        description='Cleans specific entries from whatlastgenre cache.')
    parser.add_argument('keys', nargs='*',
                        help='keys to clean from cache (regex)')
    parser.add_argument('-e', '--empty', action='store_true',
                        help='clean empty data from cache')
    parser.add_argument('-f', '--file',
                        default=os.path.expanduser('~/.whatlastgenre/cache'),
                        help='location of the cache file')
    args = parser.parse_args()

    print("Loading cache... ", end='')
    with open(args.file) as file_:
        cache = json.load(file_)
    print("done! (%d entries, %.2f MB)"
          % (len(cache), os.path.getsize(args.file) / 2 ** 20))

    print("Cleaning cache... ", end='')
    size = len(cache)
    for key, val in cache.items():
        if ((args.empty and not val.get('data')) or
                any(re.match(k, key, re.I) for k in args.keys)):
            del cache[key]
    diff = size - len(cache)
    print("done! (%d removed)" % diff)

    if not diff:
        return

    print("Saving cache... ", end='')
    with open(args.file, 'w') as file_:
        json.dump(cache, file_)
    print("done! (%d entries, %.2f MB)"
          % (len(cache), os.path.getsize(args.file) / 2 ** 20))

if __name__ == "__main__":
    main()
