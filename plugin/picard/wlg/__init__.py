# -*- coding: utf-8 -*-

PLUGIN_NAME = 'WhatLastGenre'
PLUGIN_AUTHOR = 'Adam Jakab'
PLUGIN_DESCRIPTION = 'Use WhatLastGenere to improve genre tags.'
PLUGIN_VERSION = "0.1"
PLUGIN_API_VERSIONS = ['2.2']
PLUGIN_LICENSE = "MIT"
PLUGIN_LICENSE_URL = "https://github.com/YetAnotherNerd/whatlastgenre/blob" \
                     "/master/LICENSE"

import os.path
from argparse import Namespace

from PyQt5 import QtCore, QtWidgets
from picard import config, log
from picard.album import Album
from picard.config import BoolOption, IntOption, TextOption
from picard.formats import MP3File
from picard.metadata import register_track_metadata_processor, Metadata
from picard.ui.options import register_options_page, OptionsPage

from wlg import whatlastgenre
from wlg.mediafile import Metadata as WlgMetadata


def process_track(album: Album, metadata: Metadata, track, release):
    if not config.setting["wlg_enable"]:
        return

    wlg_genres = _get_genres(album, metadata)
    if len(wlg_genres) == 1:
        wlg_genre = wlg_genres[0]
    else:
        genres_glue = str(config.setting["wlg_genres_glue"])
        wlg_genre = genres_glue.join(wlg_genres)

    # log.debug('{}'.format("=" * 80))
    log.debug('WLG Genres: {}'.format(wlg_genres))
    # log.debug('WLG Genre: {}'.format(wlg_genre))
    # log.debug('{}'.format("=" * 80))

    metadata["genre"] = wlg_genre


def _get_genres(album: Album, metadata: Metadata):
    album_dir = None
    mp3: MP3File = next(album.iterfiles(), None)
    if mp3:
        album_dir = os.path.dirname(mp3.filename)

    artists = []
    for track in album.tracks:
        tmd: Metadata = track.metadata
        artists.append((
            tmd.getall("albumartist")[0],
            tmd.getall("musicbrainz_albumartistid")[0],
        ))

    metadata = WlgMetadata(
        path=album_dir,
        type='beet',
        artists=artists,
        albumartist=(
            metadata.getall("albumartist")[0],
            metadata.getall("musicbrainz_albumartistid")[0],
        ),
        album=metadata.getall("album")[0],
        mbid_album=metadata.getall("musicbrainz_albumid")[0],
        mbid_relgrp=metadata.getall("musicbrainz_releasegroupid")[0],
        year=metadata.getall("originalyear")[0],
        releasetype=metadata.getall("releasetype")[0],
    )
    # log.info('WLG METADATA: {}'.format(metadata))

    wlg = _get_wlg(False, 1)

    wlg_genres, _ = wlg.query_album(metadata)

    return wlg_genres


def _get_wlg(update_cache=False, verbose=0):
    conf = whatlastgenre.Config(Namespace(
        tag_limit=int(config.setting["wlg_max_tags"]),
        separator=str(config.setting["wlg_genres_glue"]),
        whitelist='',
        update_cache=update_cache,
        verbose=verbose,
        dry=False,
        difflib=False,
        release=False)
    )

    return whatlastgenre.WhatLastGenre(conf)


class WlgOptionsPage(OptionsPage):
    NAME = "whatlastgenre"
    TITLE = "WhatLastGenre"
    PARENT = "plugins"

    options = [
        BoolOption("setting", "wlg_enable", False),
        IntOption("setting", "wlg_max_tags", 3),
        TextOption("setting", "wlg_genres_glue", " / "),
    ]

    def __init__(self, parent=None):
        super(WlgOptionsPage, self).__init__(parent)
        self.ui = Ui_WlgOptionsPage()
        self.ui.setupUi(self)

    def load(self):
        setting = config.setting
        self.ui.wlg_enable.setChecked(setting["wlg_enable"])
        self.ui.max_genres.setValue(setting["wlg_max_tags"])

    def save(self):
        setting = config.setting
        setting["wlg_enable"] = self.ui.wlg_enable.isChecked()
        setting["wlg_max_tags"] = self.ui.max_genres.value()


class Ui_WlgOptionsPage(object):
    def setupUi(self, WlgOptionsPage):
        WlgOptionsPage.setObjectName("WlgOptionsPage")
        WlgOptionsPage.resize(590, 471)
        #
        self.verticalLayout = QtWidgets.QVBoxLayout(WlgOptionsPage)
        self.verticalLayout.setObjectName("verticalLayout")
        #
        self.wlg_enable = QtWidgets.QGroupBox(WlgOptionsPage)
        self.wlg_enable.setFlat(False)
        self.wlg_enable.setCheckable(True)
        self.wlg_enable.setChecked(False)
        self.wlg_enable.setObjectName("wlg_enable")
        #
        self.hboxlayout1 = QtWidgets.QHBoxLayout()
        self.hboxlayout1.setContentsMargins(0, 0, 0, 0)
        self.hboxlayout1.setSpacing(6)
        self.hboxlayout1.setObjectName("hboxlayout1")
        self.label_max_genres = QtWidgets.QLabel(self.wlg_enable)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                           QtWidgets.QSizePolicy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(
            self.label_max_genres.sizePolicy().hasHeightForWidth())
        self.label_max_genres.setSizePolicy(sizePolicy)
        self.label_max_genres.setObjectName("label_max_genres")
        self.hboxlayout1.addWidget(self.label_max_genres)
        self.max_genres = QtWidgets.QSpinBox(self.wlg_enable)
        self.max_genres.setMaximum(100)
        self.max_genres.setObjectName("max_genres")
        self.hboxlayout1.addWidget(self.max_genres)
        self.verticalLayout.addLayout(self.hboxlayout1)

        self.retranslateUi(WlgOptionsPage)
        QtCore.QMetaObject.connectSlotsByName(WlgOptionsPage)

    def retranslateUi(self, WlgOptionsPage):
        _ = QtCore.QCoreApplication.translate
        self.wlg_enable.setTitle(_("@default", "Enable WLG plugin"))
        self.label_max_genres.setText(_("@default", "Maximum number of "
                                                    "genres."))


register_track_metadata_processor(process_track)
register_options_page(WlgOptionsPage)
