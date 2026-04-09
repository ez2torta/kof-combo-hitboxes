# -*- coding: utf-8 -*-
"""
Deep targeted analysis of specific memory regions across all snapshots.
Examines:
  1. Team struct full hex (during loading)
  2. Pre-camera area (0x27CA00)
  3. Where character selection data lives (char_select vs loading)
  4. Decomp buffer 0x300000 content
  5. Potential game phase indicator
  6. High-volatility regions 0x140000-0x1A0000
"""
import struct, sys, os, json
from collections import OrderedDict

if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def hexdump(data, offset=0, width=16, max_lines=None):
    lines = []
    for i in range(0, len(data), width):
        if max_lines and len(lines) >= max_lines:
            break
        chunk = data[i:i+width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {offset+i:06X}: {hex_part:<{width*3}}  {ascii_part}")
    return "\n".join(lines)

def r8(d, o): return d[o] if o < len(d) else 0
def r16(d, o): return struct.unpack_from("<H", d, o)[0] if o+2 <= len(d) else 0
def rs16(d, o): return struct.unpack_from("<h", d, o)[0] if o+2 <= len(d) else 0
def r32(d, o): return struct.unpack_from("<I", d, o)[0] if o+4 <= len(d) else 0

ROSTER = {
    0x00: "Ash", 0x01: "Oswald", 0x02: "Shen Woo",
    0x03: "Elisabeth", 0x04: "Duo Lon", 0x05: "Benimaru",
    0x06: "Terry", 0x07: "Kim", 0x08: "Duck King",
    0x09: "Ryo", 0x0A: "Yuri", 0x0B: "King",
    0x0C: "Kyo", 0x0D: "Shingo", 0x0E: "Iori",
}

# Chronological order of snapshots
CHRONO_ORDER = [
    "titulo", "how_to_play", "char_select_ash", "char_select_kyo_16_seconds",
    "leader_select_ash", "order_select_ash_leader",
    "loading_first_stage", "loading_first_stage_2",
]

def main():
    snap_dir = sys.argv[1] if len(sys.argv) > 1 else "aw_data/snapshots"

    # Load in chronological order
    snapshots = OrderedDict()
    for name in CHRONO_ORDER:
        path = os.path.join(snap_dir, name + ".bin")
        if os.path.exists(path):
            with open(path, "rb") as f:
                snapshots[name] = f.read()

    print(f"Loaded {len(snapshots)} snapshots in chronological order")
    print()

    # ===================================================================
    # A. TEAM STRUCT P1 FULL DUMP (loading_first_stage)
    # ===================================================================
    print("=" * 70)
    print("A. TEAM STRUCT P1 FULL HEX (loading_first_stage)")
    print("   Team P1 at 0x27CB50, size 0x1F8 = 504 bytes")
    print("=" * 70)
    d = snapshots.get("loading_first_stage")
    if d:
        team_off = 0x27CB50
        print(hexdump(d[team_off:team_off+0x1F8], team_off))
        print()

        # Byte-by-byte annotation of known fields
        print("  Field annotations:")
        print(f"    +0x000 byte0:        0x{r8(d, team_off):02X}")
        print(f"    +0x001 leader:       {r8(d, team_off+1)}")
        print(f"    +0x002 byte2:        0x{r8(d, team_off+2):02X}")
        print(f"    +0x003 point:        {r8(d, team_off+3)}")
        print(f"    +0x004-006:          {d[team_off+4:team_off+7].hex()}")
        print(f"    +0x007 comboCount:   {r8(d, team_off+7)}")

        # Look at area +0x008 to +0x037 (unknown)
        print(f"    +0x008-0x037 (unknown 48 bytes):")
        print(hexdump(d[team_off+8:team_off+0x38], team_off+8))

        print(f"    +0x038 super:        {r32(d, team_off+0x38)} (0x{r32(d, team_off+0x38):X})")
        print(f"    +0x03C skillStock:   {r32(d, team_off+0x3C)} (0x{r32(d, team_off+0x3C):X})")

        # Projectiles at +0x0C0 (16 x 4 bytes)
        print(f"\n    +0x0C0 projectiles (16 x SH4 ptr):")
        for i in range(16):
            p = r32(d, team_off + 0x0C0 + i*4)
            print(f"      proj[{i:2d}]: 0x{p:08X}", end="")
            if p != 0:
                ram = (p & 0x1FFFFFFF) - 0x0C000000
                if 0 <= ram < 0x1000000:
                    print(f" -> RAM 0x{ram:06X}", end="")
            print()

        # Entries at +0x144
        print(f"\n    +0x144 entries (3 x SH4 ptr):")
        for i in range(3):
            p = r32(d, team_off + 0x144 + i*4)
            print(f"      entry[{i}]: 0x{p:08X}")

        # PlayerExtra at +0x150 (3 x 0x20)
        print(f"\n    +0x150 playerExtra (3 x 0x20):")
        for i in range(3):
            pe = team_off + 0x150 + i*0x20
            cid = r8(d, pe+1)
            print(f"      [{i}] charID={cid} ({ROSTER.get(cid, '?')}) "
                  f"hp={rs16(d, pe+8)} visHP={rs16(d, pe+0xA)} "
                  f"maxHP={rs16(d, pe+0xC)} teamPos={r8(d, pe+0x10)}")
            print(hexdump(d[pe:pe+0x20], pe))

        # What's after playerExtra? +0x1B0 to +0x1F8
        print(f"\n    +0x1B0 to +0x1F8 (last 72 bytes of team struct):")
        print(hexdump(d[team_off+0x1B0:team_off+0x1F8], team_off+0x1B0))

    # ===================================================================
    # B. PRE-CAMERA AREA (0x27C000 to 0x27CB50)
    # ===================================================================
    print(f"\n{'='*70}")
    print("B. GAME STATE AREA BEFORE TEAM STRUCTS")
    print("   Looking at 0x27C800 - 0x27CBF0 across snapshots")
    print("=" * 70)
    # Show this region for specific transitions
    for name in ["titulo", "char_select_ash", "order_select_ash_leader",
                  "loading_first_stage", "loading_first_stage_2"]:
        d = snapshots.get(name)
        if not d:
            continue
        print(f"\n  [{name}]")
        # Camera area +/- context
        print(f"    0x27CA70-0x27CB00:")
        print(hexdump(d[0x27CA70:0x27CB00], 0x27CA70))

    # ===================================================================
    # C. CHARACTER SELECTION STORAGE
    # ===================================================================
    print(f"\n{'='*70}")
    print("C. WHERE IS CHARACTER SELECTION STORED?")
    print("   Scanning for charID patterns between char_select and loading")
    print("=" * 70)

    # Look for the transition from order_select to loading
    # to find where charIDs first appear
    d_order = snapshots.get("order_select_ash_leader")
    d_load = snapshots.get("loading_first_stage")
    if d_order and d_load:
        # Scan for bytes that change from 0xFF to 0x00 (Ash's ID)
        # in the team struct area
        print("\n  Bytes that changed from 0xFF to 0x00 (Ash ID) "
              "between order_select and loading:")
        count = 0
        for off in range(0x27CB50, 0x27CF40):
            if d_order[off] == 0xFF and d_load[off] == 0x00:
                print(f"    0x{off:06X}: 0xFF -> 0x00")
                count += 1
        print(f"  Total: {count} matches")

        # Scan wider for any region that gains the pattern 00 01 02
        # (Ash, Oswald, Shen Woo IDs)
        print(f"\n  Scanning full RAM for pattern 00 01 02 (charIDs) "
              f"present in loading but not in order_select:")
        pattern = bytes([0x00, 0x01, 0x02])
        load_positions = set()
        order_positions = set()
        pos = 0
        while True:
            idx = d_load.find(pattern, pos)
            if idx == -1: break
            load_positions.add(idx)
            pos = idx + 1
        pos = 0
        while True:
            idx = d_order.find(pattern, pos)
            if idx == -1: break
            order_positions.add(idx)
            pos = idx + 1

        new_positions = load_positions - order_positions
        print(f"  Pattern 00 01 02 in loading: {len(load_positions)} instances")
        print(f"  Pattern 00 01 02 in order_select: {len(order_positions)} instances")
        print(f"  NEW in loading (not in order_select): {len(new_positions)}")
        for p in sorted(new_positions)[:20]:
            context = d_load[max(0,p-4):p+12]
            print(f"    0x{p:06X}: {context.hex()}")

    # ===================================================================
    # D. DECOMP BUFFER DETAIL (0x300000)
    # ===================================================================
    print(f"\n{'='*70}")
    print("D. DECOMP BUFFER 0x300000 CONTENT ANALYSIS")
    print("=" * 70)
    for name in ["titulo", "char_select_ash", "loading_first_stage",
                  "loading_first_stage_2"]:
        d = snapshots.get(name)
        if not d:
            continue
        region = d[0x300000:0x400000]
        nonzero = sum(1 for b in region if b != 0)
        # Find first and last nonzero byte
        first_nz = next((i for i, b in enumerate(region) if b != 0), -1)
        last_nz = next((len(region)-1-i for i, b in enumerate(reversed(region)) if b != 0), -1)
        print(f"\n  [{name}]: {nonzero} nonzero bytes")
        if first_nz >= 0:
            print(f"    Range: 0x{0x300000+first_nz:06X} to 0x{0x300000+last_nz:06X}")
            # Scan for known headers
            for sig in [b"GBIX", b"PVRT", b"PVPL", b"SOSB", b"SOSP", b"ENDP", b"maz\x00"]:
                idx = region.find(sig)
                if idx >= 0:
                    print(f"    Found {sig} at 0x{0x300000+idx:06X}")
            # Show first 128 nonzero bytes
            start = first_nz
            print(f"    First 128 bytes from 0x{0x300000+start:06X}:")
            print(hexdump(region[start:start+128], 0x300000+start, max_lines=8))

    # ===================================================================
    # E. GAME PHASE INDICATOR SEARCH
    # ===================================================================
    print(f"\n{'='*70}")
    print("E. GAME PHASE INDICATOR SEARCH")
    print("   Looking for a byte/word that uniquely identifies each game phase")
    print("=" * 70)

    # Check specific known locations
    candidates = [
        (0x27CA7E, "byte", "cam_area_byte"),
        (0x27CAA4, "byte", "pre_cam_byte"),
        (0x27CB5D, "byte", "team_area_byte"),
        (0x27CB64, "byte", "team_inner_byte"),
    ]

    # Also scan the area 0x27C000-0x27CA00 for a "mode" byte
    # that changes uniquely per phase
    print("\n  Checking known volatile bytes across phases:")
    for off, fmt, label in candidates:
        vals = []
        for name in CHRONO_ORDER:
            d = snapshots.get(name)
            if d:
                v = r8(d, off) if fmt == "byte" else r16(d, off)
                vals.append((name, v))
        print(f"\n    {label} (0x{off:06X}):")
        for n, v in vals:
            print(f"      {n:35s}: 0x{v:02X} ({v})")

    # Scan lower game state area for phase indicators
    print("\n  Scanning 0x27C000-0x27CA00 for bytes that are unique per snapshot:")
    d_all = {name: snapshots[name] for name in CHRONO_ORDER if name in snapshots}
    unique_bytes = []
    for off in range(0x27C000, 0x27CA00):
        vals = tuple(d[off] for name, d in d_all.items())
        # Check if this byte differs between titulo, char_select, and loading
        if len(set(vals)) >= 4:  # At least 4 distinct values
            unique_bytes.append((off, vals))
    print(f"  Found {len(unique_bytes)} bytes with 4+ distinct values")
    for off, vals in unique_bytes[:10]:
        pairs = " ".join(f"{v:02X}" for v in vals)
        print(f"    0x{off:06X}: {pairs}")

    # ===================================================================
    # F. VOLATILE REGION 0x140000-0x1A0000 CHARACTERIZATION
    # ===================================================================
    print(f"\n{'='*70}")
    print("F. VOLATILE REGION 0x140000-0x1A0000 (heap/object pool?)")
    print("=" * 70)
    d_t = snapshots.get("titulo")
    d_l = snapshots.get("loading_first_stage")
    if d_t and d_l:
        for region_start in [0x140000, 0x150000, 0x180000, 0x190000]:
            region_t = d_t[region_start:region_start+0x100]
            region_l = d_l[region_start:region_start+0x100]
            print(f"\n  0x{region_start:06X} (titulo):")
            print(hexdump(region_t[:64], region_start, max_lines=4))
            print(f"  0x{region_start:06X} (loading):")
            print(hexdump(region_l[:64], region_start, max_lines=4))

    # ===================================================================
    # G. OBJECT POOL / ENTRY TABLE SCAN
    # ===================================================================
    print(f"\n{'='*70}")
    print("G. OBJECT POOL SCAN (0x200000 region)")
    print("   Trying to find the entry table that team.entries[] points to")
    print("=" * 70)
    d = snapshots.get("loading_first_stage")
    if d:
        # Even though entries are NULL in loading, let's see what's at 0x200000
        print("\n  0x200000 (first 256 bytes):")
        print(hexdump(d[0x200000:0x200100], 0x200000))

        # Scan for SH-4 style pointers (0x0C??????) in the team struct
        print("\n  SH-4 pointers (0x0C??????) in team P1 area:")
        for off in range(0x27CB50, 0x27CD48, 4):
            val = r32(d, off)
            if (val & 0xFF000000) == 0x0C000000:
                ram = (val & 0x1FFFFFFF) - 0x0C000000
                print(f"    0x{off:06X}: 0x{val:08X} -> RAM 0x{ram:06X}")

    # ===================================================================
    # H. CHRONOLOGICAL DIFF CHAIN
    # ===================================================================
    print(f"\n{'='*70}")
    print("H. TEAM STRUCT CHRONOLOGICAL CHANGES")
    print("   Tracking team P1 (0x27CB50) and team P2 (0x27CD48) byte-by-byte")
    print("=" * 70)

    prev_name = None
    prev_data = None
    for name in CHRONO_ORDER:
        d = snapshots.get(name)
        if not d:
            continue
        if prev_data is not None:
            # Compare team P1
            for label, toff in [("P1", 0x27CB50), ("P2", 0x27CD48)]:
                changes = []
                for i in range(0x1F8):
                    a = prev_data[toff+i]
                    b = d[toff+i]
                    if a != b:
                        changes.append((i, a, b))
                if changes:
                    print(f"\n  {prev_name} -> {name} ({label}):")
                    for off, a, b in changes[:15]:
                        print(f"    +0x{off:03X}: 0x{a:02X} -> 0x{b:02X}")
                    if len(changes) > 15:
                        print(f"    ... ({len(changes)} total)")

        prev_name = name
        prev_data = d

    print(f"\n{'='*70}")
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
