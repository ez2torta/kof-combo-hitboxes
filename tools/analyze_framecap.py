"""
Analyze framecap sessions — reconstruct frames, extract game state,
and find the exact frame where player structs become valid.
"""
import os
import sys
import struct
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis_utils import (
    SH4_RAM_SIZE, CAMERA_PTR, TEAM_PTRS, TEAM_SIZE,
    PLAYER_STRUCT_SIZE, PLAYER_OFFSETS, HITBOX_OFFSET, HITBOX_SIZE,
    HITBOX_COUNT, PLAYER_EXTRA_OFFSET, PLAYER_EXTRA_SIZE,
    TEAM_ENTRIES_OFFSET, PLAYER_EXTRA_FIELDS,
    extract_camera, extract_team_fields, extract_player_fields,
    extract_hitboxes, resolve_player_offset, char_name,
)

PAGE_SIZE = 4096


def load_framecap(session_dir):
    """Load a framecap session: base frame + delta stream."""
    base_path = os.path.join(session_dir, "frame_base.bin")
    delta_path = os.path.join(session_dir, "deltas.bin")

    with open(base_path, "rb") as f:
        base = bytearray(f.read())

    with open(delta_path, "rb") as f:
        header = f.read(20)
        magic, version, page_size, num_pages, frame_count = struct.unpack(
            "<4sIIII", header)
        assert magic == b"FCAP", f"Bad magic: {magic}"

        frames = []
        for _ in range(frame_count):
            hdr = f.read(6)
            if len(hdr) < 6:
                break
            frame_num, page_count = struct.unpack("<IH", hdr)
            pages = []
            for _ in range(page_count):
                pg_hdr = f.read(2)
                if len(pg_hdr) < 2:
                    break
                pg_idx = struct.unpack("<H", pg_hdr)[0]
                pg_data = f.read(page_size)
                pages.append((pg_idx, pg_data))
            frames.append((frame_num, pages))

    return base, frames, frame_count


def reconstruct_frame(base, deltas_up_to, frame_idx):
    """Reconstruct a specific frame by applying deltas 0..frame_idx."""
    ram = bytearray(base)
    for i in range(frame_idx + 1):
        if i >= len(deltas_up_to):
            break
        _, pages = deltas_up_to[i]
        for pg_idx, pg_data in pages:
            off = pg_idx * PAGE_SIZE
            ram[off:off + PAGE_SIZE] = pg_data
    return bytes(ram)


def extract_full_state(data):
    """Extract all game state from a reconstructed frame."""
    cam = extract_camera(data)
    teams = []
    players = []
    for side in range(2):
        team = extract_team_fields(data, TEAM_PTRS[side])
        teams.append(team)

        point = team.get("point", 0)
        player_info = {"side": side + 1, "resolved": False}

        # Try to resolve each slot
        for slot in range(3):
            pe = team["playerExtra"][slot]
            off = resolve_player_offset(data, TEAM_PTRS[side], slot)
            if off is not None:
                player_info["resolved"] = True
                player_info["slot"] = slot
                player_info["offset"] = off
                player_info["fields"] = extract_player_fields(data, off)
                player_info["hitboxes"] = extract_hitboxes(data, off)
                player_info["charID"] = pe.get("charID", 0xFF)
                player_info["charName"] = char_name(pe.get("charID", 0xFF))
                break  # just find the first resolvable for now

        # Also try all 3 slots
        player_info["all_slots"] = []
        for slot in range(3):
            pe = team["playerExtra"][slot]
            off = resolve_player_offset(data, TEAM_PTRS[side], slot)
            slot_info = {
                "slot": slot,
                "charID": pe.get("charID", 0xFF),
                "charName": char_name(pe.get("charID", 0xFF)),
                "health": pe.get("health", 0),
                "teamPos": pe.get("teamPosition", 0xFF),
                "offset": off,
            }
            if off is not None:
                slot_info["fields"] = extract_player_fields(data, off)
                slot_info["hitboxes"] = extract_hitboxes(data, off)
            player_info["all_slots"].append(slot_info)

        players.append(player_info)

    return {"camera": cam, "teams": teams, "players": players}


def scan_entries(data, side):
    """Read the raw entry pointer values for a team."""
    team_off = TEAM_PTRS[side]
    entries = []
    for i in range(3):
        ptr_off = team_off + TEAM_ENTRIES_OFFSET + i * 4
        raw = data[ptr_off:ptr_off + 4]
        val = struct.unpack("<I", raw)[0]
        entries.append(val)
    return entries


def find_player_transition(base, frames):
    """Find the exact frame where player entries become non-NULL."""
    ram = bytearray(base)
    first_valid = None
    for i, (frame_num, pages) in enumerate(frames):
        for pg_idx, pg_data in pages:
            off = pg_idx * PAGE_SIZE
            ram[off:off + PAGE_SIZE] = pg_data

        entries_p1 = scan_entries(ram, 0)
        entries_p2 = scan_entries(ram, 1)

        any_valid = any(e != 0 for e in entries_p1 + entries_p2)
        if any_valid and first_valid is None:
            first_valid = frame_num
            return first_valid, i, entries_p1, entries_p2

    return None, None, [], []


def track_game_state_timeline(base, frames, sample_every=1):
    """Track key game state values across all frames."""
    ram = bytearray(base)
    timeline = []
    for i, (frame_num, pages) in enumerate(frames):
        for pg_idx, pg_data in pages:
            off = pg_idx * PAGE_SIZE
            ram[off:off + PAGE_SIZE] = pg_data

        if i % sample_every != 0:
            continue

        cam = extract_camera(bytes(ram))
        entries_p1 = scan_entries(ram, 0)
        entries_p2 = scan_entries(ram, 1)

        # Team quick fields
        t1 = extract_team_fields(bytes(ram), TEAM_PTRS[0])
        t2 = extract_team_fields(bytes(ram), TEAM_PTRS[1])

        entry = {
            "frame": frame_num,
            "cam": [cam["posX"], cam["posY"]] if cam else None,
            "entries_p1": [f"0x{e:08X}" for e in entries_p1],
            "entries_p2": [f"0x{e:08X}" for e in entries_p2],
            "p1_point": t1.get("point"),
            "p2_point": t2.get("point"),
            "p1_super": t1.get("super"),
            "p2_super": t2.get("super"),
        }

        # If entries valid, extract player state
        for side, entries, team in [(0, entries_p1, t1), (1, entries_p2, t2)]:
            point = team.get("point", 0)
            for slot in range(3):
                pe = team["playerExtra"][slot]
                if pe.get("teamPosition") == point:
                    off = resolve_player_offset(bytes(ram), TEAM_PTRS[side], slot)
                    if off is not None:
                        pf = extract_player_fields(bytes(ram), off)
                        prefix = f"p{side+1}"
                        entry[f"{prefix}_char"] = char_name(pe.get("charID", 0xFF))
                        entry[f"{prefix}_hp"] = pe.get("health", 0)
                        entry[f"{prefix}_action"] = pf.get("actionID")
                        entry[f"{prefix}_anim"] = pf.get("animFrameIndex")
                        entry[f"{prefix}_pos"] = pf.get("position")
                        entry[f"{prefix}_facing"] = pf.get("facing")
                        entry[f"{prefix}_vel"] = pf.get("velocity")
                        entry[f"{prefix}_hbActive"] = pf.get("hitboxesActive")
                        entry[f"{prefix}_stun"] = pf.get("stunTimer")
                    break

        timeline.append(entry)

    return timeline


def track_changed_regions(base, frames):
    """For each frame, report which named regions had changes."""
    REGIONS = [
        ("bios",      0x000000, 0x030000),
        ("code",      0x030000, 0x133000),
        ("meta",      0x133000, 0x200000),
        ("obj_pool",  0x200000, 0x270000),
        ("game_st",   0x270000, 0x280000),
        ("heap",      0x280000, 0x300000),
        ("decomp",    0x300000, 0x400000),
        ("gfx_lo",    0x400000, 0x800000),
        ("gfx_hi",    0x800000, 0xC00000),
        ("ram_top",   0xC00000, 0x1000000),
    ]

    per_frame = []
    for frame_num, pages in frames:
        counts = {}
        for pg_idx, pg_data in pages:
            addr = pg_idx * PAGE_SIZE
            for rname, rstart, rend in REGIONS:
                if rstart <= addr < rend:
                    counts[rname] = counts.get(rname, 0) + 1
                    break
        per_frame.append((frame_num, counts))
    return per_frame


def analyze_session(session_dir, label):
    """Full analysis of one framecap session."""
    print(f"\n{'='*70}")
    print(f"  ANALYZING: {label}")
    print(f"  {session_dir}")
    print(f"{'='*70}\n")

    base, frames, frame_count = load_framecap(session_dir)
    print(f"Loaded: {frame_count} frames, base={len(base)} bytes")

    # --- 1. Find player struct transition ---
    print("\n--- 1. Player Entry Transition ---")
    trans_frame, trans_idx, e1, e2 = find_player_transition(base, frames)
    if trans_frame is not None:
        print(f"  First non-NULL entries at frame {trans_frame}")
        print(f"    P1 entries: {[f'0x{e:08X}' for e in e1]}")
        print(f"    P2 entries: {[f'0x{e:08X}' for e in e2]}")
    else:
        # Check the base frame itself
        e1_base = scan_entries(base, 0)
        e2_base = scan_entries(base, 1)
        any_base = any(e != 0 for e in e1_base + e2_base)
        if any_base:
            print(f"  Entries already valid in base frame!")
            print(f"    P1 entries: {[f'0x{e:08X}' for e in e1_base]}")
            print(f"    P2 entries: {[f'0x{e:08X}' for e in e2_base]}")
            trans_frame = -1  # base frame
        else:
            print(f"  No valid entries found in any frame!")

    # --- 2. Extract state at key frames ---
    print("\n--- 2. Key Frame States ---")
    key_frames = []
    if trans_frame is not None and trans_frame >= 0:
        # Frames around transition
        for f in [0, max(0, trans_frame - 2), trans_frame,
                  trans_frame + 1, trans_frame + 5,
                  min(frame_count - 1, trans_frame + 20)]:
            if f not in key_frames:
                key_frames.append(f)
    else:
        # Sample evenly
        for f in [0, frame_count // 4, frame_count // 2,
                  3 * frame_count // 4, frame_count - 1]:
            if f not in key_frames and f >= 0:
                key_frames.append(f)

    # Always include last frame
    if frame_count - 1 not in key_frames:
        key_frames.append(frame_count - 1)

    key_states = {}
    for fidx in sorted(key_frames):
        if fidx < 0:
            continue
        data = reconstruct_frame(base, frames, fidx)
        state = extract_full_state(data)
        key_states[fidx] = state

        cam = state["camera"]
        print(f"\n  Frame {fidx}:")
        if cam:
            print(f"    Camera: ({cam['posX']}, {cam['posY']}) "
                  f"restrictor={cam['restrictor']}")

        for pi in state["players"]:
            side = pi["side"]
            if pi["resolved"]:
                f = pi["fields"]
                print(f"    P{side}: {pi['charName']} "
                      f"action={f.get('actionID')} "
                      f"anim={f.get('animFrameIndex')} "
                      f"pos={f.get('position')} "
                      f"facing={f.get('facing')} "
                      f"hbAct=0x{f.get('hitboxesActive', 0):02X} "
                      f"stun={f.get('stunTimer')}")
                hbs = pi["hitboxes"]
                active_hbs = [h for h in hbs
                              if h["width"] > 0 or h["height"] > 0]
                if active_hbs:
                    for h in active_hbs:
                        print(f"      box[{h['slot']}] id={h['boxID']} "
                              f"pos=({h['posX']},{h['posY']}) "
                              f"size={h['width']}x{h['height']}")
            else:
                # Show playerExtra anyway
                team = state["teams"][side - 1]
                chars = [f"{char_name(pe.get('charID', 0xFF))}"
                         f"(hp={pe.get('health',0)})"
                         for pe in team.get("playerExtra", [])]
                entries_raw = scan_entries(
                    reconstruct_frame(base, frames, fidx), side - 1)
                print(f"    P{side}: entries NULL, roster={chars}")

    # --- 3. Full timeline ---
    print("\n--- 3. Game State Timeline ---")
    sample = max(1, frame_count // 60)  # ~60 samples
    timeline = track_game_state_timeline(base, frames, sample_every=sample)

    for entry in timeline[:5]:
        print(f"  f={entry['frame']:4d} cam={entry.get('cam')} "
              f"p1_act={entry.get('p1_action','?')} "
              f"p2_act={entry.get('p2_action','?')} "
              f"p1_hp={entry.get('p1_hp','?')} "
              f"p2_hp={entry.get('p2_hp','?')}")
    if len(timeline) > 5:
        print(f"  ... ({len(timeline)} samples total)")

    # --- 4. Region change stats ---
    print("\n--- 4. Region Change Pattern ---")
    region_data = track_changed_regions(base, frames)

    # Find active/quiet frame groups
    active_frames = [(fn, c) for fn, c in region_data if sum(c.values()) > 0]
    quiet_frames = [(fn, c) for fn, c in region_data if sum(c.values()) == 0]
    print(f"  Active frames: {len(active_frames)} / {frame_count}")
    print(f"  Quiet frames: {len(quiet_frames)} / {frame_count}")

    if active_frames:
        # Average per-region pages/frame in active frames
        region_avg = {}
        for _, counts in active_frames:
            for rname, cnt in counts.items():
                region_avg.setdefault(rname, []).append(cnt)
        print(f"\n  Region averages (active frames only):")
        for rname in sorted(region_avg, key=lambda r: -sum(region_avg[r])):
            vals = region_avg[rname]
            avg = sum(vals) / len(vals)
            mx = max(vals)
            print(f"    {rname:<12} avg={avg:5.1f} pg/f  "
                  f"max={mx:4d}  present={len(vals)}/{len(active_frames)}")

    # --- 5. Detailed hitbox snapshot (if player resolved) ---
    print("\n--- 5. Hitbox Snapshots ---")
    # Find a frame in active combat
    for fidx in sorted(key_states.keys(), reverse=True):
        state = key_states[fidx]
        for pi in state["players"]:
            if pi["resolved"]:
                f = pi["fields"]
                if f.get("hitboxesActive", 0) > 0:
                    print(f"  Frame {fidx}, P{pi['side']} "
                          f"({pi['charName']}):")
                    print(f"    action={f['actionID']} "
                          f"anim={f['animFrameIndex']} "
                          f"hbActive=0x{f['hitboxesActive']:02X}")
                    for h in pi["hitboxes"]:
                        w, ht = h["width"], h["height"]
                        marker = " *" if w > 0 or ht > 0 else ""
                        print(f"    [{h['slot']}] id={h['boxID']:2d} "
                              f"pos=({h['posX']:+4d},{h['posY']:+4d}) "
                              f"size={w:3d}x{ht:3d}{marker}")

    return {
        "label": label,
        "session_dir": session_dir,
        "frame_count": frame_count,
        "trans_frame": trans_frame,
        "key_states": key_states,
        "timeline": timeline,
        "region_data": region_data,
    }


def write_report(results, output_path):
    """Write combined markdown report for all analyzed sessions."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Framecap Analysis Report\n\n")
        f.write(f"**Fecha**: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")

        for r in results:
            label = r["label"]
            fc = r["frame_count"]
            tf = r["trans_frame"]
            f.write(f"---\n\n## {label}\n\n")
            f.write(f"- Frames: {fc}\n")
            f.write(f"- Directorio: `{os.path.basename(r['session_dir'])}`\n")

            if tf is not None and tf >= 0:
                f.write(f"- **Player entries validos desde frame {tf}**\n")
            elif tf == -1:
                f.write(f"- **Player entries ya validos en frame base**\n")
            else:
                f.write(f"- Player entries: nunca validos en esta captura\n")
            f.write("\n")

            # Key frame table
            f.write("### Estados en Frames Clave\n\n")
            f.write("| Frame | Camera | P1 | Action | Anim | HP | "
                    "P2 | Action | Anim | HP |\n")
            f.write("|-------|--------|----|--------|------|----"
                    "|----|--------|------|----|\n")

            for fidx in sorted(r["key_states"].keys()):
                state = r["key_states"][fidx]
                cam = state["camera"]
                cam_str = f"({cam['posX']},{cam['posY']})" if cam else "?"

                p1 = state["players"][0]
                p2 = state["players"][1]

                def player_cols(pi, teams, side):
                    if pi["resolved"]:
                        pf = pi["fields"]
                        return (pi["charName"],
                                str(pf.get("actionID", "?")),
                                str(pf.get("animFrameIndex", "?")),
                                str(teams[side]["playerExtra"][0].get("health", "?")))
                    else:
                        pe = teams[side]["playerExtra"]
                        chars = "/".join(char_name(p.get("charID", 0xFF))
                                         for p in pe)
                        return (chars, "-", "-",
                                str(pe[0].get("health", "?")))

                c1 = player_cols(p1, state["teams"], 0)
                c2 = player_cols(p2, state["teams"], 1)

                f.write(f"| {fidx} | {cam_str} "
                        f"| {c1[0]} | {c1[1]} | {c1[2]} | {c1[3]} "
                        f"| {c2[0]} | {c2[1]} | {c2[2]} | {c2[3]} |\n")
            f.write("\n")

            # Hitbox details for resolved frames
            for fidx in sorted(r["key_states"].keys()):
                state = r["key_states"][fidx]
                for pi in state["players"]:
                    if not pi["resolved"]:
                        continue
                    pf = pi["fields"]
                    hbs = pi["hitboxes"]
                    active = [h for h in hbs if h["width"] > 0 or h["height"] > 0]
                    if active:
                        f.write(f"#### Frame {fidx} — P{pi['side']} "
                                f"{pi['charName']} Hitboxes "
                                f"(action={pf['actionID']}, "
                                f"anim={pf['animFrameIndex']}, "
                                f"hbActive=0x{pf.get('hitboxesActive',0):02X})\n\n")
                        f.write("| Slot | BoxID | PosX | PosY | Width | Height | Tipo |\n")
                        f.write("|------|-------|------|------|-------|--------|------|\n")
                        box_names = ["attack", "vuln1", "vuln2", "vuln3",
                                     "grab", "hb6", "collision"]
                        for h in hbs:
                            w, ht = h["width"], h["height"]
                            name = box_names[h["slot"]] if h["slot"] < len(box_names) else "?"
                            active_mark = " **" if w > 0 or ht > 0 else ""
                            f.write(f"| {h['slot']} | {h['boxID']} "
                                    f"| {h['posX']:+d} | {h['posY']:+d} "
                                    f"| {w} | {ht} | {name}{active_mark} |\n")
                        f.write("\n")

            # Player fields detail for one fully resolved frame
            for fidx in sorted(r["key_states"].keys(), reverse=True):
                state = r["key_states"][fidx]
                for pi in state["players"]:
                    if not pi["resolved"]:
                        continue
                    pf = pi["fields"]
                    f.write(f"#### Frame {fidx} — P{pi['side']} "
                            f"{pi['charName']} Full Player Fields\n\n")
                    f.write("| Field | Value |\n")
                    f.write("|-------|-------|\n")
                    for fname in sorted(pf.keys()):
                        val = pf[fname]
                        if isinstance(val, int) and fname not in (
                                "actionID", "animFrameIndex", "facing",
                                "stunTimer"):
                            f.write(f"| {fname} | 0x{val:X} ({val}) |\n")
                        elif isinstance(val, tuple):
                            f.write(f"| {fname} | {val} |\n")
                        else:
                            f.write(f"| {fname} | {val} |\n")
                    f.write("\n")
                # Only do this for the last key frame with data
                break

            # All 3 slots detail
            for fidx in sorted(r["key_states"].keys(), reverse=True):
                state = r["key_states"][fidx]
                has_any = any(pi["resolved"] for pi in state["players"])
                if not has_any:
                    continue
                f.write(f"#### Frame {fidx} — All Player Slots\n\n")
                for pi in state["players"]:
                    f.write(f"**P{pi['side']}**:\n\n")
                    f.write("| Slot | Char | HP | TeamPos | Offset | "
                            "Action | Anim | Facing |\n")
                    f.write("|------|------|----|---------|--------|"
                            "--------|------|--------|\n")
                    for sl in pi["all_slots"]:
                        off_str = (f"0x{sl['offset']:06X}"
                                   if sl['offset'] is not None else "NULL")
                        if sl.get("fields"):
                            sf = sl["fields"]
                            f.write(f"| {sl['slot']} | {sl['charName']} "
                                    f"| {sl['health']} | {sl['teamPos']} "
                                    f"| {off_str} "
                                    f"| {sf.get('actionID','?')} "
                                    f"| {sf.get('animFrameIndex','?')} "
                                    f"| {sf.get('facing','?')} |\n")
                        else:
                            f.write(f"| {sl['slot']} | {sl['charName']} "
                                    f"| {sl['health']} | {sl['teamPos']} "
                                    f"| {off_str} | - | - | - |\n")
                    f.write("\n")
                break

            # Timeline summary
            timeline = r["timeline"]
            if timeline:
                f.write("### Timeline de Estado\n\n")
                f.write("| Frame | Camera | P1 Char | P1 Act | P1 HP "
                        "| P2 Char | P2 Act | P2 HP |\n")
                f.write("|-------|--------|---------|--------|-------"
                        "|---------|--------|-------|\n")
                for entry in timeline:
                    cam = entry.get("cam", "?")
                    f.write(f"| {entry['frame']} | {cam} "
                            f"| {entry.get('p1_char', '?')} "
                            f"| {entry.get('p1_action', '-')} "
                            f"| {entry.get('p1_hp', '-')} "
                            f"| {entry.get('p2_char', '?')} "
                            f"| {entry.get('p2_action', '-')} "
                            f"| {entry.get('p2_hp', '-')} |\n")
                f.write("\n")

            # Region heatmap
            region_data = r["region_data"]
            active = [(fn, c) for fn, c in region_data if sum(c.values()) > 0]
            if active:
                region_totals = {}
                for _, counts in active:
                    for rname, cnt in counts.items():
                        region_totals[rname] = region_totals.get(rname, 0) + cnt

                f.write("### Actividad por Region (frames activos)\n\n")
                f.write("| Region | Paginas totales | Pag/frame |\n")
                f.write("|--------|-----------------|-----------|\n")
                for rname in sorted(region_totals,
                                    key=lambda r: -region_totals[r]):
                    total = region_totals[rname]
                    avg = total / len(active)
                    f.write(f"| {rname} | {total:,} | {avg:.1f} |\n")
                f.write("\n")

    print(f"\nReport written to: {output_path}")


def main():
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    SESSIONS_DIR = os.path.join(BASE_DIR, "aw_data", "sessions")

    # Find framecap sessions
    sessions = sorted([
        d for d in os.listdir(SESSIONS_DIR)
        if d.startswith("framecap_") and os.path.isdir(
            os.path.join(SESSIONS_DIR, d))
    ])

    # Filter to sessions with actual data (>1 frame)
    valid = []
    for s in sessions:
        sdir = os.path.join(SESSIONS_DIR, s)
        delta_path = os.path.join(sdir, "deltas.bin")
        if os.path.exists(delta_path) and os.path.getsize(delta_path) > 100:
            valid.append(s)

    print(f"Found {len(valid)} framecap sessions:")
    for s in valid:
        print(f"  {s}")

    labels = {
        "framecap_20260408_195227": "Loading -> Fight (session 2, 180f)",
        "framecap_20260408_195323": "Mid-Battle (session 3, 600f)",
    }

    results = []
    for s in valid:
        sdir = os.path.join(SESSIONS_DIR, s)
        label = labels.get(s, s)
        try:
            r = analyze_session(sdir, label)
            results.append(r)
        except Exception as e:
            print(f"  ERROR analyzing {s}: {e}")
            import traceback
            traceback.print_exc()

    if results:
        output = os.path.join(BASE_DIR, "docs", "framecap_analysis.md")
        write_report(results, output)


if __name__ == "__main__":
    main()
