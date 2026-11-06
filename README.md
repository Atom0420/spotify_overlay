# Spotify Overlay

Latest marquee/pixel-aligned overlay sources plus the media-control variant. Existing older overlay and rainbow-text source remain available. No local cache, Spotify token, listening history or user settings are uploaded.

## Run

Windows Python 3, PyQt5, psutil and requests. `python -m pip install PyQt5 psutil requests`. Run `python spotify_overlay_marquee_title_pixel_aligned.py` for the latest marquee layout or `python "SpotifyBOLD+Control.py"` for the media-control variant. Current source still depends on its configured external artwork/metadata services; service behavior was not tested.

| Script | Variant |
| --- | --- |
| `spotify_overlay_marquee_title_pixel_aligned.py` | Latest local marquee/pixel-aligned layout |
| `spotify_overlay_marquee_title_aligned.py` | Earlier marquee alignment |
| `SpotifyBOLD+Control.py` | Compact overlay with previous/play-pause/next media keys |
| `spotify_overlay_controls.py` | Previously published overlay, retained unchanged |

External artwork/metadata service availability and Spotify window behavior may change. The source is preserved as supplied; no current service/runtime test was performed.

## Source backup

Updated 5 November 2026 from matching Desktop/Documents source copies. Original local files were not edited. [Update notes](UPDATE-2026-11-05.md) and [source hashes](SOURCE-MANIFEST.json) identify the archived inputs. GUI/network behavior was not executed for this refresh.
