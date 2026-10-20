#!/bin/sh
# Install the rapport skill into agent harnesses by symlinking this folder.
#
#   ./install.sh                 link into ~/.claude/skills (Claude Code) and
#                                ~/.agents/skills (pi, Codex, and other Agent Skills harnesses)
#   ./install.sh --target DIR    also link into DIR (repeatable), e.g. ~/.pi/agent/skills
#   ./install.sh --only DIR      link into DIR instead of the defaults (repeatable)
#   ./install.sh --copy          copy instead of symlink (re-run after updating)
#   ./install.sh --bin           also put `rapport` on PATH (~/.local/bin)
#   ./install.sh --venv          build .venv in this folder with the dependencies
#                                (for sandboxed/offline use where `uv run` can't fetch)
#   ./install.sh --uninstall     remove only what this script installed
#   ./install.sh --dry-run       show what would happen
set -eu

REPO=$(cd "$(dirname "$0")" && pwd -P)
NAME=rapport
MARKER=".rapport-installed-copy"
DEFAULT_TARGETS="$HOME/.claude/skills $HOME/.agents/skills"
TARGETS=""
ONLY=0
COPY=0
BIN=0
VENV=0
UNINSTALL=0
DRY=0

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case $1 in
    --target) TARGETS="$TARGETS $2"; shift ;;
    --only) TARGETS="$TARGETS $2"; ONLY=1; shift ;;
    --copy) COPY=1 ;;
    --bin) BIN=1 ;;
    --venv) VENV=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
[ $ONLY -eq 1 ] || TARGETS="$DEFAULT_TARGETS $TARGETS"

run() { if [ $DRY -eq 1 ]; then echo "  would: $*"; else "$@"; fi; }

resolves_to_repo() {  # is $1 a symlink pointing at this repo?
  [ -L "$1" ] && [ "$(cd "$1" 2>/dev/null && pwd -P)" = "$REPO" ]
}

if [ $UNINSTALL -eq 1 ]; then
  for t in $TARGETS; do
    dest="$t/$NAME"
    if resolves_to_repo "$dest"; then
      echo "remove link $dest"; run rm "$dest"
    elif [ -d "$dest" ] && [ -f "$dest/$MARKER" ]; then
      echo "remove copy $dest"; run rm -rf "$dest"
    elif [ -e "$dest" ]; then
      echo "skip $dest (not installed by this script)"
    fi
  done
  binlink="$HOME/.local/bin/$NAME"
  if [ -L "$binlink" ] && [ "$(readlink "$binlink")" = "$REPO/bin/$NAME" ]; then
    echo "remove $binlink"; run rm "$binlink"
  fi
  exit 0
fi

chmod +x "$REPO/bin/$NAME" "$REPO/scripts/rapport.py" 2>/dev/null || true

for t in $TARGETS; do
  dest="$t/$NAME"
  if resolves_to_repo "$dest"; then
    echo "ok    $dest → $REPO (already linked)"
    continue
  fi
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    if [ -d "$dest" ] && [ -f "$dest/$MARKER" ]; then
      echo "update copy $dest"; run rm -rf "$dest"
    else
      echo "skip  $dest exists and is not ours; move it away and re-run" >&2
      continue
    fi
  fi
  run mkdir -p "$t"
  if [ $COPY -eq 1 ]; then
    echo "copy  $REPO → $dest"
    if [ $DRY -eq 0 ]; then
      mkdir -p "$dest"
      (cd "$REPO" && tar --exclude ./.git --exclude ./.venv --exclude '*/__pycache__' -cf - .) | (cd "$dest" && tar -xf -)
      touch "$dest/$MARKER"
    fi
  else
    echo "link  $dest → $REPO"; run ln -s "$REPO" "$dest"
  fi
done

if [ $BIN -eq 1 ]; then
  run mkdir -p "$HOME/.local/bin"
  binlink="$HOME/.local/bin/$NAME"
  if [ -e "$binlink" ] && ! [ -L "$binlink" ]; then
    echo "skip  $binlink exists and is not a symlink" >&2
  else
    echo "link  $binlink → $REPO/bin/$NAME"; run ln -sf "$REPO/bin/$NAME" "$binlink"
    case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "note: add ~/.local/bin to your PATH" ;; esac
  fi
fi

if [ $VENV -eq 1 ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "venv  $REPO/.venv (uv)"
    run uv venv --quiet "$REPO/.venv"
    run uv pip install --quiet --python "$REPO/.venv/bin/python" -r "$REPO/requirements.txt"
  else
    echo "venv  $REPO/.venv (python3 -m venv)"
    run python3 -m venv "$REPO/.venv"
    run "$REPO/.venv/bin/python" -m pip install --quiet -r "$REPO/requirements.txt"
  fi
fi

if [ -x "$REPO/.venv/bin/python" ]; then
  echo "deps  using $REPO/.venv"
elif command -v uv >/dev/null 2>&1; then
  echo "deps  via uv (inline script metadata); warming the cache…"
  if [ $DRY -eq 0 ]; then
    echo "      ready: $("$REPO/bin/$NAME" --version)"
  fi
else
  echo "deps  uv not found. Either install uv (https://docs.astral.sh/uv/), run ./install.sh --venv,"
  echo "      or: python3 -m pip install -r $REPO/requirements.txt"
fi
echo "done. Agents load the skill from SKILL.md; you can try: $REPO/bin/$NAME gallery --out /tmp/rapport-gallery"
