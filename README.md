<p align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.waxermaster.Klipo.svg" alt="Klipo logo" height="128">
</p>

<h1 align="center">Klipo</h1>
<p align="center"><em>Take, annotate and copy screenshots. Record your screen.</em></p>

Klipo is a screenshot and screen recording tool for GNOME, built for one flow:

> shortcut → select an area → annotate → **Ctrl+C** → paste anywhere → close.

No file has to be saved, and closing the editor never asks "Do you want to save?".

> [!NOTE]
> Klipo is under active development. See [Roadmap](#roadmap) for what already works.

## Features

- Capture an area, a window, a screen or all screens through the desktop portal
  (works on Wayland and X11, with multiple monitors)
- Annotations that stay editable until you copy or save: arrows, lines, rectangles,
  ellipses, pen, highlighter, text, numbered steps, censoring and cropping
- Single instance: every `klipo` command is sent to the running editor
- Optional backgrounds, padding and shadows, text recognition (OCR) and quick upload

## Command line

| Command | Action |
|---|---|
| `klipo` | Open the editor |
| `klipo --screenshot=INTERACTIVE` | Select an area, then open it in the editor |
| `klipo --screenshot=FULL` | Capture all screens |
| `klipo --screenshot=WINDOW` | Capture the active window |
| `klipo --screenshot=SCREEN` | Capture the screen under the pointer |
| `klipo --screenshot --delay=3` | Wait 3 seconds before capturing |
| `klipo --record` / `--stop-recording` / `--toggle-recording` | Control video recording |
| `klipo image.png` | Open an image |

When installed as a Flatpak, prefix commands with `flatpak run io.github.waxermaster.Klipo`.

## Keyboard shortcuts (GNOME)

```bash
./install-shortcuts.sh
```

| Keys | Action |
|---|---|
| Print | Capture an area |
| Shift+Print | Capture all screens |
| Alt+Print | Capture the active window |
| Ctrl+Shift+Alt+R | Start or stop a video recording |

The script frees these keys from GNOME's own screenshot tool, keeps your
other custom shortcuts, allows the Flatpak to take screenshots without asking,
and saves what it changes. Use `--dry-run` to preview the changes and
`--uninstall` to put everything back as it was.

## Building

Requirements: `flatpak`, `flatpak-builder` and the GNOME 51 SDK.

```bash
flatpak install flathub org.gnome.Sdk//51 org.gnome.Platform//51
flatpak-builder --user --install --force-clean build-dir build-aux/flatpak/io.github.waxermaster.Klipo.Devel.json
flatpak run io.github.waxermaster.Klipo.Devel
```

With GNOME Builder: open the project folder, pick the
`io.github.waxermaster.Klipo.Devel.json` build configuration and press Run.

Unit tests:

```bash
python3 -m unittest discover -s tests
```

## Roadmap

1. Rename, single instance, command line options, Flatpak build ✅
2. ksnip-style editor, reliable clipboard copy, close without confirmation ✅
3. `install-shortcuts.sh` for GNOME keyboard shortcuts ✅
4. Basic video recording (screen, MP4) ✅
5. Area and window recording, audio, GIF/WebM, countdown, preview ✅
6. Preferences, translations, documentation

## Credits

Klipo is a fork of **[Gradia](https://github.com/AlexanderVanhee/Gradia)** by Alexander Vanhee
and contributors, which provides the image editor, annotation tools and much more.

It is also inspired by:

- **[Kooha](https://github.com/SeaDve/Kooha)** by Dave Patrick Caberto: the screen recording
  approach (ScreenCast portal → PipeWire → GStreamer), reimplemented in Python
- **[ksnip](https://github.com/ksnip/ksnip)**: the editor layout and workflow

## License

Klipo is licensed under the [GNU General Public License v3.0 or later](COPYING),
like Gradia and Kooha.
