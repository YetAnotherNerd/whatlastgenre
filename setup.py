#!/usr/bin/env python

from setuptools import setup

setup(
    name='whatlastgenre',
    version='0.1',
    description=('Improves genre metadata of audio files '
                 'based on tags from various music sites.'),
    url='http://github.com/YetAnotherNerd/whatlastgenre',
    # saying tags.txt is a script is ugly but works
    # please tell me a better solution if there is one
    scripts=['whatlastgenre.py', 'tags.txt'],
    package_data={'': ['tags.txt']},
    install_requires=['musicbrainzngs', 'mutagen', 'requests'],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Topic :: Multimedia :: Sound/Audio',
        "Topic :: Utilities",
    ]
)
