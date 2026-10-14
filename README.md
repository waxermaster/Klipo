<p align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.waxermaster.Klipo.svg" alt="Klipo logo" height="128">
</p>

<h1 align="center">Klipo</h1>
<p align="center"><em>Take, annotate and copy screenshots. Record your screen.</em></p>

Klipo is a screenshot and screen recording tool for GNOME, built for one flow:

> shortcut → select an area → annotate → **Ctrl+C** → paste anywhere → close.

No file has to be saved, closing the editor never asks "Do you want to save?",
and what you copied can still be pasted after Klipo is closed.

## Features

### Screenshots

- Capture an area, the active window, the screen under the pointer or all screens,
  with an optional delay (3, 5 or 10 s)
- Works with several monitors, on Wayland and X11
- Every capture opens in a tab of the editor; open images with Ctrl+O, drag and drop,
  or paste one with Ctrl+V

### Editor

- ksnip-like layout: capture buttons on top, tools on the left, properties on the right,
  zoom in the status bar
- Tools with one-letter shortcuts: **S** select, **F** arrow, **L** line, **R** rectangle,
  **E** ellipse, **P** pen, **H** highlighter, **T** text, **B** blur / pixelate,
  **N** numbered steps, **C** crop
- Annotations stay editable until you copy or save: select, move, resize, delete,
  undo / redo (Ctrl+Z, Ctrl+Shift+Z)
- Text with font, size, bold, italic, underline, color, background and outline;
  blur or pixelate with adjustable strength; opacity for every colored tool
- **Ctrl+C** copies the annotated image as PNG, **Ctrl+S** saves it to `~/Pictures/Klipo`
  with a timestamped name, **Ctrl+Shift+S** asks where to save it (PNG, JPEG, WebP)
- From Gradia: backgrounds, padding, rounded corners, shadows, text recognition (OCR),
  upload to image hosts, source code images and pinned images

### Screen recording

- Record a screen, a window or an area selected on a live preview
- MP4 (H.264), WebM (VP8) or GIF, 30 or 60 frames per second
- Hardware encoding (NVIDIA NVENC, VA-API) when available, x264 otherwise; Klipo
  falls back automatically and never fails because a hardware encoder is missing
- System sound and microphone, separately; countdown; pointer shown or hidden
- A small floating window and a notification show the time and let you pause or stop
- After recording: play the video, copy the file (paste it into Files, Discord or a
  browser), open its folder or move it to the trash

## Installation

Klipo is not on Flathub yet. Build and install it with Flatpak:

```bash
flatpak install flathub org.gnome.Sdk//51 org.gnome.Platform//51
git clone https://github.com/waxermaster/Klipo.git
cd Klipo
flatpak-builder --user --install --force-clean build-dir build-aux/flatpak/io.github.waxermaster.Klipo.json
```

Use `io.github.waxermaster.Klipo.Devel.json` instead for a development build, which
installs next to the regular one.

## Keyboard shortcuts (GNOME)

```bash
./install-shortcuts.sh
```

| Keys | Action |
|---|---|
| Print | Capture an area |
| Shift+Print | Capture all screens |
| Alt+Print | Capture the active window |
| Ctrl+Shift+Alt+R | Start or stop a screen recording |

The script frees these keys from GNOME's own screenshot tool, keeps your
other custom shortcuts, allows the Flatpak to take screenshots without asking,
and saves what it changes. Use `--dry-run` to preview the changes and
`--uninstall` to put everything back as it was.

## Command line

Klipo is a single instance application: every command is sent to the running Klipo.

| Command | Action |
|---|---|
| `klipo` | Open the editor |
| `klipo --screenshot=INTERACTIVE` | Select an area, then open it in the editor |
| `klipo --screenshot=FULL` | Capture all screens |
| `klipo --screenshot=WINDOW` | Capture the active window |
| `klipo --screenshot=SCREEN` | Capture the screen under the pointer |
| `klipo --screenshot --delay=3` | Wait 3 seconds before capturing |
| `klipo --record` | Start a screen recording with the current options |
| `klipo --stop-recording` | Stop the recording |
| `klipo --toggle-recording` | Start or stop a recording |
| `klipo image.png` | Open an image |

When installed as a Flatpak, prefix commands with `flatpak run io.github.waxermaster.Klipo`.

## Known limitations

- **Active window and single screen captures on Wayland.** The screenshot portal can
  only capture everything or let you pick. On X11, Klipo crops a full capture to the
  window or screen; on Wayland, Window opens GNOME's capture interface (pick "Window"
  there) and Screen captures all screens.
- **The recording window can be recorded.** GNOME does not let apps exclude a window
  from a screen recording; the indicator is small and can be dragged aside.
- **Keeping windows above others** works on X11 only: on Wayland, use the window menu
  (Alt+Space → Always on Top) for the recording indicator.

## Development

```bash
flatpak-builder --user --install --force-clean build-dir build-aux/flatpak/io.github.waxermaster.Klipo.Devel.json
flatpak run io.github.waxermaster.Klipo.Devel
python3 -m unittest discover -s tests
```

With GNOME Builder: open the project folder, pick the
`io.github.waxermaster.Klipo.Devel.json` build configuration and press Run.

The Python package is still named `gradia` to make merging fixes from Gradia easier.
Screen recording lives in `gradia/recording/`.

### Translations

Klipo is translated with gettext (`po/`). After changing strings:

```bash
meson setup builddir
ninja -C builddir klipo-update-po
```

## Credits

Klipo is a fork of **[Gradia](https://github.com/AlexanderVanhee/Gradia)** by Alexander Vanhee
and contributors, which provides the image editor, annotation tools and much more.

It is also inspired by:

- **[Kooha](https://github.com/SeaDve/Kooha)** by Dave Patrick Caberto: the screen recording
  approach (ScreenCast portal → PipeWire → GStreamer) and the area selector,
  reimplemented in Python
- **[ksnip](https://github.com/ksnip/ksnip)**: the editor layout and workflow

## License

Klipo is licensed under the [GNU General Public License v3.0 or later](COPYING),
like Gradia and Kooha.
