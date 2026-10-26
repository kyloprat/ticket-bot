#!/usr/bin/env bash
# Build a git-free copy of the relay and deploy it to Vercel ("other" account).
# A git-free copy avoids Vercel Hobby blocking CLI deploys whose commit author isn't a team member.
#   usage: ./deploy-relay.sh            (production deploy)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$(mktemp -d)"
mkdir -p "$out/api/_lib"
cp "$here/api/telegram.py" "$out/api/"
cp "$here"/{relay,chat,district,main}.py "$here/cinemas.json" "$out/api/_lib/"
cat > "$out/pyproject.toml" <<'TOML'
[project]
name = "bms-ticket-relay"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["requests>=2.32", "tzdata"]

[tool.vercel]
entrypoint = "api.telegram:handler"
TOML
cfg="${VERCEL_CONFIG:-$APPDATA/com.vercel.cli-other}"
cd "$out"
if [ -f "$here/.vercel/project.json" ]; then
  mkdir -p .vercel && cp "$here/.vercel/project.json" .vercel/
else
  vercel link --yes --project bms-ticket-relay --global-config "$cfg"
  mkdir -p "$here/.vercel" && cp .vercel/project.json "$here/.vercel/"
fi
vercel deploy --prod --yes --global-config "$cfg"
