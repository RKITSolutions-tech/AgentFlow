# Remote Instance Setup (Federated Session Dashboard)

How to stand up a second AgentFlow instance on another machine (e.g. a
Windows server) and drive its sessions from this ("master") instance's UI,
without opening the other instance's UI directly. Implements the federation
layer in `app/federation/` (the API a remote instance exposes) and
`app/instances/` (the registry + proxy UI on the master). See
`docs/CLOUDCLI_GAP_ANALYSIS.md` G11 for why this is sensitive and why it
stays off by default.

## What this does and doesn't do

- Does: list a remote instance's existing Projects, list/start/send-to/stop
  its agent sessions, and show their output -- all from the master's UI.
  Also set up a wiki folder on the remote machine as a project knowledge store
  that sessions here can search and write (Wiki → wiki
  folders; docs/WIKI_INTEGRATION_AND_PRESENTATION.md §14).
- Doesn't: create Projects/repositories remotely (do that once on the
  remote instance's own UI), or federate Backlog/Sprints/Pipelines/Ralph/Runs
  (open that instance directly for those, for now).

## 0. Decide how the remote instance runs

Nothing in this codebase has been run on native Windows -- `HostExecutionProvider`
assumes POSIX subprocess/signal behaviour and `run.sh` is bash. If the remote
machine is Windows, run AgentFlow inside **WSL2** there, not native Windows
Python. Everything below assumes a POSIX shell on the remote machine (a
native Linux/macOS box, or WSL2 on Windows).

## 1. Network

The master instance needs to reach the remote instance's AgentFlow port
(5000 by default) over a **private** network -- Tailscale is the easiest
fit (both machines get a stable address without extra firewall work). Plain
LAN works too if both machines are already on one you trust. Decide this
address now; it's used twice below as `<host-address>`.

## 2. On the remote machine

```bash
git clone <this repo's url> agentflow   # or pull an existing checkout
cd agentflow
deploy/remote-instance/setup.sh <host-address>
```

`<host-address>` is the Tailscale IP or LAN hostname from step 1 -- not
`localhost`. By default the allowed project root is the parent of this
checkout; pass one or more paths to override:

```bash
deploy/remote-instance/setup.sh <host-address> /srv/projects /home/me/code
```

This creates a venv, installs dependencies, generates a federation token
(reused on re-run, so pulling updates and re-running is safe), and writes
`deploy/remote-instance/agentflow.env` (gitignored -- see
`agentflow.env.example` for what's in it) plus a ready-to-install systemd
unit. It prints the token and base URL you'll need in step 4 -- note them
down now.

Start it:

```bash
deploy/remote-instance/run-foreground.sh        # foreground, Ctrl-C to stop
```

or, to survive logout/reboot (needs `systemd=true` in WSL2's `/etc/wsl.conf`
if that applies to you):

```bash
sudo cp deploy/remote-instance/agentflow-remote.generated.service /etc/systemd/system/agentflow-remote.service
sudo systemctl enable --now agentflow-remote
```

## 3. Create a Project on the remote instance

Open `http://<host-address>:5000/` **on/from the remote machine's network**
and create a Project pointing at a repository under one of the allowed
roots from step 2, the same way you would on any AgentFlow instance. The
federation layer only lists/drives sessions on Projects that already exist
-- it has no remote "create project" call.

## 4. Verify reachability

From the master machine:

```bash
curl -H "Authorization: Bearer <token>" http://<host-address>:5000/federation/api/ping
```

should return `{"status": "ok", "version": "agentflow-federation-1"}`. If
it doesn't, fix connectivity/firewall before step 5 -- the registry page
will just show "UNREACHABLE" without more detail than that.

## 5. Register it on the master

On the master instance's UI: **Remote Instances** (sidebar) → fill in a
name, `http://<host-address>:5000` as the base URL, and the token from
step 2 → Add. The list should show **ONLINE**; click through to Projects →
Sessions to start driving a session there.

## Troubleshooting

- **UNREACHABLE on the master, curl in step 4 worked**: the token in the
  registry (master) doesn't match `AGENTFLOW_FEDERATION_TOKEN` on the
  remote -- re-add the instance with the exact token from
  `deploy/remote-instance/agentflow.env`.
- **Remote instance won't start / refuses to bind**: `AGENTFLOW_HOST` must
  resolve to the address itself, and `AGENTFLOW_ALLOW_UNSAFE_BIND=1` must be
  set -- `setup.sh` writes both, so this usually means `agentflow.env`
  wasn't sourced (check you're using `run-foreground.sh`/the systemd unit,
  not a bare `python wsgi.py`).
- **403/path errors creating a Project on the remote**: the repository path
  must be under one of the roots passed to `setup.sh`
  (`AGENTFLOW_ALLOWED_ROOTS`, checked by `app/security.py`).
