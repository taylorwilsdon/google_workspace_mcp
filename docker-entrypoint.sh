#!/bin/sh
# Secure Docker entrypoint: exec-form friendly, no shell-form CMD interpolation.
set -eu

# Preserve any args passed via CMD / `docker run ...`
set -- "$@"

if [ -n "${TOOL_TIER:-}" ]; then
  set -- "$@" --tool-tier "${TOOL_TIER}"
fi

if [ -n "${TOOLS:-}" ]; then
  set -- "$@" --tools
  # TOOLS is a space-separated list of tool group names; quote each token.
  # shellcheck disable=SC2086
  for tool in ${TOOLS}; do
    set -- "$@" "${tool}"
  done
fi

exec uv run main.py "$@"
