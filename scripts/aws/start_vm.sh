#!/usr/bin/env bash
# Run this from your Mac to start the jobs-auto-apply GUI VM manually.
# Usage: bash scripts/aws/start_vm.sh
set -euo pipefail
cd "$(dirname "$0")/../../aws-deploy"

REGION="ap-south-2"
INSTANCE_ID="$(cat instance_id.txt)"

STATE="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
    --query "Reservations[0].Instances[0].State.Name" --output text)"

if [ "$STATE" = "running" ]; then
    echo "Already running."
else
    echo "Starting $INSTANCE_ID (current state: $STATE)..."
    aws ec2 start-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null
    aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
fi

PUBLIC_IP="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
    --query "Reservations[0].Instances[0].PublicIpAddress" --output text)"
echo "$PUBLIC_IP" > public_ip.txt

echo ""
echo "VM running at: $PUBLIC_IP"
echo ""
echo "The IP changes on every start (no Elastic IP — saves ~\$3/mo). Update the"
echo "security group to your current IP before connecting if it's been a while:"
echo ""
echo "  MY_IP=\$(curl -s https://checkip.amazonaws.com)"
echo "  SG_ID=\$(cat sg_id.txt)"
echo "  aws ec2 authorize-security-group-ingress --region $REGION --group-id \"\$SG_ID\" --protocol tcp --port 22 --cidr \"\$MY_IP/32\""
echo "  aws ec2 authorize-security-group-ingress --region $REGION --group-id \"\$SG_ID\" --protocol tcp --port 3389 --cidr \"\$MY_IP/32\""
echo ""
echo "SSH:  ssh -i jobs-auto-apply-key.pem ubuntu@$PUBLIC_IP"
echo "RDP:  connect Microsoft Remote Desktop to $PUBLIC_IP:3389 (user: ubuntu)"
