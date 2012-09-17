# whatlastgenre

Improves genre-metadata of audio-files based on tags from various music-sites.

## How it works

## Installation

## Configuration

Empty configuration file will be created on first run.

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
	  -l N, --tag-limit N  max. number of genre tags (default: 6)
	  
	  --no-whatcd          disable lookup on What.CD (default: False)
	  --no-lastfm          disable lookup on Last.FM (default: False)
	  --no-mbrainz         disable lookup on MusicBrainz (default: False)
	  --no-discogs         disable lookup on Discogs (default: False)
	  
	  --config CONFIG      location of the configuration file (default: ~/.whatlastgenre/config)


## Examples

To tag the release-type and max. 5 genre tags for all albums in /home/user/music:

	$ whatlastgenre -rl 5 /home/user/music
