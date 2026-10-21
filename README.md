# NONGman
> External NONG browser and downloader for Geometry Dash.

## What is NONGman?
NONGman is a NONG browser and downloader for Geometry Dash, powered by tkinter and SFH. You can search by a song ID, browser all NONGs, and quickly download one.

## What is a NONG?
A NONG means "Not On NewGrounds". It is any track that does not exist on Newgrounds, and therefore cannot be officially used ingame. The common way to add a NONG to a level is to download the song and overwrite the track in %localappdata%/GeometryDash, which is what this tool automates.

## By using NONGman, am I locking myself out of the original song?
**No!** NONGman can revert most changes by pulling the original from Newgrounds (you will see the original song as an entry in available NONGs).

> [!WARNING]
> This is not guaranteed to work for all songs. In those cases, delete the track in Geometry Dash, and redownload it ingame.

## Where do I get NONGman?
You can get the latest version from [GitHub Releases](https://github.com/mralfiem591/nongman/releases/latest). Just grab `NONGman.exe` and run it!

## "You scammer! Windows is giving me a popup when I try to run your app!"
NONGman is completely safe. Windows gives a popup because I don't go through their hassle of signing my app. If you really don't trust it that much, either don't use it, or read the source code and run it yourself.

## Speaking of that, how do you run it yourself?
- Clone: `git clone https://github.com/mralfiem591/nongman.git`
- Sync: `uv sync`
- Run: `uv run main.py`

## What file types does NONGman support?
NONGman supports `.mp3`, `.wav`, and `.ogg` files.

## Why only Newgrounds songs? Why not the music library or official songs?
Because that is too hard, and will come at a later stage of development. Also, they are riskier to restore.

## Why not other file types?
First reason of the question above.

## Credits
- **Jukebox**: For inspiring this project
- **SFH**: For hosting the NONG database used

## License
This project is licensed under the terms of the MIT License. View full terms in the LICENSE file.

> mralfiem591 :)