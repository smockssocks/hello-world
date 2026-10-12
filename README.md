# DS5 Forge

Free DualSense (PS5 controller) upgrade for Windows PC games: **HD haptics over USB *and* Bluetooth,
adaptive triggers, and support for the free DualSense game mods that were made for the paid DSX app**.
No purchases required.

Built for: **Kingdom Hearts Birth by Sleep** (and the rest of 1.5+2.5 / 2.8), **Kingdom Hearts III**,
**Skyrim Special Edition / Anniversary**, **Cyberpunk 2077**, and any other game via profiles.

## What you get

| | USB cable | Bluetooth |
|---|---|---|
| HD haptics (PS5-style detailed vibration) | ✅ full quality | ✅ experimental |
| Game rumble → HD haptics (Kingdom Hearts, Skyrim, any Xbox-controller game) | ✅ | ✅ |
| Game sound → HD haptics, with crisp taps on hits | ✅ | ✅ |
| Adaptive triggers from DSX mods (Skyrim, Cyberpunk, Fallout 4, RDR2…) | ✅ | ✅ |
| Your own trigger effects per game (Trigger Lab + profiles) | ✅ | ✅ |
| Lightbar, player LEDs, mic LED | ✅ | ✅ |
| Auto-switches profile when a game starts | ✅ | ✅ |

## Install

**Option A – ready-made .exe (easiest)**
1. On GitHub open this repository's **Actions** tab → **Build Windows app** → newest green run →
   download **DS5Forge-windows**, unzip, run `DS5Forge.exe`.
   (SmartScreen may warn because the exe isn't signed: *More info → Run anyway*.)
2. Install **ViGEmBus** (free driver that lets DS5 Forge create a virtual Xbox controller – needed for
   Kingdom Hearts and Skyrim): <https://github.com/nefarius/ViGEmBus/releases> → `ViGEmBus_*_x64_x86_arm64.exe`.

**Option B – run from source**
1. Install [Python 3.12](https://www.python.org/downloads/) (tick *Add python.exe to PATH*).
2. Download this repository (Code → Download ZIP), unzip, double-click **`run.bat`**.
3. Double-click **`install-xbox-bridge.bat`** once (it installs the virtual Xbox controller support
   and offers the ViGEmBus driver installer).

**Optional: HidHide** (<https://github.com/nefarius/HidHide/releases>). Only needed if a game reacts to
every button twice (it sees the real DualSense *and* the virtual Xbox pad). In HidHide: add
`DS5Forge.exe` (or `pythonw.exe` for option B) under *Applications*, then tick your DualSense under
*Devices*. If DS5 Forge isn't in the Applications list, it can't see the controller either.

## First run

1. Connect the controller by USB or Bluetooth (Windows Settings → Bluetooth → hold *Create* + *PS* until
   the light flashes). The Dashboard should say *DualSense connected*.
2. **Haptics Lab** tab → press *Heartbeat* and *Left / right*. You should feel distinct taps.
3. USB only: if the Dashboard says the controller's sound device has 2 channels, open Windows *Sound
   settings → More sound settings → Speakers (Wireless Controller) → Configure* → **Quadraphonic**.
4. Keep your headset/speakers as Windows' default playback device (Windows sometimes switches to the
   controller when it's plugged in, which stops the sound-based haptics).
5. **Turn Steam Input OFF** for each game (Steam → right-click game → Properties → Controller →
   *Disable Steam Input*). Otherwise Steam fights DS5 Forge for the controller.

## Your games

### Kingdom Hearts Birth by Sleep / 1.5+2.5 / 2.8
- Profile switches on automatically when the game starts.
- DS5 Forge presents a virtual Xbox controller, so the game works normally, and **turns the game's
  rumble into HD haptics**: big hits and bosses give a deep rumble, quick taps stay crisp.
  The game's sound adds taps on keyblade hits and magic.
- Triggers are free by default. If you want resistance, try effects in the **Trigger Lab** and save them
  into the profile.

### Kingdom Hearts III
- Same as above. If auto-detection doesn't notice KH3 starting, open **Profiles → Kingdom Hearts III →
  Add running game…** while the game is running, pick its .exe, then *Save as my profile*.

### Skyrim Special Edition / Anniversary
1. Install [SKSE](https://skse.silverlock.org/) and the **"Skyrim SE Dual Sense Adaptive Trigger Mod" by
   dvize** from Nexus Mods. Skip its DSX instructions: DS5 Forge accepts the same messages.
2. Keep **Accept DSX game mods** on (Dashboard).
3. Play. The mod sets the triggers (bow draw, blocking, spellcasting…); Skyrim's rumble and sound become HD haptics.

### Cyberpunk 2077
- Cyberpunk supports the DualSense itself, so the profile is **hands-off**: DS5 Forge doesn't touch the
  triggers or lights, and the game drives them.
- **Wired (USB):** the game also does its own HD haptics; DS5 Forge stays out of it.
- **Wireless:** the game can't send HD haptics over Bluetooth, so DS5 Forge adds them from the game's sound.
- **Want triggers over Bluetooth, or more trigger effects?** Install
  [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) and the
  **"DualSense Adaptive Trigger Effects"** mod (Nexus). DS5 Forge finds the mod's trigger file inside
  `bin/x64/plugins/cyber_engine_tweaks/mods` automatically and applies it. Skip any DSX steps.

### Any other game
- **Game supports Xbox controllers only:** pick **Xbox-only game (rumble to haptics)** on the Profiles tab.
- **Shooters:** pick **Shooter (any game)**. Aiming (L2) gives R2 a gun-trigger break, and gunfire in the
  game's sound kicks back through the trigger.
- **Has a DSX mod on Nexus?** Install it, skip the DSX part, and it works with DS5 Forge.
- **Has native DualSense support?** Copy the Cyberpunk profile, change the name and .exe.

## Make your own profiles

**Trigger Lab:** try every trigger effect live with sliders, then *Copy JSON*.
**Profiles tab:** edit a profile's JSON and *Save as my profile* (stored in `%APPDATA%\DS5Forge\profiles`;
your profiles win over built-in ones for the same game).

```json
{
  "name": "My Game",
  "exe": ["MyGame.exe"],
  "audio_haptics": {"enabled": true, "gain": 1.0, "cutoff": 250, "hits": true, "trigger_kick": false},
  "xbox_bridge": {"enabled": true},
  "triggers": {"left": {"type": "feedback", "position": 2, "strength": 3}},
  "rules": [{"when": ["l2"], "right": {"type": "weapon", "start": 3, "end": 5, "strength": 7}}],
  "lightbar": [255, 40, 0]
}
```

| Key | Meaning |
|---|---|
| `exe` | Process names that switch this profile on |
| `audio_haptics` | Game sound → haptics (`gain`, `cutoff` Hz, `hits` = extra taps on sharp sounds, `trigger_kick` = held trigger kicks on gunfire) |
| `xbox_bridge` | Virtual Xbox controller; game rumble → HD haptics (`trigger_rumble` echoes strong rumble into held triggers) |
| `triggers` / `rules` | Static trigger effects, and effects while buttons are held (`l2`, `r1`, `cross`…) |
| `hands_off` | Don't touch triggers/lights (game has native DualSense support). Mods can still set them |
| `haptics` | `auto`, `off`, or `bluetooth_only` |
| `mod_text_search` | Folder (relative to the game's .exe) holding a DSX text-file mod's trigger file |

Trigger types: `feedback`, `slope`, `weapon`, `vibration`, `bow`, `galloping`, `machine`,
`multi_feedback`, `multi_vibration`, `off`.

## How it works

- `ds5forge/protocol.py`: DualSense HID reports (USB 0x02, Bluetooth 0x31 with CRC32, and the Bluetooth
  audio-haptics report 0x32: 3 kHz stereo 8-bit PCM).
- `ds5forge/haptics/`: synthesizer and mixer. The same effects render at 48 kHz for USB, 3 kHz for
  Bluetooth, or as classic rumble.
- `ds5forge/dsx_server.py`: DSX-compatible mod server (UDP 127.0.0.1:6969, JSON instructions, plus the
  older text-file mode).
- `ds5forge/layers.py`: game-sound haptics, hit taps, and the virtual Xbox controller bridge.
- `ds5forge/integrations/`: telemetry readers for racing games (Forza, F1, Assetto, BeamNG). Unused by
  default, but available if you add a profile with `"integration": "forza"` and similar.
- `ds5forge/engine.py`: combines everything at 100 Hz. `ds5forge/gui.py` is the window.

Run the tests with `python -m pytest`.

## Credits

- Bluetooth audio-haptics packet format reverse-engineered by
  [SAxense](https://github.com/egormanga/SAxense) (independent reimplementation here).
- DSX mod protocol from [DualSenseX](https://github.com/Paliverse/DualSenseX)'s published UDP example and
  [DualSenseY-v2](https://github.com/WujekFoliarz/DualSenseY-v2) (MIT).
- Output report layout from the Linux `hid-playstation` driver; trigger encodings documented by Nielk1.
- [ViGEmBus](https://github.com/nefarius/ViGEmBus) / [vgamepad](https://github.com/yannbouteiller/vgamepad) for the Xbox bridge.

Not affiliated with Sony. Don't use mods in online games.
