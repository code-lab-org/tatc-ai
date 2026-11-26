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
        --match-head-commit | --match-head-commit=*)
            echo "$0: --match-head-commit is set by the script" >&2
            exit 2
            ;;
        *)
            extra_args+=("$1")
            ;;
    esac
    shift
done

head_before=$(gh pr view "$pr_number" ${repo_args[@]+"${repo_args[@]}"} --json headRefOid --jq .headRefOid)

if ! gh pr checks "$pr_number" ${repo_args[@]+"${repo_args[@]}"}; then
    echo "Refusing to merge pull request #$pr_number because its checks are failing or pending." >&2
    exit 1
fi

head_after=$(gh pr view "$pr_number" ${repo_args[@]+"${repo_args[@]}"} --json headRefOid --jq .headRefOid)
if [ "$head_before" != "$head_after" ]; then
    echo "Refusing to merge pull request #$pr_number because its head moved during the check." >&2
    exit 1
fi

repo=$(gh repo view ${repo_args[@]+"${repo_args[@]}"} --json nameWithOwner --jq .nameWithOwner)
bad=$(gh api --paginate "repos/$repo/commits/$head_before/check-runs" \
    --jq '.check_runs[] | select(.status != "completed" or (.conclusion != "success" and .conclusion != "neutral" and .conclusion != "skipped")) | .name')
if [ -n "$bad" ]; then
    echo "Refusing to merge pull request #$pr_number because checks for $head_before are not all green: $bad" >&2
    exit 1
fi

gh pr merge "$pr_number" --match-head-commit "$head_before" --merge ${extra_args[@]+"${extra_args[@]}"} ${repo_args[@]+"${repo_args[@]}"}
