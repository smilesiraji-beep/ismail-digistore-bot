#!/usr/bin/env sh
set -eu
python healthcheck.py &
exec python bot.py
