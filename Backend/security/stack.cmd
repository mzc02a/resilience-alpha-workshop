@echo off
rem Always use the complete protected stack. Does not remove orphan containers.
docker compose -f "%~dp0..\..\docker-compose.yml" -f "%~dp0..\..\docker-compose.face-dev.yml" -f "%~dp0..\ml-api\compose.yml" -f "%~dp0compose.yml" %*
