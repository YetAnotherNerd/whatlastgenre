# whatlastgenre

Improves genre metadata of audio files based on tags from various music-sites.

* Supported audio files: flac, ogg, mp3
* Supported music-sites: wcd, lastfm, musicbrainz, discogs

## How it works: tag scoring
It scans through folders for albums and receives genre tags for this releases and their
artists from different music-sites. The tags get scored and put together, then the best
scored tags will be saved as genre metadata in the corresponding album tracks. If a tag
is supplied from more then one source, their individual scores will be merged.

### Tags with count (lastfm, musicbrainz, wcd partially)
If counts are supplied for the tags, they will get scored by `count/topcount`, where
`topcount` is the highest count of all tags from a source. So the top tag gets a score
of 1.0, a tag having only half of the top tag's count gets a score of 0.5 and so on. 

### Tags without count (discogs, wcd partially)
Tags supplied without a count will be scored `0.85^(n-1)`, where `n` is the total number
of tags supplied by this source. The more tags the lower the score for each tag will be.
So if only one tag is supplied, it will get a score of 1.0, two tags will get a score of
0.85 each, three get 0.72 each and so on...

### Score multiplier for different sources
Every source (wcd, lastfm, mbrainz, discogs) has its own score multiplier, so sources
that generally provide higher quality tags can be given advantage over sources that
often provide bad, inaccurate or personal tags.

### Personal score modifiers
One can set a list of tags in the configuration file that will get an initial score
offset (maybe a tag-score-multiplier too?). Consider this as some kind of "soft"
white-/blacklist, where you can reduce the occurrence of hated or inaccurate tags
without fully banning them. See Configuration for more details.


If you have any ideas on improving this scoring, please let me know :)


## Installation

### Dependencies
* python 2.7
* musicbrainzngs
* mutagen
* requests


	$ python setup.py install


## Configuration

Empty configuration file will be created on first run. Some score multipliers and
modifiers can be tuned in the source if you know what you are doing and act with caution.

### Example configuration file
	[whatcd]
	username = whatuser
	password = myscretwhatcdpassword
	[genres]
	whitelist = 
	blacklist = Unknown
	uppercase = IDM, UK, US
	score_up = 
	score_down = Electronic, Other, Other


### Configuration options explained

#### genres section

##### whitelist, blacklist option
Using a whitelist is not recommended, depending on your music collection you might have
to create a really large list to get resonable results. Use the blacklist to ban specific tags.
whitelist and blacklist can't be used at the same time, obviously ;).

##### score_up, score_down option
This should be considered as "soft" white-/blacklist where you can in-/decrease the
occurrence of specific tags that you dont like or that are to inaccurate for you without
fully banning them like with the blacklist option. Tags listed here will get an initial
score offset, to modify their score even more, just mention them more then once.


## Usage

	usage: whatlastgenre [-h] [-v] [-n] [-i] [-l N] [-r] [--no-whatcd]
	                     [--no-lastfm] [--no-mbrainz] [--no-discogs]
	                     [--config CONFIG]
	                     path [path ...]
	
	Improves genre-metadata of audio-files based on tags from various music-sites.
	
	positional arguments:
	  path                 folder(s) to scan
	
	optional arguments:
	  -h, --help           show this help message and exit
	  -v, --verbose        run verbose (more output) (default: False)
	  -n, --dry-run        dry-run (write nothing) (default: False)
	  -i, --interactive    interactive mode (default: False)
	  -r, --tag-release    tag release type from whatcd (default: False)
	  -s, --stats          collect stats to written genres (default: False)
	  -l N, --tag-limit N  max. number of genre tags (default: 4)
	  
	  --no-whatcd          disable lookup on What.CD (default: False)
	  --no-lastfm          disable lookup on Last.FM (default: False)
	  --no-mbrainz         disable lookup on MusicBrainz (default: False)
	  --no-discogs         disable lookup on Discogs (default: False)
	  
	  --config CONFIG      location of the configuration file (default: ~/.whatlastgenre/config)


If you seriously want to tag release-types (-r) you should also enable (-i, --interactive).


## Examples

To tag the release-type and max. 5 genre tags for all albums in /home/user/music:

	$ whatlastgenre -irl 5 /home/user/music


Do a dry run on your albums in /home/user/music changing nothing, but getting some statistics on your possible genres:

	$ whatlastgenre -ns /home/user/music

