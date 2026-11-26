#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <pull-request-number> [--repo <owner/repo>] [gh pr merge arguments...]" >&2
    exit 2
fi

pr_number=$1
shift

# Resolve --repo/-R once, then pass a normalized --repo to every gh pr
# command. gh repo view takes a positional repository, not a flag.
repo_name=""
extra_args=()
while [ "$#" -gt 0 ]; do
    case $1 in
        --repo=*)
            repo_name=${1#--repo=}
            if [ -z "$repo_name" ]; then
                echo "$0: --repo needs a repository argument" >&2
                exit 2
            fi
            ;;
        -R=*)
            repo_name=${1#-R=}
            if [ -z "$repo_name" ]; then
                echo "$0: -R needs a repository argument" >&2
                exit 2
            fi
            ;;
        --repo | -R)
            if [ "$#" -lt 2 ] || [ -z "$2" ]; then
                echo "$0: $1 needs a repository argument" >&2
                exit 2
            fi
            repo_name=$2
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

repo_flag=()
if [ -n "$repo_name" ]; then
    repo_flag=(--repo "$repo_name")
fi

head_before=$(gh pr view "$pr_number" ${repo_flag[@]+"${repo_flag[@]}"} --json headRefOid --jq .headRefOid)

if ! gh pr checks "$pr_number" ${repo_flag[@]+"${repo_flag[@]}"}; then
    echo "Refusing to merge pull request #$pr_number because its checks are failing or pending." >&2
    exit 1
fi

head_after=$(gh pr view "$pr_number" ${repo_flag[@]+"${repo_flag[@]}"} --json headRefOid --jq .headRefOid)
if [ "$head_before" != "$head_after" ]; then
    echo "Refusing to merge pull request #$pr_number because its head moved during the check." >&2
    exit 1
fi

if [ -n "$repo_name" ]; then
    repo=$repo_name
else
    repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
fi

bad=$(gh api --paginate "repos/$repo/commits/$head_before/check-runs" \
    --jq '.check_runs[] | select(.status != "completed" or (.conclusion != "success" and .conclusion != "neutral" and .conclusion != "skipped")) | .name')
bad_status=$(gh api --paginate "repos/$repo/commits/$head_before/status" \
    --jq '[.statuses[] | select(.state != "success") | .context] | join(",")')
if [ -n "$bad" ] || [ -n "$bad_status" ]; then
    echo "Refusing to merge pull request #$pr_number because checks for $head_before are not all green: $bad $bad_status" >&2
    exit 1
fi

gh pr merge "$pr_number" --match-head-commit "$head_before" --merge ${extra_args[@]+"${extra_args[@]}"} ${repo_flag[@]+"${repo_flag[@]}"}
