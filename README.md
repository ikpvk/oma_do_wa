# Compose Monitor

An Omarchy shell bar widget that monitors only the docker-compose stacks you pick.

- **Add** a compose file with the **+** button in the panel header (or press `a`) — opens the desktop file chooser.
- Each stack lists its containers with **CPU**, **RAM** and **disk** (container writable
  layer; hover a row for the image size). Refreshes every `refreshIntervalSec` while open.
- **Check updates** (circular-arrow button) compares each service's local image digest with the registry
  (`docker buildx imagetools inspect`). Digest-pinned images and locally built services
  are reported but never updated.
- **Update** (download-arrow button) appears when updates exist: runs `docker compose pull --ignore-buildable`
  then `docker compose up -d --remove-orphans`, which recreates only changed containers.
- **Stop watching** (trash-can button) stops monitoring the stack (containers are untouched).

Right-click the bar icon to refresh. A dot on the icon means cached checks found updates.

Watched files and the last check results are kept in
`~/.local/state/nausica.compose-monitor/state.json`.

Requires: `docker` with the compose and buildx plugins, your user in the `docker` group,
`python3`.

IPC: `omarchy-shell nausica.compose-monitor toggle|refresh|updates`

## Install

```bash
omarchy plugin add https://github.com/ikpvk/oma_do_wa.git --enable
```

## Uninstall

1. Remove the plugin (this also takes it off the bar):

   ```bash
   omarchy plugin remove nausica.compose-monitor
   ```

2. `remove` keeps a backup of the plugin folder. Delete it if you don't need it:

   ```bash
   rm -rf ~/.config/omarchy/plugins/.nausica.compose-monitor.bak.*
   ```

3. Optionally delete the saved watch list and update-check results:

   ```bash
   rm -rf ~/.local/state/nausica.compose-monitor
   ```

Uninstalling never touches your compose stacks — containers, images and volumes stay as they are.
