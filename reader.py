#!/usr/bin/env python3
"""
reader.py — the READ side of heliograph.

Reads the flashes back: give it the video you filmed of the other station's
screen (or a folder of still photos), and it reconstructs the original file and
verifies it against the sha256 carried in the header flash.

Usually invoked through the top-level CLI:

    python heliograph.py read <video-or-image-folder> [--out DIR]

It scans for QR flashes frame by frame, feeds the fountain packets into the
decoder, and as soon as it has collected enough it rebuilds the file.

Dependencies: opencv-python, numpy (both pulled in by `pip install opencv-python`).
"""

import argparse
import os
import sys

import codec

try:
    import cv2
    import numpy as np
except ImportError:
    sys.exit("ERROR: opencv is required for reading. Install with:\n"
             "    pip install opencv-python")

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".heic"}


def iter_video_frames(path, stride):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"ERROR: could not open video: {path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % stride == 0:
            yield i, total, frame
        i += 1
    cap.release()


def iter_image_frames(folder):
    files = sorted(
        os.path.join(folder, f) for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS
    )
    for i, fp in enumerate(files):
        img = cv2.imread(fp)
        if img is not None:
            yield i, len(files), img


def read_flashes(detector, frame):
    """Pull one or more QR payloads ("flashes") out of a camera frame.

    Tries the raw frame, then a grayscale upscale, which rescues small or
    slightly blurry codes.
    """
    results = []
    try:
        ok, decoded, _, _ = detector.detectAndDecodeMulti(frame)
        if ok:
            results.extend([d for d in decoded if d])
    except cv2.error:
        pass
    if results:
        return results
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    big = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    data, _, _ = detector.detectAndDecode(big)
    if data:
        results.append(data)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="heliograph read",
        description="Read a heliograph signal back into the original file.")
    ap.add_argument("source", help="video file, or folder of still images, of the flashing screen")
    ap.add_argument("--out", default=".", help="output directory (default: current dir)")
    ap.add_argument("--stride", type=int, default=2,
                    help="process every Nth video frame (default 2; raise to go faster)")
    args = ap.parse_args(argv)

    detector = cv2.QRCodeDetector()
    dec = codec.FountainDecoder()

    if os.path.isdir(args.source):
        source_iter = iter_image_frames(args.source)
        kind = "images"
    else:
        ext = os.path.splitext(args.source)[1].lower()
        if ext not in VIDEO_EXTS:
            print(f"warning: {ext} isn't a known video type; trying anyway", file=sys.stderr)
        source_iter = iter_video_frames(args.source, args.stride)
        kind = "video"

    print(f"Reading flashes from {kind}: {args.source}")
    scanned = 0
    unique_packets = 0
    last_report = -1

    for i, total, frame in source_iter:
        scanned += 1
        for text in read_flashes(detector, frame):
            raw = codec.text_to_frame(text)
            if raw is None:
                continue
            info = codec.parse_packet(raw)
            if info is None:
                continue
            if dec.add_packet(info):
                unique_packets += 1

        if dec.ready:
            pct = dec.solved_count * 100 // max(1, dec.K)
            if pct != last_report and pct % 5 == 0:
                print(f"  reconstructing: {dec.solved_count}/{dec.K} blocks ({pct}%) "
                      f"— {unique_packets} unique flashes from {scanned} frames")
                last_report = pct

        if dec.is_complete():
            break

    print("-" * 60)
    if not dec.ready:
        sys.exit("FAILED: never read a header flash. Film from the start of a loop, "
                 "get closer/steadier, and keep the whole QR panel in view.")
    if not dec.is_complete():
        sys.exit(f"INCOMPLETE: recovered {dec.solved_count}/{dec.K} blocks. "
                 f"Film another full loop (flashes accumulate across a longer recording) "
                 f"or improve focus/lighting, then try again.")

    data = dec.result()
    ok = dec.verify()
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, os.path.basename(dec.filename) or "recovered.bin")
    if os.path.exists(out_path):
        base, e = os.path.splitext(out_path)
        k = 1
        while os.path.exists(f"{base}.recovered{k}{e}"):
            k += 1
        out_path = f"{base}.recovered{k}{e}"
    with open(out_path, "wb") as fh:
        fh.write(data)

    print(f"RECOVERED {dec.filename}: {len(data):,} bytes -> {out_path}")
    print(f"sha256 integrity check: {'PASS ✓' if ok else 'FAIL ✗ (file is corrupt)'}")
    print(f"  expected {dec.sha256}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
