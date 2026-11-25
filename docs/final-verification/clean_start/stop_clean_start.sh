#!/usr/bin/env bash
# Stop every process started by run_clean_start.sh for work dir $1 (from its pid files).
CS=${1:-/srv/cleanstart}
[ -f "$CS/pids" ] && for p in $(cat "$CS/pids"); do pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done
[ -f "$CS/clamd.pid" ] && kill "$(cat "$CS/clamd.pid")" 2>/dev/null
redis-cli -p 6390 shutdown nosave >/dev/null 2>&1
[ -d "$CS/pg/data" ] && su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D $CS/pg/data -m fast stop" >/dev/null 2>&1
sleep 2; echo "stopped clean-start stack in $CS"
