#!/usr/bin/env python3
"""
flasher.py — the FLASH side of heliograph.

Turns any file into a looping animation of QR codes (the "flashes") that you
display on the sender's screen. The receiving station films that screen and
reads the file back (see reader.py).

Usually invoked through the top-level CLI:

    python heliograph.py flash <file> [options]

Output (written to an --out directory, default: <file>_heliograph/):
  player.html   self-contained looping QR animation — open in a browser,
                press F for fullscreen, and point the receiving camera at it.
  movie.mp4     (optional, --mp4) same animation as a video file.
  frames/       (optional, --png) individual QR PNGs.

The file is fountain-coded (see codec.py), so the receiver needs neither every
flash nor any particular order — just "enough" unique flashes from anywhere in
the loop. The header flash (filename, size, sha256, fountain parameters) is
interleaved throughout so the receiver locks on no matter where it starts.

Only dependency for HTML output: `qrcode` (pure Python). `--mp4`/`--png` also
need numpy, and --mp4 needs opencv.
"""

import argparse
import base64
import json
import os
import sys

import codec

try:
    import qrcode
except ImportError:
    sys.exit("ERROR: the 'qrcode' package is required for flashing. Install with:\n"
             "    pip install qrcode")

EC_LEVELS = {
    "L": "ERROR_CORRECT_L",
    "M": "ERROR_CORRECT_M",
    "Q": "ERROR_CORRECT_Q",
    "H": "ERROR_CORRECT_H",
}


def qr_matrix(text, ec="M"):
    """Return (N, packed_b64) for the QR encoding of `text`.

    The matrix is N x N booleans (no quiet zone — the player adds one),
    packed row-major MSB-first into bytes and base64-encoded for embedding.
    """
    qr = qrcode.QRCode(
        error_correction=getattr(qrcode.constants, EC_LEVELS[ec]),
        border=0,
    )
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    bits = bytearray((n * n + 7) // 8)
    pos = 0
    for row in matrix:
        for cell in row:
            if cell:
                bits[pos >> 3] |= 0x80 >> (pos & 7)
            pos += 1
    return n, base64.b64encode(bytes(bits)).decode("ascii")


def build_sequence(header_text, data_frames, header_every=12):
    """Interleave the header flash through the data flashes for the loop."""
    seq = [("H", header_text)]
    for i, f in enumerate(data_frames):
        seq.append(("D", f))
        if (i + 1) % header_every == 0:
            seq.append(("H", header_text))
    return seq


PLAYER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>heliograph — __NAME__</title>
<style>
  :root { color-scheme: dark; }
  html,body { margin:0; height:100%; background:#0d0e10; color:#e8e8ea;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  #stage { height:100%; display:flex; flex-direction:column; align-items:center;
    justify-content:center; gap:14px; }
  #panel { background:#fff; padding:3.5vmin; border-radius:8px;
    box-shadow:0 0 0 1px #000, 0 12px 40px rgba(0,0,0,.6); }
  canvas { display:block; image-rendering:pixelated; }
  #bar { display:flex; gap:18px; align-items:center; font-size:14px;
    color:#b9b9c2; flex-wrap:wrap; justify-content:center; padding:0 12px; }
  #bar b { color:#fff; font-weight:600; }
  .mark { color:#e0a43b; font-weight:600; letter-spacing:.04em; }
  .k { font-variant-numeric:tabular-nums; }
  button { background:#1e1f24; color:#e8e8ea; border:1px solid #3a3a42;
    border-radius:6px; padding:6px 12px; font-size:13px; cursor:pointer; }
  button:hover { background:#2a2b31; }
  #hint { font-size:12px; color:#6f6f7a; }
  #prog { height:4px; background:#1e1f24; width:min(80vw,520px); border-radius:2px;
    overflow:hidden; }
  #progfill { height:100%; width:0; background:#e0a43b; }
</style>
</head>
<body>
<div id="stage">
  <div id="panel"><canvas id="c"></canvas></div>
  <div id="prog"><div id="progfill"></div></div>
  <div id="bar">
    <span class="mark">HELIOGRAPH</span>
    <span><b>__NAME__</b></span>
    <span class="k">flash <span id="fi">0</span>/<span id="ft">0</span></span>
    <span class="k"><span id="fps">0</span> fps</span>
    <button id="slower">– slower</button>
    <button id="faster">faster +</button>
    <button id="pause">pause</button>
    <button id="fs">fullscreen (F)</button>
  </div>
  <div id="hint">Point the receiving station's camera at the white panel. Let it flash until the other side reads a complete file.</div>
</div>
<script>
const DATA = __DATA__;      // {name, fps, headerEvery, seq:[{t,n,b}], count}
let frames = DATA.seq;
let fps = DATA.fps;
let idx = 0, paused = false, timer = null;

const cv = document.getElementById('c');
const ctx = cv.getContext('2d');
const fiEl = document.getElementById('fi');
const ftEl = document.getElementById('ft');
const fpsEl = document.getElementById('fps');
const progfill = document.getElementById('progfill');
ftEl.textContent = DATA.count;
fpsEl.textContent = fps;

// Unpack a base64 packed-bit matrix into a flat Uint8Array of 0/1.
function unpack(n, b64) {
  const raw = atob(b64);
  const out = new Uint8Array(n * n);
  for (let p = 0; p < n * n; p++) {
    const byte = raw.charCodeAt(p >> 3);
    out[p] = (byte >> (7 - (p & 7))) & 1;
  }
  return out;
}

const QUIET = 4; // modules of white quiet zone around the code

function draw(frame) {
  const n = frame.n;
  const bits = unpack(n, frame.b);
  const avail = Math.min(window.innerWidth, window.innerHeight) * 0.80;
  const cell = Math.max(2, Math.floor(avail / (n + QUIET * 2)));
  const dim = (n + QUIET * 2) * cell;
  if (cv.width !== dim) { cv.width = dim; cv.height = dim; }
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, dim, dim);
  ctx.fillStyle = '#000';
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      if (bits[y * n + x]) {
        ctx.fillRect((x + QUIET) * cell, (y + QUIET) * cell, cell, cell);
      }
    }
  }
}

function tick() {
  const frame = frames[idx];
  draw(frame);
  fiEl.textContent = idx + 1;
  progfill.style.width = ((idx + 1) / frames.length * 100) + '%';
  idx = (idx + 1) % frames.length;
}

function schedule() {
  if (timer) clearInterval(timer);
  timer = setInterval(() => { if (!paused) tick(); }, Math.round(1000 / fps));
}

document.getElementById('slower').onclick = () => { fps = Math.max(1, fps - 1); fpsEl.textContent = fps; schedule(); };
document.getElementById('faster').onclick = () => { fps = Math.min(30, fps + 1); fpsEl.textContent = fps; schedule(); };
document.getElementById('pause').onclick = (e) => { paused = !paused; e.target.textContent = paused ? 'resume' : 'pause'; };
document.getElementById('fs').onclick = goFs;
function goFs() { if (!document.fullscreenElement) document.documentElement.requestFullscreen(); else document.exitFullscreen(); }
window.addEventListener('keydown', (e) => {
  if (e.key === 'f' || e.key === 'F') goFs();
  else if (e.key === ' ') { paused = !paused; document.getElementById('pause').textContent = paused ? 'resume' : 'pause'; e.preventDefault(); }
  else if (e.key === 'ArrowUp') document.getElementById('faster').click();
  else if (e.key === 'ArrowDown') document.getElementById('slower').click();
});
window.addEventListener('resize', () => draw(frames[(idx - 1 + frames.length) % frames.length]));

tick();
schedule();
</script>
</body>
</html>
"""


def write_player(path, name, fps, header_every, seq_matrices):
    data = {
        "name": name,
        "fps": fps,
        "headerEvery": header_every,
        "count": len(seq_matrices),
        "seq": [{"t": t, "n": n, "b": b} for (t, n, b) in seq_matrices],
    }
    html = (PLAYER_HTML
            .replace("__NAME__", name.replace("<", "&lt;").replace(">", "&gt;"))
            .replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


def write_mp4(path, name, fps, seq_matrices, cell=8, quiet=4):
    import numpy as np
    import cv2
    max_n = max(n for (_, n, _) in seq_matrices)
    dim = (max_n + quiet * 2) * cell
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(path, fourcc, fps, (dim, dim))
    for (_, n, b) in seq_matrices:
        raw = base64.b64decode(b)
        img = np.full((dim, dim), 255, dtype=np.uint8)
        off = ((dim - n * cell) // 2)
        for p in range(n * n):
            if (raw[p >> 3] >> (7 - (p & 7))) & 1:
                y, x = divmod(p, n)
                yy = off + y * cell
                xx = off + x * cell
                img[yy:yy + cell, xx:xx + cell] = 0
        vw.write(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    vw.release()


def write_png_frames(folder, seq_matrices, cell=8, quiet=4):
    """Hand-rolled PNG writer (no Pillow) — one file per flash."""
    import zlib
    import struct as _struct
    os.makedirs(folder, exist_ok=True)

    def png_bytes(n, raw, cell, quiet):
        dim = (n + quiet * 2) * cell
        rows = bytearray()
        for py in range(dim):
            rows.append(0)  # filter type 0
            my = py // cell - quiet
            for px in range(dim):
                mx = px // cell - quiet
                black = 0 <= my < n and 0 <= mx < n and \
                    ((raw[(my * n + mx) >> 3] >> (7 - ((my * n + mx) & 7))) & 1)
                rows.append(0 if black else 255)

        def chunk(tag, data):
            return (_struct.pack(">I", len(data)) + tag + data +
                    _struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = _struct.pack(">IIBBBBB", dim, dim, 8, 0, 0, 0, 0)
        idat = zlib.compress(bytes(rows), 6)
        return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

    for i, (_, n, b) in enumerate(seq_matrices):
        raw = base64.b64decode(b)
        with open(os.path.join(folder, f"flash_{i:05d}.png"), "wb") as fh:
            fh.write(png_bytes(n, raw, cell, quiet))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="heliograph flash",
        description="Flash a file as a looping animated-QR signal.")
    ap.add_argument("input", help="file to flash")
    ap.add_argument("--out", help="output directory (default: <input>_heliograph)")
    ap.add_argument("--block-size", type=int, default=256,
                    help="payload bytes per flash (default 256; lower = easier to read, slower)")
    ap.add_argument("--redundancy", type=float, default=2.5,
                    help="fountain overhead; total flashes = redundancy x K (default 2.5)")
    ap.add_argument("--fps", type=int, default=6, help="flashes per second (default 6)")
    ap.add_argument("--ec", choices=list(EC_LEVELS), default="M",
                    help="QR error-correction level (default M)")
    ap.add_argument("--header-every", type=int, default=12,
                    help="interleave the header flash every N data flashes")
    ap.add_argument("--mp4", action="store_true", help="also write movie.mp4 (needs opencv + numpy)")
    ap.add_argument("--png", action="store_true", help="also write individual PNG flashes")
    args = ap.parse_args(argv)

    with open(args.input, "rb") as fh:
        data = fh.read()
    name = os.path.basename(args.input)
    outdir = args.out or (args.input.rstrip("/\\") + "_heliograph")
    os.makedirs(outdir, exist_ok=True)

    print(f"Flashing {name} ({len(data):,} bytes)")
    header_text, data_frames = codec.encode_file(
        data, name, block_size=args.block_size, redundancy=args.redundancy)
    K = max(1, (len(data) + args.block_size - 1) // args.block_size)
    print(f"  K = {K} source blocks, {len(data_frames)} fountain flashes "
          f"({len(data_frames)/K:.1f}x), QR payload ~{len(data_frames[0])} chars")

    seq = build_sequence(header_text, data_frames, args.header_every)
    print(f"  Building {len(seq)} QR flashes (EC={args.ec}) ...")
    seq_matrices = []
    for t, text in seq:
        n, b = qr_matrix(text, args.ec)
        seq_matrices.append((t, n, b))
    grid_n = seq_matrices[1][1] if len(seq_matrices) > 1 else seq_matrices[0][1]
    print(f"  QR module grid: {grid_n}x{grid_n}")

    player = os.path.join(outdir, "player.html")
    write_player(player, name, args.fps, args.header_every, seq_matrices)
    print(f"  wrote {player}")

    if args.mp4:
        mp4 = os.path.join(outdir, "movie.mp4")
        write_mp4(mp4, name, args.fps, seq_matrices)
        print(f"  wrote {mp4}")
    if args.png:
        write_png_frames(os.path.join(outdir, "frames"), seq_matrices)
        print(f"  wrote {outdir}/frames/ ({len(seq_matrices)} PNGs)")

    loop_seconds = len(seq_matrices) / args.fps
    print(f"\nOne full loop is about {loop_seconds:.0f}s at {args.fps} fps.")
    print("Open player.html, press F for fullscreen, and let it flash.")
    print("On the other station:  python heliograph.py read <your-recording>")


if __name__ == "__main__":
    main()
