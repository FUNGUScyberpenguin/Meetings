"""Entry point.

    python -m meeting_recorder                    open the desktop app
    python -m meeting_recorder devices            list mics and speakers
    python -m meeting_recorder record -t "Sync"   record from the terminal until Ctrl+C
    python -m meeting_recorder transcribe FOLDER  (re)transcribe a saved meeting
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import SETTINGS_PATH, Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meeting_recorder")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("devices", help="list audio devices")
    rec = sub.add_parser("record", help="record from the terminal until Ctrl+C")
    rec.add_argument("-t", "--title", default="")
    rec.add_argument("--no-transcribe", action="store_true")
    tr = sub.add_parser("transcribe", help="transcribe a saved meeting folder")
    tr.add_argument("folder", type=Path)
    args = parser.parse_args(argv)

    settings = Settings.load()

    if args.cmd is None:
        from .app import main as gui_main

        gui_main()
        return 0

    if args.cmd == "devices":
        from .audio import list_devices

        devices = list_devices()
        print("Microphones:")
        for name in devices["microphones"]:
            print(f"  {name}")
        print("Speakers (system audio is captured from these):")
        for name in devices["speakers"]:
            print(f"  {name}")
        print(f"\nSettings file: {SETTINGS_PATH}")
        return 0

    from . import meetings
    from .transcribe import fmt_clock

    def progress(frac: float, who: str) -> None:
        print(f"\rTranscribing: {frac:4.0%} ({who})   ", end="", flush=True)

    if args.cmd == "record":
        from .audio import Recorder

        meeting = meetings.new_meeting(settings, args.title)
        recorder = Recorder(meeting.folder, settings.sample_rate,
                            settings.mic_device, settings.speaker_device)
        recorder.start()
        print(f"Recording to {meeting.folder}\nPress Ctrl+C to stop.")
        try:
            while not recorder.errors:
                print(f"\r{fmt_clock(recorder.elapsed)}", end="", flush=True)
                time.sleep(0.5)
            print("\n" + "\n".join(recorder.errors), file=sys.stderr)
        except KeyboardInterrupt:
            pass
        recorder.stop()
        meeting.duration = recorder.elapsed
        meeting.status = meetings.STATUS_RECORDED
        meeting.save()
        print(f"\nSaved {fmt_clock(meeting.duration)} of audio.")
        if args.no_transcribe:
            return 0
        meeting = meetings.process(meeting, settings, progress=progress)
    else:
        meeting = meetings.process(meetings.Meeting.load(args.folder), settings, progress=progress)

    print()
    if meeting.status != meetings.STATUS_DONE:
        print(f"Failed: {meeting.error}", file=sys.stderr)
        return 1
    print(f"Transcript: {meeting.transcript_txt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
