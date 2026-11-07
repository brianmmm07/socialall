# SocialAll as a Mac app

SocialAll can run like an app on your Mac: its server stays in the background, and it opens in a window of its
own with a Dock icon, instead of a browser tab.

## Start it

Double-click **Start SocialAll** (`Start SocialAll.command`) in the socialall folder.

The first run makes a `.venv` folder and installs SocialAll into it, which takes about a minute. After that it
opens in a second or two. It needs Python 3.11 or newer (from python.org or Homebrew).

The server runs in the background and the app opens in its own window: a Chrome, Edge or Brave app window when
one of those is installed, otherwise your browser. Closing the window, or the Terminal window, doesn't stop the
server.

## Make it feel like an app

1. **Install it.** In the SocialAll window, open the browser menu (⋮) and choose **Install SocialAll**. You get
   `SocialAll.app` in `~/Applications/Chrome Apps`, with the SocialAll icon in Launchpad and Spotlight. Right-click
   its Dock icon and choose **Options → Keep in Dock**. From then on, Start SocialAll opens that app.
2. **Start at login.** Run this once in Terminal from the socialall folder:

   ```bash
   ./Start\ SocialAll.command autostart on
   ```

   The server then starts in the background whenever you log in, so the Dock icon opens SocialAll instantly. It
   uses a LaunchAgent (`~/Library/LaunchAgents/local.socialall.server.plist`). `autostart off` removes it.
3. **Full screen.** Use the window's green button, or press Ctrl+Cmd+F.

## Updating

Run `git pull`, then double-click Start SocialAll. It sees the code changed and restarts the background server on
the new version, and installs new Python packages when `pyproject.toml` changed.

## From the command line

Anything after `Start SocialAll.command` (or `socialall-app`, once installed) is a command:

| Command | What it does |
| --- | --- |
| `launch` | Start the background server if it isn't running, then open the window (the default) |
| `stop` | Stop the background server |
| `restart` | Restart it, for example after editing `.env` |
| `status` | Say whether it's running and whether it starts at login |
| `autostart on`, `off`, `status` | Start SocialAll when you log in |
| `serve` | Run the server with no window (what launch and start at login use) |

Add `--no-window` to launch without opening a window, or `--port 8010` to use another port.

The background server logs to `cache/socialall.log`, and keeps using `.env`, `socialall.db` and `cache/videos/`
in the socialall folder, the same as `socialall serve`. On Linux, `./start-socialall.sh` does the same as the Mac
launcher (start at login is Mac only for now).

## The port

SocialAll uses port 8000 unless `SOCIALALL_PORT` is set in `.env`. Port 8000 is popular with other development
servers: if another program has it, SocialAll picks the next free port and saves it as `SOCIALALL_PORT` in
`.env`. An installed app remembers its address, so if the port changes, install the app again from the new
window.

## Safety

Because the server keeps running, it only answers requests addressed to `localhost` or `127.0.0.1`, and refuses
changes sent from other websites. Only the launcher can stop or restart it.

## If something's off

- **The Mac won't open Start SocialAll** ("unidentified developer" or "Apple could not verify"). Open
  **System Settings → Privacy & Security**, scroll down and click **Open Anyway**. On macOS 14 or older,
  right-click the file and choose **Open**. This only happens when the folder came from a downloaded zip rather
  than `git clone`; `xattr -dr com.apple.quarantine path/to/socialall` clears it too.
- **The Mac asks whether Python may access your Documents or Desktop folder.** The socialall folder is inside it.
  Allow it, or move the folder somewhere like `~/socialall`.
- **Platform keys don't work after it started at login.** Login items don't see your shell's environment
  variables, so put the keys in `.env` rather than exporting them.
- **Removing it.** Run `autostart off` and `stop`, delete `SocialAll.app` from `~/Applications/Chrome Apps`, then
  delete the folder.
