#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${DEVCONTAINER_SMOKE_IMAGE:-mtics-devcontainer:smoke}"
VOLUME="mtics-devcontainer-smoke-${GITHUB_RUN_ID:-local}-${GITHUB_JOB:-job}-$$"

cleanup() {
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ "${DEVCONTAINER_SMOKE_REUSE_IMAGE:-false}" == "true" ]]; then
    docker image inspect "$IMAGE" >/dev/null
else
    docker build --tag "$IMAGE" --file "$ROOT/.devcontainer/Dockerfile" "$ROOT"
fi
DEVCONTAINER_METADATA="$(docker image inspect \
    --format '{{ index .Config.Labels "devcontainer.metadata" }}' \
    "$IMAGE")"
test "$DEVCONTAINER_METADATA" = "[]"
docker volume create "$VOLUME" >/dev/null

docker run --rm \
    --user root \
    --entrypoint bash \
    --volume "$ROOT:/source:ro" \
    --volume "$VOLUME:/workspaces/mtics.github.io" \
    "$IMAGE" \
    -c '
        set -euo pipefail
        tar --directory /source \
            --exclude=.git \
            --exclude=.jekyll-cache \
            --exclude=_site \
            --exclude=node_modules \
            --create --file - . |
            tar --directory /workspaces/mtics.github.io --extract --file -
        chown -R vscode:vscode /workspaces/mtics.github.io
    '

docker run --rm \
    --user vscode \
    --env HOME=/home/vscode \
    --entrypoint bash \
    --volume "$VOLUME:/workspaces/mtics.github.io" \
    --workdir /workspaces/mtics.github.io \
    "$IMAGE" \
    -c '
        set -euo pipefail
        test "$(ruby --disable-gems -e '\''print RUBY_VERSION'\'')" = "3.4.10"
        test "$RUBY_DOWNLOAD_URL" = "https://cache.ruby-lang.org/pub/ruby/3.4/ruby-3.4.10.tar.xz"
        test "$RUBY_DOWNLOAD_SHA256" = "6f32ad662baafc228d12030dbcd284f83b034dd4337b300dc84ac74d11a1eb68"
        test "$(cd /tmp && bundle --version)" = "Bundler version 2.6.9"
        test "$(bundle _2.6.9_ --version)" = "Bundler version 2.6.9"
        test "$(node --version)" = "v24.18.0"
        test "$(npm --version)" = "11.18.0"
        test "$(python3 --version)" = "Python 3.13.14"
        bundle _2.6.9_ install --jobs 4 --retry 3
        npm ci
        python3 -m pip install --user --break-system-packages --require-hashes -r requirements-build.txt
        bundle _2.6.9_ check
        python3 -c "import nbconvert, pip_audit, playwright, rendercv"
        for seed in 1 2 3; do
            bundle _2.6.9_ exec ruby test/devcontainer_start_behavior_test.rb --seed "$seed"
        done
    '
