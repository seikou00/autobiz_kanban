#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT="${1:-autobiz_kanban_workspace.zip}"

cd "$SCRIPT_DIR"

if ! command -v zip >/dev/null 2>&1; then
  echo "Error: zip command not found." >&2
  exit 1
fi

declare -a items=()

[[ -f ".mcp.json" ]] && items+=(".mcp.json")
[[ -d "board_core" ]] && items+=("board_core")
[[ -d "hooks" ]] && items+=("hooks")
[[ -d "skills" ]] && items+=("skills")
[[ -d "agents" ]] && items+=("agents")

while IFS= read -r file; do
  items+=("$file")
done < <(find . -maxdepth 1 -type f -name "*.py" -print | sed 's#^\./##' | sort)

while IFS= read -r file; do
  items+=("$file")
done < <(find . -maxdepth 1 -type f -name "*.json" ! -name ".mcp.json" -print | sed 's#^\./##' | sort)

if [[ ${#items[@]} -eq 0 ]]; then
  echo "Error: no matching files found to package." >&2
  exit 1
fi

rm -f "$OUTPUT"
zip -r "$OUTPUT" "${items[@]}" \
  -x "*/__pycache__/*" "*.pyc" "*/.DS_Store" ".DS_Store"

if [[ "$OUTPUT" = /* ]]; then
  echo "Created $OUTPUT"
else
  echo "Created $SCRIPT_DIR/$OUTPUT"
fi
