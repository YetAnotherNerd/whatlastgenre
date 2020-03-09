# whatlastgenre
# Improves genre metadata of audio files
# based on tags from various music sites.
#
# Copyright (c) 2012-2020 YetAnotherNerd
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

"""tests"""

from argparse import Namespace

from wlg.whatlastgenre import Config


def get_config():
    args = Namespace(
        tag_limit=4,
        verbose=2,
        update_cache=False,
        interactive=False,
        dry=False,
        difflib=False,
        release=False)
    try:
        conf = Config(args)
    except SystemExit:
        # default config created
        conf = Config(args)
    return conf
