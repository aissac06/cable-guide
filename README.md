# cable-guide

TV listings for a personal cable-style channel app. Every 3 hours a GitHub Action runs
`build_guide.py`, which reads `channels.txt`, pulls free XMLTV listings, and writes `guide.json`
(what's on now and next per channel number) plus `guide-report.txt` (which listing each channel matched).

To pin a channel to a specific listing, put that listing's channel id in the last column of `channels.txt`.
