@echo off
docker compose -f "%~dp0..\..\docker-compose.yml" -f "%~dp0..\..\docker-compose.face-dev.yml" -f "%~dp0..\ml-api\compose.yml" -f "%~dp0compose.yml" -f "%~dp0compose.dos.yml" %*
