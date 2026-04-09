"""
Deep framecap analysis — multi-scene session with scene detection,
combo tracking, quickshift detection, and per-phase breakdown.
Designed for long sessions covering loading→fight→win→loading→fight→win.
"""
import os
import sys
import struct
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis_utils import (
    SH4_RAM_SIZE, CAMERA_PTR, TEAM_PTRS, TEAM_SIZE,
    PLAYER_STRUCT_SIZE, PLAYER_OFFSETS, HITBOX_OFFSET, HITBOX_SIZE,
    HITBOX_COUNT, PLAYER_EXTRA_OFFSET, PLAYER_EXTRA_SIZE,
    TEAM_ENTRIES_OFFSET, PLAYER_EXTRA_FIELDS, TEAM_OFFSETS,
    extract_camera, extract_team_fields, extract_player_fields,
    extract_hitboxes, resolve_player_offset, char_name, sh4_to_ram_offset,
)

PAGE_SIZE = 4096

# ─── FCAP loader ───────────────────────────────────────────────────────────

def load_framecap(session_dir):
    base_path = os.path.join(session_dir, "frame_base.bin")
    delta_path = os.path.join(session_dir, "deltas.bin")
    with open(base_path, "rb") as f:
        base = bytearray(f.read())
    with open(delta_path, "rb") as f:
        hdr = f.read(20)
        magic, version, page_size, num_pages, frame_count = struct.unpack("<4sIIII", hdr)
        assert magic == b"FCAP"
        frames = []
        for _ in range(frame_count):
            h = f.read(6)
            if len(h) < 6:
                break
            fnum, pcount = struct.unpack("<IH", h)
            pages = []
            for _ in range(pcount):
                pi = f.read(2)
                if len(pi) < 2:
                    break
                idx = struct.unpack("<H", pi)[0]
                data = f.read(page_size)
                pages.append((idx, data))
            frames.append((fnum, pages))
    return base, frames, frame_count


# ─── Per-frame state extractor ─────────────────────────────────────────────

def extract_frame_state(ram):
    """Extract all relevant game state from a RAM snapshot (bytes or bytearray)."""
    data = bytes(ram) if isinstance(ram, bytearray) else ram
    state = {}

    # Camera
    cam = extract_camera(data)
    state["camX"] = cam["posX"] if cam else 0
    state["camY"] = cam["posY"] if cam else 0

    # Teams
    for side in range(2):
        prefix = f"p{side+1}"
        team_off = TEAM_PTRS[side]
        team = extract_team_fields(data, team_off)
        state[f"{prefix}_point"] = team.get("point", 0xFF)
        state[f"{prefix}_leader"] = team.get("leader", 0xFF)
        state[f"{prefix}_combo"] = team.get("comboCounter", 0)
        state[f"{prefix}_super"] = team.get("super", 0)
        state[f"{prefix}_skill"] = team.get("skillStock", 0)

        # Entry pointers
        entries = []
        for i in range(3):
            ptr_off = team_off + TEAM_ENTRIES_OFFSET + i * 4
            raw = data[ptr_off:ptr_off + 4]
            entries.append(struct.unpack("<I", raw)[0])
        state[f"{prefix}_entries"] = entries
        entries_valid = any(e != 0 for e in entries)
        state[f"{prefix}_entries_valid"] = entries_valid

        # PlayerExtra (charID, health, teamPos) for 3 slots
        for slot in range(3):
            pe_off = team_off + PLAYER_EXTRA_OFFSET + slot * PLAYER_EXTRA_SIZE
            cid = data[pe_off + 1]
            hp = struct.unpack_from("<h", data, pe_off + 0x008)[0]
            vis_hp = struct.unpack_from("<h", data, pe_off + 0x00A)[0]
            max_hp = struct.unpack_from("<h", data, pe_off + 0x00C)[0]
            tpos = data[pe_off + 0x010]
            state[f"{prefix}_s{slot}_char"] = cid
            state[f"{prefix}_s{slot}_hp"] = hp
            state[f"{prefix}_s{slot}_vishp"] = vis_hp
            state[f"{prefix}_s{slot}_maxhp"] = max_hp
            state[f"{prefix}_s{slot}_tpos"] = tpos

        # Raw team bytes: timer (+0x028) and power gauge (+0x030)
        state[f"{prefix}_timer"] = struct.unpack_from("<H", data, team_off + 0x028)[0]
        state[f"{prefix}_power"] = struct.unpack_from("<I", data, team_off + 0x030)[0]

        # Resolve point character using teamPosition matching (NOT direct slot index)
        # team.point is a teamPosition value; find the slot whose teamPosition matches
        point_tpos = team.get("point", 0)
        point_slot = None
        for s in range(3):
            pe_off = team_off + PLAYER_EXTRA_OFFSET + s * PLAYER_EXTRA_SIZE
            tpos = data[pe_off + 0x010]
            if tpos == point_tpos:
                point_slot = s
                break
        if point_slot is None:
            point_slot = 0  # fallback
        state[f"{prefix}_point_slot"] = point_slot

        if entries_valid and 0 <= point_slot <= 2:
            off = resolve_player_offset(data, team_off, point_slot)
            if off is not None:
                pf = extract_player_fields(data, off)
                state[f"{prefix}_action"] = pf.get("actionID", 0)
                state[f"{prefix}_prevAction"] = pf.get("prevActionID", 0)
                state[f"{prefix}_actionCat"] = pf.get("actionCategory", 0)
                state[f"{prefix}_anim"] = pf.get("animFrameIndex", 0)
                state[f"{prefix}_pos"] = pf.get("position", (0, 0))
                state[f"{prefix}_vel"] = pf.get("velocity", (0, 0))
                state[f"{prefix}_facing"] = pf.get("facing", 0)
                state[f"{prefix}_hbActive"] = pf.get("hitboxesActive", 0)
                state[f"{prefix}_stun"] = pf.get("stunTimer", 0)
                state[f"{prefix}_animPlay"] = pf.get("animPlayFlag", 0)
                state[f"{prefix}_sprOX"] = pf.get("spriteOffsetX", 0)
                state[f"{prefix}_sprOY"] = pf.get("spriteOffsetY", 0)
                # Hitboxes
                hbs = extract_hitboxes(data, off)
                state[f"{prefix}_hitboxes"] = hbs
            else:
                state[f"{prefix}_action"] = None
        else:
            state[f"{prefix}_action"] = None

        # Also resolve ALL slots for quickshift analysis
        for slot in range(3):
            off = resolve_player_offset(data, team_off, slot) if entries_valid else None
            if off is not None:
                pf = extract_player_fields(data, off)
                state[f"{prefix}_s{slot}_action"] = pf.get("actionID", 0)
                state[f"{prefix}_s{slot}_offset"] = off
            else:
                state[f"{prefix}_s{slot}_action"] = None
                state[f"{prefix}_s{slot}_offset"] = None

    return state


# ─── Scene detection ───────────────────────────────────────────────────────

def detect_scenes(timeline, page_counts):
    """Detect scene boundaries from timeline + page count data.

    Returns list of scene dicts with start/end frames and type hints.
    """
    scenes = []
    boundaries = [0]  # Always start with frame 0

    for i in range(1, len(timeline)):
        prev = timeline[i - 1]
        curr = timeline[i]
        fi = curr["frame"]
        pc = page_counts.get(fi, 0)

        is_boundary = False
        reason = []

        # 1. Burst of page changes (>150 pages = scene transition)
        if pc > 150:
            is_boundary = True
            reason.append(f"burst={pc}pg")

        # 2. Entries validity transition
        prev_valid = prev.get("p1_entries_valid", False) and prev.get("p2_entries_valid", False)
        curr_valid = curr.get("p1_entries_valid", False) and curr.get("p2_entries_valid", False)
        if prev_valid != curr_valid:
            is_boundary = True
            reason.append(f"entries:{'on' if curr_valid else 'off'}")

        # 3. HP reset (any slot going from <100 to 112)
        for side in range(1, 3):
            for slot in range(3):
                prev_hp = prev.get(f"p{side}_s{slot}_hp", 0)
                curr_hp = curr.get(f"p{side}_s{slot}_hp", 0)
                if prev_hp < 80 and curr_hp >= 112:
                    is_boundary = True
                    reason.append(f"p{side}s{slot}_hp_reset:{prev_hp}->{curr_hp}")

        # 4. Camera X discontinuity (>200 pixel jump)
        prev_cx = prev.get("camX", 0)
        curr_cx = curr.get("camX", 0)
        if abs(curr_cx - prev_cx) > 200:
            if not any("burst" in r for r in reason):  # Don't double-count with burst
                is_boundary = True
                reason.append(f"cam_jump:{prev_cx}->{curr_cx}")

        if is_boundary:
            boundaries.append(i)

    # Merge close boundaries (within 5 frames)
    merged = [boundaries[0]]
    for b in boundaries[1:]:
        if b - merged[-1] > 5:
            merged.append(b)
        else:
            merged[-1] = b  # keep the later one

    # Build scenes from boundaries
    for j in range(len(merged)):
        start_idx = merged[j]
        end_idx = merged[j + 1] - 1 if j + 1 < len(merged) else len(timeline) - 1
        start_frame = timeline[start_idx]["frame"]
        end_frame = timeline[end_idx]["frame"]

        # Classify scene
        s_start = timeline[start_idx]
        s_mid_idx = (start_idx + end_idx) // 2
        s_mid = timeline[min(s_mid_idx, len(timeline) - 1)]

        entries_valid = s_mid.get("p1_entries_valid", False) and s_mid.get("p2_entries_valid", False)
        mid_hp_sum = 0
        for side in range(1, 3):
            for slot in range(3):
                mid_hp_sum += max(0, s_mid.get(f"p{side}_s{slot}_hp", 0))

        # Check page activity in this range
        frame_range = range(start_idx, end_idx + 1)
        avg_pages = 0
        if len(frame_range) > 0:
            total_p = sum(page_counts.get(timeline[k]["frame"], 0) for k in frame_range)
            avg_pages = total_p / len(frame_range)

        scene_type = "unknown"
        if not entries_valid:
            if avg_pages < 10:
                scene_type = "loading_static"
            else:
                scene_type = "loading_active"
        elif entries_valid and mid_hp_sum > 500:
            scene_type = "fight"
        elif entries_valid and avg_pages < 55:
            scene_type = "win_cinematic"
        elif entries_valid:
            scene_type = "fight"

        scenes.append({
            "index": j,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "start_idx": start_idx,
            "end_idx": end_idx,
            "duration_frames": end_frame - start_frame + 1,
            "type": scene_type,
            "entries_valid": entries_valid,
            "avg_pages": avg_pages,
        })

    return scenes


# ─── Event detection ───────────────────────────────────────────────────────

def detect_events(timeline):
    """Detect quickshifts, combos (via HP drops), KOs, and notable states."""
    events = []

    # Track active slot per side (slot with action != 87) for quickshift detection
    prev_active = {1: None, 2: None}

    # Track HP for heuristic combo detection:
    # A "combo" is a sequence of HP-drop frames on the same target with gaps <= 20f
    combo_tracker = {1: {"last_drop_frame": -100, "hits": 0, "start_frame": 0},
                     2: {"last_drop_frame": -100, "hits": 0, "start_frame": 0}}

    for i in range(1, len(timeline)):
        prev = timeline[i - 1]
        curr = timeline[i]
        fi = curr["frame"]

        for side in range(1, 3):
            prefix = f"p{side}"

            # ── Quickshift detection via action != 87 + teamPosition rotation ──
            # Find the currently active slot (action != 87)
            curr_active_slot = None
            for slot in range(3):
                act = curr.get(f"{prefix}_s{slot}_action")
                if act is not None and act != 87:
                    if curr_active_slot is None:
                        curr_active_slot = slot
                    else:
                        # Multiple non-87 slots: during intro/win, skip detection
                        curr_active_slot = None
                        break

            if (curr_active_slot is not None and
                    prev_active[side] is not None and
                    curr_active_slot != prev_active[side]):
                prev_slot = prev_active[side]
                prev_char = char_name(prev.get(f"{prefix}_s{prev_slot}_char", 0xFF))
                curr_char = char_name(curr.get(f"{prefix}_s{curr_active_slot}_char", 0xFF))
                events.append({
                    "frame": fi,
                    "type": "quickshift",
                    "side": side,
                    "detail": f"{prev_char} → {curr_char} (slot {prev_slot}→{curr_active_slot})",
                })
            if curr_active_slot is not None:
                prev_active[side] = curr_active_slot

            # ── KO detection: HP drops to 0 or below ──
            for slot in range(3):
                prev_hp = prev.get(f"{prefix}_s{slot}_hp", 112)
                curr_hp = curr.get(f"{prefix}_s{slot}_hp", 112)
                if prev_hp > 0 and curr_hp <= 0:
                    cname = char_name(curr.get(f"{prefix}_s{slot}_char", 0xFF))
                    events.append({
                        "frame": fi,
                        "type": "ko",
                        "side": side,
                        "detail": f"{cname} KO'd (slot {slot}, hp {prev_hp}→{curr_hp})",
                    })

            # ── Heuristic combo detection via HP drops on the OPPONENT ──
            opp = 3 - side  # opponent side
            opp_prefix = f"p{opp}"
            # Find opponent's point character HP
            opp_point_slot = curr.get(f"{opp_prefix}_point_slot")
            if opp_point_slot is not None and 0 <= opp_point_slot <= 2:
                prev_ohp = prev.get(f"{opp_prefix}_s{opp_point_slot}_hp", 112)
                curr_ohp = curr.get(f"{opp_prefix}_s{opp_point_slot}_hp", 112)
                if curr_ohp < prev_ohp and curr_ohp > 0:
                    ct = combo_tracker[side]
                    if fi - ct["last_drop_frame"] <= 20:
                        ct["hits"] += 1
                    else:
                        # End previous combo if it was meaningful
                        if ct["hits"] >= 2:
                            events.append({
                                "frame": ct["start_frame"],
                                "type": "combo",
                                "side": side,
                                "detail": f"{ct['hits']} hits (f{ct['start_frame']}-f{ct['last_drop_frame']})",
                            })
                        ct["hits"] = 1
                        ct["start_frame"] = fi
                    ct["last_drop_frame"] = fi

            # ── Hitbox activation changes ──
            prev_hb = prev.get(f"{prefix}_hbActive", 0)
            curr_hb = curr.get(f"{prefix}_hbActive", 0)
            if prev_hb == 0 and curr_hb != 0:
                act = curr.get(f"{prefix}_action", "?")
                events.append({
                    "frame": fi,
                    "type": "hb_activate",
                    "side": side,
                    "detail": f"hbActive 0x00→0x{curr_hb:02X} action={act}",
                })

    # Flush any pending combos
    for side in (1, 2):
        ct = combo_tracker[side]
        if ct["hits"] >= 2:
            events.append({
                "frame": ct["start_frame"],
                "type": "combo",
                "side": side,
                "detail": f"{ct['hits']} hits (f{ct['start_frame']}-f{ct['last_drop_frame']})",
            })

    return events


# ─── Hitbox activity analysis ──────────────────────────────────────────────

def analyze_hitboxes_across_frames(timeline):
    """Aggregate hitbox data to understand patterns."""
    hb_events = []
    action_hb_map = {}  # action -> set of hbActive values seen

    for i in range(len(timeline)):
        curr = timeline[i]
        for side in range(1, 3):
            prefix = f"p{side}"
            action = curr.get(f"{prefix}_action")
            hb_active = curr.get(f"{prefix}_hbActive", 0)
            hbs = curr.get(f"{prefix}_hitboxes", [])
            char_id = None

            # Find which char is on point (via resolved point_slot)
            point_slot = curr.get(f"{prefix}_point_slot", 0)
            if 0 <= point_slot <= 2:
                char_id = curr.get(f"{prefix}_s{point_slot}_char", 0xFF)

            if action is not None and hb_active > 0:
                key = (char_name(char_id) if char_id is not None else "?", action)
                if key not in action_hb_map:
                    action_hb_map[key] = {"hbActive_values": set(), "hitbox_samples": [], "count": 0}
                action_hb_map[key]["hbActive_values"].add(hb_active)
                action_hb_map[key]["count"] += 1
                if len(action_hb_map[key]["hitbox_samples"]) < 3:
                    active_hbs = [h for h in hbs if h["width"] > 0 or h["height"] > 0]
                    if active_hbs:
                        action_hb_map[key]["hitbox_samples"].append({
                            "frame": curr["frame"],
                            "hbActive": hb_active,
                            "boxes": active_hbs,
                        })

    return action_hb_map


# ─── Memory region analysis per scene ──────────────────────────────────────

REGIONS = [
    ("bios",      0x000000, 0x030000),
    ("game_code", 0x030000, 0x133000),
    ("meta",      0x133000, 0x200000),
    ("heap_low",  0x200000, 0x270000),
    ("obj_pool",  0x270000, 0x280000),
    ("heap_hi",   0x280000, 0x300000),
    ("decomp",    0x300000, 0x400000),
    ("gfx_lo",    0x400000, 0x800000),
    ("gfx_hi",    0x800000, 0xC00000),
    ("ram_top",   0xC00000, 0x1000000),
]


def classify_page(page_idx):
    addr = page_idx * PAGE_SIZE
    for rname, rstart, rend in REGIONS:
        if rstart <= addr < rend:
            return rname
    return "other"


# ─── Main analysis pipeline ───────────────────────────────────────────────

def run_full_analysis(session_dir):
    print(f"Loading framecap from {session_dir}...")
    t0 = time.time()
    base, frames, frame_count = load_framecap(session_dir)
    print(f"  Loaded {frame_count} frames in {time.time()-t0:.1f}s")

    # ── Pass 1: Iterate all frames, extract state every frame ──
    print("Pass 1: Extracting game state for all frames...")
    t0 = time.time()
    ram = bytearray(base)
    timeline = []
    page_counts = {}
    region_counts_per_frame = {}

    for i, (fnum, pages) in enumerate(frames):
        # Apply deltas
        for pg_idx, pg_data in pages:
            off = pg_idx * PAGE_SIZE
            ram[off:off + PAGE_SIZE] = pg_data

        pc = len(pages)
        page_counts[fnum] = pc

        # Region breakdown for this frame
        rcounts = {}
        for pg_idx, _ in pages:
            rname = classify_page(pg_idx)
            rcounts[rname] = rcounts.get(rname, 0) + 1
        region_counts_per_frame[fnum] = rcounts

        # Extract state every frame for precision
        state = extract_frame_state(ram)
        state["frame"] = fnum
        state["pages"] = pc
        timeline.append(state)

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{frame_count} frames ({elapsed:.1f}s)")

    elapsed = time.time() - t0
    print(f"  Done: {frame_count} frames in {elapsed:.1f}s ({frame_count/elapsed:.0f} f/s)")

    # ── Pass 2: Scene detection ──
    print("\nPass 2: Detecting scenes...")
    scenes = detect_scenes(timeline, page_counts)
    print(f"  Found {len(scenes)} scenes")
    for sc in scenes:
        print(f"    [{sc['index']}] f{sc['start_frame']}-{sc['end_frame']} "
              f"({sc['duration_frames']}f) type={sc['type']} "
              f"avg_pg={sc['avg_pages']:.1f}")

    # ── Pass 3: Event detection ──
    print("\nPass 3: Detecting events...")
    events = detect_events(timeline)
    print(f"  Found {len(events)} events")

    event_counts = {}
    for ev in events:
        event_counts[ev["type"]] = event_counts.get(ev["type"], 0) + 1
    for etype, count in sorted(event_counts.items()):
        print(f"    {etype}: {count}")

    # ── Pass 4: Hitbox analysis ──
    print("\nPass 4: Analyzing hitbox patterns...")
    hb_map = analyze_hitboxes_across_frames(timeline)
    print(f"  {len(hb_map)} unique (char, action) combos with active hitboxes")

    return {
        "session_dir": session_dir,
        "frame_count": frame_count,
        "timeline": timeline,
        "page_counts": page_counts,
        "region_counts": region_counts_per_frame,
        "scenes": scenes,
        "events": events,
        "hb_map": hb_map,
    }


# ─── Report writer ────────────────────────────────────────────────────────

def write_report(result, output_path):
    timeline = result["timeline"]
    scenes = result["scenes"]
    events = result["events"]
    hb_map = result["hb_map"]
    page_counts = result["page_counts"]
    region_counts = result["region_counts"]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Analisis de Framecap Completo — KOF XI\n\n")
        f.write(f"**Sesion**: `{os.path.basename(result['session_dir'])}`\n")
        f.write(f"**Frames totales**: {result['frame_count']}\n")
        f.write(f"**Fecha de analisis**: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write("---\n\n")

        # ── 1. Scene Overview ──
        f.write("## 1. Escenas Detectadas\n\n")
        f.write("| # | Frames | Duracion | Tipo | Avg pag/f | Entries | Notas |\n")
        f.write("|---|--------|----------|------|-----------|---------|-------|\n")
        for sc in scenes:
            # Determine chars in this scene
            mid_idx = (sc["start_idx"] + sc["end_idx"]) // 2
            mid_state = timeline[mid_idx]
            chars = []
            for side in range(1, 3):
                point = mid_state.get(f"p{side}_point", 0)
                if 0 <= point <= 2:
                    cid = mid_state.get(f"p{side}_s{point}_char", 0xFF)
                    chars.append(char_name(cid))
                else:
                    chars.append("?")
            chars_str = f"{chars[0]} vs {chars[1]}" if sc["entries_valid"] else "-"

            f.write(f"| {sc['index']} | {sc['start_frame']}-{sc['end_frame']} "
                    f"| {sc['duration_frames']}f "
                    f"| {sc['type']} | {sc['avg_pages']:.1f} "
                    f"| {'Si' if sc['entries_valid'] else 'No'} "
                    f"| {chars_str} |\n")
        f.write("\n")

        # ── 2. Timeline por Escena ──
        f.write("## 2. Timeline por Escena\n\n")
        for sc in scenes:
            f.write(f"### Escena {sc['index']}: {sc['type']} (f{sc['start_frame']}-{sc['end_frame']})\n\n")

            # Sample ~20 frames per scene
            start_i = sc["start_idx"]
            end_i = sc["end_idx"]
            n = end_i - start_i + 1
            step = max(1, n // 20)

            indices = list(range(start_i, end_i + 1, step))
            if end_i not in indices:
                indices.append(end_i)

            has_player_data = sc["entries_valid"]
            if has_player_data:
                f.write("| Frame | Pg | Cam | P1 Char | Act | HP | "
                        "P2 Char | Act | HP | Timer | Power P1 |\n")
                f.write("|-------|----|----|---------|-----|----|"
                        "---------|-----|----|-------|----------|\n")
            else:
                f.write("| Frame | Pg | Cam | Entries | HP Sum |\n")
                f.write("|-------|----|----|---------|--------|\n")

            for idx in indices:
                if idx >= len(timeline):
                    break
                st = timeline[idx]
                fi = st["frame"]
                pg = st.get("pages", 0)
                cam = f"({st['camX']},{st['camY']})"

                if has_player_data:
                    p1_char = "?"
                    p2_char = "?"
                    p1_ps = st.get("p1_point_slot", 0)
                    p2_ps = st.get("p2_point_slot", 0)
                    if 0 <= p1_ps <= 2:
                        p1_char = char_name(st.get(f"p1_s{p1_ps}_char", 0xFF))
                    if 0 <= p2_ps <= 2:
                        p2_char = char_name(st.get(f"p2_s{p2_ps}_char", 0xFF))

                    p1_act = st.get("p1_action", "-")
                    p2_act = st.get("p2_action", "-")
                    p1_hp = st.get(f"p1_s{p1_ps}_hp", "-") if 0 <= p1_ps <= 2 else "-"
                    p2_hp = st.get(f"p2_s{p2_ps}_hp", "-") if 0 <= p2_ps <= 2 else "-"
                    timer = st.get("p1_timer", "-")
                    power = st.get("p1_power", 0)

                    p1_act_str = str(p1_act) if p1_act is not None else "-"
                    p2_act_str = str(p2_act) if p2_act is not None else "-"

                    f.write(f"| {fi} | {pg} | {cam} | {p1_char} | {p1_act_str} | {p1_hp} "
                            f"| {p2_char} | {p2_act_str} | {p2_hp} | {timer} | {power} |\n")
                else:
                    ev = "valid" if st.get("p1_entries_valid") else "null"
                    hp_sum = sum(max(0, st.get(f"p{s}_s{sl}_hp", 0))
                                for s in range(1, 3) for sl in range(3))
                    f.write(f"| {fi} | {pg} | {cam} | {ev} | {hp_sum} |\n")
            f.write("\n")

            # Events in this scene
            scene_events = [ev for ev in events
                            if sc["start_frame"] <= ev["frame"] <= sc["end_frame"]]
            if scene_events:
                f.write(f"**Eventos en esta escena** ({len(scene_events)}):\n\n")
                # Limit to interesting events
                interesting = [ev for ev in scene_events
                              if ev["type"] in ("quickshift", "ko", "combo")]
                remaining = [ev for ev in scene_events if ev not in interesting]
                for ev in interesting:
                    f.write(f"- **f{ev['frame']}** [{ev['type']}] P{ev['side']}: {ev['detail']}\n")
                if remaining:
                    hb_acts = len([e for e in remaining if e["type"] == "hb_activate"])
                    if hb_acts:
                        f.write(f"- _(+{hb_acts} hitbox activations)_\n")
                f.write("\n")

        # ── 3. Quickshifts ──
        quickshifts = [ev for ev in events if ev["type"] == "quickshift"]
        f.write("## 3. Quickshifts Detectados\n\n")
        if quickshifts:
            f.write(f"Total: {len(quickshifts)}\n\n")
            f.write("| Frame | Lado | Cambio |\n")
            f.write("|-------|------|--------|\n")
            for qs in quickshifts:
                f.write(f"| {qs['frame']} | P{qs['side']} | {qs['detail']} |\n")
            f.write("\n")
        else:
            f.write("No se detectaron quickshifts.\n\n")

        # ── 4. Combos (heuristic via HP drops) ──
        combos = [ev for ev in events if ev["type"] == "combo"]
        f.write("## 4. Combos Detectados (heuristico via drops consecutivos de HP)\n\n")
        if combos:
            f.write(f"Total combos detectados: {len(combos)}\n\n")
            f.write("| Frame inicio | Lado | Detalle |\n")
            f.write("|-------------|------|--------|\n")
            for ce in combos:
                f.write(f"| {ce['frame']} | P{ce['side']} | {ce['detail']} |\n")
            f.write("\n")
            max_hits = max(int(ce["detail"].split()[0]) for ce in combos)
            f.write(f"**Combo mas largo**: {max_hits} hits\n\n")
            f.write("_Nota: Deteccion heuristica — una secuencia de drops de HP con <=20 frames entre cada drop._\n\n")
        else:
            f.write("No se detectaron combos de 2+ hits.\n\n")

        # ── 5. KOs ──
        kos = [ev for ev in events if ev["type"] == "ko"]
        f.write("## 5. KOs\n\n")
        if kos:
            f.write("| Frame | Lado | Detalle |\n")
            f.write("|-------|------|--------|\n")
            for ko in kos:
                f.write(f"| {ko['frame']} | P{ko['side']} | {ko['detail']} |\n")
            f.write("\n")
        else:
            f.write("No se detectaron KOs.\n\n")

        # ── 6. Timer & Power Gauge ──
        f.write("## 6. Timer y Power Gauge\n\n")
        f.write("### Timer (team +0x028, u16)\n\n")
        f.write("| Frame | Timer P1 | Timer P2 |\n")
        f.write("|-------|----------|----------|\n")
        for sc in scenes:
            if sc["type"] != "fight":
                continue
            # Sample 10 points per fight
            start_i = sc["start_idx"]
            end_i = sc["end_idx"]
            n = end_i - start_i + 1
            step = max(1, n // 10)
            for idx in range(start_i, end_i + 1, step):
                if idx >= len(timeline):
                    break
                st = timeline[idx]
                f.write(f"| {st['frame']} | {st.get('p1_timer', '-')} | {st.get('p2_timer', '-')} |\n")
        f.write("\n")

        f.write("### Power Gauge (team +0x030, u32)\n\n")
        f.write("| Frame | Power P1 | Power P2 |\n")
        f.write("|-------|----------|----------|\n")
        for sc in scenes:
            if sc["type"] != "fight":
                continue
            start_i = sc["start_idx"]
            end_i = sc["end_idx"]
            n = end_i - start_i + 1
            step = max(1, n // 10)
            for idx in range(start_i, end_i + 1, step):
                if idx >= len(timeline):
                    break
                st = timeline[idx]
                f.write(f"| {st['frame']} | {st.get('p1_power', '-')} | {st.get('p2_power', '-')} |\n")
        f.write("\n")
        f.write("_Nota: `super` (+0x038) leyo 0 durante toda la sesion. "
                "`power` (+0x030) parece ser un acumulador progresivo._\n\n")

        # ── 7. Hitbox Analysis ──
        f.write("## 7. Analisis de Hitboxes por Accion\n\n")
        f.write("Acciones donde `hitboxesActive > 0` (hasta 40 mas frecuentes):\n\n")
        f.write("| Char | ActionID | hbActive vals | Frames |\n")
        f.write("|------|----------|--------------|--------|\n")
        sorted_hb = sorted(hb_map.items(), key=lambda x: -x[1]["count"])
        for (cname, action), info in sorted_hb[:40]:
            hb_vals = ", ".join(f"0x{v:02X}" for v in sorted(info["hbActive_values"]))
            f.write(f"| {cname} | {action} | {hb_vals} | {info['count']} |\n")
        f.write("\n")

        # Show a few detailed hitbox snapshots
        f.write("### Muestras de Hitboxes Activos\n\n")
        shown = 0
        for (cname, action), info in sorted_hb[:15]:
            for sample in info["hitbox_samples"][:1]:
                f.write(f"**{cname} action={action}** (f{sample['frame']}, hbActive=0x{sample['hbActive']:02X}):\n\n")
                f.write("| Slot | Tipo | BoxID | Pos | Size |\n")
                f.write("|------|------|-------|-----|------|\n")
                types = ["attack", "vuln1", "vuln2", "vuln3", "grab", "hb6", "collision"]
                for h in sample["boxes"]:
                    tp = types[h["slot"]] if h["slot"] < len(types) else f"hb{h['slot']}"
                    f.write(f"| {h['slot']} | {tp} | {h['boxID']} "
                            f"| ({h['posX']:+d},{h['posY']:+d}) "
                            f"| {h['width']}x{h['height']} |\n")
                f.write("\n")
                shown += 1
                if shown >= 10:
                    break
            if shown >= 10:
                break

        # ── 8. Memory activity per scene ──
        f.write("## 8. Actividad de Memoria por Escena\n\n")
        for sc in scenes:
            f.write(f"### Escena {sc['index']}: {sc['type']} (f{sc['start_frame']}-{sc['end_frame']})\n\n")

            # Aggregate region counts for this scene
            agg = {}
            n = 0
            for idx in range(sc["start_idx"], sc["end_idx"] + 1):
                if idx >= len(timeline):
                    break
                fi = timeline[idx]["frame"]
                rc = region_counts.get(fi, {})
                for rname, cnt in rc.items():
                    agg[rname] = agg.get(rname, 0) + cnt
                n += 1

            if n > 0 and agg:
                f.write("| Region | Total pags | Pag/frame |\n")
                f.write("|--------|-----------|----------|\n")
                for rname in sorted(agg, key=lambda r: -agg[r]):
                    f.write(f"| {rname} | {agg[rname]:,} | {agg[rname]/n:.1f} |\n")
                f.write("\n")

        # ── 9. Page burst analysis ──
        f.write("## 9. Bursts de Paginas (>100 pages/frame)\n\n")
        f.write("| Frame | Paginas | Regions |\n")
        f.write("|-------|---------|--------|\n")
        for st in timeline:
            if st["pages"] > 100:
                fi = st["frame"]
                rc = region_counts.get(fi, {})
                rc_str = ", ".join(f"{r}={c}" for r, c in sorted(rc.items(), key=lambda x: -x[1])[:5])
                f.write(f"| {fi} | {st['pages']} | {rc_str} |\n")
        f.write("\n")

        # ── 10. Player struct offsets ──
        f.write("## 10. Player Struct Offsets Observados\n\n")
        offsets_seen = {}
        for st in timeline:
            if not st.get("p1_entries_valid"):
                continue
            for side in range(1, 3):
                for slot in range(3):
                    off = st.get(f"p{side}_s{slot}_offset")
                    cid = st.get(f"p{side}_s{slot}_char", 0xFF)
                    if off is not None:
                        key = (side, slot)
                        if key not in offsets_seen:
                            offsets_seen[key] = set()
                        offsets_seen[key].add((off, cid))

        if offsets_seen:
            f.write("| Lado | Slot | Offset(s) | Char(s) |\n")
            f.write("|------|------|-----------|--------|\n")
            for (side, slot) in sorted(offsets_seen.keys()):
                pairs = offsets_seen[(side, slot)]
                off_strs = ", ".join(f"0x{o:06X}" for o, _ in sorted(pairs))
                char_strs = ", ".join(char_name(c) for _, c in sorted(pairs))
                f.write(f"| P{side} | {slot} | {off_strs} | {char_strs} |\n")
            f.write("\n")

        # ── 11. Facing field analysis ──
        f.write("## 11. Valores de Facing Observados\n\n")
        facing_values = {}
        for st in timeline:
            for side in range(1, 3):
                fval = st.get(f"p{side}_facing")
                if fval is not None:
                    facing_values[fval] = facing_values.get(fval, 0) + 1
        if facing_values:
            f.write("| Valor | Hex | Binario | Frecuencia |\n")
            f.write("|-------|-----|---------|------------|\n")
            for val in sorted(facing_values.keys()):
                f.write(f"| {val} | 0x{val:02X} | {val:08b} | {facing_values[val]} |\n")
            f.write("\n")
            # Analysis
            f.write("Analisis del bit 1 (facing direction):\n")
            f.write("- Si bit 1 = facing right, los valores con bit 1 set serian: ")
            right_vals = [v for v in facing_values if v & 0x02]
            left_vals = [v for v in facing_values if not (v & 0x02)]
            f.write(f"{sorted(right_vals)}\n")
            f.write(f"- Valores sin bit 1 (facing left?): {sorted(left_vals)}\n\n")

        # ── 12. Action category analysis ──
        f.write("## 12. Categorias de Accion (actionCategory field)\n\n")
        cat_actions = {}
        for st in timeline:
            for side in range(1, 3):
                cat = st.get(f"p{side}_actionCat")
                act = st.get(f"p{side}_action")
                if cat is not None and act is not None:
                    if cat not in cat_actions:
                        cat_actions[cat] = set()
                    cat_actions[cat].add(act)
        if cat_actions:
            f.write("| Category | Hex | Actions observadas |\n")
            f.write("|----------|-----|-------------------|\n")
            for cat in sorted(cat_actions.keys()):
                acts = sorted(cat_actions[cat])
                acts_str = ", ".join(str(a) for a in acts[:20])
                if len(acts) > 20:
                    acts_str += f" (+{len(acts)-20} mas)"
                f.write(f"| {cat} | 0x{cat:02X} | {acts_str} |\n")
            f.write("\n")

        # ── 13. Position / Y-level analysis ──
        f.write("## 13. Analisis de Posicion Y (ground vs airborne)\n\n")
        y_values = {}
        for st in timeline:
            for side in range(1, 3):
                pos = st.get(f"p{side}_pos")
                if pos:
                    y = pos[1] if isinstance(pos, (tuple, list)) else pos
                    if isinstance(y, int):
                        y_values[y] = y_values.get(y, 0) + 1

        if y_values:
            sorted_y = sorted(y_values.items(), key=lambda x: -x[1])
            f.write("Valores Y mas frecuentes:\n\n")
            f.write("| Y | Frecuencia | Interpretacion |\n")
            f.write("|---|-----------|----------------|\n")
            for y, count in sorted_y[:15]:
                interp = ""
                if y == 672:
                    interp = "**Ground level**"
                elif y < 672 and y > 400:
                    interp = "Airborne (jumping)"
                elif y > 672:
                    interp = "Below ground?"
                f.write(f"| {y} | {count} | {interp} |\n")
            f.write("\n")

        # ── 14. Entry pointer analysis ──
        f.write("## 14. Entry Pointers a lo Largo del Tiempo\n\n")
        f.write("Verificacion de si los entry pointers cambian entre escenas:\n\n")
        entries_timeline = []
        prev_entries = None
        for st in timeline:
            if st.get("p1_entries_valid"):
                entries = (tuple(st["p1_entries"]), tuple(st["p2_entries"]))
                if entries != prev_entries:
                    entries_timeline.append({
                        "frame": st["frame"],
                        "p1": [f"0x{e:08X}" for e in st["p1_entries"]],
                        "p2": [f"0x{e:08X}" for e in st["p2_entries"]],
                    })
                    prev_entries = entries

        if entries_timeline:
            f.write("| Frame | P1 Entries | P2 Entries |\n")
            f.write("|-------|-----------|------------|\n")
            for et in entries_timeline:
                f.write(f"| {et['frame']} | {', '.join(et['p1'])} | {', '.join(et['p2'])} |\n")
            f.write("\n")
            if len(entries_timeline) == 1:
                f.write("**Los entry pointers son constantes durante toda la sesion.**\n\n")
            else:
                f.write(f"**Los entry pointers cambian {len(entries_timeline)} veces!**\n\n")

    print(f"\nReport written to {output_path}")


# ─── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import glob

    session_dir = None

    # Check for CLI arg
    if len(sys.argv) > 1:
        session_dir = sys.argv[1]
    else:
        # Auto-find the most recent session
        sess_root = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                 "aw_data", "sessions")
        pattern = os.path.join(sess_root, "framecap_*")
        dirs = sorted(glob.glob(pattern))
        if dirs:
            session_dir = dirs[-1]
            print(f"Auto-selected: {session_dir}")

    if not session_dir or not os.path.isdir(session_dir):
        print("Usage: python analyze_framecap_full.py [session_dir]")
        sys.exit(1)

    result = run_full_analysis(session_dir)

    output = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                          "docs", "framecap_full_analysis.md")
    write_report(result, output)
