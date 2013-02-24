# whatlastgenre

Improves genre metadata of audio files based on tags from various music-sites.

* Supported audio files: flac, ogg, mp3
* Supported music-sites: What, Last.FM, MusicBrainz, Discogs
* Feature Overview
	* Gets genre tags from various music sites and splits, merges, scores and
	filters them.
		* Splitting by common separators, eg. Jazz+Funk -> Jazz, Funk;
		Rock/Pop -> Rock, Pop; Jazz&Funk+Rock -> Jazz, Funk, Rock
		* Splitting by space for common prefixes,
		like Alternative, Progressive, etc. (see tags.txt)
		* Merges similar tags in different writings,
		eg. DnB, D&B, Drum and Bass -> Drum & Bass
		* Scores them with different methods while taking personal
		preferences into account
		* Filters them by personal preferences and preset or custom filters
	* Optional: writes release type (Album, EP, Anthology, ...) (from What)
	* Optional: writes MusicBrainz IDs
	* Optional: caches already proceeded albums to speed things up next time
	* Makes use of MusicBrainz IDs when possible
	* Interactive mode, especially for release types and mbids
	(it's not guessing wrong data)
		* Progressive fuzzy matching to reduce needed user input
		* eg. MusicBrainz: Tries to identify artists by looking at its albums 
	* Dry-mode for safe testing

## How it works
It scans through folders for albums and receives genre tags for this releases
and their artists from different music-sites. The tags get scored and put
together, then the best scored tags will be saved as genre metadata in the
corresponding album tracks. If a tag is supplied from more then one source,
their individual scores will be summed up. Equal tags in different writings
will be merged together.

### Tags scoring with count (Last.FM, MusicBrainz, What partially)
If counts are supplied for the tags, they will get scored by `count/topcount`,
where `topcount` is the highest count of all tags from a source. So the top
tag gets a score of 1.0, a tag having only half of the top tag's count gets a
score of 0.5 and so on. 

### Tags scoring without count (Discogs, What partially)
Tags supplied without a count will be scored `0.85^(n-1)`, where `n` is the
total number of tags supplied by this source. The more tags the lower the
score for each tag will be. So if only one tag is supplied, it will get a
score of 1.0, two tags will get a score of 0.85 each, three get 0.72 each
and so on...

### Score multiplier/modifier

#### Score multiplier for different sources
Every source (What, Last.FM, MusicBrainz, Discogs) has its own score
multiplier, so sources that generally provide higher quality tags can be given
advantage over sources that often provide bad, inaccurate or personal tags.

#### Split score multiplier
There is a score multiplier for modifying the score of the base tag from a tag
that got split up, this enables you to decide whether to keep, prefer or ban
the base tag. For example, lets say we have 'Alternative Rock' with a score of
10. It will end up as Alternative with score 10, Rock with score 10, and
Alternative Rock with score 10 * SCORE_SPLIT.
So if you dont want to keep Alternative Rock, just set SCORE_SPLIT = 0. 

#### Artist score multiplier
There is an extra multiplier for tags gathered by searching for artists to
enable multiple albums from one artist getting more equal tags.

#### Personal score modifiers
One can set a list of tags that will get a multiplier bonus. Consider this as
some kind of "soft" white-/blacklist, where you can reduce the occurrence of
hated or inaccurate tags without fully banning them.
See Configuration for more details.

You can adjust this multipliers at the beginning of the source code.

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
	username = whatusername
	password = whatpassword
	[genres]
	blacklist = charts, composer, live, unknown
	score_up = soundtrack
	score_down = electronic, alternative, indie, other, other
	filters = label, location, year

### Configuration options explained

#### genres section

##### score_up, score_down option
This should be considered as "soft" white-/blacklist where you can in-/decrease
the occurrence of specific tags that you don't like or that are too inaccurate
for you without fully banning them like with the blacklist option. Tags listed
here will get an initial score offset and a score multiplier bonus of `+/-0.25`
per default, to boost this even more, just mention them more then once.

##### filters
Use this to activate filtering of specific tag groups from genres:
* label: filters label names
* location: filters country, city and nationality names
* year: filters year tags, like 1980s
* create your own filter lists by adding filter sections to the tags.txt file

Consider custom filter lists as large blacklists.

## Usage
	  
	usage: whatlastgenre.py [-h] [-v] [-n] [-i] [-r] [-m] [-c] [-l N]
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
	  -r, --tag-release    tag release type (from What) (default: False)
	  -m, --tag-mbids      tag musicbrainz ids (default: False)
	  -c, --use-cache      cache processed albums (default: False)
	  -l N, --tag-limit N  max. number of genre tags (default: 4)
	  --no-whatcd          disable lookup on What (default: False)
	  --no-lastfm          disable lookup on Last.FM (default: False)
	  --no-mbrainz         disable lookup on MusicBrainz (default: False)
	  --no-discogs         disable lookup on Discogs (default: False)
	  --config CONFIG      location of the configuration file (default: ~/.whatlastgenre/config)
	  --cache CACHE        location of the cache (default: ~/.whatlastgenre/cache)


If you seriously want to tag release-types (-r) or musicbrainz-ids (-m) you
should also enable interactive-mode (-i). Consider to save the mbids (-m) when
not using --no-mbrainz, you searched for them, why not save them? ;)
Think about using the cache feature (-c) if you have a large set of albums to
speed up things next time. Disabling music-sites is not recommended, the more
sources, the better tags.

### Examples

Do a verbose dry-run on your albums in /home/user/music changing nothing:

	$ whatlastgenre.py -vn /home/user/music

Tag max. 3 genre tags for all albums in /home/user/music:

	$ whatlastgenre.py -cl 3 /home/user/music

To get the most of it for all albums in /media/music:

	$ whatlastgenre.py -cirml 5 /media/music
	
Just tag release-types and mbids (this is not intended) on /media/music:

	$ whatlastgenre.py -cirml 0 --no-lastfm --no-discogs /media/music


### Ended up with a tag that shouldn't be there?

* If it's an impartial correct tag, just use score_down or blacklist to get
rid of it.
* If it's a label or location tag that should have been filtered, please name
it to me so i can add it to the tags.txt file (do it yourself until i did it).
* If the tag is personal, crappy or somehow else bad, just talk to me and i'll
try to find out what's happening and improve the scoring.

## How to help

Thanks for being interested in helping to improve it :)
Things you can tell me about:
* Tag that are similar but haven't been merged or naming inconsistencies,
e.g. 'Trip Hop' <-> 'Trip-Hop'
* Tags that doesn't get split but should, or do get split but shouldn't.
* Did i miss something in tags.txt?
* If your unhappy with the tag results.
* Any errors of course ;)

I'm also happy for any other suggestions :)
