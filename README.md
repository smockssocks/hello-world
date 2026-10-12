# DS5 Forge

Free DualSense (PS5 controller) upgrade for Windows PC games: **HD haptics over USB *and* Bluetooth,
adaptive triggers, game-aware racing feedback, and haptics in any game** — no paid apps required.

## What it does

| Feature | USB cable | Bluetooth |
|---|---|---|
| HD haptics (the PS5-style detailed vibration) | ✅ full quality | ✅ experimental (lower sample rate) |
| Classic rumble fallback | ✅ | ✅ |
| Adaptive triggers (resistance, weapon break, vibration, bow…) | ✅ | ✅ |
| Lightbar, player LEDs | ✅ | ✅ |
| **Any game:** game audio → haptics | ✅ | ✅ |
| **Shooter profile:** trigger break while aiming, gunfire kicks back through the trigger | ✅ | ✅ |
| **Xbox-only games:** virtual Xbox pad, game rumble → HD haptics + trigger kick | ✅ | ✅ |
| **Racing telemetry:** ABS & lock-up through L2, wheelspin through R2, kerbs left/right, engine, gear shifts, crashes, shift-light lightbar | ✅ | ✅ |

Racing telemetry is built in for **Forza Horizon 4/5, Forza Motorsport (7 and 2023), EA SPORTS F1 23/24/25,
Assetto Corsa, Assetto Corsa Competizione, BeamNG.drive and Live for Speed**. Profiles switch
automatically when one of these games starts.

## Getting it

**Option A – ready-made .exe (easiest)**
1. On GitHub, open this repository's **Actions** tab → **Build Windows app** → the latest green run.
2. Download the **DS5Forge-windows** artifact, unzip it, run `DS5Forge.exe`.
   (Windows SmartScreen may warn because the exe isn't signed: *More info → Run anyway*.)

**Option B – run from source**
1. Install [Python 3.12](https://www.python.org/downloads/) (tick *Add python.exe to PATH*).
2. Download this repository (Code → Download ZIP), unzip it, double-click **`run.bat`**.
   The first run installs everything into a private `.venv` folder.
3. Optional, for Xbox-only games: double-click **`install-xbox-bridge.bat`** (installs the free ViGEmBus driver).

## First-time checklist

- **Connect the controller** by USB cable or Bluetooth (Windows Settings → Bluetooth → hold
  *Create* + *PS* until the light flashes). The Dashboard should say *DualSense connected*.
- **Haptics Lab tab:** press *Heartbeat* or *Left / right*. You should feel detailed taps, not a buzz.
- **USB only:** if the Dashboard says the controller's sound device has 2 channels, open Windows
  *Sound settings → More sound settings → Speakers (Wireless Controller) → Configure*, choose
  **Quadraphonic**, then reconnect. Channels 3 and 4 are the haptic motors.
- **Keep your headset/speakers as Windows' default playback device.** Plugging in the controller
  sometimes makes Windows switch to it, which breaks game-audio haptics.
- **Bluetooth HD haptics** are experimental. If they don't work on your controller/firmware the app
  falls back to rumble by itself. You can also turn them off on the Dashboard.
- **Steam:** if a game runs through Steam Input, Steam also controls the lightbar/rumble and may fight
  DS5 Forge. For best results open the game's *Properties → Controller* and choose
  *Disable Steam Input* (unless the game needs it).
- **Games with native DualSense support** (e.g. Spider-Man, Returnal, Ghost of Tsushima) already drive the
  haptics and triggers themselves. For those, pick a profile with audio haptics off, or quit DS5 Forge.

## Per-game setup

The **Game setup** tab inside the app lists these steps as well.

- **Forza Horizon / Motorsport:** Settings → HUD and Gameplay → *Data Out* **On**, IP `127.0.0.1`,
  port `5300` (Motorsport: packet format *Dash*). Microsoft Store / Game Pass copies: run
  `allow-forza-localhost.bat` once as administrator.
- **F1 23/24/25:** Settings → Telemetry → UDP Telemetry **On**, IP `127.0.0.1`, port `20777`, send rate 60 Hz.
- **Assetto Corsa / ACC:** nothing to do.
- **BeamNG.drive:** Options → Other → *OutGauge* on, IP `127.0.0.1`, port `4444`.
- **Shooters (any game):** choose the **Shooter (any game)** profile on the *Profiles* tab → *Use when no game runs*.
- **Games that only support Xbox controllers:** choose **Xbox-only game (rumble to haptics)**.

## Making it yours

- **Trigger Lab:** try every trigger effect live with sliders, then *Copy JSON*.
- **Profiles:** each profile is a small JSON file. Edit one in the app and *Save as my profile*. It's
  stored in `%APPDATA%\DS5Forge\profiles` and overrides the built-in profile with the same name.

```json
{
  "name": "My Game",
  "exe": ["MyGame.exe"],
  "audio_haptics": {"enabled": true, "gain": 1.0, "cutoff": 250, "trigger_kick": true},
  "triggers": {"left": {"type": "feedback", "position": 2, "strength": 3}},
  "rules": [{"when": ["l2"], "right": {"type": "weapon", "start": 3, "end": 5, "strength": 7}}],
  "lightbar": [255, 40, 0]
}
```

`rules` apply while buttons are held (e.g. aiming). Trigger types: `feedback`, `slope`, `weapon`,
`vibration`, `bow`, `galloping`, `machine`, `multi_feedback`, `multi_vibration`, `off`.

### Adding full support for a new game

Games that publish telemetry (UDP or shared memory) can get a dedicated integration like the
racing ones: subclass `Integration` in `ds5forge/integrations/`, register it in
`integrations/__init__.py`, and point a profile at it with `"integration": "<id>"`.

## How it works (for the curious)

- `ds5forge/protocol.py` – DualSense HID reports (USB 0x02, Bluetooth 0x31 with CRC32, and the
  Bluetooth audio-haptics report 0x32: 3 kHz stereo 8-bit PCM).
- `ds5forge/haptics/` – a small synthesizer and mixer. The same effects render at 48 kHz for the USB
  sound card or 3 kHz for Bluetooth, or are converted to classic rumble as a fallback.
- `ds5forge/haptics/audio.py` – WASAPI loopback capture of what your PC is playing, low-passed to the
  range the actuators reproduce.
- `ds5forge/integrations/` – telemetry readers (Forza, F1, Assetto, OutGauge).
- `ds5forge/engine.py` – combines everything at 100 Hz; `ds5forge/gui.py` – the window.

Run the tests with `python -m pytest`.

## Credits

- Bluetooth audio-haptics packet format reverse-engineered by
  [SAxense](https://github.com/egormanga/SAxense) (independent reimplementation here).
- Output report layout from the Linux `hid-playstation` driver; trigger effect encodings documented by Nielk1.
- [ViGEmBus](https://github.com/nefarius/ViGEmBus) / [vgamepad](https://github.com/yannbouteiller/vgamepad) for the Xbox bridge.

Not affiliated with Sony. Use at your own risk.
