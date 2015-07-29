# whatlastgenre plugin for beets

Plug whatlastgenre into [beets](http://github.com/sampsyo/beets).


## Installation

Install and configure beets and whatlastgenre according to its docs.

Run wlg standalone to see if its working (see [wlg doc]
(http://github.com/YetAnotherNerd/whatlastgenre/blob/master/README.md))

Configure beets to use wlg plugin, for example (adjust path):

    pluginpath:
        ~/git/whatlastgenre/plugin/beets/beetsplug

    wlg:
        auto: yes

If you didn't install wlg, make sure to have it in PYTHONPATH, for example

    export PYTHONPATH="~/git/whatlastgenre/:${PYTHONPATH}"

See also: [beets doc about plugins]
(http://beets.readthedocs.org/en/latest/plugins/index.html)


## Configuration

The wlg plugin uses the same configuration file as the standalone version.
See whatlastgenre documentation for how to configure it.

Additionally, there are some configuration options in the beets configuration:

    wlg:
        auto: yes
        force: no
        count: 4
        separator: ', '
        whitelist: wlg

* auto: Fetch genres automatically during import. Default: `yes`
* force: Force cache updates. Default: `no`
* count: Number of genres to fetch. Default: `4`
* separator: A separator for multiple genres. Default: `', '`
* whitelist: Default: `wlg`
    * `wlg` use whitelist from whatlastgenre (default)
    * `beets` use whitelist from lastgenre beets plugin
    * or use custom path to whitelist


## Usage

### Automatically use during import
Make sure `auto: yes` is set in the `wlg` part of the beets configuration.

### Run manually
    beet wlg [QUERY]


## Known issues / Differences to stand-alone

* plug: always `separator` separated tag; solo: only when using id3v23
(See [[beets#505](http://github.com/sampsyo/beets/issues/505)])
* needs testing


## Help

I did some basic testing, but since i don't use beets on a regular basis myself,
i can't be sure that everything works flawlessly. Feedback welcome.
