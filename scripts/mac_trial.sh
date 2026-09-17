#!/bin/bash
# proofcut on a Mac, in one file — docs/plans/LAUNCH.md § Step 2.
#
# One file, two ways to get it onto a Mac:
#
#   bash scripts/mac_trial.sh                  on the Mac, from a clone of the public repo: install
#                                              the tools, run docs/DEMO.md in that clone, zip a
#                                              report for a GitHub issue
#   bash scripts/mac_trial.sh --pack OUT.sh    on the dev box: copy this script to OUT.sh and
#                                              append the repo at HEAD, for a tester with no repo
#                                              access (the private-repo route, and the friend kit)
#   bash proofcut-mac-test.sh                  on the Mac: unpack, then the same run
#   bash <either> --uninstall                  on the Mac: remove what the test added, and only that
#
# The report is written to be attached to a public issue: the Mac's home folder is replaced by
# `~` in every text file in it, and the rest is footage the demo generated.
#
# The Mac half runs DEMO.md's commands as written, into ~/proofcut-mac-trial/demo instead of
# ~/proofcut-demo, and stops at the first one that fails, since that is the finding.
#
# What it installs, and why each is the light option:
#   - Homebrew formulae uv, ffmpeg-full, espeak-ng, auto-editor. Not `mlt`, which pulls 135
#     (OpenCV, VTK, OpenVINO, GCC). ffmpeg-full rather than ffmpeg, which Homebrew builds without
#     freetype or libass: no drawtext, so the demo's footage stops at its first command, and no
#     subtitles filter for a caption burn — the first mac-demo run, 2026-09-13. ffmpeg-full is
#     keg-only, so it goes first on PATH below; auto-editor still pulls the plain one in.
#   - melt comes from the Shotcut app instead, which bundles one and which picture.melt_bundles()
#     already finds, and which proofcut doctor's own fix names. Its modules are unmeasured
#     (PORTABILITY.md step 4), so the two frames in the report are the check, not melt's exit code.
#   - whisper from `uv tool`, since Homebrew's openai-whisper pulls llvm and pytorch as formulae.
#   - no ImageMagick: only cards need it and the demo draws none; doctor reports it unavailable.
#
# Everything added is written to installed.txt as it happens, and --uninstall reads that, so it
# never removes a thing the tester already had. Written for the macOS system bash (3.2): no
# associative arrays, no ${x,,}.

set -u

if [ "${1:-}" = "--pack" ]; then
    out="${2:?usage: mac_trial.sh --pack OUT.sh}"
    repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)" || exit 1
    if [ -n "$(git -C "$repo" status --porcelain)" ]; then
        echo "refusing: the working tree is dirty, and the pack is HEAD — commit first" >&2
        exit 1
    fi
    rev="$(git -C "$repo" rev-parse --short HEAD)"
    sed "s/^PACKED_REV=.*/PACKED_REV=$rev/" "$0" > "$out"
    echo "__PAYLOAD__" >> "$out"
    git -C "$repo" archive --format=tar.gz --prefix=proofcut/ HEAD | base64 -w 76 >> "$out"
    echo "packed proofcut $rev -> $out ($(du -h "$out" | cut -f1))"
    exit 0
fi

PACKED_REV=unpacked
ISSUE_URL="https://github.com/tydude001/proofcut/issues/new?template=mac-test.yml"
# The PROOFCUT_TRIAL_* overrides exist for a dry run off the Mac, and REPORT for the CI job
# (.github/workflows/mac-demo.yml), which uploads the report rather than leaving it on a Desktop.
W="${PROOFCUT_TRIAL_DIR:-$HOME/proofcut-mac-trial}"
DEMO="$W/demo"
REPORT="${PROOFCUT_TRIAL_REPORT:-$HOME/Desktop/proofcut-mac-report.zip}"
MANIFEST="$W/installed.txt"   # what this test added; survives a re-run, read by --uninstall
BREW_PATHS="${PROOFCUT_TRIAL_BREW:-/opt/homebrew/bin/brew /usr/local/bin/brew}"
APPS="${PROOFCUT_TRIAL_APPS:-/Applications}"
UVPY="${UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}"
WHISPER_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/whisper"
FORMULAE="uv ffmpeg-full espeak-ng auto-editor"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This is the Mac test. Run it on a Mac." >&2
    exit 1
fi

find_brew() {
    brew_bin=""
    for b in $BREW_PATHS; do
        [ -x "$b" ] && brew_bin="$b" && break
    done
    [ -n "$brew_bin" ] && eval "$("$brew_bin" shellenv)"
}

ask() {  # ask "question" -> 0 on y
    local ans
    printf "%s [y/N] " "$1"
    read -r ans
    [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

# ---- --uninstall --------------------------------------------------------------------------
if [ "${1:-}" = "--uninstall" ]; then
    if [ ! -f "$MANIFEST" ]; then
        echo "No record of a proofcut test on this Mac ($MANIFEST is missing), so nothing to remove."
        exit 0
    fi
    find_brew
    export PATH="$HOME/.local/bin:$PATH"
    # brew uninstall also autoremoves every orphaned dependency, the tester's own included — 8 on
    # the dev box's Linuxbrew, none of them the test's. Only what installed.txt names goes.
    export HOMEBREW_NO_AUTOREMOVE=1
    entries() { grep "^$1 " "$MANIFEST" | cut -d' ' -f2- | sort -u; }
    formulae="$(entries brew | tr '\n' ' ')"
    brew_itself=$(grep -c '^homebrew-itself$' "$MANIFEST")

    echo
    echo "  This removes what the proofcut test added to this Mac, and nothing else:"
    [ -n "$(entries cask)" ] && echo "    - the Shotcut app"
    [ -n "$(entries uv-tool)" ] && echo "    - whisper (speech-to-text)"
    [ -n "$(entries whisper-model)" ] && echo "    - the speech model it downloaded"
    grep -q '^uv-cache$' "$MANIFEST" && echo "    - uv's download cache"
    [ -n "$(entries uv-python)" ] && echo "    - the Python versions uv downloaded for the test"
    [ -n "$formulae" ] && echo "    - $(echo "$formulae" | wc -w | tr -d ' ') Homebrew packages: $formulae"
    [ "$brew_itself" -gt 0 ] && echo "    - Homebrew itself (you will be asked separately)"
    echo "    - the proofcut-mac-trial folder"
    echo
    ask "  Go ahead?" || exit 0

    if [ -n "$(entries uv-tool)" ] && command -v uv >/dev/null; then
        uv tool uninstall openai-whisper
    fi
    if grep -q '^uv-cache$' "$MANIFEST" && command -v uv >/dev/null; then
        uv cache clean
    fi
    entries uv-python | while read -r p; do
        case "$p" in "$HOME"/*) rm -rf "$p" ;; esac
    done
    entries whisper-model | while read -r p; do
        case "$p" in "$HOME"/*) rm -f "$p" ;; esac
    done
    # rmdir removes a folder only when it is empty, so a folder holding anything of theirs stays.
    rmdir "$WHISPER_CACHE" "$UVPY" "$HOME/.local/share/uv/tools" "$HOME/.local/share/uv" \
        "$HOME/.local/share" "$HOME/.local/bin" "$HOME/.local" 2>/dev/null

    if [ -n "$brew_bin" ]; then
        # Plain uninstall, never --zap: the test runs melt, never Shotcut itself, so it wrote no
        # Shotcut settings, and any that exist are from the tester's own earlier use.
        for c in $(entries cask); do
            brew uninstall --cask "$c"
        done
        removed_brew=0
        if [ "$brew_itself" -gt 0 ]; then
            echo
            echo "  Homebrew was installed by this test. If you don't use it for anything else,"
            if ask "  remove Homebrew completely too?"; then
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/uninstall.sh)"
                removed_brew=1
            fi
        fi
        if [ "$removed_brew" -eq 0 ] && [ -n "$formulae" ]; then
            # One command first, so Homebrew weighs the set as a whole. If it refuses — something
            # installed since depends on one of these — go one at a time, dependents before their
            # dependencies, and leave whatever is still needed.
            # shellcheck disable=SC2086
            if ! brew uninstall $formulae 2>/dev/null; then
                left="${formulae% }"
                while [ -n "$left" ]; do
                    next=""
                    for f in $left; do
                        brew list --formula "$f" >/dev/null 2>&1 || continue
                        brew uninstall "$f" >/dev/null 2>&1 || next="${next:+$next }$f"
                    done
                    [ "$next" = "$left" ] && break
                    left="$next"
                done
                [ -n "$left" ] && echo "  Kept (something else on this Mac needs them): $left"
            fi
        fi
    fi

    rm -rf "$W"
    echo
    echo "  Done. The report on your Desktop (proofcut-mac-report.zip) is yours to delete once sent."
    grep -q '^__PAYLOAD__$' "$0" || echo "  The proofcut folder you cloned is yours too: delete it when you are finished with it."
    [ "$brew_itself" -gt 0 ] && echo "  Apple's Command Line Tools, which Homebrew set up, stay installed; other apps use them."
    exit 0
fi

# ---- the test -----------------------------------------------------------------------------
# Packed, proofcut is unpacked into $W; otherwise this script must be sitting in a proofcut checkout,
# and the run uses that checkout as it is.
if grep -q '^__PAYLOAD__$' "$0"; then
    REPO="$W/proofcut"
    SEND_TO="send it to whoever gave you this file"
else
    REPO="$(cd "$(dirname "$0")/.." && pwd)"
    if [ ! -f "$REPO/scripts/make_demo.py" ] || ! grep -q '^name = "proofcut"' "$REPO/pyproject.toml" 2>/dev/null; then
        echo "This copy is not inside a proofcut checkout and has no proofcut packed into it." >&2
        echo "Clone the repo and run it from there:  git clone https://github.com/tydude001/proofcut" >&2
        exit 1
    fi
    PACKED_REV="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "no-git")"
    SEND_TO="attach it to a Mac test report: $ISSUE_URL"
    case "$REPO/" in "$W"/*)
        echo "The checkout is inside $W, which every run clears. Clone it somewhere else." >&2
        exit 1 ;;
    esac
fi

cat <<'EOF'

  proofcut — Mac test
  -------------------
  This will:
    1. install the tools proofcut needs (Homebrew packages, the Shotcut app, whisper)
    2. make a short test video and let proofcut edit it
    3. put proofcut-mac-report.zip on your Desktop, with your home folder's name taken out

  It takes 15–30 minutes, mostly downloading. You can leave it running.
  To remove everything it added afterwards, run this same file with --uninstall.

EOF
# Homebrew's installer aborts on anything but arm64 ("Homebrew on macOS is only supported on Apple
# Silicon processors!"), with no override — the first Intel run, issue #4, 2026-09-17. So an Intel
# Mac with no Homebrew stops here, before the prompt, rather than at the first step. One that already
# has Homebrew goes on: whether that brew still installs these formulae is itself the finding.
# A Terminal under Rosetta reports x86_64 on Apple silicon too, and the installer reads the same.
if [ "$(uname -m)" != "arm64" ]; then
    find_brew
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
        echo "  This Mac has Apple silicon, but Terminal is running under Rosetta, as an Intel app."
        echo "  Homebrew will not install that way. Turn off \"Open using Rosetta\" in Terminal's"
        echo "  Get Info window, open a new Terminal, and run this again."
        exit 1
    elif [ -z "$brew_bin" ]; then
        echo "  This is an Intel Mac, and the test cannot run on it: Homebrew, which installs the"
        echo "  tools, no longer supports Intel Macs, and its installer stops straight away."
        echo "  Nothing has been installed. Thank you for trying. Please say so on"
        echo "  https://github.com/tydude001/proofcut/issues/1 — an Apple silicon Mac is what's needed."
        exit 1
    fi
    echo "  Note: this is an Intel Mac. Homebrew no longer supports Intel, so the install may stop"
    echo "  early; the report still says where. Some tools may compile from source, which is slower."
    echo
fi
printf "  Press Enter to start, or Ctrl-C to stop. "
read -r _

if [ -f "$W/.proofcut-mac-trial" ]; then
    [ -f "$MANIFEST" ] && cp "$MANIFEST" "$HOME/.proofcut-mac-trial-installed"
    rm -rf "$W"
fi
mkdir -p "$W"
touch "$W/.proofcut-mac-trial"
if [ -f "$HOME/.proofcut-mac-trial-installed" ]; then
    mv "$HOME/.proofcut-mac-trial-installed" "$MANIFEST"
fi
touch "$MANIFEST"
LOG="$W/report.txt"
exec > >(tee -a "$LOG") 2>&1

record() { echo "$*" >> "$MANIFEST"; }
listing() { [ -d "$1" ] && find "$1" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort; }
whisper_before="$(listing "$WHISPER_CACHE")"
uvpy_before="$(listing "$UVPY")"
[ -d "${XDG_CACHE_HOME:-$HOME/.cache}/uv" ] || grep -q '^uv-cache$' "$MANIFEST" || record uv-cache
# Never upgrade, reinstall or clean up a package the tester already had.
export HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1

STEPS=()
fail=""

step() {  # step "what this is" command args...
    local what="$1"; shift
    echo
    echo "── $what"
    echo "\$ $*"
    local t0=$SECONDS
    "$@"
    local rc=$?
    local took=$((SECONDS - t0))
    if [ $rc -eq 0 ]; then
        STEPS+=("ok    ${took}s  $what")
    else
        STEPS+=("FAIL  ${took}s  $what  (exit $rc)")
        fail="$what"
        echo "!! failed (exit $rc) after ${took}s"
    fi
    return $rc
}

record_new_formulae() {  # anything in `brew list` now that was not there before the install
    [ -n "${formulae_before+x}" ] || return 0
    comm -13 <(echo "$formulae_before") <(brew list --formula -1 | sort) | while read -r f; do
        [ -n "$f" ] && ! grep -qx "brew $f" "$MANIFEST" && record "brew $f"
    done
}

finish() {
    record_new_formulae
    # Whatever arrived during the run — a speech model, a uv-managed Python — is the test's.
    comm -13 <(echo "$whisper_before") <(listing "$WHISPER_CACHE") | while read -r f; do
        [ -n "$f" ] && record "whisper-model $WHISPER_CACHE/$f"
    done
    comm -13 <(echo "$uvpy_before") <(listing "$UVPY") | while read -r f; do
        [ -n "$f" ] && record "uv-python $UVPY/$f"
    done

    echo
    echo "════ summary"
    echo "proofcut $PACKED_REV · macOS $(sw_vers -productVersion) · $(uname -m)"
    for s in ${STEPS[@]+"${STEPS[@]}"}; do echo "  $s"; done
    if [ -n "$fail" ]; then
        echo "STOPPED AT: $fail"
    else
        echo "ALL STEPS RAN"
    fi

    # The report names files under this Mac's home folder, and so do the project's manifest and
    # timeline, which store absolute paths; the username is the only personal thing in any of
    # them. Copies, never `sed -i`: tee still holds the log open and would keep writing to the
    # replaced file's old inode.
    sleep 1
    rm -rf "$W/report" "$REPORT"
    mkdir -p "$W/report"
    sed "s#$HOME#~#g" "$LOG" > "$W/report/report.txt"
    for f in demo/proj/proofcut.json demo/proj/project.otio; do
        [ -e "$W/$f" ] && sed "s#$HOME#~#g" "$W/$f" > "$W/report/$(basename "$f")"
    done
    for f in frame-3s.png frame-10s.png demo/demo.mp4; do
        [ -e "$W/$f" ] && cp "$W/$f" "$W/report/"
    done
    (cd "$W/report" && zip -q "$REPORT" ./*)
    echo
    echo "  Done. The report is on your Desktop:  proofcut-mac-report.zip"
    echo "  Please $SEND_TO"
    echo "  To remove everything the test installed:      bash $0 --uninstall"
    echo
    open -R "$REPORT" 2>/dev/null
}

# Ctrl-C mid-install still records what had landed, so --uninstall can find it.
trap 'echo; echo "!! stopped by Ctrl-C"; fail="stopped by Ctrl-C"; finish; exit 130' INT

echo "proofcut Mac test · proofcut $PACKED_REV · $(date '+%Y-%m-%d %H:%M %Z')"
echo "macOS $(sw_vers -productVersion) ($(sw_vers -buildVersion)) · $(uname -m) · $(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
echo "memory $(( $(sysctl -n hw.memsize) / 1073741824 )) GB · free disk $(df -h "$HOME" | awk 'NR==2 {print $4}')"

if [ "$REPO" = "$W/proofcut" ]; then
    line=$(awk '/^__PAYLOAD__$/ {print NR + 1; exit}' "$0")
    if ! step "unpack proofcut" sh -c "tail -n +$line '$0' | base64 --decode | tar -xz -C '$W'"; then finish; exit 1; fi
else
    echo "proofcut checkout: $(echo "$REPO" | sed "s#$HOME#~#")"
fi

# Homebrew. Its installer asks for the Mac's password and may install Apple's command line tools.
find_brew
if [ -z "$brew_bin" ]; then
    echo
    echo "Homebrew is not installed. Installing it now — it will ask for your Mac password."
    # A function, so step's `$ ...` line names it rather than printing the installer's whole source.
    install_homebrew() { /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; }
    step "install Homebrew" install_homebrew
    find_brew
    if [ -z "$brew_bin" ]; then fail="install Homebrew"; finish; exit 1; fi
    record homebrew-itself
fi
export PATH="$HOME/.local/bin:$PATH"

formulae_before="$(brew list --formula -1 | sort)"
# shellcheck disable=SC2086
step "install tools (Homebrew)" brew install $FORMULAE
brew_rc=$?
record_new_formulae
[ $brew_rc -eq 0 ] || { finish; exit 1; }
# Keg-only: installed but not linked, so without this every `ffmpeg` below is auto-editor's plain one.
ffmpeg_full="$(brew --prefix ffmpeg-full)"
export PATH="$ffmpeg_full/bin:$PATH"

if [ -d "$APPS/Shotcut.app" ]; then
    echo
    echo "── Shotcut is already installed; using it"
else
    if ! step "install Shotcut (for its melt renderer)" brew install --cask shotcut; then finish; exit 1; fi
    record "cask shotcut"
    # proofcut runs melt from inside the app, never the app itself, so macOS's first-launch prompt has
    # nowhere to appear. Homebrew has already checked the download against its checksum.
    xattr -dr com.apple.quarantine "$APPS/Shotcut.app" 2>/dev/null
fi

# A whisper already on PATH is used, never replaced: uv refuses to overwrite one it did not
# install ("Executable already exists", measured on the dry run), and it is the tester's anyway.
if command -v whisper >/dev/null; then
    echo
    echo "── whisper is already installed ($(command -v whisper)); using it"
else
    step "install whisper (uv tool)" uv tool install --python 3.12 openai-whisper
    whisper_rc=$?
    uv tool list 2>/dev/null | grep -q '^openai-whisper ' && record "uv-tool openai-whisper"
    [ $whisper_rc -eq 0 ] || { finish; exit 1; }
fi
echo
# shellcheck disable=SC2086
brew list --versions $FORMULAE
echo "shotcut: $(brew list --cask --versions shotcut 2>/dev/null || echo "not from Homebrew")"
echo "whisper: $(command -v whisper)"
echo "ffmpeg: $(command -v ffmpeg)"

cd "$REPO" || { fail="enter repo"; finish; exit 1; }
if ! step "uv sync" uv sync; then finish; exit 1; fi
step "proofcut doctor (informational)" uv run proofcut doctor || fail=""   # the demo below is the verdict

L() { uv run proofcut -C "$DEMO/proj" "$@"; }

step "DEMO 1 make the footage" uv run python scripts/make_demo.py "$DEMO" &&
step "DEMO 2 init" uv run proofcut init "$DEMO/proj" &&
step "DEMO 2 import vo" L import "$DEMO/vo.wav" --clip-id vo &&
step "DEMO 2 import blue" L import "$DEMO/broll-blue.mp4" --clip-id blue &&
step "DEMO 2 import rust" L import "$DEMO/broll-rust.mp4" --clip-id rust &&
step "DEMO 2 transcribe (first run downloads a 1.5 GB speech model)" L transcribe vo &&
step "DEMO 2 seed" L seed vo &&
step "DEMO 3 find the retake" L transcript vo --search "let me try that again" &&
step "DEMO 3 read around it" L transcript vo --first 8 --last 26 &&
step "DEMO 4 cut --plan" L cut vo 11:23 --plan &&
step "DEMO 4 cut" L cut vo 11:23 --pad 0.1 &&
step "DEMO 5 cue blue" L cue add vo --phrase "Every cut you make names a word" blue &&
step "DEMO 5 cue rust" L cue add vo --phrase "the render can be checked" rust &&
step "DEMO 5 shots" L shots &&
step "DEMO 6 import the score" L import "$DEMO/music.wav" --clip-id score &&
step "DEMO 6 score it" L music --asset score --clip-id vo --start-word 0 --fade-in 1 --fade-out 2 --under 18 &&
step "DEMO 7 render and master (melt)" L export "$DEMO/demo.mp4" --render --loudness -16 &&
step "DEMO 7 verify" L verify "$DEMO/demo.mp4" &&
step "DEMO 7 frames" L frames "$DEMO/demo.mp4"

# DEMO.md § 9: at ~3s the frame should read "BLUE 3s", at ~10s "RUST 0s". A person reads these.
if [ -f "$DEMO/demo.mp4" ]; then
    ffmpeg -v error -y -ss 3 -i "$DEMO/demo.mp4" -frames:v 1 "$W/frame-3s.png"
    ffmpeg -v error -y -ss 10 -i "$DEMO/demo.mp4" -frames:v 1 "$W/frame-10s.png"
fi

finish
trap - INT   # from here Ctrl-C only closes the editor window; the report is already written

if [ -z "$fail" ]; then
    printf "  Want to see proofcut's editor window with the result? Type y and Enter (Ctrl-C closes it): "
    read -r ans
    if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
        uv run proofcut -C "$DEMO/proj" open
    fi
fi
exit 0
