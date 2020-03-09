[![Build Status](https://travis-ci.org/YetAnotherNerd/whatlastgenre.svg?branch=master)](https://travis-ci.org/YetAnotherNerd/whatlastgenre)
[![codecov](https://codecov.io/gh/YetAnotherNerd/whatlastgenre/branch/master/graph/badge.svg)](https://codecov.io/gh/YetAnotherNerd/whatlastgenre)

# whatlastgenre
Improve genre metadata of audio files based on tags from various music sites.

* Supported audio files: flac, ogg, mp3, m4a
* Supported music sites: Discogs, Last.FM, MusicBrainz, Redacted.ch
* Feature Overview
  * Gets genre tags for artists and albums from music sites and finds the most
  eligible ones.
    * Merges similar tags in different writings to ensure consistent naming
    via [aliases and regex](wlg/data/tags.txt)
    * Splits tags in various applicable ways
    * Uses a [whitelist](wlg/data/genres.txt) to avoid bad tags
    * Scores tags while taking personal preferences into account
  * Caches all data received from music sites to speedup reruns
  * Uses MusicBrainz IDs for searching when available
  * Optional: gets release info from redacted (with interactivity if ambiguous)
  * Can be used as plugin in other software, currently:
  [beets](https://github.com/beetbox/beets)
  * Dry-mode for safe testing
* [Example output](https://gist.github.com/YetAnotherNerd/3e59c4d02f1930d9e52e)


## How it works
It scans through directories for albums and receives genre tags for them and
their artists from selected music sites. Equal tags in different writings will
be split and/or merged to ensure consistent names and proper scoring. Artist
and album tags get handled separately and then merged using configurable score
modifiers. The best scored tags will be saved as genre metadata in the
corresponding album tracks. All data received from music sites gets cached so
that rerunning the script will be super fast. There are several score
multipliers to adjust the scoring to your needs and take your personal
preferences into account. Please take a look at "Configuration options
explained" below for more details.

##### Tag scoring with counts/weights
    lastfm, mbrainz, redacted artist
If counts are supplied for the tags they will get scored by `count/topcount`,
where `topcount` is the highest count of all tags from a source. So the top
tag gets a score of `1.0`, a tag having only half of the top tag's count gets
a score of `0.5` and so on.

##### Tag scoring without counts/weights
     discogs, redacted album
Tags supplied without a count will be scored `max(1/3, 0.85^(n-1))`, where `n`
is the total number of tags supplied by this source. The more tags the lower
the score for each tag will be. So if only one tag is supplied, it will get a
score of `1.0`, two tags will get a score of `0.85` each and so on. The minimum
score is `1/3`, which applies if there are more than 7 tags supplied.

##### Tag merging of artist and album tags
After all tags have been gathered the scores of album and artist tags will be
scaled and then merged together taking the artist score modifier option into
account. This enables that multiple albums from one artist get more equal tags
by giving tags found by artist searches advantage over tags found by album
searches.
See `artist` score option below.

##### Tag scoring for various artist albums without any specific album artist
If there is no specific album artist for a various artist album every track
artist can get used for searching. Tags for artists that appear multiple times
in that album get counted multiple times.
See `various` score option below.

##### Multiple value metadata and old ID3 versions
Mutagen's ID3 API is primary targeted at id3v2.4, so by default any id3 tags
will be upgraded to 2.4 and saving a file will make it 2.4 as well.
See [mutagen doc](https://mutagen.readthedocs.io/en/latest/user/id3.html#id3-versions)
for details.
However, if you don't want to use v2.4 tags you can use the `id3v23sep` config
option explained below.

##### Release info tagging and interactivity
    releasetype, year, label, catalog, edition, media
Releaseinfo can be tagged for snatched torrents (make sure to enable 'Snatched
torrents indicator' in your redacted profile settings). Although prefiltering
tries to minimize needed user input, some might be required in ambiguous cases
while tagging releaseinfo. `-r` implies interactivity, `-n` disables
interactivity. Be aware that `-r` saves the original release year into the
`date` metadata field.


## Installation
You'll need Python. It should work with 2.7 and 3.x.

Install the dependencies with your package manager, on Debian based distros run
this as root:

    apt-get install python-mutagen python-requests

* Alternatively, install the dependencies using python-pip:
`pip install mutagen requests`
* Clone the repository or download and unzip the
[source package](http://github.com/YetAnotherNerd/whatlastgenre/archive/master.zip)
* Run it without install by using `./whatlastgenre` from the directory you
cloned/extracted to
* Install it by running `python setup.py install` as root in that directory

##### Optional dependencies
* `configparser` is required for py27.
* `rauth` is required for Discogs. If you want to use Discogs, install `rauth`
with pip like above and activate `discogs` in the config file (see below).
* `requests-cache` can additionally cache the raw queries from requests if
installed. This is mainly a developers feature.


## Configuration
A configuration file with default values will be created at
`~/.whatlastgenre/config` on first run.

### Example configuration file
```
[wlg]
sources = discogs, lastfm, redacted
whitelist =
tagsfile =
id3v23_sep =
[genres]
love = trip-rock
hate = alternative, electronic, indie, pop, rock
[scores]
artist = 1.33
various = 0.66
splitup = 0.33
minimum = 0.10
src_discogs = 1.00
src_lastfm = 0.66
src_mbrainz = 0.66
src_redacted = 1.50
[discogs]
token =
secret =
[redacted]
username =
password =
session =
```

### Configuration options explained

#### whatlastgenre (wlg) section

##### sources option
Source | Artist | Album | Auth | ...
-------|:------:|:-----:|:----:|-----
[discogs](http://discogs.com) | 0 | 1 | 1 | fixed list of [genres and styles](http://www.discogs.com/help/doc/submission-guidelines-release-genres-styles)
[lastfm](http://last.fm)| 1 | 1 | 0 |  many personal tags from users
[mbrainz](http://musicbrainz.org) | 1 | 1 | 0 | home of mbids (overstrained)
[redacted](https://redacted.ch) | 1 | 1 | 1 | well-kept tags from community

##### whitelist/tagsfile option
Path to your custom whitelist and tagsfile. Use shipped
[whitelist](wlg/data/genres.txt)/[tagsfile](wlg/data/tags.txt)
if empty (default). Instead of setting the path here, you can also put the
files in your config directory and they should get recognized (see debug log).

##### id3v23sep option
By (mutagen) default all id3 v2.3 tags will be upgraded to v2.4. Since v2.3
can't store multiple value metadata you need to set a separator if you intend
to use old v2.3 tags (not recommended).
Setting this to a non-empty value (for example `,`) will downgrade all id3 tags
to v2.3 and store all genres in one tag separated by `id3v23sep` instead of
using v2.4 tags that can have multiple values. Empty by default.
You should upgrade your other software to support id3v24 instead of using this.

#### genres section

##### love and hate options
List of tags that get a multiplier bonus of `2.0` and `0.5` respectively.
Should be considered as "soft white-/blacklist" where you can in-/decrease the
occurrence of specific tags that you don't like or that are too inaccurate for
you without fully banning them.

#### scores section

##### artist option
Score multiplier to give tags found by albumartist searches advantage over tags
from album searches. The tags get stored separately at first but then put
together while taking this multiplier into account. This enables that multiple
albums from one artist get more equal tags.

`<artist tags> * <artist score> + <album tags>`

Default `1.33`
* `= 0.0` disable artist queries
* `< 1.0` prefer album tags
* `= 1.0` handle them equally
* `> 1.0` prefer artist tags

##### various option
Score multiplier similar to the artist option, but this one applies to various
artists releases if there is no albumartist and all the track artists get used
for searching. This will make queries for va-albums without an albumartist take
significantly longer, but yields more results.
For example: a 5 track va-album with 3 tracks from artist A and 2 tracks from
artist B will get tags like this:

`(3 * <artist A tags> + 2 * <artist B tags>) * <various score> + <album tags>`

Default `0.66`
* `= 0.0` disable various artist queries
* `< 1.0` prefer album tags
* `= 1.0` handle them equally
* `> 1.0` prefer various artist tags

##### splitup option
Score multiplier for modifying the score of a tag that got split up by space.
This enables you to decide whether to keep, lessen or ignore the 'base' tags.

Default `0.33`
* `= 0.0` forget about the base tags
* `< 1.0` reduce score of base tags
* `= 1.0` leave score unmodified

##### minimum option
Minimum score for final filtering.

Default `0.10`
* `= 0.0` no minimum score
* `= 1.0` same effect as `--tag-limit 1`

##### src_* options
Every source has its own score multiplier, so music sites that generally
provide higher quality tags can be given advantage over sources that often
provide bad, inaccurate or personal tags. Increase if you trust the tags from
a source, lower if the source provides many inaccurate or personal tags. If you
don't want tags from a specific source remove it from the sources list option.

Default `1.0`, see `sources` option above.

#### dataprovider related sections

##### discogs token and secret options
Authentication information for discogs, will be set interactively.

##### redacted username, password and session options
You can optionally store your username and/or password here. If you don't store
them, you will be asked interactively every time the automatically stored
session cookie expired (or does not exist yet).


## Usage
```
usage: whatlastgenre [-h] [-v] [-n] [-u] [-l N] [-r] [-d] path [path ...]

positional arguments:
  path                 path(s) to scan for albums

optional arguments:
  -h, --help           show this help message and exit
  -v, --verbose        verbose output (-vv for debug) (default: 0)
  -n, --dry            don't save metadata (default: False)
  -u, --update-cache   force cache update (default: False)
  -l N, --tag-limit N  max. number of genre tags (default: 4)
  -r, --release        get release info from redacted (default: False)
  -d, --difflib        enable difflib matching (slow) (default: False)
```

If you want to tag releasetypes `-r`, you should do a dry-run beforehand to
fill the cache and then be able to choose the right results without much
waiting time in between.

Remove the cache file to reset the cache or use `-u` to force cache updates.

whatlastgenre doesn't correct any other tags. If your music files are badly or
not tagged it won't work well at all.

### Examples
Do a verbose dry-run on your albums in /media/music changing nothing:

	whatlastgenre -vn /media/music

Tag up to 3 genre tags for all albums in /media/music and /home/user/music:

	whatlastgenre -l 3 /media/music /home/user/music

Tag releaseinfo and up to 4 genre tags for all albums in /media/music:

	whatlastgenre -r /media/music


## Plugins
whatlastgenre can be used in other software via plugins.

At the moment there is a [plugin for beets](plugin/beets).

See README files in the [plugin](plugin) directory for details.


## Help / Debug

#### Errors / Bugs / Crashes
If you encounter any strange errors (especially after updating to a later
version), please delete the cache file and try again with an empty cache
before reporting it (do a backup first in case it doesn't solve the issue).

#### Mediaplayers and file modification times
Since file modification times are preserved, some players don't realize the
changed genre metadata automatically and might require some manual steps, e.g.:

* mpd: needs rescan instead of normal update to get the mpd database updated.

#### Debug tag handling / Improve tag results
In order to debug tag handling to improve the whitelist and tagsfile, you can
do a debug dry run and save the output to a logfile (adjust path):

    whatlastgenre -nvv /media/music > /tmp/wlg.log

Then search the logfile for specific lines:

    grep ^tag /tmp/wlg.log | sort | uniq -c | sort -r | less

Use the results to add missing whitelist entries, aliases or replaces.

Feel free to share your improvements to the [data/*.txt](wlg/data) files or
send me your logfile so i can use it for debugging myself.

Another way to find possible aliases is using the difflib `-d` argument.


Please report any bugs and errors, i would like to fix them :)

Thanks to everyone who made suggestions and reported problems <3
