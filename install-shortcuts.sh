#!/usr/bin/env bash
# Install (or remove) GNOME keyboard shortcuts for Klipo.
#
# Copyright (C) 2026 Klipo contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
#   Print                  capture an area
#   Shift+Print            capture all screens
#   Alt+Print              capture the active window
#   Ctrl+Shift+Alt+R       start / stop a video recording
#
# GNOME's own bindings for these keys are freed, and their previous values
# are saved so that --uninstall puts everything back. Existing custom
# shortcuts are kept: new entries are appended to the list. A custom
# shortcut that already uses one of these keys is disabled (and restored on
# --uninstall). The Flatpak is also allowed to take screenshots without
# asking, since GNOME cannot show that question while Klipo hides itself.

set -euo pipefail

MEDIA_KEYS="org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA="$MEDIA_KEYS.custom-keybinding"
CUSTOM_BASE="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
SHELL_KEYS="org.gnome.shell.keybindings"
BACKUP_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/klipo"
BACKUP_FILE="$BACKUP_DIR/shortcuts-backup"

# id | name | binding | arguments | GNOME key that owns the binding
SHORTCUTS=(
  "klipo-area|Klipo: Capture Area|Print|--screenshot=INTERACTIVE|show-screenshot-ui"
  "klipo-all|Klipo: Capture All Screens|<Shift>Print|--screenshot=FULL|screenshot"
  "klipo-window|Klipo: Capture Window|<Alt>Print|--screenshot=WINDOW|screenshot-window"
  "klipo-record|Klipo: Start or Stop Recording|<Ctrl><Shift><Alt>r|--toggle-recording|show-screen-recording-ui"
)

DRY_RUN=0
UNINSTALL=0
APP_ID=""

usage() {
  cat <<EOF
Usage: $0 [--uninstall] [--dry-run] [--app-id ID]

  --uninstall   Remove Klipo shortcuts and restore GNOME's previous ones
  --dry-run     Show what would change without changing anything
  --app-id ID   Flatpak to launch (default: io.github.waxermaster.Klipo,
                or the .Devel build if only that one is installed)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) UNINSTALL=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --app-id) shift; APP_ID="${1:?--app-id needs a value}" ;;
    --app-id=*) APP_ID="${1#--app-id=}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
change() { printf '  \033[1m%s\033[0m\n' "$*"; }

run() {
  # Print a change, and apply it unless this is a dry run
  if [ "$DRY_RUN" = 1 ]; then
    printf '  (dry run) %s\n' "$*"
  else
    "$@"
  fi
}

require_gnome() {
  if ! command -v gsettings >/dev/null; then
    echo "gsettings not found: this script is for GNOME." >&2
    exit 1
  fi
  if ! gsettings list-keys "$SHELL_KEYS" >/dev/null 2>&1; then
    echo "GNOME Shell keybinding settings not found: is this a GNOME session?" >&2
    exit 1
  fi
}

# --- GVariant string array helpers (paths and accelerators never contain quotes)

array_items() { grep -o "'[^']*'" <<<"$1" | tr -d "'" || true; }

to_array() {
  local out="" item
  for item in "$@"; do
    out+="${out:+, }'$item'"
  done
  if [ -z "$out" ]; then echo "@as []"; else echo "[$out]"; fi
}

lower() { tr '[:upper:]' '[:lower:]' <<<"$1"; }

# Accelerators match regardless of case and of <Ctrl>/<Control>/<Primary> spelling
normalize_accel() {
  lower "$1" | sed -e 's/<control>/<ctrl>/g' -e 's/<primary>/<ctrl>/g'
}

custom_paths() { array_items "$(gsettings get "$MEDIA_KEYS" custom-keybindings)"; }

custom_get() { gsettings get "$CUSTOM_SCHEMA:$1" "$2"; }
custom_set() { run gsettings set "$CUSTOM_SCHEMA:$1" "$2" "$3"; }

set_custom_paths() { run gsettings set "$MEDIA_KEYS" custom-keybindings "$(to_array "$@")"; }

# --- Backup file: one "kind<TAB>key<TAB>value" line per saved setting

backup_get() {
  [ -f "$BACKUP_FILE" ] || return 1
  awk -F'\t' -v k="$1" -v n="$2" '$1 == k && $2 == n { print $3; found = 1 } END { exit !found }' "$BACKUP_FILE"
}

backup_add() {
  if [ "$DRY_RUN" = 1 ]; then return; fi
  mkdir -p "$BACKUP_DIR"
  if ! backup_get "$1" "$2" >/dev/null 2>&1; then
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$BACKUP_FILE"
  fi
}

# --- Which command launches Klipo

detect_command() {
  if [ -n "$APP_ID" ]; then
    echo "flatpak run $APP_ID"
    return
  fi
  local id
  for id in io.github.waxermaster.Klipo io.github.waxermaster.Klipo.Devel; do
    if command -v flatpak >/dev/null && flatpak info "$id" >/dev/null 2>&1; then
      APP_ID="$id"
      echo "flatpak run $id"
      return
    fi
  done
  if command -v klipo >/dev/null; then
    echo "klipo"
    return
  fi
  echo "Klipo is not installed (no Flatpak and no 'klipo' command found)." >&2
  exit 1
}

install() {
  local command
  command="$(detect_command)"
  say "Installing Klipo shortcuts for: $command"
  [ "$DRY_RUN" = 1 ] && say "(dry run: nothing will be changed)"

  local entry id name binding args gnome_key
  for entry in "${SHORTCUTS[@]}"; do
    IFS='|' read -r id name binding args gnome_key <<<"$entry"
    local wanted
    wanted="$(normalize_accel "$binding")"

    # 1. Free the key from GNOME Shell, keeping its other accelerators
    local current kept=() accel removed=0
    current="$(gsettings get "$SHELL_KEYS" "$gnome_key")"
    while IFS= read -r accel; do
      [ -z "$accel" ] && continue
      if [ "$(normalize_accel "$accel")" = "$wanted" ]; then
        removed=1
      else
        kept+=("$accel")
      fi
    done < <(array_items "$current")
    if [ "$removed" = 1 ]; then
      say "GNOME '$gnome_key' uses $binding: freeing it (was $current)"
      backup_add shell "$gnome_key" "$current"
      change "gsettings set $SHELL_KEYS $gnome_key \"$(to_array "${kept[@]+"${kept[@]}"}")\""
      run gsettings set "$SHELL_KEYS" "$gnome_key" "$(to_array "${kept[@]+"${kept[@]}"}")"
    fi

    # 2. Disable other custom shortcuts bound to the same key
    local path
    while IFS= read -r path; do
      [ -z "$path" ] && continue
      [[ "$path" == "$CUSTOM_BASE/klipo-"* ]] && continue
      local other
      other="$(custom_get "$path" binding | tr -d "'")"
      if [ -n "$other" ] && [ "$(normalize_accel "$other")" = "$wanted" ]; then
        say "Custom shortcut $(custom_get "$path" name) also uses $binding: disabling it"
        backup_add custom-binding "$path" "$other"
        change "binding of $path -> ''"
        custom_set "$path" binding "''"
      fi
    done < <(custom_paths)

    # 3. Add (or update) Klipo's own custom shortcut
    local klipo_path="$CUSTOM_BASE/$id/"
    say "$binding -> $name"
    change "$klipo_path: $command $args"
    custom_set "$klipo_path" name "'$name'"
    custom_set "$klipo_path" command "'$command $args'"
    custom_set "$klipo_path" binding "'$binding'"

    local paths=() found=0
    while IFS= read -r path; do
      [ -z "$path" ] && continue
      paths+=("$path")
      [ "$path" = "$klipo_path" ] && found=1
    done < <(custom_paths)
    if [ "$found" = 0 ]; then
      paths+=("$klipo_path")
      set_custom_paths "${paths[@]}"
    fi
  done

  # 4. Screenshot permission for the Flatpak
  if [ -n "$APP_ID" ] && command -v flatpak >/dev/null; then
    if ! flatpak permission-show "$APP_ID" 2>/dev/null | grep -q "^screenshot"; then
      say "Allowing $APP_ID to take screenshots without asking"
      change "flatpak permission-set screenshot screenshot $APP_ID yes"
      run flatpak permission-set screenshot screenshot "$APP_ID" yes
      backup_add permission screenshot "$APP_ID"
    fi
  fi

  say ""
  if [ "$DRY_RUN" = 1 ]; then
    say "Dry run finished: nothing was changed."
  else
    say "Done. Your previous settings are saved in $BACKUP_FILE"
    say "Run '$0 --uninstall' to restore them."
  fi
}

uninstall() {
  say "Removing Klipo shortcuts"
  [ "$DRY_RUN" = 1 ] && say "(dry run: nothing will be changed)"

  # 1. Remove Klipo's custom shortcuts, keep everything else in order
  local paths=() path
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    if [[ "$path" == "$CUSTOM_BASE/klipo-"* ]]; then
      change "removing $path"
      run gsettings reset-recursively "$CUSTOM_SCHEMA:$path"
    else
      paths+=("$path")
    fi
  done < <(custom_paths)
  set_custom_paths "${paths[@]+"${paths[@]}"}"

  if [ ! -f "$BACKUP_FILE" ]; then
    say "No backup found at $BACKUP_FILE: GNOME's own shortcuts were left as they are."
    return
  fi

  # 2. Restore saved GNOME Shell bindings, custom bindings and permissions
  local kind key value
  while IFS=$'\t' read -r kind key value; do
    case "$kind" in
      shell)
        change "gsettings set $SHELL_KEYS $key \"$value\""
        run gsettings set "$SHELL_KEYS" "$key" "$value"
        ;;
      custom-binding)
        change "binding of $key -> '$value'"
        custom_set "$key" binding "'$value'"
        ;;
      permission)
        change "flatpak permission-remove screenshot screenshot $value"
        run flatpak permission-remove screenshot screenshot "$value" || true
        ;;
    esac
  done <"$BACKUP_FILE"

  if [ "$DRY_RUN" = 0 ]; then
    rm -f "$BACKUP_FILE"
  fi
  say ""
  say "Done: GNOME's previous shortcuts are back."
}

require_gnome
if [ "$UNINSTALL" = 1 ]; then
  uninstall
else
  install
fi
