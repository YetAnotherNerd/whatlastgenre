# whatlastgenre

Improves genre metadata of audio files based on tags from various music-sites.

* Supported audio files: flac, ogg, mp3
* Supported music-sites: wcd, lastfm, musicbrainz, discogs

## How it works: tag scoring
It scans through folders for albums and receives genre tags for this releases
and their artists from different music-sites. The tags get scored and put
together, then the best scored tags will be saved as genre metadata in the
corresponding album tracks. If a tag is supplied from more then one source,
their individual scores will be merged.

### Tags with count (lastfm, musicbrainz, wcd partially)
If counts are supplied for the tags, they will get scored by `count/topcount`,
where `topcount` is the highest count of all tags from a source. So the top
tag gets a score of 1.0, a tag having only half of the top tag's count gets a
score of 0.5 and so on. 

### Tags without count (discogs, wcd partially)
Tags supplied without a count will be scored `0.85^(n-1)`, where `n` is the
total number of tags supplied by this source. The more tags the lower the score
for each tag will be. So if only one tag is supplied, it will get a score of
1.0, two tags will get a score of 0.85 each, three get 0.72 each and so on...

### Score multiplier for different sources
Every source (wcd, lastfm, mbrainz, discogs) has its own score multiplier, so
sources that generally provide higher quality tags can be given advantage over
sources that often provide bad, inaccurate or personal tags.

### Personal score modifiers
One can set a list of tags that will get an initial score offset and a
tag-score bonus. Consider this as some kind of "soft" white-/blacklist, where
you can reduce the occurrence of hated or inaccurate tags without fully banning
them. See Configuration for more details.


If you have any ideas on improving this scoring, please let me know :)


## Installation

	$ python setup.py install

### Dependencies
* musicbrainzngs
* mutagen
* requests


## Configuration

An empty configuration file will be created on first run. The score multipliers
for sources can be tuned in the source, but act with caution.

### Example configuration file
	[whatcd]
	username = whatuser
	password = myscretwhatcdpassword
	[genres]
	whitelist = 
	blacklist = Live, Unknown
	uppercase = IDM, UK, US
	score_up = 
	score_down = Electronic, Other, Other


### Configuration options explained

#### genres section

##### whitelist, blacklist option
Using a whitelist is not recommended, depending on your music collection you
might have to create a really large list to get reasonable results, you might
want to use score_up instead. Use the blacklist to ban specific tags.
The white- and blacklist can't be used at the same time, obviously ;)

##### score_up, score_down option
This should be considered as "soft" white-/blacklist where you can in-/decrease
the occurrence of specific tags that you don't like or that are to inaccurate
for you without fully banning them like with the blacklist option. Tags listed
here will get an initial score offset and a score multiplier bonus of `+/-0.2`,
to modify their score offset even more, just mention them more then once.


## Usage

	usage: whatlastgenre.py [-h] [-v] [-n] [-i] [-r] [-m] [-s] [-b] [-c] [-l N]
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
	  -s, --stats          collect statistics (default: False)
	  -b, --use-colors     colorful output (default: False)
	  -c, --use-cache      cache processed albums (default: False)
	  -l N, --tag-limit N  max. number of genre tags (default: 4)
	  --no-whatcd          disable lookup on What.CD (default: False)
	  --no-lastfm          disable lookup on Last.FM (default: False)
	  --no-mbrainz         disable lookup on MusicBrainz (default: False)
	  --no-discogs         disable lookup on Discogs (default: False)
	  --config CONFIG      location of the configuration file (default: ~/.whatlastgenre/config)
	  --cache CACHE        location of the cache (default: ~/.whatlastgenre/cache)


If you seriously want to tag release-types (-r) you should also enable (-i, --interactive).
Disabling music-sites is not recommended, the more sources, the better tags.


## Examples

Tag the release-type and max. 5 genre tags for all albums in /home/user/music:

	$ whatlastgenre.py -irl 5 /home/user/music


Do a dry run on your albums in /home/user/music changing nothing, but collect
some statistics on your genres:

	$ whatlastgenre.py -nsb /home/user/music


## How to help

Thanks for being interested in helping to improve it :)
Things you can tell me about:
* Tag naming inconsistencies like a tag sometimes named 'Trip Hop' or 'Trip-Hop'
* If your unhappy with the tag result for some albums
* Any errors of course ;)

Or just paste me the output so i can take a look on it for scoring improvements.
I'm also happy for any other suggestions :)

### Ended up with a tag that shouldn't be there?
If it's an impartial correct tag, just use score_down or blacklist to get rid of it.
If the tag is personal, crappy or somehow else bad, just rerun the script with
`whatlastgenre.py -vn /path/to/that/album/with/wrong/genres > wlg.log`
and send me the `wlg.log`, i'll try to improve the scoring.

