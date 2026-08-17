#!/usr/bin/env bash
# app/plugins/autogrid360/scripts/dev.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST_ROOT="$(cd "$PLUGIN_ROOT/../../.." && pwd)"

fail() {
    printf 'AutoGrid360 development error: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'USAGE'
Usage: ./scripts/dev.sh <command>

Commands:
  build   Build the Flask-AAS host image with AutoGrid360 included.
  run     Run the previously built AutoGrid360 development image.
  shell   Open a shell in the previously built development image.
  help    Show this help.

Environment overrides:
  AUTOGRID360_DEV_IMAGE      Docker image name (default: autogrid360-dev:local).
  AUTOGRID360_DEV_CONTAINER  Docker container name (default: autogrid360-dev).
  AUTOGRID360_DEV_PORT       Host port mapped to container port 5000 (default: 5000).
USAGE
}

COMMAND="${1:-help}"
if [[ "$COMMAND" == "help" || "$COMMAND" == "-h" || "$COMMAND" == "--help" ]]; then
    usage
    exit 0
fi

EXPECTED_PLUGIN_ROOT="$HOST_ROOT/app/plugins/autogrid360"
[[ "$PLUGIN_ROOT" == "$EXPECTED_PLUGIN_ROOT" ]] || fail \
    "AutoGrid360 must be located at $EXPECTED_PLUGIN_ROOT (current: $PLUGIN_ROOT)."

[[ -f "$HOST_ROOT/Dockerfile" ]] || fail \
    "Flask-AAS Dockerfile not found at $HOST_ROOT/Dockerfile. Set up Flask-AAS first."
[[ -f "$HOST_ROOT/entrypoint.sh" ]] || fail \
    "Flask-AAS entrypoint not found at $HOST_ROOT/entrypoint.sh. Set up Flask-AAS first."
[[ -f "$HOST_ROOT/.env" ]] || fail \
    "Flask-AAS .env not found at $HOST_ROOT/.env. Configure the Flask-AAS development environment first."
command -v docker >/dev/null 2>&1 || fail "Docker is required."

IMAGE="${AUTOGRID360_DEV_IMAGE:-autogrid360-dev:local}"
CONTAINER="${AUTOGRID360_DEV_CONTAINER:-autogrid360-dev}"
PORT="${AUTOGRID360_DEV_PORT:-5000}"

require_image() {
    docker image inspect "$IMAGE" >/dev/null 2>&1 || fail \
        "Development image '$IMAGE' does not exist. Run './scripts/dev.sh build' first."
}

case "$COMMAND" in
    build)
        docker build \
            -f "$HOST_ROOT/Dockerfile" \
            -t "$IMAGE" \
            "$HOST_ROOT"
        ;;
    run)
        require_image
        docker run --rm \
            --name "$CONTAINER" \
            -p "$PORT:5000" \
            --env-file "$HOST_ROOT/.env" \
            "$IMAGE"
        ;;
    shell)
        require_image
        docker run --rm -it \
            --entrypoint /bin/bash \
            --env-file "$HOST_ROOT/.env" \
            "$IMAGE"
        ;;
    *)
        usage >&2
        fail "Unknown command: $COMMAND"
        ;;
esac
