# whatlastgenre

Improves genre metadata of audio files based on tags from various music-sites.



## How it works: tag scoring

It gets tags for albums and their artists from different music-sites, scores them,
and puts the best scored tags as genre metadata in the album tracks. If a tag is
supplied from more then one source, their individual scores will be merged.

### Source with count

If counts are supplied for the tags, they will get scored by count/topcount, where
topcount is the highest count of all tags from a source. So the top tag gets a score
of 1.0, a tag having only half of the top tag's count gets a score of 0.5 and so on. 

### Source without count

Tags supplied without a count will be scored 0.85^(x-1), where x is the total number
of tags supplied by this source. The more tags the lower the score for each tag will be.
So if only one tag is supplied, it will get a score of 1.0, two tags will get a score
of 0.85 each, three get 0.72 each and so on...

### Score multiplier for different sources

Every source (whatcd, lastfm, musicbrainz, discogs) has its own score multiplier,
so sources that generally provide higher quality tags can be given advantage over
sources that often provide inaccurate tags.

#### Personal score modifiers

One can set a list of tags in the configuration file that will get an initial score
offset (maybe a tag-score-multiplier too?). Consider this as some kind of "soft"
white-/blacklist, where you can reduce the occurrence of hated or inaccurate tags
without fully banning them. To up/down-score a tag even more, just mention it multiple
times in the configuration file. See Configuration for more details.


If you have any ideas on improving this scoring, please let me know :)



## Installation

### Dependencies

Run this to install the required modules:

	$ pip install -r requirements.txt



## Configuration

Empty configuration file will be created on first run.

### Example configuration file

	[whatcd]
	username = whatuser
	password = myscretwhatcdpassword
	[lastfm]
	apikey = 54bee5593b60d0a5bf379cedcad79052
	[genres]
	whitelist = 
	blacklist = 
	uppercase = IDM, UK, US
	score_up = 
	score_down = Electronic, Other, Unknown, Unknown


### Configuration options explained

#### score_up, score_down

This should be considered as "soft" white-/blacklist where you can in-/decrease the
occurrence of specific tags that you dont like or that are to inaccurate for you without
fully banning them like in the tag_blacklist option. Tags listed here will get an initial
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



## Examples

To tag the release-type and max. 5 genre tags for all albums in /home/user/music:

	$ whatlastgenre -irl 5 /home/user/music


Do a dry run on your albums in /home/user/music changing nothing, but getting some statistics on your possible genres:

	$ whatlastgenre -ns /home/user/music

	

