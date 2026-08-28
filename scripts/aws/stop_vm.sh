#!/usr/bin/env bash
# Run this from your Mac to stop the jobs-auto-apply GUI VM manually (e.g. if
# you want to end a session early, before the auto-shutdown quota is hit).
# Usage: bash scripts/aws/stop_vm.sh
set -euo pipefail
cd "$(dirname "$0")/../../aws-deploy"

REGION="ap-south-2"
INSTANCE_ID="$(cat instance_id.txt)"

STATE="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
    --query "Reservations[0].Instances[0].State.Name" --output text)"

if [ "$STATE" != "running" ]; then
    echo "Not running (state: $STATE) — nothing to stop."
    exit 0
fi

echo "Stopping $INSTANCE_ID..."
aws ec2 stop-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null
aws ec2 wait instance-stopped --region "$REGION" --instance-ids "$INSTANCE_ID"
echo "Stopped. Compute billing paused (EBS storage still bills a small amount while stopped)."
