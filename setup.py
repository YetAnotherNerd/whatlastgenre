#!/usr/bin/env python

# whatlastgenre
# Improves genre metadata of audio files
# based on tags from various music sites.
#
# Copyright (c) 2012-2016 YetAnotherNerd
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

"""whatlastgenre setup"""

import os

from setuptools import setup
from wlg import __version__


def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


setup(
    name='whatlastgenre',
    version=__version__,
    license='MIT',
    url='http://github.com/YetAnotherNerd/whatlastgenre',
    description=('Improves genre metadata of audio files '
                 'based on tags from various music sites.'),
    long_description=read('README.md'),
    packages=['wlg'],
    package_data={'wlg': ['data/genres.txt', 'data/tags.txt']},
    entry_points={
        'console_scripts': [
            'whatlastgenre = wlg.whatlastgenre:main'
        ]
    },
    install_requires=['mutagen', 'requests'],
    extras_require={
        'discogs': ['rauth'],
        'reqcache': ['requests-cache'],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Topic :: Multimedia :: Sound/Audio',
        'Topic :: Utilities'
    ]
)
