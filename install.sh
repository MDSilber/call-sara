#!/usr/bin/env bash
# Symlink this repo's skills into ~/.claude/skills/ and arm the secret scanner.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.claude/skills
for d in "$here"/skills/*/ ; do
  name="$(basename "$d")"
  target=~/.claude/skills/"$name"
  if [ -L "$target" ] || [ -e "$target" ]; then
    echo "skip  $name (exists)"
  else
    ln -s "$d" "$target"; echo "link  $name"
  fi
done
chmod +x "$here"/skills/finance/scripts/init_vault.sh "$here"/skills/finance/tools/run 2>/dev/null || true
git -C "$here" config core.hooksPath .githooks   # this repo scans its own commits too
# Python 3.11+ is required by the tools (tomllib). Install via brew if absent.
PYV="$(python3 -c 'import sys; print(sys.version_info >= (3,11))' 2>/dev/null || echo False)"
if [ "$PYV" != "True" ]; then
  if command -v brew >/dev/null 2>&1; then
    echo "installing python (3.11+ required by the tools)…"; brew install -q python@3.12
  else
    echo "⚠️  Python 3.11+ required — install it, then re-run"; exit 1
  fi
fi

# Two helpers most people don't have: gitleaks (the scanner that blocks
# account numbers from ever being committed) and poppler (pdftotext, for
# reading PDF statements). Install them via brew if missing.
if command -v brew >/dev/null 2>&1; then
  command -v gitleaks  >/dev/null 2>&1 || { echo "installing gitleaks (commit-time secret scanner)…"; brew install -q gitleaks; }
  command -v pdftotext >/dev/null 2>&1 || { echo "installing poppler (PDF statement reading)…"; brew install -q poppler; }
else
  echo "⚠️  Homebrew not found — install gitleaks + poppler yourself (the vault refuses to commit without gitleaks)"
fi
echo ""
echo "✓ skill linked · secret scanner armed · helpers present · python OK"
echo "Start a NEW Claude Code session (skills register at session start), then say"
echo "\"set up my finances\" to create a vault (or run skills/finance/scripts/init_vault.sh)."
