---
name: heliograph
description: Move a file across an air gap as light — no network, cable, or shared storage. One machine FLASHES a file as a looping animation of QR codes on its screen; another machine READS those flashes with a camera to reconstruct the file, verified by sha256. Use when the user wants to transfer a file to or from an isolated/air-gapped system, do optical/"data over light" file transfer, send a file via QR codes or a QR video, or bridge two computers that share only a screen and a camera. The flash side turns any file into player.html (and optionally an MP4); the read side turns a camera recording of that animation back into the original file. Pure-Python flash side (needs only `qrcode`); read side needs `opencv-python`. Invoked as `python heliograph.py flash <file>` and `python heliograph.py read <video>`.
---

# heliograph — air-gap file transfer over light

Transfers a file between two computers that share **no** network, USB, or disk —
only a **screen on the sender** and a **camera on the receiver**. The file is
fountain-coded into a looping animation of QR codes (the "flashes"); the receiver
films the screen until it has collected enough flashes to rebuild the file, then
verifies it against the sha256 in the header.

Named for the 19th-century heliograph, which flashed sunlight off a mirror to
signal across open country. Here the screen is the mirror and the camera is the
telescope.

Because it uses a **fountain code** (Luby Transform), the receiver needs neither
every flash nor any order — just "enough" unique flashes from anywhere in the
loop. Dropped, blurry, or out-of-order frames only cost time.

## The two sides

One machine usually runs only one side:

- **Flash (sender / has the file):** `python heliograph.py flash <file>`. Pure
  Python, only needs `qrcode`. Safe on an isolated machine — produces a
  `player.html`; nothing touches the network.
- **Read (receiver / has the camera):** `python heliograph.py read <video>`.
  Needs `opencv-python`. Takes the filmed recording and rebuilds the file.

## Dependencies

```
pip install qrcode          # flash side (pure Python, offline-installable)
pip install opencv-python   # read side (pulls in numpy)
```
`flash --mp4`/`--png` also need numpy/opencv. On an air-gapped sender with no
internet, `qrcode` is pure Python and can be carried over as wheels or source.

## Workflow (what to tell the user)

**On the SENDER:**
```
python heliograph.py flash <file>
```
Writes `<file>_heliograph/player.html`. Tell the user to open it in a browser
and press **F** for fullscreen; it loops the flashing QR animation. One loop
takes `flashes / fps` seconds (printed at the end).

**On the RECEIVER:**
1. The user points the camera at the sender's screen and records the animation —
   at least one full loop; longer is strictly better.
2. Get that video onto the receiver, then:
```
python heliograph.py read <video-file> --out <dir>
```
It reports progress (`blocks recovered / K`), and when it has enough it writes
the original file to `<dir>` and prints a sha256 PASS/FAIL. If it says
INCOMPLETE, the user films another loop and runs again — flashes accumulate.

A **folder of still photos** works instead of a video: pass the folder path.

## Running it

- Invoke with the Python in the user's environment. Verify imports first if
  unsure: `python -c "import qrcode"` / `import cv2`.
- All of `heliograph.py`, `flasher.py`, `reader.py`, `codec.py` must sit together.
  Both sides import `codec.py`; its fountain parameters must match on each side,
  so don't edit them casually.
- Verify end-to-end with no hardware: `python heliograph.py selftest`.

## Key options (flash)

| option | default | effect |
|---|---|---|
| `--block-size N` | 256 | payload bytes per flash. **Lower = sparser, easier-to-read QR** but more flashes. Raise (400–600) only if reading is rock-solid. |
| `--redundancy R` | 2.5 | total flashes = R × K. Higher = more resilient to a bad camera, longer loop. |
| `--fps F` | 6 | flashes/sec. **Lower is safer**: each code stays on screen longer so the camera catches a clean copy. |
| `--ec {L,M,Q,H}` | M | QR error-correction. Q/H survive glare/blur better but hold less per flash. |
| `--mp4` | off | also emit `movie.mp4` (needs opencv) as an alternate way to display the animation. |
| `--png` | off | also emit individual QR PNGs in `frames/`. |

Rule of thumb for a flaky phone camera: `flash --block-size 200 --fps 4 --ec Q`.

## Capture tips

- **Fill the frame** with the white QR panel; keep the whole panel in view the
  whole time. Steady the camera.
- **Avoid glare**; even, moderate lighting.
- **Focus** matters more than resolution — sharp 720p beats blurry 4K.
- Film **at least one full loop**; the reader accumulates flashes across the whole
  recording, so record longer when in doubt.
- Stalling short of 100%? Get closer/steadier/sharper, or lower `--fps` /
  `--block-size` on the sender and re-film.

## Capacity

Throughput ≈ `block_size × fps` bytes/sec of unique data (~1.5 KB/s at defaults);
you need ≈1.3× the file's size in flashes to reconstruct. Built for **keys,
configs, certificates, documents, small archives** — up to a few MB comfortably.

## How it works (one paragraph)

`flash` splits the file into K fixed-size blocks and generates R×K *fountain
flashes*, each an XOR of a pseudo-random subset of blocks chosen by a seed that
travels (as 4 bytes) inside the flash. A header flash carries the filename, size,
sha256, and fountain parameters, interleaved throughout the loop. `read` pulls QR
payloads out of the video with OpenCV, and a peeling decoder in `codec.py`
reconstructs the K blocks once ≈1.3×K independent flashes have arrived, then
checks the sha256. The seed scheme means both sides agree on every flash's block
set without ever transmitting it.

## Security note

This is an optical transfer channel: the same mechanism used for legitimate
air-gap bridging is what air-gap *exfiltration* research studies. Use it only to
move data you're authorized to move. It provides **integrity** (sha256) but **no
confidentiality** — anyone who can film the screen can read it. If the data is
sensitive, encrypt the file before flashing and decrypt after reading.
