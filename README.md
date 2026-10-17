# Oma-do-wa

An Omarchy shell bar widget that monitors only the docker-compose stacks you pick.

- **Add** a compose file with the **+** button in the panel header (or press `a`) — opens the desktop file chooser.
- Each stack lists its containers with **CPU**, **RAM** and **disk** (container writable
  layer; hover a row for the image size). Refreshes every `refreshIntervalSec` while open;
  disk sizes are recomputed at most once a minute because they are slow to measure.
- An override file next to the compose file (`compose.override.yaml` for `compose.yaml`,
  `docker-compose.override.yml` for `docker-compose.yml`, and so on) is applied, just as a
  plain `docker compose` run would.
- **Check updates** (circular-arrow button) compares each service's local image digest with the registry
  (`docker buildx imagetools inspect`). Digest-pinned images and locally built services
  are reported but never updated.
- **Update** (download-arrow button) appears when updates exist: runs `docker compose pull --ignore-buildable`,
  then `docker compose up -d` for the services that are currently running. Of those, only
  containers whose image or config changed are recreated. Stopped services stay stopped (unless a
  running service depends on them), and orphan containers are never removed.
- **Stop watching** (trash-can button) stops monitoring the stack (containers are untouched).

Right-click the bar icon to refresh. A dot on the icon means cached checks found updates.

Watched files and the last check results are kept in
`~/.local/state/ikpvk.oma-do-wa/state.json`.

This plugin used to be called Compose Monitor (`nausica.compose-monitor`). If you have that
installed, remove it and install this one. Your watch list is read from the old
`~/.local/state/nausica.compose-monitor` folder until the plugin first saves to the new one.

Requires: `docker` with the compose and buildx plugins, your user in the `docker` group,
`python3`.

`python3` needs no extra install on Omarchy: `omarchy` depends on `uwsm`, which depends on
`python`. The helper (`compose.py`) uses only the standard library, so there is nothing to
`pip install`.

IPC: `omarchy-shell ikpvk.oma-do-wa toggle|refresh|updates`

## Tests

```bash
python3 -m unittest discover -s tests
node tests/test_model.js
```

## Install

```bash
omarchy plugin add https://github.com/ikpvk/oma_do_wa.git --enable
```

## Uninstall

1. Remove the plugin (this also takes it off the bar):

   ```bash
   omarchy plugin remove ikpvk.oma-do-wa
   ```

2. `remove` keeps a backup of the plugin folder. Delete it if you don't need it:

   ```bash
   rm -rf ~/.config/omarchy/plugins/.ikpvk.oma-do-wa.bak.*
   ```

3. Optionally delete the saved watch list and update-check results:

   ```bash
   rm -rf ~/.local/state/ikpvk.oma-do-wa
   ```

Uninstalling never touches your compose stacks — containers, images and volumes stay as they are.
