# Stream Mouse Overlay

Windows desktop overlay for screen recording, tutorials, live streams, and presentations.

## Install

```powershell
python -m pip install -r requirements.txt
```

## Run

```powershell
python stream_mouse.py
```

Pick the monitor you want to use, then click **Start overlay**.

If OBS Studio has WebSocket enabled on the default `127.0.0.1:4455`, the keyboard HUD shows stream status and the current scene. If OBS WebSocket uses a password, set it before running:

```powershell
$env:OBS_WEBSOCKET_PASSWORD="your-password"
python stream_mouse.py
```

## Shortcuts

- `Ctrl+F1`: start or stop mouse path recording. Stopping draws the path as a red dashed line.
- `Ctrl+F2`: freeze the selected screen and enter drawing mode.
- `Ctrl+F3`: enter magnifier mode.
- `Esc`: return to normal mode and clear temporary freeze/magnifier state.
- Drawing mode colors: `R` red, `Y` yellow, `G` green, `B` blue, `K` black, `W` white.
- Mouse wheel in magnifier mode: zoom from 1x to 6x.

## Notes

- The tool targets Windows.
- The overlay applies only to the monitor selected at startup.
- Freeze mode uses a screenshot plus an input-capturing overlay. It does not pause the underlying application process.
- The keyboard HUD displays recent typed input and special keys. Avoid running it while entering sensitive passwords or secrets.
- The keyboard HUD can be dragged anywhere on the selected monitor. Its background appears only while the mouse is over it.
- The OBS status badge shows `LIVE` and the current scene when OBS WebSocket is reachable; otherwise it shows `OFFLINE`.
