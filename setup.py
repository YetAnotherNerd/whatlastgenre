#!/usr/bin/env python
'''whatlastgenre setup'''

from _version import __version__
from setuptools import setup
import os


def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(
    name='whatlastgenre',
    version=__version__,
    description=('Improves genre metadata of audio files '
                 'based on tags from various music sites.'),
    url='http://github.com/YetAnotherNerd/whatlastgenre',
    py_modules=['_version', 'mediafile', 'dataprovider'],
    scripts=['whatlastgenre'],
    package_data={'': ['tags.txt']},
    data_files=[('', ['tags.txt'])],
    install_requires=['mutagen', 'requests'],
    long_description=read('README.md'),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Topic :: Multimedia :: Sound/Audio',
        "Topic :: Utilities",
    ]
)
