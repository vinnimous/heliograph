# heliograph

**Move a file across an air gap as light.** One machine *flashes* a file as a
looping animation of QR codes on its screen; another machine *reads* those
flashes with a camera and rebuilds the file — byte-for-byte, verified by
sha256. No network, no cable, no shared storage. Just a screen and a camera.

> A [heliograph](https://en.wikipedia.org/wiki/Heliograph) is a 19th-century
> signalling instrument that flashed sunlight off a mirror to send Morse across
> miles of open country — line-of-sight, no wires. This is the same idea with a
> screen for the mirror and a camera for the telescope. Fittingly, *heliography*
> ("sun-writing") was also the name of the first photographic process: writing
> with light, read back by a lens.

---

## Why

When two systems share no network — an isolated research box, a cleanroom
instrument, a hardware wallet — the only channel left between them is the
physical world. Light is the most robust optical path across that gap: it's
self-clocking, immune to acoustic noise, and needs no line to be tapped into.

heliograph encodes the file with a **fountain code** (a Luby Transform), so the
receiver needs neither every flash nor any particular order — just *enough*
unique flashes from anywhere in the loop. Dropped, blurry, or out-of-order
frames cost only a little time. The transmitter loops forever; the receiver
films until it has what it needs.

## How it works

```
   file bytes ──► K source blocks ──► fountain flashes ──► QR codes ──► looping
   (1-D)          (split)              (XOR of random        (2-D)       animation
                                        block subsets,                   on screen
                                        chosen by a seed                 (+time = 3-D)
                                        carried in each flash)
                                                                           │
                                                        photons across the air gap
                                                                           ▼
   recovered file ◄── sha256 verify ◄── peeling decoder ◄── QR read ◄── camera
```

Each flash is one fountain symbol: the XOR of a pseudo-random subset of the
source blocks, with only a 4-byte seed carried inside it. Both sides derive the
same subset from that seed, so the index set never has to be transmitted. A
header flash (filename, size, sha256, parameters) is interleaved throughout the
loop so the receiver locks on wherever it starts watching. Once it has collected
roughly 1.3× the file's worth of independent flashes, a peeling decoder solves
the system and checks the hash.

## Install

Nothing to do by hand: just use the launcher for your OS. It needs only
Python 3.8+.

| OS | command |
|---|---|
| macOS / Linux | `./run.sh <command> [options]` |
| Windows | `run.bat <command> [options]` |
| any | `python run.py <command> [options]` |

Every launch, the launcher:

1. uses the virtual environment that's **already active**, if there is one;
2. otherwise uses the project's `.venv`, **creating it** if it doesn't exist
   (set `HELIOGRAPH_VENV` to put it somewhere else);
3. **checks every package in `requirements.txt`** and installs any that are
   missing or the wrong version;
4. runs `heliograph.py` with your arguments, returning its exit code.

On Debian/Ubuntu, creating a venv needs the `python3-venv` package
(`sudo apt install python3-venv`); the launcher tells you if it's missing.

**Offline machine?** `qrcode` is pure Python, so you can carry it over as wheels.
Copy the wheels over and point pip at them before launching:

```bash
PIP_NO_INDEX=1 PIP_FIND_LINKS=/path/to/wheels ./run.sh flash secret.pdf
```

(On Windows: `set PIP_NO_INDEX=1` and `set PIP_FIND_LINKS=C:\path\to\wheels`,
then `run.bat ...`.)

Prefer to manage it yourself? `pip install -r requirements.txt`, then run
`python heliograph.py ...` directly. The flash side needs only `qrcode`.
The read side and `flash --mp4`/`--png` also need `opencv-python`/`numpy`.

## Use

(Windows: use `run.bat` in place of `./run.sh`.)

**On the SENDER (has the file):**

```bash
./run.sh flash secret.pdf
```

Writes `secret.pdf_heliograph/player.html`. Open it in a browser, press **F**
for fullscreen. It loops the flashing QR animation forever.

**On the RECEIVER (has the camera):**

1. Point the camera at the sender's screen and record a video of the animation —
   at least one full loop; longer never hurts.
2. Get that recording onto the receiver, then:

```bash
./run.sh read my-recording.mp4 --out ./recovered
```

It reports progress and, once it has enough, writes the original file to
`./recovered` and prints a sha256 **PASS/FAIL**. If it says INCOMPLETE, film
another loop (or a longer clip) and run it again — flashes accumulate.

The sender prints the file's sha256 and shows it under the player; the receiver
prints both the **expected** hash (from the sender) and the **actual** hash of
what it rebuilt. Compare the expected hash with the one on the sender's screen
for an independent check. On a FAIL, the receiver saves the result as
`<name>.corrupt`, never under the real filename, and exits non-zero.

A **folder of still photos** works too — just pass the folder to `read`.

**Verify the install with no hardware at all:**

```bash
./run.sh selftest
```

## Options (flash)

| option | default | effect |
|---|---|---|
| `--block-size N` | 256 | payload bytes per flash. Lower = sparser, easier-to-read QR, more flashes. |
| `--redundancy R` | 2.5 | total flashes = R × K. Higher = more resilient to a bad camera, longer loop. |
| `--fps F` | 6 | flashes per second. Lower is safer — each code stays on screen longer. |
| `--ec {L,M,Q,H}` | M | QR error correction. Q/H survive glare/blur better but hold less. |
| `--mp4` / `--png` | off | also emit a video / individual PNG flashes. |

Flaky phone-camera setup? Try `flash --block-size 200 --fps 4 --ec Q`.

## Capture tips

- **Fill the frame** with the white QR panel and keep it fully in view the whole
  time. Steady the camera (prop it or use a tripod).
- **Focus beats resolution** — a sharp 720p recording beats a blurry 4K one.
- **Avoid glare** on the sender's screen; use even, moderate lighting.
- Film **at least one full loop**; when in doubt, record longer.
- Stalling short of 100%? Get closer/steadier/sharper, or lower `--fps` /
  `--block-size` on the sender and re-film.

## Capacity

Throughput is about `block_size × fps` bytes/sec of unique data (~1.5 KB/s at
defaults), and you need ≈1.3× the file's size in flashes to reconstruct. Built
for **keys, configs, certificates, documents, and small archives** — up to a few
MB comfortably. Larger files just mean proportionally longer loops.

## Security note

This is an optical transfer channel. The same mechanism that bridges an air gap
legitimately is what air-gap *exfiltration* research studies — use it only to
move data you are authorized to move. heliograph provides **integrity** (sha256)
but **no confidentiality**: anyone who can film the screen can read it. If the
data is sensitive, encrypt the file before flashing and decrypt after reading.

## As a Claude skill

`SKILL.md` lets this double as a drop-in skill: clone the repo into your skills
directory and an assistant can drive the `flash` and `read` sides directly.

## License

MIT — free to use, modify, and distribute. See [LICENSE](LICENSE).
