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
# **It installs with `proofcut setup`, never with code of its own** (HISTORY.md § The kits call
# setup). This script fetches one thing, uv, pinned by URL and SHA-256 for the Mac's CPU, then
# runs scripts/setup_trial.py: `proofcut setup --yes` (which installs only what doctor marks ✗,
# from setup's own pins), `proofcut doctor`, and docs/DEMO.md's commands into
# ~/proofcut-mac-trial/demo, stopping at the first one that fails, since that is the finding. So a
# person's run is a run of the installer every Mac user gets, and the pins live in one place,
# src/proofcut/install.py. Until 2026-09-21 this kit carried its own install half — Homebrew on
# Apple silicon, a pinned-download folder on an Intel Mac — which setup's Mac routes grew out of.
#
# uv's caches, Pythons and tools, and whisper's speech model, are pointed into ~/proofcut-mac-trial,
# so they go when it goes. What setup installs goes where setup puts it — its folder under
# ~/.local/share/proofcut and links in ~/.local/bin — and setup records it, so --uninstall runs
# `proofcut setup --uninstall` first and then deletes the folder. Written for the macOS system
# bash (3.2): no associative arrays, no ${x,,}.

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
REPORT="${PROOFCUT_TRIAL_REPORT:-$HOME/Desktop/proofcut-mac-report.zip}"
MARKER="$W/.proofcut-mac-trial"
TOOLS="$W/tools"

# uv, the one download this script makes: url, sha256, CPU. The x86_64 pin is the one the Intel
# route has carried since 2026-09-17; the arm64 one is the same release, hashed 2026-09-21 and
# matching astral's own .sha256 file.
PIN_UV_X86_64="https://github.com/astral-sh/uv/releases/download/0.12.13/uv-x86_64-apple-darwin.tar.gz 5e287ef61cb6a9b61b3a83fef124fd143e400468a7dac794230147a810e17119 x86_64"
PIN_UV_ARM64="https://github.com/astral-sh/uv/releases/download/0.12.13/uv-aarch64-apple-darwin.tar.gz 7e6ddb9316acc00f2296c82ff4d99977870ee34b2f0ddcae9444d714db9364ed aarch64"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This is the Mac test. Run it on a Mac." >&2
    exit 1
fi

ask() {  # ask "question" -> 0 on y
    local ans
    printf "%s [y/N] " "$1"
    read -r ans
    [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

# Packed, proofcut is unpacked into $W; otherwise this script must be sitting in a proofcut checkout.
if grep -q '^__PAYLOAD__$' "$0"; then
    REPO="$W/proofcut"
else
    REPO="$(cd "$(dirname "$0")/.." && pwd)"
fi

# Everything uv and whisper would otherwise put under the home folder goes in $W. Only-managed,
# or `uv sync` takes a Python the Mac already has and the .venv depends on something outside.
uv_env() {
    export UV_CACHE_DIR="$W/uv/cache" UV_PYTHON_INSTALL_DIR="$W/uv/python"
    export UV_TOOL_DIR="$W/uv/tools" UV_TOOL_BIN_DIR="$W/uv/bin" UV_PYTHON_PREFERENCE=only-managed
    export XDG_CACHE_HOME="$W/cache"   # whisper keeps its speech model under here
    # setup's ffmpeg links go in ~/.local/bin, as they do for anyone who runs it.
    export PATH="$HOME/.local/bin:$UV_TOOL_BIN_DIR:$TOOLS/bin:$PATH"
}

# ---- --uninstall --------------------------------------------------------------------------
if [ "${1:-}" = "--uninstall" ]; then
    if [ ! -f "$MARKER" ]; then
        echo "No record of a proofcut test on this Mac ($MARKER is missing), so nothing to remove."
        exit 0
    fi
    echo
    echo "  This removes what the proofcut test added to this Mac, and nothing else:"
    echo "    - what 'proofcut setup' installed, from setup's own record"
    echo "    - the proofcut-mac-trial folder: uv, the Pythons and whisper it downloaded, the speech"
    echo "      model, and the test video"
    # A kit from before 2026-09-21 installed with Homebrew on Apple silicon; that is not undone here.
    if [ -f "$W/installed.txt" ] && grep -q '^brew ' "$W/installed.txt"; then
        echo "    - NOT the Homebrew packages an older version of this script installed:"
        echo "      $(grep '^brew ' "$W/installed.txt" | cut -d' ' -f2 | sort -u | tr '\n' ' ')"
    fi
    echo
    ask "  Go ahead?" || exit 0

    if [ -x "$TOOLS/bin/uv" ] && [ -f "$W/before-setup.json" ] && [ -f "$REPO/scripts/setup_trial.py" ]; then
        uv_env
        (cd "$REPO" && uv run python scripts/setup_trial.py "$W" --uninstall) ||
            echo "  setup's uninstall reported a difference (above); the folder is removed regardless."
    fi
    rm -rf "$W"
    echo
    echo "  Done. The report on your Desktop (proofcut-mac-report.zip) is yours to delete once sent."
    if ! grep -q '^__PAYLOAD__$' "$0"; then
        echo "  The proofcut folder you cloned is yours too: delete it when you are finished with it."
        echo "  Its .venv folder used a Python from the removed folder, so run 'uv sync' again if you keep using it."
    fi
    exit 0
fi

# ---- the test -----------------------------------------------------------------------------
if grep -q '^__PAYLOAD__$' "$0"; then
    SEND_TO="send it to whoever gave you this file"
else
    if [ ! -f "$REPO/scripts/setup_trial.py" ] || ! grep -q '^name = "proofcut"' "$REPO/pyproject.toml" 2>/dev/null; then
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

# A Terminal under Rosetta reports x86_64 on Apple silicon, and would install the Intel build of
# everything — whisper on torch 2.2.2 and numpy 1.x, run translated. Sent back to arm64 instead.
PIN_UV="$PIN_UV_ARM64"
if [ "$(uname -m)" != "arm64" ]; then
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
        echo "  This Mac has Apple silicon, but Terminal is running under Rosetta, as an Intel app,"
        echo "  so everything would install as Intel software. Turn off \"Open using Rosetta\" in"
        echo "  Terminal's Get Info window, open a new Terminal, and run this again."
        exit 1
    fi
    PIN_UV="$PIN_UV_X86_64"
    # OpenTimelineIO has no Intel Mac wheel, so `uv sync` compiles it, which needs Apple's compiler.
    if ! xcode-select -p >/dev/null 2>&1; then
        echo "  This Intel Mac needs Apple's Command Line Tools first: one of proofcut's parts is"
        echo "  compiled on the spot. A window is opening to install them. When it has finished,"
        echo "  run this again. Nothing else has been installed."
        xcode-select --install 2>/dev/null
        exit 1
    fi
fi

cat <<EOF

  proofcut — Mac test
  -------------------
  This will:
    1. download uv into one folder, $(echo "$W" | sed "s#^$HOME#~#"), and use it to run
       'proofcut setup', proofcut's own installer, which fetches what this Mac is missing
       of ffmpeg, auto-editor, Shotcut's renderer and whisper. It asks for no password.
    2. make a short test video and let proofcut edit it
    3. put proofcut-mac-report.zip on your Desktop, with your home folder's name taken out

  It takes 20–40 minutes, mostly downloading. You can leave it running.
  To remove everything it added afterwards, run this same file with --uninstall.

EOF
printf "  Press Enter to start, or Ctrl-C to stop. "
read -r _

# A re-run starts clean. What setup installed last time stays in setup's own record, and setup
# leaves it alone this time (doctor reads it ✓), so --uninstall still finds it.
[ -f "$MARKER" ] && rm -rf "$W"
mkdir -p "$W"
touch "$MARKER"
# The console, whole. report.txt is setup_trial.py's, and is what trial_check.py reads.
LOG="$W/kit.txt"
exec > >(tee -a "$LOG") 2>&1

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

finish() {
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
    for f in kit.txt report.txt demo/proj/proofcut.json demo/proj/project.otio; do
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

download_uv() {
    # shellcheck disable=SC2086  # the three fields, split on purpose
    set -- $PIN_UV
    local url="$1" sha="$2" dl="$TOOLS/dl" file="$TOOLS/dl/uv.tar.gz" got try
    mkdir -p "$TOOLS/bin" "$dl" || return 1
    for try in 1 2 3; do
        curl -fsSL -o "$file" "$url" && break
        rm -f "$file"
        [ "$try" -eq 3 ] && { echo "could not download $url"; return 1; }
        echo "try $try failed, trying again in $((10 * try))s"
        sleep $((10 * try))
    done
    got="$(shasum -a 256 "$file" | cut -d' ' -f1)"
    if [ "$got" != "$sha" ]; then
        rm -f "$file"
        echo "uv: SHA-256 is $got, expected $sha. Not using it."
        return 1
    fi
    echo "sha256 ok: $got  uv ($3)"
    tar -xzf "$file" -C "$dl" && mv "$dl"/uv-*-apple-darwin/uv "$TOOLS/bin/" || return 1
    rm -rf "$dl"
    "$TOOLS/bin/uv" --version
}

uv_env
if ! step "download uv (pinned, checked by SHA-256)" download_uv; then finish; exit 1; fi
cd "$REPO" || { fail="enter repo"; finish; exit 1; }
if ! step "uv sync" uv sync; then finish; exit 1; fi
# setup, doctor and DEMO.md, logged step by step into report.txt, then the frames at 3 s and 10 s.
if ! step "proofcut setup, doctor and the demo (scripts/setup_trial.py)" uv run python scripts/setup_trial.py "$W"; then
    inner="$(sed -n 's/^STOPPED AT: //p' "$W/report.txt" 2>/dev/null | tail -1)"
    [ -n "$inner" ] && fail="$inner"   # name the step that stopped, as the issue form asks
fi

finish
trap - INT   # from here Ctrl-C only closes the editor window; the report is already written

if [ -z "$fail" ]; then
    printf "  Want to see proofcut's editor window with the result? Type y and Enter (Ctrl-C closes it): "
    read -r ans
    if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
        uv run proofcut -C "$W/demo/proj" open
    fi
fi
exit 0
