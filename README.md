# whatlastgenre

Improves genre metadata of audio files based on tags from various music sites.

* Supported audio files: flac, ogg, mp3, m4a
* Supported music sites: What.CD, Last.FM, MusicBrainz, Discogs, EchoNest
* Feature Overview
  * Gets genre tags for artists and albums from music sites and finds the most
  eligible ones.
    * Merges similar tags in different writings with aliases and regex
    replacements to ensure consistent naming,
    eg. DnB, D&B, Drum and Bass -> Drum & Bass;
    Alt., Altern, Alterneitif -> Alternative
    * Splits tags in various applicable ways, eg.
    Jazz/Funk&Rock -> Jazz, Funk, Rock;
    Alternative Rock -> Alternative, Rock
    * Uses a whitelist to avoid crappy tags (see genres.txt)
    * Scores tags while taking personal preferences into account
  * Caches all data received from music sites to make reruns super fast
  * Uses MusicBrainz IDs for searching when available
  * Optional: gets release type (Album, EP, Anthology, ...) from What.CD
  (with interactivity mode for ambiguous results)
  * Can be used as plugin in 3rd party software, currently: beets
  * Dry-mode for safe testing


## How it works
It scans through folders for albums and receives genre tags for them and their
artists from selected music sites. Equal tags in different writings will be
split and/or merged to ensure consistent names and proper scoring. Artist and
album tags get handled separately and then merged using configurable score
modifiers. The best scored tags will be saved as genre metadata in the
corresponding album tracks. All data received from music sites gets cached so
that rerunning the script will be super fast. There are several score
multipliers to adjust the scoring to your needs and take your personal
preferences into account. Please take a look at "Configuration options
explained" below for more details.

##### Tag scoring with count (What.CD, Last.FM, MusicBrainz)
If counts are supplied for the tags they will get scored by `count/topcount`,
where `topcount` is the highest count of all tags from a source. So the top
tag gets a score of `1.0`, a tag having only half of the top tag's count gets
a score of `0.5` and so on.

##### Tag scoring without count (What.CD, Discogs, EchoNest)
Tags supplied without a count will be scored `max(0.1, 0.85^(n-1))`, where `n`
is the total number of tags supplied by this source. The more tags the lower
the score for each tag will be. So if only one tag is supplied, it will get a
score of `1.0`, two tags will get a score of `0.85` each and so on. The minimum
score is `0.1`, which applies if there are more than 15 tags supplied.

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
See `vaqueries` and `various` options below.

##### Multiple value metadata and old ID3 versions
Mutagen's ID3 API is primary targeted at id3v2.4, so by default any id3 tags
will be upgraded to 2.4 and saving a file will make it 2.4 as well.
See [mutagen doc here]
(https://mutagen.readthedocs.org/en/latest/tutorial.html#id3-versions)
for details.
However, if you don't want to use v2.4 tags you can use the `id3v23sep` config
option explained below.


## Installation
You'll need Python 2.7.

Install the dependencies with your package manager, on Debian based distros run
this as root:

    apt-get install python-mutagen python-requests

* Alternatively, install the dependencies using python-pip:
`pip install mutagen requests`
* Clone the repository or download and unzip the [source package]
(http://github.com/YetAnotherNerd/whatlastgenre/archive/master.zip)
* Run it without install by using `./whatlastgenre` from the directory you
cloned/extracted to
* Install it by running `python setup.py install` as root in that directory

##### Optional Dependencies
* `rauth` is required for Discogs support. If you want to use
Discogs, install `rauth` with pip like above and activate `discogs` in the
configuration file (see below).
* `requests-cache` can additionally cache the raw queries from requests if
installed. This is mainly a developers feature.


## Configuration
A configuration file with default values will be created at
`~/.whatlastgenre/config` on first run.

### Example configuration file
```
[wlg]
sources = whatcd, lastfm, discogs, mbrainz
whatcduser = whatusername
whatcdpass = whatpassword
whitelist =
vaqueries = True
id3v23_sep =
[genres]
love = trip-rock
hate = alternative, electronic, indie, pop, rock
[scores]
artist = 1.33
various = 0.66
splitup = 0.33
src_whatcd = 1.50
src_lastfm = 0.66
src_mbrainz = 0.66
src_discogs = 1.00
src_echonest = 1.00
```

### Configuration options explained

#### whatlastgenre (wlg) section

##### sources option
The music sites where to get the genre tags from.
* `whatcd` [[URL](https://what.cd)]
well-kept tags from community
* `lastfm` [[URL](http://last.fm)]
many personal tags from users
* `mbrainz` [[URL](http://musicbrainz.org)]
home of mbids
* `discogs` [[URL](http://discogs.com)]
album only, fixed list of [genres and styles]
(http://www.discogs.com/help/doc/submission-guidelines-release-genres-styles),
requires authentication (own account needed)
* `echonest` [[URL](http://echonest.com)]
artist only, fixed list of
[genres](http://developer.echonest.com/docs/v4/genre.html#list)

##### whitelist option
Path to your custom whitelist. Defaults to shipped whitelist if empty.

Default ` `

##### vaqueries option
Search for all artists if there is no albumartist on albums with various
artists. This will make queries for va-albums without albumartist take
significantly longer, but yields more results.
See `various` score option below.

Default `True`

##### id3v23sep option
By (mutagen) default all id3 v2.3 tags will be upgraded to v2.4. Since v2.3
can't store multiple value metadata you need to set a seperator if you intend
to use old v2.3 tags (not recommended).
Setting this to a non-empty value (for example `,`) will downgrade all id3 tags
to v2.3 and store all genres in one tag seperated by `id3v23sep` instead of
using v2.4 tags that can have multiple values.
You should upgrade your other software to support id3v24 instead of using this.

Default ` ` (recommended)

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

Default `1.33`, Range `0.5 - 2.0`
* `< 1.0` prefer album tags
* `= 1.0` handle them equally
* `> 1.0` prefer artist tags

##### various option
Score multiplier similar to artist option, but this one applies to various
artists releases if there is no albumartist and all the track artists get used
for searching, which can be en/disabled with the `vaqueries` option (see above).
For example: a 5 track va-album with 3 tracks from artist A and 2 tracks from
artist B will get tags like this:

`(3 * <artist A tags> + 2 * <artist B tags>) * <various score> + <album tags>`

Default `0.66`, Range `0.1 - 1.0`
* `< 1.0` prefer album tags
* `= 1.0` handle them equally

##### splitup option
Score multiplier for modifying the score of the base tag from a tag that got
split up by space. This enables you to decide whether to keep, prefer or ban
the base tags. For example, lets say we have 'Alternative Rock' with a score
of 1: It will end up as Alternative with score 1, Rock with score 1 and
Alternative Rock with score `1 * <splitup-score>`. So if you don't want to keep
Alternative Rock, just set it to 0.

Default `0.33`, Range `0.0 - 1.0`
* `= 0.0` forget about the base tags
* `< 1.0` prefer split parts
* `= 1.0` handle them equally

##### src_* options
Every source has its own score multiplier, so music sites that generally
provide higher quality tags can be given advantage over sources that often
provide bad, inaccurate or personal tags. Increase if you trust the tags from
a source, lower if the source provides many inaccurate or personal tags. If you
don't want tags from a specific source remove it from the sources list option.

Default `1.0`, Range `0.5 - 2.0`. See `sources` option above.


## Usage
```
usage: whatlastgenre [-h] [-v] [-n] [-u] [-l N] [-r] [-i] [-d] path [path ...]

positional arguments:
  path                 folder(s) to scan for albums

optional arguments:
  -h, --help           show this help message and exit
  -v, --verbose        verbose output (-vv for debug) (default: 0)
  -n, --dry            don't save metadata (default: False)
  -u, --update-cache   force cache update (default: False)
  -l N, --tag-limit N  max. number of genre tags (default: 4)
  -r, --tag-release    tag release type (from What.CD) (default: False)
  -i, --interactive    interactive mode (default: False)
  -d, --difflib        enable difflib matching (slow) (default: False)
```

If you seriously want to tag release-types `-r` you should also enable
interactive mode `-i`. I recommend first doing a dry-run to fill the cache and
then doing a normal run with `-ir` enabled. This way you can choose the right
results without much waiting time in between.

Remove the cache file to reset the cache or use `-u` to force cache updates.

Don't waste your time running -n and -i together.

whatlastgenre doesn't correct any other tags. If your music files are badly or
not tagged it won't work well at all.

### Examples

Do a verbose dry-run on your albums in /media/music changing nothing:

	whatlastgenre -vn /media/music

Tag up to 3 genre tags for all albums in /media/music and /home/user/music:

	whatlastgenre -l 3 /media/music /home/user/music

Tag releasetypes and up to 4 genre tags for all albums in /media/music:

	whatlastgenre -ir /media/music


## Plugins

whatlastgenre can be used in other software via plugins.

At the moment there is only a plugin for beets.

See README files in plugin folder for details.


## Help / Improving tagsfile and whitelist

How to debug tag handling:

Do a debug dry run and save output to log:

    whatlastgenre -nvv /media/music > /tmp/wlg.log 2>&1

Search the log for specific lines and see if they are valid:

    grep -E "^(tag |Error)" /tmp/wlg.log | sort -u | less


Feel free to send me your log file so i can use it for debugging myself.


Please report any bugs and errors you encounter, i would like to fix them :)
