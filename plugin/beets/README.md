# whatlastgenre plugin for beets

Plug [whatlastgenre](https://github.com/YetAnotherNerd/whatlastgenre)
into [beets](https://github.com/beetbox/beets).


## Installation

Install and configure beets and whatlastgenre according to its docs.

Run whatlastgenre standalone to see if it is working.

The beets plugin is registered to the `beetsplug` namespace automatically when whatlastgenre was installed via pip.

    plugins: wlg

    wlg:
        auto: yes
        force: no

If you didn't install whatlastgenre, make sure to have it in PYTHONPATH:

    export PYTHONPATH="${PYTHONPATH}:~/git/whatlastgenre"

In that case you also must configure beets to point to the plugin's path, for example:

    pluginpath:
        ~/git/whatlastgenre/plugin/beets/beetsplug


See also: [beets doc about plugins](https://beets.readthedocs.io/en/latest/plugins/index.html)


## Configuration

The wlg plugin uses the same config file as the standalone version.
Additionally, there are some config options in the beets config:

    wlg:
        auto: no
        force: no
        count: 4
        separator: ', '
        whitelist: wlg

* auto: Automatically fetch genres during import. Default: `no`
* force: Force overwrite existing genres. Default: `no`
* count: Number of genres to store. Default: `4`
* separator: Separator for multiple genres. Default: `', '`
* whitelist:
    * `wlg` use whitelist from whatlastgenre (default)
    * `beets` use whitelist from lastgenre beets plugin
    * or use custom path to whitelist


## Usage

### Automatically use during import
Make sure `auto: yes` is set in the `wlg` part of the beets configuration.

### Run manually

    Usage: beet wlg [options]

    Options:
      -h, --help          show this help message and exit
      -v, --verbose       verbose output (-vv for debug)
      -f, --force         force overwrite existing genres
      -u, --update-cache  force update cache


## Known issues / Differences to standalone

* genres always one tag with separation / only when using id3v23
(See [beets#505](https://github.com/beetbox/beets/issues/505))
