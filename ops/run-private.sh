#!/usr/bin/env bash
# Run as root on the selected deployment host after building leadsgen:0.2.0.
# Creates a PRIVATE candidate only; does not change public routing or the mail service.
set -euo pipefail
if docker container inspect leadsgen-private >/dev/null 2>&1; then
  echo 'leadsgen-private already exists; inspect it before replacing.' >&2
  exit 1
fi
docker image inspect leadsgen:0.2.0 >/dev/null
install -d -m 0700 -o 10001 -g 10001 /srv/leadsgen-data
install -d -m 0700 /srv/leadsgen-config
if [ ! -f /srv/leadsgen-config/private.env ]; then
  umask 077
  {
    printf 'LEADSGEN_PROXY_KEY='
    openssl rand -hex 32
    printf 'LEADSGEN_ALLOWED_HOSTS=localhost,127.0.0.1\n'
  } > /srv/leadsgen-config/private.env
fi
docker run -d --name leadsgen-private --restart unless-stopped \
  --read-only --tmpfs /tmp:rw,nosuid,noexec,size=64m \
  --security-opt no-new-privileges --cap-drop ALL --pids-limit 100 \
  --memory 512m --cpus 1 \
  --env-file /srv/leadsgen-config/private.env \
  -p 127.0.0.1:8910:8910 -v /srv/leadsgen-data:/data \
  --log-opt max-size=10m --log-opt max-file=3 leadsgen:0.2.0
