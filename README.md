# Meeting Recorder

A desktop app for Windows and macOS that records your meetings and turns them into transcripts, the way Otter, Read.ai and MeetGeek do. It has no bot joining the call and no cloud upload. The audio and transcripts stay on your computer.

It doesn't summarize anything. Click **Copy transcript** and paste it into whichever AI you prefer (ChatGPT, Claude, Copilot, Gemini, a local model).

## What it does

- Records two tracks at once: your microphone, and the system audio (everyone else on the call). It works with any meeting app, because it captures what your speakers or headset play.
- Transcribes locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), a faster reimplementation of OpenAI's Whisper speech model. Nothing is sent over the network except the one-time model download.
- Labels each line as you or the other side, based on which track the speech came from.
- Watches for Zoom, Teams, Webex, Slack, Discord or a browser (Google Meet) turning on your mic, and pops up "Record this meeting?". If you started recording from that prompt, it stops on its own about 20 seconds after the app releases the mic.
- Keeps running when you close the window, so it can keep watching for meetings. On Windows it lives in the system tray by the clock: the icon turns red while recording, and right-clicking it lets you start or stop a recording, open the window, or quit. On a Mac it stays in the Dock like other Mac apps: click the Dock icon to reopen the window, and press Cmd+Q to quit.
- Reminds you, each time a recording starts, to tell participants you're recording and to follow the laws and policies that apply. The recording is already running while the reminder is open, so nothing is lost. Tick "Don't show this again" to turn it off.
- Saves each meeting in its own folder under `Documents\Meetings`:

| File | Use |
|---|---|
| `transcript.txt` | Plain text with timestamps. Paste this into an AI tool. |
| `transcript.md` | Formatted version for notes apps. |
| `transcript.srt` | Subtitles. Open `meeting.wav` in VLC with this file to follow along. |
| `transcript.json` | Segments with start/end times, for scripts. |
| `meeting.wav` | Both sides mixed together for playback. |
| `mic.wav`, `system.wav` | The raw tracks. |

## Install

### Windows

Download `MeetingRecorder-Setup-<version>.exe` from the repository's **Releases** page and run it. It installs for your user only, so it doesn't ask for admin rights. It adds Start menu and desktop shortcuts, and it sets the app to start with Windows in the tray so it can catch meetings as they start. The installer's options page lets you untick either one. Uninstall it from Settings > Apps like any other program.

Windows SmartScreen may say "Windows protected your PC" the first time, because the exe isn't code-signed. Click **More info**, then **Run anyway**.

Prefer no installer? Download `MeetingRecorder-portable-<version>.zip` instead, unzip it anywhere, and run `MeetingRecorder.exe`. Keep the files next to the exe together.

### macOS

You need macOS 13 (Ventura) or later. Download the disk image that matches your Mac from the **Releases** page: `MeetingRecorder-mac-arm64-<version>.dmg` for Apple silicon (M1 and later), or `MeetingRecorder-mac-x86_64-<version>.dmg` for Intel. Open it and drag **Meeting Recorder** into Applications.

The app isn't signed with an Apple Developer ID yet, so macOS blocks the first launch. Open it once, then go to System Settings > Privacy & Security, scroll down, and click **Open Anyway**.

The app asks for two permissions:

1. **Microphone**, the first time you record. This is your side of the call.
2. **Screen & System Audio Recording**, also on the first recording. This is how it hears the other people on the call. macOS files system audio under screen recording, but the app only keeps the audio. After you turn it on, macOS asks you to quit and reopen the app. If you clicked Don't Allow, the app offers to open the right page in System Settings.

On macOS 15 and later, macOS asks now and then whether Meeting Recorder should keep that permission. Click **Allow** to keep recording both sides.

At its first launch the app sets itself to start when you log in, opening hidden in the Dock so it can catch meetings. Turn that off in Settings. Meeting detection needs macOS 14 (Sonoma) or later. On macOS 13 you start recordings yourself.

### Both platforms

The first transcription downloads the Whisper model. "small", the default, is about 500 MB.

### Where the builds come from

GitHub Actions workflows build everything on real machines for every push: `.github/workflows/build-windows.yml` makes the Windows installer and zip, and `.github/workflows/build-macos.yml` makes a disk image for Apple silicon and one for Intel. Before a workflow keeps a build, it runs the finished app in self-test mode against a spoken test sentence. Builds from ordinary pushes appear under the workflow run's **Artifacts**. Pushing a tag such as `v0.1.0` also publishes them as a release:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

### Build the Windows app yourself

1. Install Python 3.10, 3.11 or 3.12 from [python.org](https://www.python.org/downloads/windows/) and tick "Add python.exe to PATH".
2. Open `scripts\install.ps1` in PowerShell ISE and run it (F5). This sets up a `.venv` folder and a desktop shortcut that runs the app from source.
3. Open `scripts\build_exe.ps1` in ISE and run it. The app lands in `dist\MeetingRecorder\MeetingRecorder.exe`. If [Inno Setup 6](https://jrsoftware.org/isdl.php) is installed, the script builds the installer too.

If ISE refuses to run the scripts, run this in the ISE console once, then try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Build the Mac app yourself

Install the Xcode command line tools (`xcode-select --install`) and Python 3.10 to 3.12, then run `./scripts/build_macos.sh` in Terminal. It compiles the ScreenCaptureKit helper, builds `dist/Meeting Recorder.app`, self-tests it, and makes the `.dmg`. To run from source instead, use `./scripts/build_macos.sh helper`, then `.venv/bin/python -m meeting_recorder`. From source, macOS attributes the permissions to Terminal instead of Meeting Recorder.

## Use

1. Type a title, or leave it blank, and click **Record**. The two level bars show that the mic and the system audio are both coming in.
2. Click **Stop** when the meeting ends. Transcription starts in the background.
3. Pick the meeting in the list and click **Copy transcript**.

From a source install you can also run it in a terminal:

```powershell
.venv\Scripts\python -m meeting_recorder devices            # list mics and speakers
.venv\Scripts\python -m meeting_recorder record -t "1:1"    # record until Ctrl+C, then transcribe
.venv\Scripts\python -m meeting_recorder transcribe "C:\Users\you\Documents\Meetings\2026-09-24_1430_1-1"
```

## Settings

Open **File > Settings**.

The Whisper model is the main tradeoff. `small` runs at a usable speed on most laptop CPUs, while `medium` and `large-v3` are more accurate but slow without a GPU. If you have an NVIDIA card, pick `large-v3` and leave "Run Whisper on" at `auto`. GPU use also needs NVIDIA's cuBLAS and cuDNN libraries (see the faster-whisper README), and the app falls back to the CPU when they're missing.

Leave Language blank to auto-detect, or set `en` if every meeting is in English. A fixed language skips detection and avoids the odd sentence that comes out in the wrong language.

Keep the microphone and speaker on the Windows default unless a level bar stays flat while people talk. The speaker setting has to match the device you actually hear the call through, since that's where the system audio gets captured.

Everything the app does on its own is on by default and has a checkbox here: the meeting-detected prompt, stopping when the meeting app releases the mic, transcribing right after recording, the recording reminder, keeping the app in the tray when you close the window, and starting at login. Turn off "keep running" and closing the window quits the app instead.

Settings live in `%APPDATA%\MeetingRecorder\settings.json` on Windows and `~/Library/Application Support/MeetingRecorder/settings.json` on a Mac.

## Limits

Speaker labels are two-sided: "Me" and "Others", not a name per person. Telling several remote speakers apart needs a diarization model (software that groups speech by voice). This app leaves that out to keep the install small and offline, and `transcript.json` keeps the timings if you want to add one later.

Use headphones if you can. On laptop speakers the mic hears the other side too. The app drops mic lines that match what the system track said at the same moment, but a headset still gives a cleaner transcript.

It runs on Windows 10/11 and macOS 13 or later. Linux isn't supported. On Windows, system audio comes from WASAPI loopback (a Windows audio API). On a Mac it comes from ScreenCaptureKit, through a small Swift helper bundled inside the app.

The Mac build is ad-hoc signed, not signed with a Developer ID. That's why macOS blocks its first launch. It also means macOS may ask for the Screen & System Audio Recording permission again after each update. Signing and notarizing with a paid Apple Developer account fixes both.

Tell people you're recording. Many US states and many countries require everyone's consent, so say it at the start of the call. The app reminds you each time until you turn the reminder off.

## Development

```powershell
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m pytest
```

The code is small:

| Module | Job |
|---|---|
| `audio.py` | Captures the mic and loopback tracks, keeps them time-aligned, mixes them. |
| `transcribe.py` | Runs Whisper per track, removes echo, merges turns, writes export formats. |
| `meetings.py` | Meeting folders, `meta.json`, and the processing step. |
| `detect.py` | Finds which apps are using the mic. |
| `app.py` | The Tkinter window. |
| `tray.py` | The system tray icon and its menu. |
| `startup.py` | Start at login: the Windows `Run` key, or a macOS LaunchAgent. |
| `macos.py` | Calls the Swift helper: system audio, permission checks, which apps use the mic. |
| `packaging/macos/AudioHelper.swift` | The Swift helper itself (ScreenCaptureKit and CoreAudio). |
| `__main__.py` | Command-line entry point. |
| `selftest.py` | Checks a packaged build can load its libraries and transcribe. |
| `packaging/` | PyInstaller spec, exe launcher, Inno Setup installer script. |
