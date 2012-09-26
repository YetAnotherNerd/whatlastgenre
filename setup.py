#!/usr/bin/env python

from setuptools import setup

setup(
    name='whatlastgenre',
    version='0.1',
    description=('Improves genre metadata of audio files based on '
                 'tags from various music-sites.'),
    author='YetAnotherNerd',
    author_email='qpdb@foosion.de',
    url='http://github.com/YetAnotherNerd/whatlastgenre',
    scripts=['whatlastgenre.py'],
    install_requires=[
        'musicbrainzngs',
        'mutagen',
        'requests',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Topic :: Multimedia :: Sound/Audio',
        "Topic :: Utilities",
    ],
)
