#!/usr/bin/env bash
# Deploy (or roll back to) a git ref:  deploy/deploy.sh [ref]   (default: origin/master)
# Builds the image tagged with the commit SHA, starts it, waits for /api/health, and keeps
# the last 5 images so a rollback to a recent SHA doesn't need a rebuild.
set -euo pipefail
cd "$(dirname "$0")/.."
REF="${1:-origin/master}"

git fetch --quiet origin
git checkout --quiet --detach "$REF"
SHA="$(git rev-parse --short=12 HEAD)"
PREVIOUS="$(cat .image-tag 2>/dev/null || echo none)"
echo "deploying $SHA (was $PREVIOUS)"

export IMAGE_TAG="$SHA"
if ! docker image inspect "docagent-backend:$SHA" >/dev/null 2>&1; then
  docker compose -f docker-compose.prod.yml build migrate
fi
echo "$SHA" > .image-tag
docker compose -f docker-compose.prod.yml up -d --remove-orphans

echo -n "waiting for the API to be healthy"
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$(deploy/dc ps -q api)" 2>/dev/null || true)"
  if [ "$status" = healthy ]; then echo " ok"; break; fi
  echo -n "."; sleep 3
done
if [ "$status" != healthy ]; then
  echo; echo "API not healthy after 3 min (status: ${status:-unknown}); roll back with: deploy/deploy.sh $PREVIOUS" >&2
  exit 1
fi
echo "$(date -u +%FT%TZ) $SHA" >> .deploy-history

# Keep the 5 newest backend images.
docker images docagent-backend --format '{{.CreatedAt}}\t{{.Repository}}:{{.Tag}}' \
  | sort -r | tail -n +6 | cut -f2 | xargs -r docker rmi >/dev/null 2>&1 || true
