#!/usr/bin/env bash
# Your IP appears to rotate frequently (mobile/dynamic ISP connection) — this
# clears any stale IP rules on the security group and re-authorizes SSH (22)
# and RDP (3389) for whatever your current public IP is. Run this any time a
# connection to the VM times out before assuming something else is wrong.
set -euo pipefail
cd "$(dirname "$0")/../../aws-deploy"

REGION="ap-south-2"
SG_ID="$(cat sg_id.txt)"
MY_IP="$(curl -s https://checkip.amazonaws.com)"

OLD_CIDRS="$(aws ec2 describe-security-groups --region "$REGION" --group-ids "$SG_ID" \
    --query "SecurityGroups[0].IpPermissions[].IpRanges[].CidrIp" --output text)"

for CIDR in $OLD_CIDRS; do
    aws ec2 revoke-security-group-ingress --region "$REGION" --group-id "$SG_ID" --protocol tcp --port 22 --cidr "$CIDR" 2>/dev/null || true
    aws ec2 revoke-security-group-ingress --region "$REGION" --group-id "$SG_ID" --protocol tcp --port 3389 --cidr "$CIDR" 2>/dev/null || true
done

aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" --protocol tcp --port 22 --cidr "$MY_IP/32" >/dev/null
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" --protocol tcp --port 3389 --cidr "$MY_IP/32" >/dev/null

echo "Security group now allows SSH+RDP from $MY_IP/32 only."
