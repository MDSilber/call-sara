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
command -v gitleaks >/dev/null 2>&1 || echo "⚠️  brew install gitleaks — the vault (and this repo) refuse to commit without it"
echo "Start a NEW Claude Code session (skills register at session start), then say"
echo "\"set up my finances\" to create a vault (or run skills/finance/scripts/init_vault.sh)."
