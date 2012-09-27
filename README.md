# whatlastgenre

Improves genre metadata of audio files based on tags from various music-sites.

* Supported audio files: flac, ogg, mp3
* Supported music-sites: What.CD, Last.FM, MusicBrainz, Discogs
* Feature Overview
	* Gets genretags from music sites and splits, merges, scores and filters them.
		* Splitting, eg. Jazz+Funk -> Jazz and Funk, Rock/Pop -> Rock and Pop
		* Merges similar tags in different writings, eg. DnB, D&B, Drum and Bass -> Drum & Bass
		* Scores them with different methods, can take personal preferences into account
		* Filters them by personal preferences and preset filters
	* Optional: writes release type (Album, EP, Anthology, ...) (from What.CD)
	* Optional: writes MusicBrainz IDs
	* Optional: caches already proceeded albums and mbarids too reduce needed interactivity
	* Makes use of MusicBrainz IDs when possible
	* Interactive mode, especially for releasetypes and mbids (it's not guessing wrong data)
	* Dry-mode for safe testing

## How it works
It scans through folders for albums and receives genre tags for this releases
and their artists from different music-sites. The tags get scored and put
together, then the best scored tags will be saved as genre metadata in the
corresponding album tracks. If a tag is supplied from more then one source,
their individual scores will be summed up. Equal tags in different writings
will be merged together.

### Tags scoring with count (Last.FM, MusicBrainz, What.CD partially)
If counts are supplied for the tags, they will get scored by `count/topcount`,
where `topcount` is the highest count of all tags from a source. So the top
tag gets a score of 1.0, a tag having only half of the top tag's count gets a
score of 0.5 and so on. 

### Tags scoring without count (Discogs, What.CD partially)
Tags supplied without a count will be scored `0.85^(n-1)`, where `n` is the
total number of tags supplied by this source. The more tags the lower the
score for each tag will be. So if only one tag is supplied, it will get a
score of 1.0, two tags will get a score of 0.85 each, three get 0.72 each
and so on...

### Score multiplier for different sources
Every source (What.CD, Last.FM, MusicBrainz, Discogs) has its own score
multiplier, so sources that generally provide higher quality tags can be given
advantage over sources that often provide bad, inaccurate or personal tags.

### Personal score modifiers
One can set a list of tags that will get an initial score offset and
multiplier bonus. Consider this as some kind of "soft" white-/blacklist, where
you can reduce the occurrence of hated or inaccurate tags without fully
banning them. See Configuration for more details.


If you have any ideas on improving this scoring, please let me know :)


## Installation

	$ python setup.py install

### Dependencies
* musicbrainzngs
* mutagen
* requests


## Configuration

An empty configuration file will be created on the first run. The score
modifiers/multipliers for sources and score_{up,down} can be tuned in the
source, but act with caution.

### Example configuration file
	[whatcd]
	username = whatuser
	password = myscretwhatcdpassword
	[genres]
	blacklist = Charts, Composer, Live, Unknown
	score_up = Soundtrack
	score_down = Electronic, Other, Other
	filters = country, label, year


### Configuration options explained

#### genres section

##### score_up, score_down option
This should be considered as "soft" white-/blacklist where you can in-/decrease
the occurrence of specific tags that you don't like or that are too inaccurate
for you without fully banning them like with the blacklist option. Tags listed
here will get an initial score offset and a score multiplier bonus of `+/-0.25`
per default, to boost this even more, just mention them more then once.

##### filters
Use this to filter specific groups from genres.
* country: filters countries, cities and nationalities
* label: filters label names
* year: filters year tags, like 1980s


## Usage

	usage: whatlastgenre.py [-h] [-v] [-n] [-i] [-r] [-m] [-b] [-c] [-l N]
	                        [--no-whatcd] [--no-lastfm] [--no-mbrainz]
	                        [--no-discogs] [--config CONFIG] [--cache CACHE]
	                        path [path ...]

	positional arguments:
	  path                 folder(s) to scan for albums
	
	optional arguments:
	  -h, --help           show this help message and exit
	  -v, --verbose        more detailed output (default: False)
	  -n, --dry-run        don't save metadata (default: False)
	  -i, --interactive    interactive mode (default: False)
	  -r, --tag-release    tag release type (from what.cd) (default: False)
	  -m, --tag-mbids      tag musicbrainz ids (default: False)
	  -b, --use-colors     colorful output (default: False)
	  -c, --use-cache      cache processed albums (default: False)
	  -l N, --tag-limit N  max. number of genre tags (default: 4)
	  --no-whatcd          disable lookup on What.CD (default: False)
	  --no-lastfm          disable lookup on Last.FM (default: False)
	  --no-mbrainz         disable lookup on MusicBrainz (default: False)
	  --no-discogs         disable lookup on Discogs (default: False)
	  --config CONFIG      location of the configuration file (default: ~/.whatlastgenre/config)
	  --cache CACHE        location of the cache (default: ~/.whatlastgenre/cache)


If you seriously want to tag release-types (-r) or mbrainz-ids (-m) you should
also enable interactive-mode (-i). Consider to save the mbrainz-ids (-m) when
not using --no-mbrainz, you searched for them, why not save them? ;)
Think about using the cache feature (-c, --cache) if you have a large set of
albums, to speed up things next time. Disabling music-sites is not recommended,
the more sources, the better tags.


## Examples

Do a dry-run on your albums in /home/user/music changing nothing:

	$ whatlastgenre.py -n /home/user/music

Tag max. 3 genre tags for all albums in /home/user/music:

	$ whatlastgenre.py -cl 3 /home/user/music

To get the most of it for all albums in /media/music:

	$ whatlastgenre.py -irmcl 5 /media/music
	
Just tag release-types and mbids (this is a hack ;)) on /media/music:

	$ whatlastgenre.py -irml 0 --no-lastfm --no-discogs /media/music


## How to help

Thanks for being interested in helping to improve it :)
Things you can tell me about:
* Tag that are similar but haven't been merged or naming inconsistencies, e.g. 'Trip Hop' <-> 'Trip-Hop'
* If your unhappy with the tag result for some albums
* Any errors of course ;)

Or just paste me the output so i can take a look on it for scoring improvements.
I'm also happy for any other suggestions :)

### Ended up with a tag that shouldn't be there?
* If it's an impartial correct tag, just use score_down or blacklist to get
rid of it.
* If it's a label or county tag that should have been filtered, please name
it to me so i can add it to the hardcoded filter list.
* If the tag is personal, crappy or somehow else bad, just rerun the script
with `whatlastgenre.py -vn /path/to/album/with/bad/genre > wlg.log`
and send me the `wlg.log`, i'll try to improve the scoring.

