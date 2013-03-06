# whatlastgenre

Improves genre metadata of audio files based on tags from various music sites.

* Supported audio files: flac, ogg, mp3, m4a
* Supported music sites: What, Last.FM, MusicBrainz, Discogs
* Feature Overview
	* Gets genre tags from various music sites and merges, splits, filters and
	scores them.
		* Merges similar tags in different writings, eg.
		DnB, D&B, Drum and Bass -> Drum & Bass
		* Splitting by common separators, eg. Jazz+Funk -> Jazz, Funk;
		Rock/Pop -> Rock, Pop; Jazz&Funk+Rock -> Jazz, Funk, Rock
		* Splitting by space if tag contains specific parts,
		like Alternative, Progressive, etc. (see tags.txt)
		* Filters them by personal preferences and preset or custom filters
		* Scores them with different methods while taking personal
		preferences into account
	* Caches all data received from music sites to make reruns super fast
	* Makes use of MusicBrainz IDs when possible and recognizes invalid ones
	* Optional: saves release type (Album, EP, Anthology, ...) (from What)
	* Optional: saves MusicBrainz IDs
	* Interactive mode, especially for release types and MBIDs
	(it's not guessing wrong data)
		* Progressive fuzzy matching to reduce needed user input
		* eg. MusicBrainz: Tries to identify artists by looking at its albums 
	* Dry-mode for safe testing

## How it works
It scans through folders for albums and receives genre tags for them and their
artists from different music sites. Equal tags in different writings will be
merged together. Tags containing seperators or specific parts will get split
up. The tags get filtered, scored and put together, then the best scored tags
will be saved as genre metadata in the corresponding album tracks.

### Tags scoring with count (Last.FM, MusicBrainz, What partially)
If counts are supplied for the tags they will get scored by `count/topcount`,
where `topcount` is the highest count of all tags from a source. So the top
tag gets a score of `1.0`, a tag having only half of the top tag's count gets
a score of `0.5` and so on. 

### Tags scoring without count (Discogs, What partially)
Tags supplied without a count will be scored `0.85^(n-1)`, where `n` is the
total number of tags supplied by this source. The more tags the lower the
score for each tag will be. So if only one tag is supplied, it will get a
score of `1.0`, two tags will get a score of `0.85` each, three get `0.72`
each and so on...

### Score multiplier/modifier

#### Score multiplier for different sources
Every source has its own score multiplier, so sources that generally provide
higher quality tags can be given advantage over sources that often provide
bad, inaccurate or personal tags.

#### Split score multiplier
There is a score multiplier for modifying the score of the base tag from a tag
that got split up by space, this enables you to decide whether to keep, prefer
or ban the base tags. For example, lets say we have 'Alternative Rock' with a
score of `1`. It will end up as Alternative with score `1`, Rock with score `1`
and Alternative Rock with score `1 * <splitscore>`. So if you don't want to
keep Alternative Rock, just set it to 0. 

#### Artist score multiplier
There is an extra multiplier for tags gathered by searching for artists to
enable multiple albums from one artist getting more equal tags.

#### Personal score modifiers
One can set a list of tags that will get a multiplier bonus. Consider this as
some kind of "soft" white-/blacklist, where you can reduce the occurrence of
hated or inaccurate tags without fully banning them.

See Configuration for more details.
If you have any ideas on improving this scoring, please let me know :)

### Caching
All data received from music sites will get cached after prefiltering so that
rerunning the script will be super fast. The hardcoded cache timeout is 7 days.
The cache gets saved to disk every 10 minutes and will get cleaned up at the
end of the script. Remove the cache file to manually reset the cache.

## Installation

You'll need Python 2.7. Running the following should automatically install all
needed dependencies (musicbrainzngs, mutagen, requests):

	$ python setup.py install

## Configuration

An empty configuration file will be created on the first run. Make sure to
check if your config file needs to be updated after installing a new version
(although there shouldn't be much config file changes).

### Example configuration file
	[whatcd]
	username = whatusername
	password = whatpassword
	[genres]
	blacklist = charts, other, unknown
	score_up = soundtrack
	score_down = alternative, electronic, indie
	filters = instrument, label, location, year
	[scores]
	what.cd = 1.66
	last.fm = 0.66
	mbrainz = 1.00
	discogs = 1.00
	artists = 1.33
	splitup = 0.33
	userset = 0.66


### Configuration options explained

#### genres section

##### score_up, score_down option
This should be considered as "soft" white-/blacklist where you can in-/decrease
the occurrence of specific tags that you don't like or that are too inaccurate
for you without fully banning them like with the blacklist option. Tags listed
here will get a score bonus based on the configured multiplier.

##### filters
Use this to activate filtering of specific tag groups from genres:
* instrument: filters instrument related names, like piano or guitarist
* label: filters label names
* location: filters country, city and nationality names
* year: filters year tags, like 1980s
* create your own filter lists by adding filter sections to the tags.txt file,
consider them as large blacklists.

#### scores section

Be careful when adjusting the score multipliers, setting them out of a
reasonable range may lead to unexpected results and bad tags.
Don't set them to negative values!

##### what.cd, last.fm, mbrainz, discogs

Score multipliers for the different sources. Default `1.0`, increase if you
trust the tags from a source, lower if the source provides many inaccurate or
personal tags. Should be between `0.5` and `2.0`. If you dont want tags from a
specific source you should use the `--no-<source>` commandline option instead of
setting it to `0.0`.

##### artists

Score multiplier for tags found by artist/albumartist searches.
This enables that multiple albums from one artist get more equal tags.
* `<0.5` not recommended
* `<1.0` prefer album tags
* `=1.0` no difference between album and artist tags
* `>1.0` prefer artist tags
* `>2.0` not recommended

##### splitup

Score multiplier for the "base"-tag of tags that got split up.
* `=0.0` forget about the "base" tags
* `<1.0` prefer split parts
* `=1.0` handle them equally
* `>1.0` not recommended
Consider using a very small number instead of `0` if you don't like the base
tags to avoid banning them totally.

##### userset

`1+x` score multiplier for tags set in score_up and `1-x` for tags in score_down


## Usage
	  
	usage: whatlastgenre.py [-h] [-v] [-n] [-i] [-r] [-m] [-l N] [--no-whatcd]
	                        [--no-lastfm] [--no-mbrainz] [--no-discogs]
	                        [--config CONFIG] [--cache CACHE]
	                        path [path ...]
	
	positional arguments:
	  path                 folder(s) to scan for albums
	
	optional arguments:
	  -h, --help           show this help message and exit
	  -v, --verbose        more detailed output (default: False)
	  -n, --dry            don't save metadata (default: False)
	  -i, --interactive    interactive mode (default: False)
	  -r, --tag-release    tag release type (from What) (default: False)
	  -m, --tag-mbids      tag musicbrainz ids (default: False)
	  -l N, --tag-limit N  max. number of genre tags (default: 4)
	  --no-whatcd          disable lookup on What.CD (default: False)
	  --no-lastfm          disable lookup on Last.FM (default: False)
	  --no-mbrainz         disable lookup on MBrainz (default: False)
	  --no-discogs         disable lookup on Discogs (default: False)
	  --config CONFIG      location of the configuration file (default: ~/.whatlastgenre/config)
	  --cache CACHE        location of the cache file (default: ~/.whatlastgenre/cache)


If you seriously want to tag release-types (-r) or musicbrainz-ids (-m) you
should also enable interactive mode (-i). Consider to save the mbids (-m) when
not using --no-mbrainz, you searched for them, why not save them? ;)
Disabling music-sites is not recommended, the more sources the better tags.

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

* If it's an impartial correct tag, just use score_down or blacklist to get
rid of it.
* If it's an instrument, label or location tag that should have been filtered,
please name it to me so i can add it to the tags.txt file.
* If the tag is personal, crappy or somehow else bad, just talk to me and i'll
try to improve the scoring by adding it to the generic filter.

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

