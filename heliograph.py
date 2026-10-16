#!/usr/bin/env python3
"""
heliograph — move a file across an air gap as light.

One station FLASHES a file as a looping animation of QR codes on its screen;
the other station READS those flashes with a camera and rebuilds the file.
No network, no cable, no shared storage — just a screen and a camera.

Usage:
    python heliograph.py flash <file> [options]     # sender: build the signal
    python heliograph.py read  <video> [options]    # receiver: recover the file
    python heliograph.py selftest                   # verify the codec, no hardware

Run a subcommand with -h for its own options, e.g. `python heliograph.py flash -h`.
"""

import sys

USAGE = __doc__


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    cmd, rest = argv[0], argv[1:]

    if cmd == "flash":
        import flasher
        flasher.main(rest)
    elif cmd == "read":
        import reader
        reader.main(rest)
    elif cmd == "selftest":
        import codec
        return codec._selftest()
    else:
        sys.stderr.write(f"unknown command: {cmd!r}\n\n{USAGE}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
