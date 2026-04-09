# -*- coding: utf-8 -*-
"""
Comprehensive Snapshot Analyzer
================================
Reads all .bin snapshots, extracts all known struct data,
compares across states, and outputs a structured report.

Usage:
    python tools/analyze_snapshots.py [snapshot_dir]
"""
import struct
import sys
import os
import json
import hashlib
from collections import OrderedDict

# Force UTF-8 output
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def hexdump(data, offset=0, width=16, max_lines=None):
    lines = []
    for i in range(0, len(data), width):
        if max_lines and len(lines) >= max_lines:
            lines.append(f"  ... ({len(data) - i} bytes remaining)")
            break
        chunk = data[i:i+width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {offset+i:06X}: {hex_part:<{width*3}}  {ascii_part}")
    return "\n".join(lines)


def r8(d, o):  return d[o] if o < len(d) else None
def r16(d, o): return struct.unpack_from("<H", d, o)[0] if o+2 <= len(d) else None
def rs16(d, o): return struct.unpack_from("<h", d, o)[0] if o+2 <= len(d) else None
def r32(d, o): return struct.unpack_from("<I", d, o)[0] if o+4 <= len(d) else None
def rs32(d, o): return struct.unpack_from("<i", d, o)[0] if o+4 <= len(d) else None
def rfloat(d, o): return struct.unpack_from("<f", d, o)[0] if o+4 <= len(d) else None

def sh4_to_ram(addr):
    if not addr or addr == 0:
        return None
    phys = addr & 0x1FFFFFFF
    off = phys - 0x0C000000
    return off if 0 <= off < 0x01000000 else None


ROSTER = {
    0x00: "Ash", 0x01: "Oswald", 0x02: "Shen Woo",
    0x03: "Elisabeth", 0x04: "Duo Lon", 0x05: "Benimaru",
    0x06: "Terry", 0x07: "Kim", 0x08: "Duck King",
    0x09: "Ryo", 0x0A: "Yuri", 0x0B: "King",
    0x0C: "Kyo", 0x0D: "Shingo", 0x0E: "Iori",
    0x0F: "K'", 0x10: "Kula", 0x11: "Maxima",
    0x12: "Athena", 0x13: "Momoko", 0x14: "Psycho",
    0x15: "Gato", 0x16: "B.Jenet", 0x17: "Tizoc",
    0x18: "Ralf", 0x19: "Clark", 0x1A: "Whip",
    0x1B: "Vanessa", 0x1C: "Blue Mary", 0x1D: "Ramon",
    0x1E: "Malin", 0x1F: "Kasumi", 0x20: "Eiji",
    0x21: "Adelheid", 0x22: "Gai", 0x23: "Sho",
    0x24: "Silber", 0x25: "Jyazu", 0x26: "Hayate",
    0x27: "Shion", 0x28: "Magaki",
    0x29: "Mai", 0x2A: "Iori(F)", 0x2B: "Kyo(E)",
    0x2C: "Robert", 0x2D: "Geese", 0x2E: "Mr.Big",
    0x2F: "Kyo(EX)",
}

CAM_OFF      = 0x27CAA8
TEAM_OFFS    = [0x27CB50, 0x27CD48]
TEAM_SIZE    = 0x1F8


def extract_camera(data):
    return {
        "posX": rs16(data, CAM_OFF),
        "posY": rs16(data, CAM_OFF+2),
        "restrictor": rfloat(data, CAM_OFF+4),
        "raw_8": data[CAM_OFF:CAM_OFF+8].hex(),
    }


def extract_team(data, team_off):
    info = {
        "offset": f"0x{team_off:06X}",
        "leader": r8(data, team_off+0x001),
        "point": r8(data, team_off+0x003),
        "comboCounter": r8(data, team_off+0x007),
        "super_raw": r32(data, team_off+0x038),
        "skillStock_raw": r32(data, team_off+0x03C),
        "entries": [],
        "roster": [],
    }

    # Entries (SH-4 pointers)
    for i in range(3):
        ptr = r32(data, team_off + 0x144 + i*4)
        ram = sh4_to_ram(ptr) if ptr else None
        entry_info = {"ptr": f"0x{ptr:08X}" if ptr else "NULL", "ram": None, "player_off": None}
        if ram is not None:
            entry_info["ram"] = f"0x{ram:06X}"
            data_ptr = r32(data, ram + 0x10)
            data_ram = sh4_to_ram(data_ptr) if data_ptr else None
            if data_ram is not None:
                entry_info["player_off"] = f"0x{data_ram - 0x614:06X}"
        info["entries"].append(entry_info)

    # PlayerExtra
    for i in range(3):
        pe = team_off + 0x150 + i * 0x20
        cid = r8(data, pe+0x001)
        info["roster"].append({
            "slot": i,
            "charID": cid,
            "char": ROSTER.get(cid, f"?0x{cid:02X}") if cid is not None else "?",
            "health": rs16(data, pe+0x008),
            "visibleHP": rs16(data, pe+0x00A),
            "maxHealth": rs16(data, pe+0x00C),
            "teamPosition": r8(data, pe+0x010),
            "raw": data[pe:pe+0x20].hex(),
        })

    return info


def resolve_player_offset(data, team_off):
    """Resolve the active player struct offset from team data."""
    point = r8(data, team_off + 0x003)
    if point is None:
        return None

    for entry_idx in range(3):
        pe_off = team_off + 0x150 + entry_idx * 0x20
        team_pos = r8(data, pe_off + 0x010)
        if team_pos != point:
            continue

        entry_ptr = r32(data, team_off + 0x144 + entry_idx * 4)
        entry_ram = sh4_to_ram(entry_ptr) if entry_ptr else None
        if entry_ram is None:
            continue
        data_ptr = r32(data, entry_ram + 0x10)
        data_ram = sh4_to_ram(data_ptr) if data_ptr else None
        if data_ram is None:
            continue

        poff = data_ram - 0x614
        if 0 <= poff and poff + 0x584 <= len(data):
            return poff
    return None


def extract_player(data, poff):
    """Extract all known and candidate fields from player struct."""
    if poff is None:
        return None

    info = OrderedDict()
    info["_offset"] = f"0x{poff:06X}"

    # Position (coordPair at +0x000)
    info["posX"] = rs16(data, poff)
    info["posY"] = rs16(data, poff+2)

    # Velocity (fixedPair at +0x018)
    info["velX_fixed"] = rs32(data, poff+0x18)
    info["velY_fixed"] = rs32(data, poff+0x1C)

    # Facing at +0x08C
    info["facing"] = r8(data, poff+0x08C)

    # Action region
    info["actionID_u8"] = r8(data, poff+0x0EC)
    info["actionID_u16"] = r16(data, poff+0x0EC)
    info["byte_0ED"] = r8(data, poff+0x0ED)
    info["prevActionID_u8"] = r8(data, poff+0x0EE)
    info["prevActionID_u16"] = r16(data, poff+0x0EE)
    info["byte_0EF"] = r8(data, poff+0x0EF)
    info["actionSignal_s16"] = rs16(data, poff+0x0F2)
    info["action_region_raw"] = data[poff+0x0E0:poff+0x100].hex()

    # Animation region
    info["animFrameIndex_u8"] = r8(data, poff+0x2A4)
    info["animFrameIndex_u16"] = r16(data, poff+0x2A4)
    info["byte_2A5"] = r8(data, poff+0x2A5)
    info["spriteOffsetX"] = rs16(data, poff+0x2A8)
    info["spriteOffsetY"] = rs16(data, poff+0x2AA)
    info["animPropertyA"] = r8(data, poff+0x2B2)
    info["animPropertyB"] = r8(data, poff+0x2B3)
    info["animPhaseToggle"] = r8(data, poff+0x2B4)

    # Hitboxes
    info["hitboxes"] = []
    for i in range(7):
        hb_off = poff + 0x314 + i * 10
        raw = data[hb_off:hb_off+10]
        px, py = struct.unpack_from("<hh", raw, 0)
        box_id = raw[4]
        b5, b6 = raw[5], raw[6]
        w, h = raw[7], raw[8]
        b9 = raw[9]
        info["hitboxes"].append({
            "pos": (px, py),
            "boxID": f"0x{box_id:02X}",
            "bytes_5_6": f"0x{b5:02X} 0x{b6:02X}",
            "w": w, "h": h,
            "byte_9": f"0x{b9:02X}",
            "raw": raw.hex(),
        })

    info["hitboxesActive_u8"] = r8(data, poff+0x39E)
    info["hitboxesActive_u16"] = r16(data, poff+0x39E)

    # Flags region (+0x268)
    info["flags_raw_40B"] = data[poff+0x268:poff+0x290].hex()

    # Stun
    info["stunTimer"] = rs16(data, poff+0x582)

    return info


def extract_decomp_regions(data):
    """Fingerprint decompression buffer candidate regions."""
    regions = [
        ("0x300000", 0x300000, 0x100000),
        ("0x400000", 0x400000, 0x100000),
        ("0x500000", 0x500000, 0x100000),
        ("0x600000", 0x600000, 0x100000),
        ("0x700000", 0x700000, 0x100000),
        ("0x800000", 0x800000, 0x100000),
    ]
    results = []
    for name, start, size in regions:
        end = min(start + size, len(data))
        region = data[start:end]
        nonzero = sum(1 for b in region if b != 0)

        # Detect known headers
        headers_found = []
        for sig, label in [(b"GBIX", "PVR/GBIX"), (b"PVRT", "PVR"), (b"PVPL", "PVPL"),
                           (b"SOSB", "SOSB"), (b"SOSP", "SOSP"), (b"ENDP", "ENDP"),
                           (b"maz\x00", "MAZ")]:
            pos = 0
            while True:
                idx = region.find(sig, pos)
                if idx == -1:
                    break
                headers_found.append({"sig": label, "offset": f"0x{start+idx:06X}"})
                pos = idx + 1
                if len(headers_found) > 20:
                    break

        # Hash for comparison
        h = hashlib.md5(region).hexdigest()[:16]

        results.append({
            "region": name,
            "nonzero": nonzero,
            "total": len(region),
            "pct": f"{100*nonzero/len(region):.1f}%",
            "md5_16": h,
            "headers": headers_found,
            "first_32": region[:32].hex(),
        })
    return results


def scan_for_game_phase(data):
    """Try to detect the game phase from memory patterns."""
    markers = []

    # Check MUTEKI
    if data[0x10FF50:0x10FF56] == b"MUTEKI":
        markers.append("MUTEKI_OK")

    # Camera active?
    cam_rest = rfloat(data, CAM_OFF+4)
    if cam_rest and cam_rest == 1.0:
        markers.append("camera_active")

    # Teams populated?
    for side, toff in enumerate(TEAM_OFFS):
        char0 = r8(data, toff + 0x150 + 0x001)
        if char0 is not None and char0 != 0xFF:
            markers.append(f"P{side+1}_team_populated")

    # Player structs valid?
    for side, toff in enumerate(TEAM_OFFS):
        poff = resolve_player_offset(data, toff)
        if poff is not None:
            # Check if position looks reasonable (world coords)
            px = rs16(data, poff)
            if px is not None and -2000 < px < 2000:
                markers.append(f"P{side+1}_player_valid(pos={px})")

    return markers


def diff_regions(data_a, data_b, name_a, name_b, start, size):
    """Compare a memory region between two snapshots, return changed byte ranges."""
    a = data_a[start:start+size]
    b = data_b[start:start+size]
    if a == b:
        return None

    changed_bytes = 0
    changed_ranges = []
    in_change = False
    change_start = 0

    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            changed_bytes += 1
            if not in_change:
                in_change = True
                change_start = i
        else:
            if in_change:
                changed_ranges.append((change_start + start, i + start - 1))
                in_change = False
    if in_change:
        changed_ranges.append((change_start + start, min(len(a), len(b)) - 1 + start))

    return {
        "changed_bytes": changed_bytes,
        "changed_ranges": len(changed_ranges),
        "first_ranges": [(f"0x{s:06X}", f"0x{e:06X}", e-s+1) for s, e in changed_ranges[:10]],
    }


def main():
    snap_dir = sys.argv[1] if len(sys.argv) > 1 else "aw_data/snapshots"

    # Discover snapshots
    bins = sorted([f for f in os.listdir(snap_dir) if f.endswith(".bin")])
    if not bins:
        print(f"No .bin files found in {snap_dir}")
        sys.exit(1)

    print(f"{'='*70}")
    print(f"SNAPSHOT ANALYSIS REPORT")
    print(f"Directory: {snap_dir}")
    print(f"Snapshots found: {len(bins)}")
    print(f"{'='*70}")
    print()

    # Load all snapshots
    snapshots = OrderedDict()
    for fn in bins:
        path = os.path.join(snap_dir, fn)
        with open(path, "rb") as f:
            data = f.read()
        name = fn.replace(".bin", "")
        snapshots[name] = data

        # Try to load JSON metadata
        json_path = path.replace(".bin", ".json")
        ts = ""
        if os.path.exists(json_path):
            with open(json_path) as jf:
                meta = json.load(jf)
                ts = meta.get("timestamp", "")

        print(f"  [{name}] {len(data):,} bytes, ts={ts}")
    print()

    # ===================================================================
    # PER-SNAPSHOT ANALYSIS
    # ===================================================================
    all_results = OrderedDict()

    for name, data in snapshots.items():
        print(f"\n{'#'*70}")
        print(f"# SNAPSHOT: {name}")
        print(f"{'#'*70}")

        result = {"name": name}

        # Game phase detection
        markers = scan_for_game_phase(data)
        result["phase_markers"] = markers
        print(f"\n  Phase markers: {', '.join(markers) if markers else 'NONE'}")

        # Camera
        cam = extract_camera(data)
        result["camera"] = cam
        print(f"\n  Camera: X={cam['posX']}, Y={cam['posY']}, "
              f"restrictor={cam['restrictor']}")

        # Teams
        for side, toff in enumerate(TEAM_OFFS):
            team = extract_team(data, toff)
            result[f"team_P{side+1}"] = team
            print(f"\n  Team P{side+1}: point={team['point']}, "
                  f"combo={team['comboCounter']}, "
                  f"super=0x{team['super_raw']:X}, "
                  f"skill=0x{team['skillStock_raw']:X}")
            for r in team["roster"]:
                print(f"    [{r['slot']}] {r['char']:12s} (ID=0x{r['charID']:02X}) "
                      f"HP={r['health']}/{r['maxHealth']} "
                      f"teamPos={r['teamPosition']}")
            for i, e in enumerate(team["entries"]):
                s = f"    entry[{i}]: {e['ptr']}"
                if e["ram"]:
                    s += f" -> RAM {e['ram']}"
                if e["player_off"]:
                    s += f" -> player {e['player_off']}"
                print(s)

        # Players
        for side, toff in enumerate(TEAM_OFFS):
            poff = resolve_player_offset(data, toff)
            if poff is not None:
                player = extract_player(data, poff)
                result[f"player_P{side+1}"] = player
                print(f"\n  Player P{side+1} at {player['_offset']}:")
                print(f"    pos=({player['posX']}, {player['posY']})")
                print(f"    vel=({player['velX_fixed']}, {player['velY_fixed']})")
                print(f"    facing={player['facing']}")
                print(f"    actionID: u8={player['actionID_u8']}, "
                      f"u16={player['actionID_u16']}, "
                      f"byte_0ED=0x{player['byte_0ED']:02X}")
                print(f"    prevAction: u8={player['prevActionID_u8']}, "
                      f"u16={player['prevActionID_u16']}")
                print(f"    animFrame: u8={player['animFrameIndex_u8']}, "
                      f"u16={player['animFrameIndex_u16']}, "
                      f"byte_2A5=0x{player['byte_2A5']:02X}")
                print(f"    sprite offset: ({player['spriteOffsetX']}, "
                      f"{player['spriteOffsetY']})")
                print(f"    stun={player['stunTimer']}")
                print(f"    hitboxesActive: u8=0x{player['hitboxesActive_u8']:02X}, "
                      f"u16=0x{player['hitboxesActive_u16']:04X}")
                for hb in player["hitboxes"]:
                    if hb["w"] > 0 or hb["h"] > 0 or hb["boxID"] != "0x00":
                        print(f"    hb: pos={hb['pos']} id={hb['boxID']} "
                              f"w={hb['w']} h={hb['h']} "
                              f"mid={hb['bytes_5_6']} b9={hb['byte_9']}")
            else:
                result[f"player_P{side+1}"] = None
                print(f"\n  Player P{side+1}: COULD NOT RESOLVE")

        # Decompression regions
        decomp = extract_decomp_regions(data)
        result["decomp_regions"] = decomp
        print(f"\n  Decomp regions:")
        for dr in decomp:
            print(f"    {dr['region']}: {dr['pct']} used ({dr['nonzero']:,} bytes), "
                  f"md5={dr['md5_16']}")
            if dr["headers"]:
                hdr_summary = ", ".join(f"{h['sig']}@{h['offset']}" for h in dr["headers"][:5])
                print(f"      headers: {hdr_summary}")

        all_results[name] = result

    # ===================================================================
    # CROSS-SNAPSHOT COMPARISON
    # ===================================================================
    print(f"\n\n{'='*70}")
    print(f"CROSS-SNAPSHOT COMPARISON")
    print(f"{'='*70}")

    names = list(snapshots.keys())
    compare_regions = [
        ("camera_area", 0x27CA00, 0x100),
        ("team_P1", 0x27CB50, 0x1F8),
        ("team_P2", 0x27CD48, 0x1F8),
        ("game_state_area", 0x27C000, 0x1000),
        ("decomp_0x300000", 0x300000, 0x100000),
        ("decomp_0x400000", 0x400000, 0x100000),
        ("decomp_0x500000", 0x500000, 0x100000),
        ("decomp_0x600000", 0x600000, 0x100000),
        ("decomp_0x700000", 0x700000, 0x100000),
        ("code_area", 0x100000, 0x100000),
    ]

    # Sequential comparison (each snapshot vs next)
    for i in range(len(names) - 1):
        n_a, n_b = names[i], names[i+1]
        d_a, d_b = snapshots[n_a], snapshots[n_b]
        print(f"\n  --- {n_a} vs {n_b} ---")

        for region_name, start, size in compare_regions:
            diff = diff_regions(d_a, d_b, n_a, n_b, start, size)
            if diff:
                print(f"    {region_name}: {diff['changed_bytes']:,} bytes changed "
                      f"in {diff['changed_ranges']} ranges")
                for s, e, sz in diff["first_ranges"][:3]:
                    print(f"      {s}-{e} ({sz} bytes)")
            else:
                print(f"    {region_name}: IDENTICAL")

    # ===================================================================
    # FULL 16MB DIFF SUMMARY
    # ===================================================================
    print(f"\n\n{'='*70}")
    print(f"FULL 16MB CHANGE HEATMAP")
    print(f"{'='*70}")
    print(f"  (64KB blocks, showing % changed bytes vs first snapshot)")

    ref = snapshots[names[0]]
    block_size = 0x10000  # 64KB
    n_blocks = len(ref) // block_size

    header = f"  {'Block':>10s}"
    for name in names[1:]:
        header += f" {name[:12]:>12s}"
    print(header)

    for bi in range(n_blocks):
        start = bi * block_size
        ref_block = ref[start:start+block_size]

        row = f"  0x{start:06X}  "
        any_change = False
        for name in names[1:]:
            other_block = snapshots[name][start:start+block_size]
            if ref_block == other_block:
                row += f" {'---':>12s}"
            else:
                changed = sum(1 for a, b in zip(ref_block, other_block) if a != b)
                pct = 100 * changed / block_size
                row += f" {pct:>11.1f}%"
                any_change = True

        if any_change:
            print(row)

    # ===================================================================
    # GAME PHASE TIMELINE
    # ===================================================================
    print(f"\n\n{'='*70}")
    print(f"GAME PHASE TIMELINE")
    print(f"{'='*70}")
    for name in names:
        res = all_results[name]
        markers = res["phase_markers"]
        cam = res["camera"]
        team_info = ""
        if res.get("team_P1") and res["team_P1"]["roster"][0]["charID"] != 0xFF:
            chars = [r["char"] for r in res["team_P1"]["roster"]]
            team_info = f" | P1=[{','.join(chars)}]"
        if res.get("team_P2") and res["team_P2"]["roster"][0]["charID"] != 0xFF:
            chars = [r["char"] for r in res["team_P2"]["roster"]]
            team_info += f" P2=[{','.join(chars)}]"
        print(f"  {name:35s} cam=({cam['posX']:>4d},{cam['posY']:>4d}) "
              f"{'|'.join(markers)}{team_info}")

    print(f"\n{'='*70}")
    print(f"END OF REPORT")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
