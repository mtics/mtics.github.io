#!/usr/bin/env bash
# Scan both locally built release images for one architecture with the same
# Trivy profile as .github/workflows/deploy.yml. Used by the manual
# "Trivy baseline review" workflow to collect the four reports that
# bin/create_trivy_baseline.py requires.
#
# Usage: bin/trivy_review_scan.sh <amd64|arm64> <trivy-cache-dir> <report-dir>
set -euo pipefail

architecture="${1:?architecture (amd64 or arm64) is required}"
cache_dir="${2:?Trivy cache directory is required}"
report_dir="${3:?report directory is required}"
trivy_image="${TRIVY_IMAGE:?TRIVY_IMAGE must name the digest-pinned scanner}"
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "${architecture}" in
  amd64 | arm64) ;;
  *)
    echo "unsupported architecture: ${architecture}" >&2
    exit 2
    ;;
esac

mkdir -p "${report_dir}"

scan() {
  local image="$1"
  local artifact="$2"
  shift 2
  local skip_arguments=()
  local path
  for path in "$@"; do
    skip_arguments+=(--skip-files "${path}")
  done

  docker run --rm \
    --volume /var/run/docker.sock:/var/run/docker.sock:ro \
    --volume "${cache_dir}:/trivy-cache:ro" \
    --volume "${report_dir}:/trivy-reports" \
    --volume "${repository_root}/.trivyignore.yaml:/trivy-ignore.yaml:ro" \
    "${trivy_image}" image \
      --cache-dir /trivy-cache \
      --ignorefile /trivy-ignore.yaml \
      --cache-backend memory \
      --skip-db-update \
      --skip-java-db-update \
      --skip-version-check \
      --offline-scan \
      --disable-telemetry \
      --image-src docker \
      --no-progress \
      --scanners vuln \
      --pkg-types os,library \
      --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL \
      --list-all-pkgs \
      "${skip_arguments[@]}" \
      --format json \
      --output "/trivy-reports/trivy-${image}-${architecture}.json" \
      --exit-code 0 \
      "${artifact}"
}

scan delivery mtics-al-folio:ci \
  /usr/local/bundle/ruby/3.4.0/gems/http_parser.rb-0.8.1/ext/ruby_http_parser/vendor/http-parser-java/ext/primitives.jar \
  /usr/local/bundle/ruby/3.4.0/gems/concurrent-ruby-1.3.8/lib/concurrent-ruby/concurrent/concurrent_ruby.jar

scan development mtics-devcontainer:ci \
  /usr/share/java/libintl-0.21.jar \
  /usr/share/java/gettext.jar \
  /usr/local/rvm/gems/default/gems/http_parser.rb-0.8.1/ext/ruby_http_parser/vendor/http-parser-java/ext/primitives.jar \
  /usr/local/rvm/gems/default/gems/concurrent-ruby-1.3.7/lib/concurrent-ruby/concurrent/concurrent_ruby.jar
