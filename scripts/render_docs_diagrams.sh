#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_dir="$repo_root/docs/diagrams"
output_dir="$repo_root/docs/assets"

command -v dot >/dev/null 2>&1 || {
  echo "Graphviz 'dot' is required to render documentation diagrams." >&2
  exit 1
}

mkdir -p "$output_dir"

for source in "$source_dir"/*.dot; do
  name=$(basename "$source" .dot)
  destination="$output_dir/$name.svg"
  temporary="$destination.tmp"
  dot -Tsvg "$source" -o "$temporary"
  mv "$temporary" "$destination"
  echo "Rendered ${destination#$repo_root/}"
done
