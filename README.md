# mello

a distraction free spotify speaker for kids

kids swipe through album covers and tap to play. parents control the music library from spotify on their phone.

### build video

<a href="https://youtu.be/4tn8OtKkvs8"><img src="assets/videothumb.png" width="440" alt="build video"></a>

## features

- **spotify connect** — add albums and playlists from your spotify app, mello plays them
- **album carousel** — large cover art with smooth swipe navigation
- **simple controls** — play, pause, skip. that's it
- **auto-sleep** — screen turns off after 2 minutes of inactivity
- **auto-pause** — music stops after 30 minutes (configurable) to prevent all-day playback
- **progress memory** — remembers where each album left off for up to 96 hours
- **bluetooth** — connect wireless headphones or speakers
- **wifi setup** — creates a hotspot for easy configuration if wifi drops
- **auto-updates** — pulls latest changes from github nightly
- **no account needed on the device** — authentication happens via spotify on your phone

## hardware

print the case from [makerworld](https://makerworld.com/en/models/2692843-distraction-free-spotify-player-for-kids).

| part | link |
|------|------|
| raspberry pi 3 model b | [amazon](https://www.amazon.com/dp/B07BDR5PDW) |
| raspberry pi touch display 2 (5") | [amazon](https://www.amazon.com/dp/B0FMYFKDLZ) |
| wm8960 audio hat | [amazon](https://www.amazon.com/dp/B07KN8424G) |
| 5.1v 3a usb-c power supply | [amazon](https://www.amazon.com/dp/B0CLV6WB4L) |
| usb-c panel mount bushing | [amazon](https://www.amazon.com/dp/B0CDC1X4BY) |
| micro sd card (16gb+) | — |

## quick start

### 1. flash raspberry pi os

use the [raspberry pi imager](https://www.raspberrypi.com/software/):
- choose **raspberry pi os lite (64-bit)**
- choose a hostname and username (e.g. `mello` / `mello`)
- configure wifi and enable ssh

### 2. install mello

```bash
ssh <your-user>@<your-hostname>.local
curl -sSL https://raw.githubusercontent.com/emieljanson/mello/main/install.sh | bash
sudo reboot
```

to install without anonymous usage analytics:

```bash
curl -sSL https://raw.githubusercontent.com/emieljanson/mello/main/install.sh | bash -s -- --no-analytics
```

### 3. connect spotify

1. open spotify on your phone
2. tap the speaker icon
3. select "mello"
4. start playing — it shows up on the touchscreen

## how it works

mello is a python app using pygame for the ui and [go-librespot](https://github.com/devgianlu/go-librespot) as a spotify connect receiver. when you select mello as a speaker in spotify and play an album, go-librespot handles the audio stream while mello displays the album art and provides touch controls.

```
your phone (spotify app)
    │
    ▼
go-librespot (spotify connect daemon)
    │
    ▼
mello (pygame ui + touch input)
    │
    ▼
touchscreen + speaker
```

albums and playlists you play are automatically saved to the device. kids can then browse and play them independently from the touchscreen.

## settings menu

> **how to open:** press and hold the volume button for 3 seconds. there's no gear icon or visible button — the long-press on the volume button is the only way in.

once open, you'll see a scrollable menu with these sections:

### connections
- **wifi** — view saved networks, connect to a new one, or switch. if wifi drops, mello creates a "mello-setup" hotspot you can connect to from your phone
- **bluetooth** — pair and connect wireless headphones or speakers. shows paired devices and nearby discoverable devices
- **volume levels** — set separate volume levels (low/mid/high) for the built-in speaker and bluetooth output

### playback settings
- **auto-pause** — how long mello plays before automatically pausing (15, 30, 60, or 120 minutes). tap to cycle through options. default: 30 minutes
- **remember progress** — how long mello remembers where each album left off (12, 24, 48, or 96 hours). tap to cycle. default: 96 hours

### system
- **check for updates** — manually check for and install updates (mello also updates automatically each night)
- **reset** — factory reset: clears all albums, wifi, bluetooth, spotify credentials, and settings. requires a second tap to confirm

to close the menu, tap the **✕** in the top-right corner.

### usage data

during installation, mello asks if you'd like to share anonymous usage data. this helps improve the project. only session-level events are collected (play/pause, sleep/wake) — no personal data or music choices. the choice is made once during setup.

## known issues

**spotify "audio key error" — tracks skip without playing.** this is an upstream issue in librespot (the library that handles spotify connect). it affects some spotify accounts but not others, and there's no fix yet. mello uses [go-librespot](https://github.com/devgianlu/go-librespot) which is affected by the same problem. track the issue here: [librespot-org/librespot#1649](https://github.com/librespot-org/librespot/issues/1649)

## show off your build

built a mello? i'd love to see it! share a photo on twitter/x and tag [@emieljanson](https://x.com/emieljanson).

## contributing

see [contributing.md](CONTRIBUTING.md) for development setup and guidelines.

## security

see [security.md](SECURITY.md) for the security policy and responsible disclosure.

## license

[mit](LICENSE)

## acknowledgments

- [go-librespot](https://github.com/devgianlu/go-librespot) — spotify connect implementation
- [pygame](https://www.pygame.org/) — ui framework
- [posthog](https://posthog.com/) — anonymous usage analytics
