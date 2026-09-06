#!/usr/bin/env bash
# qPCR 生物分布統整 - macOS / Linux 執行腳本
set -euo pipefail
cd "$(dirname "$0")"
python3 -m qpcr_biod.cli run -c "config/BD-TS-20260701.yaml" "$@"
