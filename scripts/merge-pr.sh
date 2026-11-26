#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <pull-request-number> [gh pr merge arguments...]" >&2
    exit 2
fi

pr_number=$1
shift

if ! gh pr checks "$pr_number"; then
    echo "Refusing to merge pull request #$pr_number because its checks are failing or pending." >&2
    exit 1
fi

gh pr merge "$pr_number" --merge "$@"
