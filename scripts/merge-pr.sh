#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <pull-request-number> [--repo <owner/repo>] [gh pr merge arguments...]" >&2
    exit 2
fi

pr_number=$1
shift

# Split --repo/-R out of the gh pr merge arguments so that checks and view
# look at the same repository that merge targets.
repo_args=()
extra_args=()
while [ "$#" -gt 0 ]; do
    case $1 in
        --repo=*)
            repo_args+=("$1")
            ;;
        --repo | -R)
            if [ "$#" -lt 2 ]; then
                echo "$0: $1 needs a repository argument" >&2
                exit 2
            fi
            repo_args+=("$1" "$2")
            shift
            ;;
        *)
            extra_args+=("$1")
            ;;
    esac
    shift
done

if ! gh pr checks "$pr_number" ${repo_args[@]+"${repo_args[@]}"}; then
    echo "Refusing to merge pull request #$pr_number because its checks are failing or pending." >&2
    exit 1
fi

head=$(gh pr view "$pr_number" ${repo_args[@]+"${repo_args[@]}"} --json headRefOid --jq .headRefOid)

gh pr merge "$pr_number" --match-head-commit "$head" --merge ${extra_args[@]+"${extra_args[@]}"}
