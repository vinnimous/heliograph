"""
codec.py — shared engine for heliograph.

heliograph moves a file across an air gap by flashing it as a looping animation
of QR codes on one screen and reading those flashes with a camera on another.
This module is the codec both sides share — the "flash" side and the "read"
side both import it.

Contains everything both the flasher and the reader need:
  * a deterministic PRNG (platform-independent xorshift)
  * a robust-soliton degree distribution with integer-quantized thresholds
  * an LT (Luby Transform) fountain encoder and a peeling decoder
  * the on-wire packet protocol (header + data packets)
  * base64 framing for stuffing packets into QR codes
  * a sha256 integrity check

The fountain layer is what makes the transfer survive a camera: the receiver
never needs *every* frame or any particular order — it just needs to collect
"enough" unique symbols, from anywhere in the looping animation, to rebuild
the file. Dropped, blurry, or out-of-order frames cost nothing but time.

This file is pure Python standard library. It is deliberately dependency-free
so it can run on an isolated/air-gapped machine with nothing but CPython.

Run `python codec.py selftest` to verify the whole codec end-to-end without
any camera or hardware.
"""

import base64
import hashlib
import math
import struct
import sys

MAGIC = b"HLG1"          # heliograph, v1
TYPE_HEADER = 0x48       # 'H'
TYPE_DATA = 0x44         # 'D'

# Fixed robust-soliton parameters. These are baked in so that BOTH the
# encoder and decoder compute an identical distribution for a given K.
SOLITON_C = 0.03
SOLITON_DELTA = 0.5
# Thresholds are quantized to this integer scale so that tiny floating-point
# differences across machines/libm versions can never change which degree a
# given seed selects. Degree sampling is done entirely in integer space.
DEGREE_SCALE = 1 << 31


# --------------------------------------------------------------------------
# Deterministic PRNG
# --------------------------------------------------------------------------
class RNG:
    """xorshift32 — identical output on every platform for a given seed.

    We do NOT use Python's `random` module: although CPython's Mersenne
    Twister is stable, rolling our own 32-bit xorshift removes any doubt and
    keeps encoder and decoder bit-for-bit in agreement.
    """

    __slots__ = ("state",)

    def __init__(self, seed):
        # Avalanche the seed (splitmix32-style finalizer) before use.
        # Raw xorshift produces tiny, highly-correlated first outputs for
        # small sequential seeds; mixing decorrelates seeds 1,2,3,... so the
        # degree distribution isn't skewed toward degree 1-2. Deterministic
        # and identical on every platform, so encoder and decoder agree.
        s = seed & 0xFFFFFFFF
        s ^= s >> 16
        s = (s * 0x7FEB352D) & 0xFFFFFFFF
        s ^= s >> 15
        s = (s * 0x846CA68B) & 0xFFFFFFFF
        s ^= s >> 16
        s &= 0xFFFFFFFF
        if s == 0:
            s = 0x1A2B3C4D
        self.state = s

    def _next(self):
        x = self.state
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= (x >> 17)
        x ^= (x << 5) & 0xFFFFFFFF
        x &= 0xFFFFFFFF
        self.state = x
        return x

    def randint(self, n):
        """Return an int in [0, n)."""
        return self._next() % n


# --------------------------------------------------------------------------
# Robust soliton degree distribution (integer thresholds)
# --------------------------------------------------------------------------
def build_degree_table(K):
    """Return (thresholds, degrees) for integer-space degree sampling.

    `thresholds` is a non-decreasing list of ints in (0, DEGREE_SCALE]; the
    last entry equals DEGREE_SCALE. To sample: draw r in [0, DEGREE_SCALE),
    find the first threshold strictly greater than r, take the matching degree.
    """
    if K <= 1:
        return [DEGREE_SCALE], [1]

    rho = [0.0] * (K + 1)
    rho[1] = 1.0 / K
    for d in range(2, K + 1):
        rho[d] = 1.0 / (d * (d - 1))

    R = SOLITON_C * math.log(K / SOLITON_DELTA) * math.sqrt(K)
    tau = [0.0] * (K + 1)
    if R > 0:
        kr = int(math.floor(K / R))
        for d in range(1, K + 1):
            if d < kr:
                tau[d] = R / (d * K)
            elif d == kr:
                tau[d] = R * math.log(R / SOLITON_DELTA) / K
            # else 0
        if kr < 1:  # R larger than K: no spike region
            tau = [0.0] * (K + 1)

    weights = [rho[d] + tau[d] for d in range(K + 1)]
    total = sum(weights[1:])
    if total <= 0:
        return [DEGREE_SCALE], [1]

    thresholds = []
    degrees = []
    running = 0.0
    for d in range(1, K + 1):
        w = weights[d]
        if w <= 0:
            continue
        running += w / total
        thr = int(running * DEGREE_SCALE)
        thresholds.append(thr)
        degrees.append(d)
    # Clamp the final threshold so sampling can never fall off the end.
    thresholds[-1] = DEGREE_SCALE
    return thresholds, degrees


def _sample_degree(rng, thresholds, degrees):
    import bisect
    r = rng.randint(DEGREE_SCALE)
    i = bisect.bisect_right(thresholds, r)
    if i >= len(degrees):
        i = len(degrees) - 1
    return degrees[i]


def symbol_indices(seed, K, thresholds, degrees):
    """Deterministically derive the source-block index set for one symbol.

    The encoder and decoder both call this with the same seed and K, so they
    agree on which blocks were XORed together — without ever transmitting the
    index list. Only the 32-bit seed travels inside each packet.
    """
    rng = RNG(seed)
    d = _sample_degree(rng, thresholds, degrees)
    if d > K:
        d = K
    chosen = set()
    while len(chosen) < d:
        chosen.add(rng.randint(K))
    return chosen


# --------------------------------------------------------------------------
# Byte helpers
# --------------------------------------------------------------------------
def _xor_into(dst, src):
    """XOR equal-length bytes `src` into bytearray `dst` (in place)."""
    a = int.from_bytes(dst, "big")
    b = int.from_bytes(src, "big")
    dst[:] = (a ^ b).to_bytes(len(dst), "big")


# --------------------------------------------------------------------------
# Packet protocol
# --------------------------------------------------------------------------
def pack_header(filename, file_size, K, block_size, sha256_hex):
    name = filename.encode("utf-8")
    digest = bytes.fromhex(sha256_hex)
    return (
        MAGIC
        + bytes([TYPE_HEADER])
        + struct.pack(">IIQ", K, block_size, file_size)
        + struct.pack(">H", len(name))
        + name
        + digest
    )


def header_variant(header_text, n):
    """Return the header frame with a trailing counter byte appended.

    The bytes after the digest are ignored by parse_packet, so every variant
    carries the same header. They exist because the header repeats throughout
    the loop: if all copies were identical, a QR pattern that a particular
    decoder can't read would make the header -- and so the file -- unreadable.
    Varying one byte gives each copy a different QR pattern.
    """
    return frame_to_text(text_to_frame(header_text) + bytes([n & 0xFF]))


def pack_data(seed, payload):
    return MAGIC + bytes([TYPE_DATA]) + struct.pack(">I", seed) + payload


def parse_packet(raw):
    """Return a dict describing the packet, or None if it isn't ours."""
    if len(raw) < 5 or raw[:4] != MAGIC:
        return None
    ptype = raw[4]
    body = raw[5:]
    if ptype == TYPE_HEADER:
        if len(body) < 18:
            return None
        K, block_size, file_size = struct.unpack(">IIQ", body[:16])
        off = 16
        (name_len,) = struct.unpack(">H", body[off:off + 2])
        off += 2
        name = body[off:off + name_len].decode("utf-8", "replace")
        off += name_len
        digest = body[off:off + 32]
        if len(digest) != 32:
            return None
        return {
            "type": "H",
            "filename": name,
            "file_size": file_size,
            "K": K,
            "block_size": block_size,
            "sha256": digest.hex(),
        }
    elif ptype == TYPE_DATA:
        if len(body) < 4:
            return None
        (seed,) = struct.unpack(">I", body[:4])
        return {"type": "D", "seed": seed, "payload": body[4:]}
    return None


def frame_to_text(raw):
    """base64 a raw packet into an ASCII string suitable for a QR code."""
    return base64.b64encode(raw).decode("ascii")


def text_to_frame(text):
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Encoder
# --------------------------------------------------------------------------
def encode_file(data, filename, block_size=256, redundancy=2.5):
    """Turn file bytes into (header_text, [data_frame_text, ...]).

    Returns the header frame plus a list of data frames. The data frames are
    the looping animation's content; collecting ~K of them (any K-ish unique
    ones) rebuilds the file.
    """
    file_size = len(data)
    sha256_hex = hashlib.sha256(data).hexdigest()
    K = max(1, math.ceil(file_size / block_size))

    padded = data + b"\x00" * (K * block_size - file_size)
    blocks = [padded[i * block_size:(i + 1) * block_size] for i in range(K)]

    thresholds, degrees = build_degree_table(K)

    num_symbols = max(K + 20, math.ceil(K * redundancy))

    header_text = frame_to_text(
        pack_header(filename, file_size, K, block_size, sha256_hex)
    )

    data_frames = []
    for seed in range(1, num_symbols + 1):
        idxs = symbol_indices(seed, K, thresholds, degrees)
        acc = bytearray(block_size)
        for i in idxs:
            _xor_into(acc, blocks[i])
        data_frames.append(frame_to_text(pack_data(seed, bytes(acc))))

    return header_text, data_frames


# --------------------------------------------------------------------------
# Decoder
# --------------------------------------------------------------------------
class FountainDecoder:
    """Peeling (belief-propagation) LT decoder with a GF(2) fallback.

    Feed it the header once, then data packets in any order. It ignores
    duplicates and reports when it has enough to reconstruct the file.

    Peeling alone can stall for small K (it needs a steady supply of
    degree-1 symbols), so when it does, the remaining unknown blocks are
    solved directly by Gaussian elimination over the stored symbols.
    """

    def __init__(self):
        self.ready = False
        self.K = None
        self.block_size = None
        self.file_size = None
        self.filename = None
        self.sha256 = None
        self._thresholds = None
        self._degrees = None
        self._solved = None
        self._remaining = None
        self._syms = []                    # list of [neighbor_set, bytearray]
        self._block_to_syms = None         # block idx -> set of sym ids
        self._ripple = None
        self._seen_seeds = set()
        self._pending = []                 # data packets seen before header
        self._solve_at = 0                 # symbol count for next GF(2) attempt

    # -- setup ------------------------------------------------------------
    def set_header(self, info):
        if self.ready:
            return
        from collections import defaultdict, deque
        self.K = info["K"]
        self.block_size = info["block_size"]
        self.file_size = info["file_size"]
        self.filename = info["filename"]
        self.sha256 = info["sha256"]
        self._thresholds, self._degrees = build_degree_table(self.K)
        self._solved = [None] * self.K
        self._remaining = self.K
        self._block_to_syms = defaultdict(set)
        self._ripple = deque()
        self.ready = True
        # flush anything that arrived early
        pending, self._pending = self._pending, []
        for seed, payload in pending:
            self.add_data(seed, payload)

    # -- ingest -----------------------------------------------------------
    def add_packet(self, info):
        """Dispatch a parsed packet dict. Returns True if newly useful."""
        if info is None:
            return False
        if info["type"] == "H":
            if not self.ready:
                self.set_header(info)
            return False
        if info["type"] == "D":
            seed = info["seed"]
            if seed in self._seen_seeds:
                return False
            self._seen_seeds.add(seed)
            if not self.ready:
                self._pending.append((seed, info["payload"]))
                return False
            return self.add_data(seed, info["payload"])
        return False

    def add_data(self, seed, payload):
        if len(payload) != self.block_size:
            # pad/trim defensively; a corrupt-length packet is just dropped
            if len(payload) < self.block_size:
                payload = payload + b"\x00" * (self.block_size - len(payload))
            else:
                payload = payload[: self.block_size]
        idxs = symbol_indices(seed, self.K, self._thresholds, self._degrees)
        self._add_symbol(set(idxs), bytearray(payload))
        self._peel()
        if self._remaining and len(self._syms) >= max(self._remaining, self._solve_at):
            self.try_solve()
        return True

    # -- core peeling -----------------------------------------------------
    def _add_symbol(self, neighbors, value):
        for i in list(neighbors):
            s = self._solved[i]
            if s is not None:
                _xor_into(value, s)
                neighbors.discard(i)
        if not neighbors:
            return
        sid = len(self._syms)
        self._syms.append([neighbors, value])
        for i in neighbors:
            self._block_to_syms[i].add(sid)
        if len(neighbors) == 1:
            self._ripple.append(sid)

    def _peel(self):
        while self._ripple:
            sid = self._ripple.popleft()
            neighbors, value = self._syms[sid]
            if len(neighbors) != 1:
                continue
            i = next(iter(neighbors))
            if self._solved[i] is not None:
                neighbors.discard(i)
                continue
            self._solved[i] = bytes(value)
            self._remaining -= 1
            neighbors.discard(i)
            for sid2 in list(self._block_to_syms[i]):
                if sid2 == sid:
                    continue
                nb2, val2 = self._syms[sid2]
                if i in nb2:
                    _xor_into(val2, self._solved[i])
                    nb2.discard(i)
                    if len(nb2) == 1:
                        self._ripple.append(sid2)
            self._block_to_syms[i].clear()

    def try_solve(self):
        """Solve the blocks peeling couldn't, by Gaussian elimination over GF(2).

        Each stored symbol is an equation: the XOR of its unsolved neighbour
        blocks equals its value. Bitmasks and values are Python ints, so XOR
        of whole rows is cheap. Attempts back off geometrically, so the total
        cost stays proportional to the final attempt. Returns True if complete.
        """
        if not self.ready or not self._remaining:
            return self.is_complete()
        self._solve_at = len(self._syms) + max(1, len(self._syms) // 4)
        pivots = {}                        # highest bit -> (mask, value)
        for neighbors, value in self._syms:
            if not neighbors:
                continue
            mask = 0
            for i in neighbors:
                mask |= 1 << i
            val = int.from_bytes(value, "big")
            while mask:
                hb = mask.bit_length() - 1
                if hb not in pivots:
                    pivots[hb] = (mask, val)
                    break
                pmask, pval = pivots[hb]
                mask ^= pmask
                val ^= pval
        if len(pivots) < self._remaining:
            return False                   # not enough independent equations yet
        # Back-substitute from the lowest pivot up: every other bit in a
        # pivot's row is lower, so it has already been solved.
        solved = {}
        for hb in sorted(pivots):
            mask, val = pivots[hb]
            mask ^= 1 << hb
            while mask:
                low = mask & -mask
                val ^= solved[low.bit_length() - 1]
                mask ^= low
            solved[hb] = val
        for i, val in solved.items():
            self._solved[i] = val.to_bytes(self.block_size, "big")
        self._remaining = 0
        return True

    # -- status / output --------------------------------------------------
    @property
    def solved_count(self):
        if not self.ready:
            return 0
        return self.K - self._remaining

    def is_complete(self):
        return self.ready and self._remaining == 0

    def result(self):
        if not self.is_complete():
            return None
        data = b"".join(self._solved)[: self.file_size]
        return data

    def verify(self):
        data = self.result()
        if data is None:
            return False
        return hashlib.sha256(data).hexdigest() == self.sha256


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------
def _selftest():
    import os
    import random

    print("heliograph codec self-test")
    print("-" * 60)
    ok = True
    for size in (1, 100, 2048, 50_000, 250_000):
        data = os.urandom(size)
        header, frames = encode_file(data, "test.bin", block_size=256, redundancy=2.5)
        total = len(frames)

        # Simulate a noisy camera: shuffle the frames and drop 35% of them,
        # and deliver the header somewhere in the middle of the stream.
        order = list(range(total))
        random.shuffle(order)
        kept = order[: int(total * 0.65)]

        dec = FountainDecoder()
        inject_at = len(kept) // 3
        for pos, idx in enumerate(kept):
            if pos == inject_at:
                dec.add_packet(parse_packet(text_to_frame(header)))
            raw = text_to_frame(frames[idx])
            dec.add_packet(parse_packet(raw))
            if dec.is_complete():
                break
        else:
            # header may never have been injected if kept was tiny
            dec.add_packet(parse_packet(text_to_frame(header)))
            dec.try_solve()

        good = dec.is_complete() and dec.verify() and dec.result() == data
        max_qr = max(len(f) for f in frames)
        print(
            f"size={size:>7}  K={dec.K:>5}  frames={total:>5}  "
            f"kept={len(kept):>5}  b64len={max_qr:>4}  "
            f"{'OK' if good else 'FAIL'}"
        )
        ok = ok and good

    print("-" * 60)
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(_selftest())
    print(__doc__)
