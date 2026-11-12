#!/usr/bin/env bash
set -euo pipefail

update_to_version() {
    version="$1"
    python_version="${2:-3.14}"

    if [ -z "$version" ]; then
        ref="$GITHUB_SHA"
    else
        ref="version-$version-hotfix"
    fi

    echo "Updating to $ref"

    git -C apps/frappe fetch --depth 1 upstream "$ref":"$ref"
    git -C apps/frappe checkout --quiet --force "$ref"

    if pgrep honcho > /dev/null; then
        pgrep honcho | xargs kill
        sleep 10
    fi

    bench migrate-env "python$python_version"

    bench setup requirements
    bench start &>> "${GITHUB_WORKSPACE}/bench_start.log" &
    bench --site test_site migrate
}

if [ "${1:-}" == "restore" ]; then
    wget "$DB_ARTIFACT_URL"
    bench --site test_site --force restore "$GITHUB_WORKSPACE/$(basename "$DB_ARTIFACT_URL")"
else
    update_to_version "${1:-}" "${2:-3.14}"
fi
