"""
KOF XI Flycast — RAM Watch Pipeline

Observa la RAM de Flycast en tiempo real, detecta cambios y genera reportes.
Diseñado para que el usuario solo tenga que cargar savestates mientras el
script se encarga de capturar y analizar todo automáticamente.

Modos:
    watch    - Monitoreo continuo: captura cambios en tiempo real
    snap     - Tomar un snapshot con nombre y metadatos
    compare  - Comparar dos snapshots guardados
    report   - Generar reporte de una sesión completa
    lztrack  - Rastrear actividad de descompresión LZ en RAM
    anim     - Capturar secuencia de animación frame-by-frame
    framecap - Captura de RAM completa a ~60fps para análisis de diffs

Uso:
    python ram_watch.py watch                     # Monitoreo interactivo
    python ram_watch.py snap --name char_select    # Snapshot nombrado
    python ram_watch.py compare snap1.bin snap2.bin
    python ram_watch.py report session_dir/
    python ram_watch.py lztrack                    # Rastrear actividad LZ
    python ram_watch.py anim --frames 120          # Capturar 120 frames
    python ram_watch.py framecap --fps 60 -d 3     # 3s a 60fps
"""
import argparse
import os
import sys
import time
import struct as st
import json
import hashlib
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis_utils import (
    find_flycast_pid, open_process, close_process, find_ram_base,
    verify_kofxi, read_process_memory, dump_full_ram, save_snapshot,
    load_snapshot, load_metadata, sh4_to_ram_offset,
    SH4_RAM_SIZE, CAMERA_PTR, TEAM_PTRS, TEAM_SIZE,
    PLAYER_STRUCT_SIZE, PLAYER_OFFSETS, HITBOX_OFFSET, HITBOX_SIZE,
    HITBOX_COUNT, PLAYER_EXTRA_OFFSET, PLAYER_EXTRA_SIZE,
    TEAM_ENTRIES_OFFSET, PLAYER_EXTRA_FIELDS,
    extract_camera, extract_team_fields, extract_player_fields,
    extract_hitboxes, resolve_player_offset, char_name,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "aw_data", "snapshots")
ANALYSIS_DIR = os.path.join(BASE_DIR, "aw_data", "analysis")
SESSIONS_DIR = os.path.join(BASE_DIR, "aw_data", "sessions")

# Memory regions of interest for change tracking
WATCH_REGIONS = [
    ("bios",           0x000000, 0x000100),
    ("game_code",      0x010000, 0x132820),
    ("heap_low",       0x200000, 0x260000),
    ("obj_pool",       0x260000, 0x27CA00),
    ("camera",         0x27CAA8, 0x27CAB0),
    ("team_p1",        0x27CB50, 0x27CB50 + 0x1F8),
    ("team_p2",        0x27CD48, 0x27CD48 + 0x1F8),
    ("post_teams",     0x27CF40, 0x27D000),
    ("gameplay_a",     0x280000, 0x300000),
    ("decomp_buffer",  0x300000, 0x400000),
    ("gfx_sprites",    0x400000, 0x800000),
    ("gfx_upper",      0x800000, 0xC00000),
    ("ram_top",        0xC00000, 0x1000000),
]

# Known LZSS signature: first 4 bytes = decompressed size (LE), usually
# followed by flag bytes. We look for regions that suddenly fill with new
# data (indicating a decompression just occurred).
LZ_BUFFER_CANDIDATES = [
    ("decomp_buf_1", 0x300000, 0x100000),  # 1MB potential decomp target
    ("decomp_buf_2", 0x400000, 0x100000),  # Graphics area
    ("decomp_buf_3", 0x500000, 0x100000),
]

def ensure_dirs():
    for d in [SNAPSHOTS_DIR, ANALYSIS_DIR, SESSIONS_DIR]:
        os.makedirs(d, exist_ok=True)


# ===========================================================================
# Flycast attachment (shared)
# ===========================================================================

def attach():
    """Find and attach to Flycast. Returns (handle, pid, ram_base)."""
    pid = find_flycast_pid()
    if pid is None:
        print("ERROR: flycast.exe no encontrado en ejecución.")
        sys.exit(1)
    handle = open_process(pid)
    ram_base = find_ram_base(handle)
    if ram_base is None:
        close_process(handle)
        print("ERROR: No se encontró la RAM del SH-4 en Flycast.")
        sys.exit(1)
    return handle, pid, ram_base


def read_region(handle, ram_base, offset, size):
    """Read a region from Flycast RAM."""
    return read_process_memory(handle, ram_base + offset, size)


def quick_game_state(data):
    """Heuristic game state detection from a full RAM dump."""
    cam = extract_camera(data)
    states = []
    # Check if teams have valid data
    for side in range(2):
        team = extract_team_fields(data, TEAM_PTRS[side])
        has_chars = any(
            pe.get("charID", 0xFF) < 0x30
            for pe in team.get("playerExtra", [{}])
        )
        if has_chars:
            states.append(f"P{side+1}:team_valid")
        # Check HP
        for pe in team.get("playerExtra", [{}]):
            if pe.get("health", 0) > 0 and pe.get("health", 0) <= 0x70:
                states.append(f"P{side+1}:hp={pe['health']}")
    # Check camera position (fight vs menus)
    if cam:
        if cam["posX"] > 0 and cam["posY"] > 0:
            states.append(f"cam:({cam['posX']},{cam['posY']})")
    return states


def extract_game_summary(data):
    """Extract a structured summary of the current game state."""
    summary = {"camera": extract_camera(data), "players": []}
    for side in range(2):
        team = extract_team_fields(data, TEAM_PTRS[side])
        player_info = {"side": side + 1, "team": team, "player": None}
        # Find current point character entry
        point = team.get("point", 0)
        for i in range(3):
            pe = team["playerExtra"][i]
            if pe.get("teamPosition", -1) == point:
                player_off = resolve_player_offset(data, TEAM_PTRS[side], i)
                if player_off is not None:
                    pf = extract_player_fields(data, player_off)
                    hbs = extract_hitboxes(data, player_off)
                    player_info["player"] = pf
                    player_info["hitboxes"] = hbs
                    player_info["player_offset"] = player_off
                break
        summary["players"].append(player_info)
    return summary


# ===========================================================================
# watch command — interactive monitoring
# ===========================================================================

def cmd_watch(args):
    """Monitoreo continuo de RAM con detección de cambios."""
    ensure_dirs()
    handle, pid, ram_base = attach()
    print(f"Conectado a Flycast (PID {pid}, RAM @ 0x{ram_base:X})")
    print("Presiona Ctrl+C para detener.\n")

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = os.path.join(SESSIONS_DIR, f"session_{session_id}")
    os.makedirs(session_dir, exist_ok=True)
    log_path = os.path.join(session_dir, "watch_log.ndjson")

    interval = args.interval or 0.5  # seconds between checks
    prev_data = None
    snap_count = 0
    change_log = []

    print(f"Sesión: {session_dir}")
    print(f"Intervalo: {interval}s")
    print(f"Modo: {'regiones clave' if not args.full else 'RAM completa'}")
    print("-" * 60)

    try:
        log_file = open(log_path, "w", encoding="utf-8")
        while True:
            try:
                if args.full:
                    data = dump_full_ram(handle, ram_base)
                else:
                    # Read only key regions for performance
                    data = dump_full_ram(handle, ram_base)
            except OSError:
                print("\n[!] Error leyendo RAM — ¿se cerró Flycast?")
                break

            summary = extract_game_summary(data)
            ts = datetime.now().isoformat()

            if prev_data is not None:
                # Compute diffs per region
                changes = []
                for name, start, end in WATCH_REGIONS:
                    region_size = end - start
                    if start + region_size > len(data) or start + region_size > len(prev_data):
                        continue
                    old = prev_data[start:end]
                    new = data[start:end]
                    if old != new:
                        changed_bytes = sum(
                            1 for a, b in zip(old, new) if a != b)
                        changes.append({
                            "region": name,
                            "start": start,
                            "end": end,
                            "changed_bytes": changed_bytes,
                            "pct": changed_bytes / region_size * 100,
                        })

                if changes:
                    snap_count += 1
                    # Print status
                    p1 = summary["players"][0]
                    p2 = summary["players"][1]
                    p1_char = "?"
                    p2_char = "?"
                    if p1["team"].get("playerExtra"):
                        for pe in p1["team"]["playerExtra"]:
                            if pe.get("teamPosition") == p1["team"].get("point"):
                                p1_char = char_name(pe.get("charID", 0xFF))
                    if p2["team"].get("playerExtra"):
                        for pe in p2["team"]["playerExtra"]:
                            if pe.get("teamPosition") == p2["team"].get("point"):
                                p2_char = char_name(pe.get("charID", 0xFF))

                    total_changed = sum(c["changed_bytes"] for c in changes)
                    regions_changed = [c["region"] for c in changes]

                    action_p1 = p1.get("player", {}).get("actionID", "?")
                    action_p2 = p2.get("player", {}).get("actionID", "?")
                    anim_p1 = p1.get("player", {}).get("animFrameIndex", "?")
                    anim_p2 = p2.get("player", {}).get("animFrameIndex", "?")

                    status = (
                        f"[{snap_count:4d}] {ts[11:19]} "
                        f"| {p1_char:<10} act={action_p1:<3} anim={anim_p1:<3} "
                        f"| {p2_char:<10} act={action_p2:<3} anim={anim_p2:<3} "
                        f"| Δ{total_changed:>6}B en {len(changes)} regiones"
                    )
                    print(status)

                    # Log to NDJSON
                    log_entry = {
                        "timestamp": ts,
                        "snap": snap_count,
                        "total_changed": total_changed,
                        "changes": changes,
                        "p1": {"char": p1_char, "action": action_p1,
                               "anim": anim_p1},
                        "p2": {"char": p2_char, "action": action_p2,
                               "anim": anim_p2},
                    }
                    log_file.write(json.dumps(log_entry,
                                              ensure_ascii=False) + "\n")

                    # Auto-save snapshot on big changes (state transitions)
                    if total_changed > 50000:
                        snap_path = os.path.join(
                            session_dir,
                            f"auto_{snap_count:04d}.bin")
                        save_snapshot(data, snap_path, {
                            "timestamp": ts,
                            "trigger": "large_change",
                            "total_changed": total_changed,
                            "regions": regions_changed,
                        })
                        print(f"       → Snapshot automático guardado ({total_changed}B cambio)")

            prev_data = data
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\nSesión detenida. {snap_count} cambios registrados.")
    finally:
        log_file.close()
        close_process(handle)
        # Write session summary
        _write_session_summary(session_dir, snap_count, log_path)


def _write_session_summary(session_dir, snap_count, log_path):
    """Generate a markdown summary of the watch session."""
    md_path = os.path.join(session_dir, "session_summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Sesión de Watch: {os.path.basename(session_dir)}\n\n")
        f.write(f"- Cambios registrados: {snap_count}\n")
        f.write(f"- Log: `{os.path.basename(log_path)}`\n\n")

        # Parse log for highlights
        if os.path.exists(log_path):
            entries = []
            with open(log_path, "r") as lf:
                for line in lf:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

            if entries:
                # Region change summary
                region_counts = defaultdict(int)
                region_bytes = defaultdict(int)
                for e in entries:
                    for c in e.get("changes", []):
                        region_counts[c["region"]] += 1
                        region_bytes[c["region"]] += c["changed_bytes"]

                f.write("## Resumen de Regiones\n\n")
                f.write("| Región | Veces cambiada | Bytes totales |\n")
                f.write("|--------|----------------|---------------|\n")
                for region in sorted(region_counts.keys(),
                                     key=lambda r: -region_bytes[r]):
                    f.write(f"| {region} | {region_counts[region]} "
                            f"| {region_bytes[region]:,} |\n")
                f.write("\n")

                # State transitions
                big_changes = [e for e in entries
                               if e.get("total_changed", 0) > 50000]
                if big_changes:
                    f.write("## Transiciones Detectadas (>50KB)\n\n")
                    for e in big_changes:
                        f.write(f"- [{e['timestamp']}] "
                                f"Δ{e['total_changed']:,}B — "
                                f"P1:{e.get('p1',{}).get('char','?')} "
                                f"P2:{e.get('p2',{}).get('char','?')}\n")

    print(f"Resumen guardado en: {md_path}")


# ===========================================================================
# snap command — named snapshots
# ===========================================================================

def cmd_snap(args):
    """Tomar un snapshot nombrado de la RAM actual."""
    ensure_dirs()
    handle, pid, ram_base = attach()
    print(f"Conectado a Flycast (PID {pid})")

    data = dump_full_ram(handle, ram_base)
    summary = extract_game_summary(data)

    name = args.name or datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(SNAPSHOTS_DIR, f"{name}.bin")

    # Build metadata
    meta = {
        "name": name,
        "timestamp": datetime.now().isoformat(),
        "description": args.description or "",
        "pid": pid,
        "ram_base": f"0x{ram_base:X}",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "game_state": quick_game_state(data),
        "summary": _serialize_summary(summary),
    }

    save_snapshot(data, filepath, meta)
    close_process(handle)

    print(f"Snapshot guardado: {filepath}")
    print(f"  Tamaño: {len(data):,} bytes")
    print(f"  SHA256: {meta['sha256'][:16]}...")
    print(f"  Estado: {', '.join(meta['game_state'])}")


def _serialize_summary(summary):
    """Make summary JSON-serializable."""
    s = {}
    if summary.get("camera"):
        s["camera"] = summary["camera"]
    s["players"] = []
    for pi in summary.get("players", []):
        p = {"side": pi["side"]}
        team = pi.get("team", {})
        p["roster"] = []
        for pe in team.get("playerExtra", []):
            p["roster"].append({
                "charID": pe.get("charID"),
                "char": char_name(pe.get("charID", 0xFF)),
                "hp": pe.get("health"),
                "teamPos": pe.get("teamPosition"),
            })
        p["point"] = team.get("point")
        p["super"] = team.get("super")
        if pi.get("player"):
            pf = pi["player"]
            p["action"] = pf.get("actionID")
            p["animFrame"] = pf.get("animFrameIndex")
            p["facing"] = pf.get("facing")
            pos = pf.get("position")
            if isinstance(pos, tuple):
                p["position"] = list(pos)
            else:
                p["position"] = pos
        s["players"].append(p)
    return s


# ===========================================================================
# compare command
# ===========================================================================

def cmd_compare(args):
    """Comparar dos snapshots y generar reporte de diferencias."""
    data1 = load_snapshot(args.file1)
    data2 = load_snapshot(args.file2)
    meta1 = load_metadata(args.file1) or {"name": os.path.basename(args.file1)}
    meta2 = load_metadata(args.file2) or {"name": os.path.basename(args.file2)}

    name1 = meta1.get("name", "A")
    name2 = meta2.get("name", "B")

    print(f"Comparando: {name1} vs {name2}")
    print(f"  Tamaños: {len(data1):,} vs {len(data2):,}")

    min_len = min(len(data1), len(data2))
    total_diff = 0
    region_diffs = []

    for rname, start, end in WATCH_REGIONS:
        if end > min_len:
            end = min_len
        if start >= end:
            continue
        changed = sum(1 for i in range(start, end)
                      if data1[i] != data2[i])
        total_diff += changed
        if changed > 0:
            region_diffs.append((rname, start, end, changed))

    print(f"\n  Total bytes diferentes: {total_diff:,}")
    print(f"\n  {'Región':<18} {'Inicio':<10} {'Fin':<10} {'Bytes Δ':<10} {'%':<8}")
    print(f"  {'─'*18} {'─'*10} {'─'*10} {'─'*10} {'─'*8}")
    for rname, start, end, changed in region_diffs:
        pct = changed / (end - start) * 100
        print(f"  {rname:<18} 0x{start:06X}  0x{end:06X}  {changed:<10} {pct:.1f}%")

    # Game state comparison
    print(f"\n--- Estado del juego: {name1} ---")
    sum1 = extract_game_summary(data1)
    _print_game_summary(sum1)
    print(f"\n--- Estado del juego: {name2} ---")
    sum2 = extract_game_summary(data2)
    _print_game_summary(sum2)

    # Generate markdown if requested
    if args.output:
        _write_compare_report(args.output, name1, name2,
                              data1, data2, region_diffs, sum1, sum2)


def _print_game_summary(summary):
    for pi in summary.get("players", []):
        team = pi.get("team", {})
        chars = [char_name(pe.get("charID", 0xFF))
                 for pe in team.get("playerExtra", [])]
        print(f"  P{pi['side']}: {'/'.join(chars)}")
        if pi.get("player"):
            pf = pi["player"]
            print(f"    action={pf.get('actionID')} "
                  f"animFrame={pf.get('animFrameIndex')} "
                  f"facing={pf.get('facing')}")


def _write_compare_report(filepath, name1, name2,
                          data1, data2, diffs, sum1, sum2):
    ensure_dirs()
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Comparación: {name1} vs {name2}\n\n")
        f.write(f"Generado: {datetime.now().isoformat()}\n\n")

        f.write("## Resumen de Diferencias\n\n")
        f.write("| Región | Inicio | Fin | Bytes Δ | % |\n")
        f.write("|--------|--------|-----|---------|---|\n")
        total = 0
        for rname, start, end, changed in diffs:
            pct = changed / (end - start) * 100
            f.write(f"| {rname} | 0x{start:06X} | 0x{end:06X} "
                    f"| {changed:,} | {pct:.1f}% |\n")
            total += changed
        f.write(f"| **TOTAL** | | | **{total:,}** | |\n\n")

        f.write("## Estado del Juego\n\n")
        for label, summary in [(name1, sum1), (name2, sum2)]:
            f.write(f"### {label}\n\n")
            for pi in summary.get("players", []):
                team = pi.get("team", {})
                chars = [char_name(pe.get("charID", 0xFF))
                         for pe in team.get("playerExtra", [])]
                f.write(f"- P{pi['side']}: {'/'.join(chars)}\n")
                if pi.get("player"):
                    pf = pi["player"]
                    f.write(f"  - action={pf.get('actionID')} "
                            f"animFrame={pf.get('animFrameIndex')} "
                            f"facing={pf.get('facing')}\n")
            f.write("\n")

        # Detailed hex diffs for player structs
        f.write("## Diffs Detallados (Player Structs)\n\n")
        for side_idx, side_name in enumerate(["P1", "P2"]):
            p_off_1 = None
            p_off_2 = None
            for pi in sum1.get("players", []):
                if pi["side"] == side_idx + 1:
                    p_off_1 = pi.get("player_offset")
            for pi in sum2.get("players", []):
                if pi["side"] == side_idx + 1:
                    p_off_2 = pi.get("player_offset")
            if p_off_1 and p_off_2 and p_off_1 == p_off_2:
                f.write(f"### {side_name} Player Struct (0x{p_off_1:06X})\n\n")
                f.write("```\n")
                for off in range(0, PLAYER_STRUCT_SIZE, 16):
                    abs_off = p_off_1 + off
                    if abs_off + 16 > len(data1) or abs_off + 16 > len(data2):
                        break
                    chunk1 = data1[abs_off:abs_off+16]
                    chunk2 = data2[abs_off:abs_off+16]
                    if chunk1 != chunk2:
                        hex1 = " ".join(f"{b:02X}" for b in chunk1)
                        hex2 = " ".join(f"{b:02X}" for b in chunk2)
                        markers = " ".join(
                            "^^" if a != b else "  "
                            for a, b in zip(chunk1, chunk2))
                        f.write(f"+{off:03X}: {hex1}  (A)\n")
                        f.write(f"+{off:03X}: {hex2}  (B)\n")
                        f.write(f"      {markers}\n")
                f.write("```\n\n")

    print(f"Reporte guardado: {filepath}")


# ===========================================================================
# lztrack command — track LZ decompression activity
# ===========================================================================

def cmd_lztrack(args):
    """Rastrear actividad de descompresión LZ en RAM."""
    ensure_dirs()
    handle, pid, ram_base = attach()
    print(f"Conectado a Flycast (PID {pid})")
    print("Rastreando actividad de descompresión LZ...")
    print("Este modo detecta cuando regiones de RAM cambian masivamente")
    print("(indicador de que un archivo .lz fue descomprimido en memoria).")
    print("Presiona Ctrl+C para detener.\n")

    session_dir = os.path.join(SESSIONS_DIR,
                               f"lztrack_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(session_dir, exist_ok=True)

    interval = args.interval or 0.2
    # Hash each 4KB page of the monitored regions
    PAGE_SIZE = 4096
    prev_hashes = {}  # (region_name, page_idx) -> hash
    event_count = 0

    # Read initial state
    for rname, start, size in LZ_BUFFER_CANDIDATES:
        try:
            region_data = read_region(handle, ram_base, start, size)
            for pg in range(0, size, PAGE_SIZE):
                key = (rname, pg // PAGE_SIZE)
                prev_hashes[key] = hashlib.md5(
                    region_data[pg:pg+PAGE_SIZE]).digest()
        except OSError:
            pass

    log_path = os.path.join(session_dir, "lz_events.ndjson")
    log_file = open(log_path, "w", encoding="utf-8")

    try:
        while True:
            for rname, start, size in LZ_BUFFER_CANDIDATES:
                try:
                    region_data = read_region(handle, ram_base, start, size)
                except OSError:
                    continue

                changed_pages = []
                for pg in range(0, size, PAGE_SIZE):
                    key = (rname, pg // PAGE_SIZE)
                    h = hashlib.md5(region_data[pg:pg+PAGE_SIZE]).digest()
                    if key in prev_hashes and prev_hashes[key] != h:
                        changed_pages.append(pg // PAGE_SIZE)
                    prev_hashes[key] = h

                if len(changed_pages) >= 4:  # At least 16KB changed = likely LZ
                    event_count += 1
                    abs_start = start + changed_pages[0] * PAGE_SIZE
                    abs_end = start + (changed_pages[-1] + 1) * PAGE_SIZE
                    ts = datetime.now().isoformat()

                    # Check header for potential LZ size field
                    first_page_off = changed_pages[0] * PAGE_SIZE
                    header_u32 = st.unpack_from(
                        "<I", region_data, first_page_off)[0]

                    # Look for known content signatures
                    sig_bytes = region_data[first_page_off:first_page_off+8]
                    sig_hex = sig_bytes.hex()
                    sig_ascii = "".join(
                        chr(b) if 32 <= b < 127 else "."
                        for b in sig_bytes)

                    event = {
                        "timestamp": ts,
                        "event": event_count,
                        "region": rname,
                        "ram_start": f"0x{abs_start:06X}",
                        "ram_end": f"0x{abs_end:06X}",
                        "pages_changed": len(changed_pages),
                        "bytes_changed": len(changed_pages) * PAGE_SIZE,
                        "header_u32": f"0x{header_u32:08X}",
                        "signature": sig_hex,
                        "signature_ascii": sig_ascii,
                    }
                    log_file.write(json.dumps(event,
                                              ensure_ascii=False) + "\n")
                    log_file.flush()

                    print(f"[{event_count:3d}] {ts[11:19]} "
                          f"| {rname} 0x{abs_start:06X}–0x{abs_end:06X} "
                          f"| {len(changed_pages)} páginas "
                          f"| hdr=0x{header_u32:08X} "
                          f"| sig={sig_ascii}")

                    # Save the changed region content
                    snap_name = f"lz_{event_count:04d}_{rname}_0x{abs_start:06X}.bin"
                    snap_path = os.path.join(session_dir, snap_name)
                    with open(snap_path, "wb") as sf:
                        sf.write(region_data[first_page_off:
                                             first_page_off + len(changed_pages) * PAGE_SIZE])

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\nRastreo detenido. {event_count} eventos detectados.")
    finally:
        log_file.close()
        close_process(handle)

    # Write summary
    md_path = os.path.join(session_dir, "lztrack_summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Sesión LZ Track\n\n")
        f.write(f"- Eventos detectados: {event_count}\n")
        f.write(f"- Intervalo: {interval}s\n\n")
        if os.path.exists(log_path):
            events = []
            with open(log_path) as lf:
                for line in lf:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            if events:
                f.write("## Eventos\n\n")
                f.write("| # | Tiempo | Región | Rango RAM | Páginas | Header | Firma |\n")
                f.write("|---|--------|--------|-----------|---------|--------|-------|\n")
                for e in events:
                    f.write(f"| {e['event']} | {e['timestamp'][11:19]} "
                            f"| {e['region']} | {e['ram_start']}–{e['ram_end']} "
                            f"| {e['pages_changed']} | {e['header_u32']} "
                            f"| {e['signature_ascii']} |\n")
    print(f"Resumen: {md_path}")


# ===========================================================================
# anim command — capture animation sequence frame-by-frame
# ===========================================================================

def cmd_anim(args):
    """Capturar una secuencia de animación frame-by-frame."""
    ensure_dirs()
    handle, pid, ram_base = attach()
    print(f"Conectado a Flycast (PID {pid})")

    side = args.player  # 1 or 2
    frames = args.frames or 300
    interval = args.interval or 0.016  # ~60fps

    session_dir = os.path.join(SESSIONS_DIR,
                               f"anim_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(session_dir, exist_ok=True)

    log_path = os.path.join(session_dir, "anim_log.ndjson")
    log_file = open(log_path, "w", encoding="utf-8")

    print(f"Capturando {frames} frames de P{side} a ~{1/interval:.0f}fps")
    print("Presiona Ctrl+C para detener.\n")

    prev_action = None
    prev_anim = None
    action_sequences = []
    current_seq = None

    try:
        for frame_num in range(frames):
            data = dump_full_ram(handle, ram_base)
            team = extract_team_fields(data, TEAM_PTRS[side - 1])
            point = team.get("point", 0)

            player_off = None
            char_id = None
            for i in range(3):
                pe = team["playerExtra"][i]
                if pe.get("teamPosition") == point:
                    player_off = resolve_player_offset(
                        data, TEAM_PTRS[side - 1], i)
                    char_id = pe.get("charID")
                    break

            if player_off is None:
                time.sleep(interval)
                continue

            pf = extract_player_fields(data, player_off)
            hbs = extract_hitboxes(data, player_off)

            action = pf.get("actionID")
            anim_frame = pf.get("animFrameIndex")

            # Detect action changes
            if action != prev_action:
                if current_seq:
                    action_sequences.append(current_seq)
                current_seq = {
                    "action": action,
                    "start_frame": frame_num,
                    "char": char_name(char_id),
                    "frames": [],
                }
                marker = f" ← NUEVA ACCIÓN {action}" if prev_action is not None else ""
                print(f"  [{frame_num:4d}] action={action} "
                      f"anim={anim_frame} {marker}")

            # Log frame
            entry = {
                "frame": frame_num,
                "timestamp": time.time(),
                "action": action,
                "animFrame": anim_frame,
                "position": pf.get("position"),
                "velocity": pf.get("velocity"),
                "facing": pf.get("facing"),
                "hitboxesActive": pf.get("hitboxesActive"),
                "spriteOffsetX": pf.get("spriteOffsetX"),
                "spriteOffsetY": pf.get("spriteOffsetY"),
                "animPropertyA": pf.get("animPropertyA"),
                "animPropertyB": pf.get("animPropertyB"),
                "animPlayFlag": pf.get("animPlayFlag"),
                "animPhaseToggle": pf.get("animPhaseToggle"),
                "stunTimer": pf.get("stunTimer"),
                "hitboxes": hbs,
            }
            log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")

            if current_seq:
                current_seq["frames"].append({
                    "f": frame_num,
                    "anim": anim_frame,
                    "hbActive": pf.get("hitboxesActive"),
                })

            prev_action = action
            prev_anim = anim_frame
            time.sleep(interval)

    except KeyboardInterrupt:
        pass
    finally:
        if current_seq:
            action_sequences.append(current_seq)
        log_file.close()
        close_process(handle)

    # Write animation analysis
    md_path = os.path.join(session_dir, "anim_analysis.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Análisis de Animación\n\n")
        f.write(f"- Personaje: {action_sequences[0]['char'] if action_sequences else '?'}\n")
        f.write(f"- Lado: P{side}\n")
        f.write(f"- Frames capturados: {frame_num + 1}\n\n")

        f.write("## Secuencias de Acción Detectadas\n\n")
        for seq in action_sequences:
            anim_frames = [fr["anim"] for fr in seq["frames"]]
            unique_anims = sorted(set(anim_frames))
            duration = len(seq["frames"])
            f.write(f"### Action {seq['action']} "
                    f"(frame {seq['start_frame']}, {duration} game frames)\n\n")
            f.write(f"- animFrameIndex range: {min(anim_frames)}–{max(anim_frames)}\n")
            f.write(f"- Unique animFrameIndex values: {unique_anims}\n")
            f.write(f"- Total animation frames: {len(unique_anims)}\n")

            # Frame-by-frame timing
            if len(unique_anims) > 1:
                f.write(f"\n| animFrame | Duration (game frames) | hitboxesActive |\n")
                f.write(f"|-----------|----------------------|----------------|\n")
                current_anim = anim_frames[0]
                count = 0
                hb_val = None
                for fr in seq["frames"]:
                    if fr["anim"] == current_anim:
                        count += 1
                        hb_val = fr["hbActive"]
                    else:
                        f.write(f"| {current_anim} | {count} | 0x{hb_val or 0:02X} |\n")
                        current_anim = fr["anim"]
                        count = 1
                        hb_val = fr["hbActive"]
                f.write(f"| {current_anim} | {count} | 0x{hb_val or 0:02X} |\n")
            f.write("\n")

        f.write("## Mapa Acción → animFrameIndex\n\n")
        f.write("| Action | Rango animFrame | Duración | Tipo Inferido |\n")
        f.write("|--------|----------------|----------|---------------|\n")
        for seq in action_sequences:
            anim_frames = [fr["anim"] for fr in seq["frames"]]
            lo, hi = min(anim_frames), max(anim_frames)
            duration = len(seq["frames"])
            f.write(f"| {seq['action']} | {lo}–{hi} | {duration}f | |\n")

    print(f"\nAnálisis guardado: {md_path}")
    print(f"Log: {log_path}")


# ===========================================================================
# report command — analyze a session
# ===========================================================================

def cmd_report(args):
    """Generar reporte de análisis de una sesión."""
    session_dir = args.session_dir
    if not os.path.isdir(session_dir):
        print(f"ERROR: {session_dir} no es un directorio.")
        sys.exit(1)

    # Find all snapshots
    snaps = sorted([f for f in os.listdir(session_dir) if f.endswith(".bin")])
    logs = sorted([f for f in os.listdir(session_dir) if f.endswith(".ndjson")])

    print(f"Sesión: {session_dir}")
    print(f"  Snapshots: {len(snaps)}")
    print(f"  Logs: {len(logs)}")

    if not snaps:
        print("  No hay snapshots para analizar.")
        return

    md_path = os.path.join(session_dir, "full_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Reporte de Sesión\n\n")
        f.write(f"- Directorio: `{session_dir}`\n")
        f.write(f"- Snapshots: {len(snaps)}\n\n")

        # Analyze each snapshot
        f.write("## Snapshots\n\n")
        prev_data = None
        for snap_file in snaps:
            snap_path = os.path.join(session_dir, snap_file)
            data = load_snapshot(snap_path)
            meta = load_metadata(snap_path)

            f.write(f"### {snap_file}\n\n")
            if meta:
                for k, v in meta.items():
                    if k not in ("sha256", "summary"):
                        f.write(f"- {k}: {v}\n")

            summary = extract_game_summary(data)
            for pi in summary.get("players", []):
                team = pi.get("team", {})
                chars = [char_name(pe.get("charID", 0xFF))
                         for pe in team.get("playerExtra", [])]
                f.write(f"- P{pi['side']}: {'/'.join(chars)}")
                if pi.get("player"):
                    pf = pi["player"]
                    f.write(f" action={pf.get('actionID')}")
                f.write("\n")

            if prev_data is not None and len(data) == len(prev_data):
                diff_bytes = sum(1 for a, b in zip(data, prev_data) if a != b)
                f.write(f"- Δ respecto anterior: {diff_bytes:,} bytes\n")

            f.write("\n")
            prev_data = data

    print(f"Reporte: {md_path}")


# ===========================================================================
# framecap command — full RAM capture at ~60fps for diff analysis
# ===========================================================================

def cmd_framecap(args):
    """Captura completa de RAM frame-by-frame para análisis de cambios."""
    ensure_dirs()
    handle, pid, ram_base = attach()
    fps = args.fps or 60
    duration = args.duration or 3.0
    total_frames = int(fps * duration)
    interval = 1.0 / fps

    PAGE_SIZE = 4096
    NUM_PAGES = SH4_RAM_SIZE // PAGE_SIZE  # 4096

    # Estimate: assume ~2% of pages change per frame (~80 pages × 4KB = 320KB)
    est_delta = total_frames * NUM_PAGES * 0.02 * PAGE_SIZE
    print(f"Conectado a Flycast (PID {pid})")
    print(f"Config: {fps}fps x {duration}s = {total_frames} frames")
    print(f"RAM: {SH4_RAM_SIZE // (1024*1024)}MB = {NUM_PAGES} paginas de {PAGE_SIZE}B")
    print(f"Espacio: base 16MB + deltas ~{est_delta / (1024*1024):.0f}MB "
          f"(estimado, peor caso {total_frames * SH4_RAM_SIZE // (1024*1024)}MB)")
    print("Presiona Ctrl+C para detener.\n")

    session_dir = os.path.join(
        SESSIONS_DIR,
        f"framecap_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(session_dir, exist_ok=True)

    # --- Base frame ---
    print("Capturando frame base...", end=" ", flush=True)
    base_data = dump_full_ram(handle, ram_base)
    base_path = os.path.join(session_dir, "frame_base.bin")
    with open(base_path, "wb") as f:
        f.write(base_data)
    print(f"OK ({len(base_data):,} bytes)")

    prev_data = bytearray(base_data)

    delta_path = os.path.join(session_dir, "deltas.bin")
    log_path = os.path.join(session_dir, "framecap_log.ndjson")
    delta_file = open(delta_path, "wb")
    log_file = open(log_path, "w", encoding="utf-8")

    # Binary header: magic + metadata (will patch frame_count at end)
    delta_file.write(st.pack("<4sIIII",
        b"FCAP",           # magic
        1,                 # version
        PAGE_SIZE,         # 4096
        NUM_PAGES,         # 4096
        total_frames,      # planned frames (patched later)
    ))

    total_delta_bytes = 20  # header size
    page_change_count = [0] * NUM_PAGES
    frame_stats = []
    captured = 0

    try:
        t0 = time.perf_counter()
        for frame_num in range(total_frames):
            t_frame = time.perf_counter()
            try:
                data = dump_full_ram(handle, ram_base)
            except OSError:
                print(f"\n[!] Error leyendo RAM en frame {frame_num}")
                break

            # Find changed pages (direct bytes comparison is fast in C)
            changed = []
            for pg in range(NUM_PAGES):
                off = pg * PAGE_SIZE
                if data[off:off + PAGE_SIZE] != prev_data[off:off + PAGE_SIZE]:
                    changed.append(pg)
                    page_change_count[pg] += 1

            # Write delta record:
            #   u32 frame_number, u16 page_count,
            #   then per changed page: u16 page_index + 4096 bytes
            delta_file.write(st.pack("<IH", frame_num, len(changed)))
            for pg in changed:
                off = pg * PAGE_SIZE
                delta_file.write(st.pack("<H", pg))
                delta_file.write(data[off:off + PAGE_SIZE])

            frame_bytes = 6 + len(changed) * (2 + PAGE_SIZE)
            total_delta_bytes += frame_bytes

            # Lightweight game state (avoid heavy extraction)
            cam = extract_camera(data)
            entry = {
                "frame": frame_num,
                "t_ms": round((time.perf_counter() - t0) * 1000, 2),
                "pages": len(changed),
                "delta_kb": round(frame_bytes / 1024, 1),
            }
            if cam:
                entry["cam"] = [cam["posX"], cam["posY"]]
            log_file.write(json.dumps(entry) + "\n")

            frame_ms = (time.perf_counter() - t_frame) * 1000
            frame_stats.append((len(changed), frame_bytes, frame_ms))

            # Progress every 10 frames
            if frame_num % 10 == 0 or frame_num == total_frames - 1:
                pct = (frame_num + 1) / total_frames * 100
                print(f"\r  [{frame_num+1:4d}/{total_frames}] "
                      f"{len(changed):4d} pag D  "
                      f"{frame_bytes/1024:6.1f}KB  "
                      f"{frame_ms:5.1f}ms/f  "
                      f"acum={total_delta_bytes/1024/1024:.1f}MB  "
                      f"({pct:.0f}%)", end="", flush=True)

            prev_data[:] = data
            captured = frame_num + 1

            # Rate limit to target fps
            elapsed = time.perf_counter() - t_frame
            wait = interval - elapsed
            if wait > 0:
                time.sleep(wait)

    except KeyboardInterrupt:
        print(f"\nCaptura interrumpida en frame {captured}.")
    finally:
        # Patch actual frame count in header
        delta_file.seek(12)  # offset of num_pages field... actually planned_frames
        delta_file.seek(16)
        delta_file.write(st.pack("<I", captured))
        delta_file.close()
        log_file.close()
        close_process(handle)

    print()
    if captured > 0:
        _write_framecap_summary(
            session_dir, frame_stats, page_change_count,
            total_delta_bytes, captured, fps, PAGE_SIZE, NUM_PAGES)


def _write_framecap_summary(session_dir, frame_stats, page_change_count,
                            total_delta_bytes, captured, target_fps,
                            page_size, num_pages):
    """Generate analysis summary for a framecap session."""
    md_path = os.path.join(session_dir, "framecap_summary.md")

    total_ms = sum(s[2] for s in frame_stats)
    actual_fps = captured / (total_ms / 1000) if total_ms > 0 else 0
    avg_pages = sum(s[0] for s in frame_stats) / captured
    avg_bytes = sum(s[1] for s in frame_stats) / captured
    max_pages = max(s[0] for s in frame_stats)
    min_pages = min(s[0] for s in frame_stats)

    # Classify pages by region
    def page_region(pg):
        addr = pg * page_size
        for rname, start, end in WATCH_REGIONS:
            if start <= addr < end:
                return rname
        return "other"

    region_freq = defaultdict(int)
    for pg, count in enumerate(page_change_count):
        if count > 0:
            region_freq[page_region(pg)] += count

    # Find hottest pages
    hot_pages = sorted(
        [(pg, count) for pg, count in enumerate(page_change_count) if count > 0],
        key=lambda x: -x[1]
    )

    # Console output
    print(f"--- Resumen framecap ---")
    print(f"  Frames: {captured}  FPS real: {actual_fps:.1f}  "
          f"(objetivo: {target_fps})")
    print(f"  Paginas D/frame: avg={avg_pages:.1f}  "
          f"min={min_pages}  max={max_pages}")
    print(f"  Espacio total: base 16MB + deltas "
          f"{total_delta_bytes/1024/1024:.1f}MB = "
          f"{(SH4_RAM_SIZE + total_delta_bytes)/1024/1024:.1f}MB")
    print(f"  Directorio: {session_dir}")

    # Markdown report
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Framecap Session\n\n")
        f.write(f"- Frames capturados: {captured}\n")
        f.write(f"- FPS objetivo: {target_fps}  |  FPS real: {actual_fps:.1f}\n")
        f.write(f"- Tiempo de captura: {total_ms/1000:.2f}s\n")
        f.write(f"- Espacio: frame_base=16MB  deltas="
                f"{total_delta_bytes/1024/1024:.1f}MB  "
                f"total={((SH4_RAM_SIZE + total_delta_bytes)/1024/1024):.1f}MB\n\n")

        f.write(f"## Estadisticas por Frame\n\n")
        f.write(f"- Paginas cambiadas/frame: "
                f"avg={avg_pages:.1f}  min={min_pages}  max={max_pages}\n")
        f.write(f"- Bytes delta/frame: "
                f"avg={avg_bytes/1024:.1f}KB  "
                f"max={max(s[1] for s in frame_stats)/1024:.1f}KB\n")
        f.write(f"- Tiempo/frame: "
                f"avg={total_ms/captured:.1f}ms  "
                f"max={max(s[2] for s in frame_stats):.1f}ms\n\n")

        # Region breakdown
        f.write(f"## Cambios por Region\n\n")
        f.write(f"| Region | Cambios totales | Cambios/frame |\n")
        f.write(f"|--------|-----------------|---------------|\n")
        for rname in sorted(region_freq, key=lambda r: -region_freq[r]):
            total = region_freq[rname]
            per_frame = total / captured
            f.write(f"| {rname} | {total:,} | {per_frame:.1f} |\n")
        f.write(f"\n")

        # Hottest pages
        f.write(f"## Top 30 Paginas Mas Volatiles\n\n")
        f.write(f"| Pagina | Direccion | Region | "
                f"Veces cambiada | % frames |\n")
        f.write(f"|--------|-----------|--------|"
                f"----------------|----------|\n")
        for pg, count in hot_pages[:30]:
            addr = pg * page_size
            region = page_region(pg)
            pct = count / captured * 100
            f.write(f"| {pg} | 0x{addr:06X} | {region} "
                    f"| {count} | {pct:.0f}% |\n")
        f.write(f"\n")

        # Page change heatmap (compact: group by 64KB blocks = 16 pages)
        BLOCK_PAGES = 16  # 64KB blocks
        f.write(f"## Heatmap (bloques de 64KB)\n\n")
        f.write(f"| Bloque | Direccion | "
                f"Cambios/frame | Actividad |\n")
        f.write(f"|--------|-----------|"
                f"--------------|----------|\n")
        for blk in range(num_pages // BLOCK_PAGES):
            blk_start = blk * BLOCK_PAGES
            blk_total = sum(page_change_count[blk_start + i]
                            for i in range(BLOCK_PAGES))
            if blk_total == 0:
                continue
            addr = blk * BLOCK_PAGES * page_size
            per_frame = blk_total / captured
            bar_len = min(int(per_frame), 40)
            bar = "#" * bar_len
            f.write(f"| {blk:3d} | 0x{addr:06X} "
                    f"| {per_frame:7.1f} | {bar} |\n")
        f.write(f"\n")

        # Frame timeline (every 10th frame)
        f.write(f"## Timeline (cada 10 frames)\n\n")
        f.write(f"| Frame | Paginas D | Delta KB | ms/frame |\n")
        f.write(f"|-------|-----------|----------|----------|\n")
        for i in range(0, len(frame_stats), 10):
            pages, byt, ms = frame_stats[i]
            f.write(f"| {i:4d} | {pages:4d} | {byt/1024:7.1f} "
                    f"| {ms:6.1f} |\n")
        f.write(f"\n")

        # Format explanation
        f.write(f"## Formato de deltas.bin\n\n")
        f.write(f"```\n")
        f.write(f"Header (20 bytes):\n")
        f.write(f"  bytes[4]  magic = 'FCAP'\n")
        f.write(f"  u32       version = 1\n")
        f.write(f"  u32       page_size = {page_size}\n")
        f.write(f"  u32       num_pages = {num_pages}\n")
        f.write(f"  u32       frame_count = {captured}\n")
        f.write(f"\n")
        f.write(f"Per frame:\n")
        f.write(f"  u32       frame_number\n")
        f.write(f"  u16       changed_page_count\n")
        f.write(f"  Per changed page:\n")
        f.write(f"    u16     page_index (0..{num_pages-1})\n")
        f.write(f"    bytes[{page_size}] page_data\n")
        f.write(f"\n")
        f.write(f"Para reconstruir frame N: aplicar deltas 0..N sobre "
                f"frame_base.bin\n")
        f.write(f"```\n")

    print(f"Resumen: {md_path}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="KOF XI Flycast — RAM Watch Pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # watch
    p_watch = subparsers.add_parser("watch",
        help="Monitoreo continuo de RAM")
    p_watch.add_argument("--interval", "-i", type=float,
        help="Segundos entre lecturas (default: 0.5)")
    p_watch.add_argument("--full", action="store_true",
        help="Leer RAM completa (16MB) en vez de solo regiones clave")

    # snap
    p_snap = subparsers.add_parser("snap",
        help="Tomar un snapshot nombrado")
    p_snap.add_argument("--name", "-n",
        help="Nombre del snapshot (default: timestamp)")
    p_snap.add_argument("--description", "-d",
        help="Descripción del estado del juego")

    # compare
    p_cmp = subparsers.add_parser("compare",
        help="Comparar dos snapshots")
    p_cmp.add_argument("file1", help="Primer snapshot .bin")
    p_cmp.add_argument("file2", help="Segundo snapshot .bin")
    p_cmp.add_argument("--output", "-o",
        help="Archivo .md para el reporte")

    # lztrack
    p_lz = subparsers.add_parser("lztrack",
        help="Rastrear descompresión LZ en RAM")
    p_lz.add_argument("--interval", "-i", type=float,
        help="Segundos entre lecturas (default: 0.2)")

    # anim
    p_anim = subparsers.add_parser("anim",
        help="Capturar secuencia de animación")
    p_anim.add_argument("--player", "-p", type=int, default=1,
        help="Lado del jugador (1 o 2)")
    p_anim.add_argument("--frames", "-f", type=int, default=300,
        help="Número de frames a capturar")
    p_anim.add_argument("--interval", "-i", type=float,
        help="Segundos entre lecturas (default: 0.016)")

    # report
    p_report = subparsers.add_parser("report",
        help="Generar reporte de sesión")
    p_report.add_argument("session_dir",
        help="Directorio de sesión a analizar")

    # framecap
    p_fc = subparsers.add_parser("framecap",
        help="Captura de RAM completa frame-by-frame (~60fps)")
    p_fc.add_argument("--fps", type=int, default=60,
        help="Frames por segundo objetivo (default: 60)")
    p_fc.add_argument("--duration", "-d", type=float, default=3.0,
        help="Duracion en segundos (default: 3.0)")

    args = parser.parse_args()
    commands = {
        "watch": cmd_watch,
        "snap": cmd_snap,
        "compare": cmd_compare,
        "lztrack": cmd_lztrack,
        "anim": cmd_anim,
        "report": cmd_report,
        "framecap": cmd_framecap,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
