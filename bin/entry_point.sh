#!/bin/bash
set -euo pipefail

CONFIG_FILE="${CONFIG_FILE:-_config.yml}"
DOCKER_DESTINATION="${DOCKER_DESTINATION:-/tmp/_site}"

verify_bundle_deps() {
    if ! bundle _2.6.9_ check; then
        echo "Locked dependencies are missing; rebuild the image instead of mutating it at runtime." >&2
        exit 1
    fi
}

verify_bundle_deps
mkdir -p "$DOCKER_DESTINATION"

exec bundle _2.6.9_ exec jekyll serve --watch --port=8080 --host=0.0.0.0 --livereload --verbose --trace --force_polling --disable-disk-cache --destination "$DOCKER_DESTINATION" --config "$CONFIG_FILE"
