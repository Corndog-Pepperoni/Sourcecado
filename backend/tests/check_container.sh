#!/bin/sh
# The check for piece 3 (the container recipe). Run it with `make check-3`.
# It builds backend/Dockerfile and looks at four things. It needs Docker.
image=sourcecado-starter-check
cd "$(dirname "$0")/.." || exit 1

step() {
    printf '  %-46s ' "$1"
    shift
    if "$@" >/dev/null 2>&1; then echo ok; else echo NO; return 1; fi
}
in_container() { docker run --rm "$image" "$@"; }
runs_as_app() { test "$(in_container id -u)" = 10001; }
can_write_state() { in_container sh -c 'test "$CLUB_STATE_DIR" = /data/club && test -w /data/club'; }
starts_public() { in_container 2>&1 | grep -q "refusing to bind off loopback"; }

echo "Building the image. The first time takes a few minutes."
docker build -q -t "$image" . >/dev/null || { echo "  The image does not build. Run: docker build backend"; exit 1; }
step "the image builds" true &&
step "the user is app, with the id 10001" runs_as_app &&
step "CLUB_STATE_DIR is /data/club, and app can write" can_write_state &&
step "it starts the server on 0.0.0.0" starts_public
