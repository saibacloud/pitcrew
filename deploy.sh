#!/bin/bash
set -e

cd /opt/pitcrew
docker build --no-cache -t pitcrew:latest .
docker stack deploy -c docker-compose.yml pitcrew
docker service update --force pitcrew_pitcrew

echo "PitCrew Deployed"