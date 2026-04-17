#!/bin/bash
# Deploy script — stages only tracked/modified files, never blindly adds everything.
# Usage: ./deploy.sh [optional commit message]
set -euo pipefail

cd "$(dirname "$0")"

MSG="${1:-update}"

# Only stage files already tracked by git (modified or deleted).
# New files must be added explicitly with `git add <file>` before running this script.
git add -u
STAGED=$(git diff --cached --name-only)

if [ -z "$STAGED" ]; then
  echo "Nothing to commit. Working tree clean."
  exit 0
fi

echo "Staging:"
echo "$STAGED"
git commit -m "$MSG"
git push
