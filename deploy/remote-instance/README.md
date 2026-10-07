# Remote instance deployment package

Scripts and templates for standing up an AgentFlow instance that a "master"
instance federates with (app/federation/, app/instances/). Full walkthrough:
`docs/REMOTE_INSTANCE_SETUP.md`.

- `setup.sh` -- run once (and after pulling updates) on the remote machine:
  builds the venv, installs dependencies, generates/reuses a federation
  token, and writes `agentflow.env` + a ready-to-install systemd unit.
- `agentflow.env.example` -- documents what `setup.sh` writes to
  `agentflow.env` (gitignored -- it holds the federation token).
- `run-foreground.sh` -- start the instance in a terminal.
- `agentflow-remote.service` -- systemd unit template; `setup.sh` fills in
  the real paths as `agentflow-remote.generated.service` (also gitignored).
