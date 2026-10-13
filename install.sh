#!/bin/sh
# Iotift installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/CoolNinja2009/Iotift2/main/install.sh | sh
#
# What it does, all inside your home folder (no sudo):
#   1. finds Python 3.8+,
#   2. installs Iotift into its own environment in ~/.local/share/iotift,
#   3. links the `iotift` command into ~/.local/bin and puts that on your PATH,
#   4. runs `iotift setup`, which installs PlatformIO and the ESP32 toolchain.
#
# Options, set as environment variables (e.g. `curl ... | IOTIFT_SKIP_SETUP=1 sh`):
#   IOTIFT_VERSION=2.0.0   install that release instead of the newest one
#   IOTIFT_SKIP_SETUP=1    skip step 4 (it then happens on your first upload)
#   IOTIFT_FROM=PATH|URL   install from a local folder, wheel or URL instead
#
# Uninstall: rm -rf ~/.local/share/iotift ~/.local/bin/iotift ~/.local/bin/iotift2

set -eu

REPO="CoolNinja2009/Iotift2"
ROOT="${IOTIFT_HOME:-$HOME/.local/share/iotift}"
VENV="$ROOT/venv"
BIN="$HOME/.local/bin"

if [ -t 1 ]; then
    C="$(printf '\033[36m')"; G="$(printf '\033[32m')"; R="$(printf '\033[31m')"; D="$(printf '\033[2m')"; N="$(printf '\033[0m')"
else
    C=""; G=""; R=""; D=""; N=""
fi
step() { printf '\n%s==> %s%s\n' "$C" "$1" "$N"; }
ok()   { printf '    %s%s%s\n' "$G" "$1" "$N"; }
note() { printf '    %s\n' "$1"; }
die()  { printf '\n%s%s%s\n' "$R" "$1" "$N" >&2; exit 1; }

OS="$(uname -s)"

printf '\n  Iotift installer\n  %sA programming language for the ESP32%s\n' "$D" "$N"

# ---------------------------------------------------------------- 1. Python
step "Looking for Python 3.8 or newer"
PY=""
for cand in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python; do
    if command -v "$cand" >/dev/null 2>&1 &&
       "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
        PY="$(command -v "$cand")"
        break
    fi
done
if [ -z "$PY" ]; then
    if [ "$OS" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            note "Python was not found. Installing it with Homebrew..."
            brew install python
            PY="$(command -v python3 || true)"
        fi
        [ -n "$PY" ] || die "Python 3.8 or newer is needed. Install it from https://www.python.org/downloads/ and run this installer again."
    else
        die "Python 3.8 or newer is needed. Install it with your package manager, for example:
    sudo apt install python3 python3-venv      (Debian, Ubuntu, Raspberry Pi OS)
    sudo dnf install python3                   (Fedora)
    sudo pacman -S python                      (Arch)
then run this installer again."
    fi
fi
ok "Python $("$PY" -c 'import sys; print(sys.version.split()[0])')"

# ---------------------------------------------------------------- 2. Iotift
step "Installing Iotift into $ROOT"
mkdir -p "$ROOT" "$BIN"
if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv "$VENV" 2>/dev/null || {
        rm -rf "$VENV"
        die "Python cannot create environments here. On Debian or Ubuntu install the venv module:
    sudo apt install python3-venv
then run this installer again."
    }
fi

fetch() {
    if command -v curl >/dev/null 2>&1; then curl -fsSL -H 'User-Agent: iotift-installer' "$1"
    elif command -v wget >/dev/null 2>&1; then wget -qO- "$1"
    else return 1; fi
}

SOURCE="${IOTIFT_FROM:-}"
if [ -z "$SOURCE" ]; then
    # /releases, not /releases/latest: the newest release may be a pre-release.
    SOURCE="$(fetch "https://api.github.com/repos/$REPO/releases?per_page=20" 2>/dev/null | "$PY" -c '
import json, sys
want = sys.argv[1].lstrip("v")
try:
    releases = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
for r in releases:
    if r.get("draft") or (want and r["tag_name"].lstrip("v") != want):
        continue
    for a in r.get("assets", []):
        if a["name"].endswith(".whl"):
            sys.stderr.write("    Release %s\n" % r["tag_name"])
            print(a["browser_download_url"])
            sys.exit(0)
' "${IOTIFT_VERSION:-}" || true)"
fi
if [ -z "$SOURCE" ]; then
    [ -z "${IOTIFT_VERSION:-}" ] || die "Release $IOTIFT_VERSION was not found."
    SOURCE="https://github.com/$REPO/archive/refs/heads/main.zip"
    note "No release yet; installing the latest source from the main branch."
fi

"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "$SOURCE" ||
    die "pip could not install Iotift (see the messages above)."

for name in iotift iotift2; do
    ln -sf "$VENV/bin/$name" "$BIN/$name"
done
ok "$("$BIN/iotift" --version)"

# ---------------------------------------------------------------- 3. PATH
step "Adding ~/.local/bin to your PATH"
case ":$PATH:" in
    *":$BIN:"*) ok "Already there" ;;
    *)
        LINE='export PATH="$HOME/.local/bin:$PATH"'
        added=""
        for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
            # Only touch files that exist, plus the one for the user's own shell.
            case "$rc:${SHELL:-}" in
                *zshrc:*zsh|*bashrc:*bash) ;;
                *) [ -f "$rc" ] || continue ;;
            esac
            if ! grep -qs '\.local/bin' "$rc"; then
                printf '\n# Added by the Iotift installer\n%s\n' "$LINE" >> "$rc"
                added="$added $rc"
            fi
        done
        if [ -n "$added" ]; then ok "Added to$added"; else ok "Your shell startup files already add it"; fi
        PATH="$BIN:$PATH"
        NEW_SHELL=1
        ;;
esac

# ---------------------------------------------------------------- 4. PlatformIO + toolchain
if [ -n "${IOTIFT_SKIP_SETUP:-}" ]; then
    step "Skipping PlatformIO setup (IOTIFT_SKIP_SETUP is set); it runs on your first upload."
else
    step "Setting up PlatformIO and the ESP32 toolchain (one time, a few minutes)"
    "$BIN/iotift" setup --yes </dev/null ||
        note "Setup did not finish. Iotift is installed; run \`iotift setup\` again later to retry."
fi

# Serial ports on Linux belong to the dialout (or uucp) group.
if [ "$OS" = "Linux" ]; then
    for grp in dialout uucp; do
        if getent group "$grp" >/dev/null 2>&1; then
            if ! id -nG | tr ' ' '\n' | grep -qx "$grp"; then
                printf '\n    %sTo flash boards without sudo, add yourself to the %s group and log in again:%s\n' "$D" "$grp" "$N"
                printf '      sudo usermod -aG %s %s\n' "$grp" "$(id -un)"
            fi
            break
        fi
    done
fi

printf '\n  %sIotift is installed.%s\n\n' "$G" "$N"
printf '  Try it:\n'
printf '    iotift upload blink.iot          build and flash the board on USB\n'
printf '    iotift upload blink.iot -m       ...then open the serial monitor\n'
printf '    iotift --help                    every command\n\n'
if [ -n "${NEW_SHELL:-}" ]; then
    printf '  %sOpen a new terminal (or run: export PATH="$HOME/.local/bin:$PATH") to use iotift.%s\n\n' "$D" "$N"
fi
