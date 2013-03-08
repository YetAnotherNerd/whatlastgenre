# whatlastgenre

Improves genre metadata of audio files based on tags from various music sites.

* Supported audio files: flac, ogg, mp3, m4a
* Supported music sites: What, Last.FM, MusicBrainz, Discogs, Idiomag, EchoNest
* Feature Overview
	* Gets genre tags from various music sites and merges, splits, filters and
	scores them.
		* Merges similar tags in different writings, eg.
		DnB, D&B, Drum and Bass -> Drum & Bass
		* Splitting by common separators, eg. Jazz+Funk -> Jazz, Funk;
		Rock/Pop -> Rock, Pop; Jazz&Funk+Rock -> Jazz, Funk, Rock
		* Splitting by space if tag contains specific parts,
		like Alternative, Progressive, etc. (see tagsfile)
		* Filters them by personal preferences and preset or custom filters
		* Scores them with different methods while taking personal
		preferences into account (see below)
	* Caches all data received from music sites to make reruns super fast
	* Makes use of MusicBrainz IDs when possible and recognizes invalid ones
	* Optional: gets release type (Album, EP, Anthology, ...) (from What)
	* Optional: gets MusicBrainz IDs
	* Interactive mode, especially for release types and MBIDs
	(it's not guessing wrong data)
		* Progressive fuzzy matching to reduce needed user input
		* eg. MusicBrainz: Tries to identify artists by looking at its albums 
	* Dry-mode for safe testing


## How it works
It scans through folders for albums and receives genre tags for them and their
artists from different music sites. Equal tags in different writings will be
merged together. Tags containing separators or specific parts will get split
up. They get filtered and scored, then the best scored tags will be saved as
genre metadata in the corresponding album tracks.

### Tags scoring with count (Last.FM, MusicBrainz, Idiomag, What partially)
If counts are supplied for the tags they will get scored by `count/topcount`,
where `topcount` is the highest count of all tags from a source. So the top
tag gets a score of `1.0`, a tag having only half of the top tag's count gets
a score of `0.5` and so on. 

### Tags scoring without count (Discogs, EchoNest, What partially)
Tags supplied without a count will be scored `0.85^(n-1)`, where `n` is the
total number of tags supplied by this source. The more tags the lower the
score for each tag will be. So if only one tag is supplied, it will get a
score of `1.0`, two tags will get a score of `0.85` each, three get `0.72`
each and so on...

### Score multiplier/modifier
There are several score multipliers to adjust the scoring to your needs and
take your personal preferences into account. Please take a look at
`Configuration options explained` below for more details.

### Caching
All data received from music sites will get cached after pre-filtering so that
rerunning the script will be super fast. The cache timeout can be set in the
configuration file. The cache gets saved to disk in an interval that can be set
in the config file and will get cleaned up at the end of the script. Use `-c`
to ignore cache hits. Remove the cache file to manually reset the cache.


## Installation
You'll need Python 2.7. Running the following should automatically install all
needed dependencies (musicbrainzngs, mutagen, requests):

	$ python setup.py install


## Configuration
A configuration file with default values will be created on first run.

### Example configuration file
	[wlg]
	sources = whatcd, mbrainz, lastfm, discogs, idiomag, echonest
	tagsfile = tags.txt
	cache_timeout = 7
	cache_saveint = 10
	whatcduser = whatusername
	whatcdpass = whatpassword
	[genres]
	love = soundtrack
	hate = alternative, electronic, indie
	blacklist = charts, other
	filters = instrument, label, location, year
	[scores]
	src_whatcd = 1.66
	src_lastfm = 0.66
	src_mbrainz = 1.00
	src_discogs = 1.00
	src_idiomag = 1.00
	src_echonest = 1.00
	artist = 1.33
	splitup = 0.33
	personal = 0.66


### Configuration options explained

#### whatlastgenre (wlg) section

##### sources option
Possible values: whatcd, mbrainz, lastfm, discogs, idiomag, echonest
The sources/music sites/data providers where to get the tags from. Will be
called in the order you named them, since lastfm supports search by MBIDs make
sure to mention mbrainz before lastfm. Disabling music sites is not
recommended, the more sources the better tags.

##### tagsfile option
Path to the tags.txt file. Use an absolute path here if you have problems
accessing the tagsfile by default.

##### cache_timout option
Default `7`, Range `1 - 30`
Time in days after which cache hits get invalid.

##### cache_saveint option
Default `10`, Range `1 - 30`
Interval in minutes to save the cache during runtime.

#### genres section

##### love and hate options
List of tags that get a multiplier bonus. Should be considered as "soft"
white-/blacklist where you can in-/decrease the occurrence of specific tags
that you don't like or that are too inaccurate for you without fully banning
them like with the blacklist option. Tags listed here will get a score bonus
based on the configured personal score multiplier (see below).

##### filters option
Use this to activate filtering of specific tag groups from genres:
* instrument: filters instrument related names, like piano or guitarist
* label: filters label names
* location: filters country, city and nationality names
* year: filters year tags, like 1980s
* create your own filter lists by adding filter sections to the tags.txt file,
consider them as large blacklists.

#### scores section

##### src_* options
Default `1.0`, Range `0.5 - 2.0`
Every source has its own score multiplier, so sources that generally provide
higher quality tags can be given advantage over sources that often provide
bad, inaccurate or personal tags. Increase if you trust the tags from a source,
lower if the source provides many inaccurate or personal tags. If you don't
want tags from a specific source you should remove it from the sources list
option instead of lowering this option to the minimum.

##### artist option
Default `1.33`, Range `0.5 - 2.0`
Score multiplier to give tags found by artist/albumartist searches advantage
over tags from album searches. Those tags get stored separately at first but
then put together while taking this multiplier into account. This enables
that multiple albums from one artist get more equal tags.
* `<1.0` prefer album tags
* `=1.0` no difference between album and artist tags
* `>1.0` prefer artist tags

##### splitup option
Default `0.33`, Range `0.0 - 1.0`
Score multiplier for modifying the score of the base tag from a tag that got
split up by space, this enables you to decide whether to keep, prefer or ban
the base tags. For example, lets say we have 'Alternative Rock' with a score
of `1`: It will end up as Alternative with score `1`, Rock with score `1` and
Alternative Rock with score `1 * <splitup-score>`. So if you don't want to keep
Alternative Rock, just set it to 0, but consider using a very small number
instead to avoid banning them totally.
* `=0.0` forget about the "base" tags
* `<1.0` prefer split parts
* `=1.0` handle them equally

##### personal option
Default `0.66`, Range `0.0 - 1.0` (see love and hate options above)
* `1+x` score multiplier for tags set in love
* `1-x` score multiplier for tags set in hate


## Usage
	  
	usage: whatlastgenre.py [-h] [-v] [-n] [-c] [-i] [-r] [-m] [-l N]
	                        [--config CONFIG] [--cache CACHE]
	                        path [path ...]
	
	positional arguments:
	  path                 folder(s) to scan for albums
	
	optional arguments:
	  -h, --help           show this help message and exit
	  -v, --verbose        more detailed output (default: False)
	  -n, --dry            don't save metadata (default: False)
	  -c, --cacheignore    ignore cache hits (default: False)
	  -i, --interactive    interactive mode (default: False)
	  -r, --tag-release    tag release type (from What) (default: False)
	  -m, --tag-mbids      tag musicbrainz ids (default: False)
	  -l N, --tag-limit N  max. number of genre tags (default: 4)
	  --config CONFIG      location of the configuration file (default: ~/.whatlastgenre/config)
	  --cache CACHE        location of the cache file (default: ~/.whatlastgenre/cache)


If you seriously want to tag release-types (-r) or musicbrainz-ids (-m) you
should also enable interactive mode (-i). Consider to save the mbids (-m) when
using mbrainz, you searched for them, why not save them? ;)

I recommend first doing a dry-run to fill the cache and then doing a normal
run with interactivity enabled. This way you can answer all interactivity
questions without much waiting time in between.

### Examples

Do a verbose dry-run on your albums in /home/user/music changing nothing:

	$ whatlastgenre.py -vn /home/user/music

Tag max. 3 genre tags for all albums in /home/user/music:

	$ whatlastgenre.py -l 3 /home/user/music

To get the most of it for all albums in /home/user/music and /media/music:

	$ whatlastgenre.py -irml 5 /home/user/music /media/music


### Ended up with a tag that shouldn't be there?

* If it's an impartial correct tag, just use hate or blacklist option to get
rid of it.
* If it's an instrument, label or location tag that should have been filtered,
please name it to me so i can add it to the tags file.
* If the tag is personal, crappy or somehow else bad, just talk to me and i'll
try to improve the filtering by adding it to the generic filters.


## How to help

Thanks for being interested in helping to improve it :)
Things you can tell me about:
* Tag that are similar but haven't been merged or naming inconsistencies,
e.g. 'Trip Hop' <-> 'Trip-Hop'
* Tags that doesn't get split but should, or do get split but shouldn't
* Tags that get filtered but shouldn't
* If your unhappy with the tag results
* Did i miss something in tags.txt?
* Any errors of course ;)

I'm also happy for any other kind of suggestions or just send me your tags
statistics output for a `-nl 10`-run, i'll try to improve tags.txt with it. :)

