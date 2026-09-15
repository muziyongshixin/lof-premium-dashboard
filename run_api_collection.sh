#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h}"
cd "$project_dir"

python3 collect_premium_api.py "$@"
python3 build_dashboard_data.py
