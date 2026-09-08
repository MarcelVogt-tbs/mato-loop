#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output=${1:?Usage: build-native.sh OUTPUT.so}
${CC:-cc} -shared -fPIC -O2 -Wall -Wextra -Werror \
    -I/usr/include/bash -I/usr/include/bash/include -I/usr/include/bash/builtins \
    "$root/src/matoloop_agent/native/shared.c" -o "$output"
# Resolve Readline symbols in Bash, not a second copy of libreadline.
bash --noprofile --norc -c 'enable -f "$1" ml_shared' bash "$output"
