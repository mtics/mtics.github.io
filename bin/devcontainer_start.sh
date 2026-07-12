#!/usr/bin/env bash
set -euo pipefail

runtime_dir="${XDG_RUNTIME_DIR:-/tmp}"
entrypoint="${DEVCONTAINER_ENTRYPOINT:-./bin/entry_point.sh}"
pid_file="${DEVCONTAINER_PID_FILE:-${runtime_dir}/mtics-jekyll-$(id -u).pid}"
log_file="${DEVCONTAINER_LOG_FILE:-${runtime_dir}/mtics-jekyll-$(id -u).log}"
process_pattern="${DEVCONTAINER_PROCESS_PATTERN:-[j]ekyll serve.*--port(=| )8080}"
health_host="${DEVCONTAINER_HEALTH_HOST:-127.0.0.1}"
health_port="${DEVCONTAINER_HEALTH_PORT:-8080}"
startup_attempts="${DEVCONTAINER_STARTUP_ATTEMPTS:-60}"
startup_interval="${DEVCONTAINER_STARTUP_INTERVAL_SECONDS:-0.5}"
startup_grace="${DEVCONTAINER_STARTUP_GRACE_SECONDS:-0.5}"

process_is_running() {
    local pid="$1"
    local state

    kill -0 "$pid" 2>/dev/null || return 1
    state="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
    [[ -n "$state" && "$state" != Z* ]]
}

process_matches_pattern() {
    local pid="$1"

    ps -o command= -p "$pid" 2>/dev/null | grep -Eq -- "$process_pattern"
}

server_is_ready() {
    local listener_pid

    command -v lsof >/dev/null 2>&1 || return 1
    listener_pid="$(
        lsof -nP -a -p "$server_pid" -iTCP:"$health_port" -sTCP:LISTEN -t 2>/dev/null |
            head -n 1 || true
    )"
    [[ "$listener_pid" == "$server_pid" ]] || return 1

    ruby -rsocket -e 'Socket.tcp(ARGV.fetch(0), Integer(ARGV.fetch(1)), connect_timeout: 0.2) { |socket| socket.close }' \
        "$health_host" "$health_port" >/dev/null 2>&1
}

print_log() {
    [[ -f "$log_file" ]] && cat "$log_file" >&2
}

stop_server() {
    local pid="$1"

    kill -TERM "$pid" 2>/dev/null || true
    for _attempt in 1 2 3 4 5 6 7 8 9 10; do
        process_is_running "$pid" || break
        sleep 0.1
    done
    process_is_running "$pid" && kill -KILL "$pid" 2>/dev/null || true
}

mkdir -p "$(dirname "$pid_file")" "$(dirname "$log_file")"
server_pid=""
started_server=false

if [[ -f "$pid_file" ]]; then
    recorded_pid="$(tr -d '[:space:]' <"$pid_file")"
    if [[ "$recorded_pid" =~ ^[0-9]+$ ]] && \
        process_is_running "$recorded_pid" && \
        process_matches_pattern "$recorded_pid"; then
        server_pid="$recorded_pid"
    else
        rm -f "$pid_file"
    fi
fi

if [[ -z "$server_pid" ]]; then
    existing_pid="$(pgrep -u "$(id -u)" -f "$process_pattern" | head -n 1 || true)"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && \
        process_is_running "$existing_pid" && \
        process_matches_pattern "$existing_pid"; then
        server_pid="$existing_pid"
        printf '%s\n' "$server_pid" >"$pid_file"
    fi
fi

if [[ -z "$server_pid" ]]; then
    nohup "$entrypoint" >"$log_file" 2>&1 &
    server_pid=$!
    started_server=true
    printf '%s\n' "$server_pid" >"$pid_file"
fi

for ((attempt = 1; attempt <= startup_attempts; attempt++)); do
    if ! process_is_running "$server_pid"; then
        status=1
        if [[ "$started_server" == true ]]; then
            wait "$server_pid" || status=$?
            (( status == 0 )) && status=1
        fi
        rm -f "$pid_file"
        printf 'Jekyll failed during devcontainer startup (exit %s):\n' "$status" >&2
        print_log
        exit "$status"
    fi

    if server_is_ready; then
        sleep "$startup_grace"
        if ! process_is_running "$server_pid"; then
            status=1
            if [[ "$started_server" == true ]]; then
                wait "$server_pid" || status=$?
                (( status == 0 )) && status=1
            fi
            rm -f "$pid_file"
            printf 'Jekyll failed during devcontainer startup (exit %s):\n' "$status" >&2
            print_log
            exit "$status"
        fi
        server_is_ready || continue

        if [[ "$started_server" == true ]]; then
            printf 'Jekyll is ready (pid %s, log %s)\n' "$server_pid" "$log_file"
        else
            printf 'Jekyll is already running and ready (pid %s, log %s)\n' "$server_pid" "$log_file"
        fi
        exit 0
    fi

    (( attempt < startup_attempts )) && sleep "$startup_interval"
done

if [[ "$started_server" == true ]]; then
    stop_server "$server_pid"
fi
[[ "$started_server" == true ]] && wait "$server_pid" 2>/dev/null || true
rm -f "$pid_file"
printf 'Jekyll startup timed out before TCP %s:%s became ready:\n' "$health_host" "$health_port" >&2
print_log
exit 1
