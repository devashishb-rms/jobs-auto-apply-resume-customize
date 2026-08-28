#!/usr/bin/env bash
# Runs on the EC2 GUI VM itself (invoked by systemd on boot — see
# jobs-auto-apply-daily.service). Repeatedly runs one apply pass per enabled
# platform until every platform has hit DAILY_QUOTA successful applications
# for today, or a safety time budget is exhausted — then shuts the VM down.
#
# Shutting the OS down (not just stopping the process) is what actually stops
# EC2 billing for compute: the instance was launched with
# --instance-initiated-shutdown-behavior stop, so `sudo shutdown -h now`
# transitions it to "stopped" (billing pauses) rather than terminating it
# (which would destroy the disk/Chrome profile). Re-running `aws ec2
# start-instances` boots the same disk back up next time.
set -uo pipefail

APP_DIR="$HOME/jobs-auto-apply-main_v2"
DAILY_QUOTA="${DAILY_QUOTA:-100}"
MAX_RUNTIME_SECONDS="${MAX_RUNTIME_SECONDS:-14400}"   # 4h safety cap regardless of quota
PASS_INTERVAL_SECONDS="${PASS_INTERVAL_SECONDS:-900}"  # 15 min between passes
LOG_FILE="$APP_DIR/data/quota_runner.log"

cd "$APP_DIR" || { echo "APP_DIR not found: $APP_DIR" >&2; exit 1; }
source .venv/bin/activate

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG_FILE"; }

enabled_platforms() {
    python3 - <<'PY'
from pathlib import Path
from jobs_auto_apply.config import load_config
c = load_config(Path("config.yaml"))
names = ["wellfound", "uplers", "naukri", "hirist", "instahyre", "cutshort"]
print(" ".join(n for n in names if getattr(c, n).enabled))
PY
}

# Counts today's (UTC calendar day) confirmed "applied" entries per platform
# from data/applied_jobs.json's history — the same file/schema the app itself
# uses for cross-run dedup, so this reads what actually happened, not a guess.
counts_today_json() {
    python3 - <<'PY'
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

path = Path("data/applied_jobs.json")
if not path.exists():
    print("{}")
    raise SystemExit

data = json.loads(path.read_text(encoding="utf-8") or "{}")
history = data.get("history", [])
today = datetime.now(timezone.utc).date()

# Only the latest entry per job_key counts (matches the app's own dedup logic),
# and only if that latest status is "applied" and happened today.
latest = {}
for entry in history:
    if not isinstance(entry, dict):
        continue
    key = str(entry.get("job_key", "")).strip()
    if key:
        latest[key] = entry

counts = Counter()
for key, entry in latest.items():
    if str(entry.get("status", "")).strip() != "applied":
        continue
    ts = str(entry.get("applied_at", ""))
    try:
        applied_date = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
    except ValueError:
        continue
    if applied_date != today:
        continue
    platform = key.split(":", 1)[0]
    counts[platform] += 1

print(json.dumps(counts))
PY
}

quota_met_for_all() {
    local platforms="$1"
    local counts_json
    counts_json="$(counts_today_json)"
    python3 - "$DAILY_QUOTA" "$counts_json" $platforms <<'PY'
import json
import sys

quota = int(sys.argv[1])
counts = json.loads(sys.argv[2])
platforms = sys.argv[3:]
if not platforms:
    sys.exit(0)  # nothing enabled — nothing to wait for
for p in platforms:
    if counts.get(p, 0) < quota:
        sys.exit(1)
sys.exit(0)
PY
}

PLATFORMS="$(enabled_platforms)"
log "Starting quota runner. Enabled platforms: $PLATFORMS. Daily quota: $DAILY_QUOTA/platform. Max runtime: ${MAX_RUNTIME_SECONDS}s."

START_TS=$(date +%s)
while true; do
    NOW_TS=$(date +%s)
    ELAPSED=$((NOW_TS - START_TS))

    if [ "$ELAPSED" -ge "$MAX_RUNTIME_SECONDS" ]; then
        log "Max runtime reached (${ELAPSED}s) — shutting down regardless of quota."
        break
    fi

    if quota_met_for_all "$PLATFORMS"; then
        log "Daily quota ($DAILY_QUOTA/platform) reached on every enabled platform — shutting down."
        break
    fi

    log "Running apply pass (elapsed ${ELAPSED}s)..."
    python3 main.py run --platform all --verbose >> "$LOG_FILE" 2>&1
    RUN_EXIT=$?
    log "Apply pass finished (exit $RUN_EXIT)."

    if quota_met_for_all "$PLATFORMS"; then
        log "Quota reached after this pass — shutting down."
        break
    fi

    log "Quota not yet met — sleeping ${PASS_INTERVAL_SECONDS}s before next pass."
    sleep "$PASS_INTERVAL_SECONDS"
done

log "Quota runner done. Final counts: $(counts_today_json)"
log "Shutting down VM now."
sudo shutdown -h now
