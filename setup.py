#!/usr/bin/env python
'''whatlastgenre setup'''

import os

from setuptools import setup
from wlg import __version__


def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


setup(
    name='whatlastgenre',
    version=__version__,
    url='http://github.com/YetAnotherNerd/whatlastgenre',
    description=('Improves genre metadata of audio files '
                 'based on tags from various music sites.'),
    long_description=read('README.md'),
    packages=['wlg'],
    package_data={'wlg': ['tags.txt']},
    entry_points={
        'console_scripts': [
            'whatlastgenre = wlg.whatlastgenre:main'
        ]
    },
    install_requires=['mutagen', 'requests'],
    extras_require={
        'discogs': ['oauth2'],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Topic :: Multimedia :: Sound/Audio',
        'Topic :: Utilities'
    ]
)
