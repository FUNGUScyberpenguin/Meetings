"""Entry point for the packaged MeetingRecorder.exe."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        from meeting_recorder.selftest import main as selftest

        sys.exit(selftest(sys.argv[2:]))
    from meeting_recorder.app import main

    main()
