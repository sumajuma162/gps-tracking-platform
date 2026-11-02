# Windows 11 development

## Install

1. **WSL2:** in an administrator PowerShell, `wsl --install`, reboot. Ubuntu 24.04 is a good default.
2. **Docker Desktop** with *Use the WSL 2 based engine* on (Settings → General). Under Resources → WSL integration,
   enable your distribution.
3. **Git for Windows**, then, before cloning:
   ```powershell
   git config --global core.autocrlf input
   ```
   The repository's `.gitattributes` forces LF for shell scripts and source files (CRLF only for `*.ps1`), so
   container entrypoints don't fail with `/bin/sh^M: bad interpreter`.
4. **VS Code** with the *Docker*, *Python*, *ESLint* and *WSL* extensions (optional).
5. For running tests outside Docker: Python 3.12 and Node.js 22 (or do it inside WSL).

Cloning inside the WSL file system (`\\wsl$\Ubuntu\home\<you>`) is much faster for Docker bind mounts than
`C:\Users\...`.

## Commands

`scripts\dev.ps1` has a PowerShell equivalent for every `make` target:

| `make` | PowerShell |
| --- | --- |
| `make up` | `.\scripts\dev.ps1 up` (creates `.env` from the example first time and stops so you can edit it) |
| `make down` / `logs` / `ps` | `.\scripts\dev.ps1 down` / `logs` / `ps` |
| `make seed` | `.\scripts\dev.ps1 seed` |
| `make superadmin EMAIL=...` | `.\scripts\dev.ps1 superadmin` (asks for the email) |
| `make test` | `.\scripts\dev.ps1 test` |
| `make lint` | `.\scripts\dev.ps1 lint` |
| `make backup` | `.\scripts\dev.ps1 backup` (writes `backups\gps_<time>.dump`) |
| `make monitoring` | `.\scripts\dev.ps1 monitoring` |
| — | `.\scripts\dev.ps1 secret` (random value for `DJANGO_SECRET_KEY` and passwords) |
| — | `.\scripts\dev.ps1 tracker-test` (Teltonika handshake to port 5027; expects `01`) |

If script execution is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Common problems

| Symptom | Cause and fix |
| --- | --- |
| `exec ./docker-entrypoint.sh: no such file or directory` or `bad interpreter` | CRLF line endings. Set `core.autocrlf input`, then `git rm --cached -r . ; git reset --hard` to re-checkout. |
| `Bind for 0.0.0.0:80 failed: port is already allocated` | IIS, Skype or another web server. Set `HTTP_PORT=8080` in `.env`, or stop the other service (`netstat -ano | findstr :80`). |
| Port 5027 in use | Another tracker server. Set `TELTONIKA_PUBLIC_PORT`. |
| Containers killed, PostgreSQL restarts | WSL memory limit. Create `%UserProfile%\.wslconfig` with `[wsl2]` / `memory=6GB`, then `wsl --shutdown`. |
| Permission denied on mounted files | Clone inside the WSL file system, not under `C:\`. |
| `docker compose` not found | Use Docker Desktop's Compose v2 (`docker compose`, not `docker-compose`). |
| Trackers on the LAN can't connect | Windows Firewall: allow inbound TCP/UDP 5027 for Docker Desktop, or test from the same machine first. |
| `.env` changes ignored | `docker compose up -d` again (environment is read when containers are created). |
