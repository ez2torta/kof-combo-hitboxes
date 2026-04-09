"""
KOF XI Memory Analyzer — Offline analysis of SH-4 RAM snapshots.

Modes:
    struct   - Extract and display player/team/camera structs from a snapshot
    diff     - Compare two snapshots and show changed regions
    heatmap  - Analyze a series of snapshots and show which bytes change
    scan     - Search for byte patterns, strings, or values in a snapshot
    gamestate - Attempt to identify what game state a snapshot represents

Usage:
    python memory_analyzer.py struct snapshot.bin
    python memory_analyzer.py diff snap1.bin snap2.bin
    python memory_analyzer.py heatmap snapshots_dir/ --output report.md
    python memory_analyzer.py scan snapshot.bin --pattern "MUTEKI"
    python memory_analyzer.py gamestate snapshot.bin
"""
import argparse
import os
import sys
import struct as st
import json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis_utils import (
    load_snapshot, load_metadata, extract_camera, extract_team_fields,
    extract_player_fields, extract_hitboxes, resolve_player_offset,
    char_name, CAMERA_PTR, TEAM_PTRS, TEAM_SIZE, PLAYER_STRUCT_SIZE,
    PLAYER_OFFSETS, HITBOX_OFFSET, HITBOX_SIZE, HITBOX_COUNT,
    PLAYER_EXTRA_OFFSET, PLAYER_EXTRA_SIZE, SH4_RAM_SIZE,
    TEAM_ENTRIES_OFFSET, sh4_to_ram_offset,
)

ANALYSIS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "aw_data", "analysis")


# ===========================================================================
# STRUCT mode
# ===========================================================================

def cmd_struct(args):
    """Extract and display game structs from a RAM snapshot."""
    data = load_snapshot(args.snapshot)
    meta = load_metadata(args.snapshot)
    label = (meta or {}).get("label", os.path.basename(args.snapshot))
    print(f"=== Análisis Estructural: {label} ===")
    print(f"Tamaño del dump: {len(data)} bytes (0x{len(data):X})")
    print()

    # Camera
    cam = extract_camera(data)
    if cam:
        print(f"Camera: X={cam['posX']} Y={cam['posY']} "
              f"restrictor={cam['restrictor']:.3f}")
    print()

    # Teams and players
    for side in range(2):
        team_off = TEAM_PTRS[side]
        team = extract_team_fields(data, team_off)
        print(f"--- P{side+1} Team (offset 0x{team_off:X}) ---")
        print(f"  point={team['point']} leader={team['leader']} "
              f"combo={team['comboCounter']} "
              f"super=0x{team['super']:X} skill=0x{team['skillStock']:X}")

        for i in range(3):
            pe = team["playerExtra"][i]
            is_point = " [POINT]" if pe["teamPosition"] == team["point"] else ""
            print(f"  slot[{i}]{is_point}: {char_name(pe['charID'])} "
                  f"(0x{pe['charID']:02X}) HP={pe['health']} "
                  f"pos={pe['teamPosition']}")

        # Resolve player struct
        for i in range(3):
            pe = team["playerExtra"][i]
            if pe["teamPosition"] != team["point"]:
                continue  # Only show point character in detail

            player_off = resolve_player_offset(data, team_off, i)
            if player_off is None:
                print(f"  >> No se pudo resolver player struct para slot[{i}]")
                continue

            print(f"\n  >> Player Struct (slot[{i}], offset 0x{player_off:X}):")
            pf = extract_player_fields(data, player_off)
            for fname, fval in pf.items():
                if isinstance(fval, tuple):
                    print(f"     {fname}: {fval}")
                elif isinstance(fval, int) and fval > 255:
                    print(f"     {fname}: {fval} (0x{fval:X})")
                else:
                    print(f"     {fname}: {fval}")

            # Hitboxes
            hbs = extract_hitboxes(data, player_off)
            active = pf.get("hitboxesActive", 0)
            print(f"     hitboxesActive: 0x{active:02X}")
            for hb in hbs:
                slot = hb["slot"]
                is_active = "✓" if (active >> slot) & 1 else " "
                print(f"     [{is_active}] hb[{slot}]: pos=({hb['posX']},{hb['posY']}) "
                      f"id=0x{hb['boxID']:02X} w={hb['width']} h={hb['height']}")
        print()


# ===========================================================================
# DIFF mode
# ===========================================================================

def cmd_diff(args):
    """Compare two RAM snapshots and report differences."""
    data1 = load_snapshot(args.snapshot1)
    data2 = load_snapshot(args.snapshot2)
    meta1 = load_metadata(args.snapshot1)
    meta2 = load_metadata(args.snapshot2)
    label1 = (meta1 or {}).get("label", os.path.basename(args.snapshot1))
    label2 = (meta2 or {}).get("label", os.path.basename(args.snapshot2))

    min_len = min(len(data1), len(data2))
    if min_len == 0:
        print("ERROR: Uno o ambos snapshots están vacíos.")
        return

    print(f"=== Diff: {label1} vs {label2} ===")
    print(f"  Tamaño A: {len(data1)} bytes")
    print(f"  Tamaño B: {len(data2)} bytes")
    print(f"  Comparando: {min_len} bytes")
    print()

    # Find all changed bytes
    changes = []
    for i in range(min_len):
        if data1[i] != data2[i]:
            changes.append(i)

    print(f"Total de bytes cambiados: {len(changes)} / {min_len} "
          f"({len(changes)/min_len*100:.4f}%)")
    print()

    if not changes:
        print("Los snapshots son idénticos.")
        return

    # Group changes into contiguous regions
    regions = []
    if changes:
        region_start = changes[0]
        region_end = changes[0]
        for i in range(1, len(changes)):
            if changes[i] <= region_end + 16:  # Allow small gaps
                region_end = changes[i]
            else:
                regions.append((region_start, region_end))
                region_start = changes[i]
                region_end = changes[i]
        regions.append((region_start, region_end))

    print(f"Regiones con cambios: {len(regions)}")
    print()

    # Annotate known regions
    known_regions = [
        (0x000000, 0x00FFFF, "BIOS/vectores"),
        (0x010000, 0x132820, "Código del juego (EPR)"),
        (0x200000, 0x27CA9F, "Heap/Object pool"),
        (CAMERA_PTR, CAMERA_PTR + 7, "Camera struct"),
        (TEAM_PTRS[0], TEAM_PTRS[0] + TEAM_SIZE - 1, "Team P1"),
        (TEAM_PTRS[1], TEAM_PTRS[1] + TEAM_SIZE - 1, "Team P2"),
        (0x280000, 0x2FFFFF, "Stack/working"),
        (0x300000, 0x3FFFFF, "Datos cargados"),
        (0x400000, 0xFFFFFF, "Gráficos/sprites"),
    ]

    def annotate_offset(offset):
        for start, end, label in known_regions:
            if start <= offset <= end:
                return label
        return "Desconocido"

    # Print regions summary
    if args.verbose or len(regions) <= 50:
        print("| Rango | Bytes cambiados | Región |")
        print("|-------|----------------|--------|")
        for start, end in regions:
            n = sum(1 for c in changes if start <= c <= end)
            ann = annotate_offset(start)
            print(f"| 0x{start:06X}–0x{end:06X} | {n} | {ann} |")
        print()

    # Summary by known region
    region_counts = defaultdict(int)
    for c in changes:
        region_counts[annotate_offset(c)] += 1

    print("Resumen por región:")
    for region_name, count in sorted(region_counts.items(),
                                      key=lambda x: -x[1]):
        print(f"  {region_name}: {count} bytes cambiados")
    print()

    # If requested, show detailed hex diff of specific areas
    if args.focus:
        focus_start = int(args.focus, 16) if args.focus.startswith("0x") \
            else int(args.focus)
        focus_size = args.focus_size or 256
        print(f"\n--- Detalle hex en 0x{focus_start:06X} "
              f"({focus_size} bytes) ---")
        for off in range(focus_start, min(focus_start + focus_size, min_len)):
            if data1[off] != data2[off]:
                print(f"  0x{off:06X}: {data1[off]:02X} -> {data2[off]:02X}")

    # Save report if output specified
    if args.output:
        _save_diff_report(args.output, label1, label2, changes, regions,
                         region_counts, min_len)


def _save_diff_report(filepath, label1, label2, changes, regions,
                      region_counts, total_size):
    """Save diff report as Markdown."""
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Diff Report: {label1} vs {label2}\n\n")
        f.write(f"- Bytes comparados: {total_size}\n")
        f.write(f"- Bytes cambiados: {len(changes)} "
                f"({len(changes)/total_size*100:.4f}%)\n")
        f.write(f"- Regiones afectadas: {len(regions)}\n\n")
        f.write("## Resumen por Región\n\n")
        f.write("| Región | Bytes Cambiados |\n|--------|----------------|\n")
        for rn, cnt in sorted(region_counts.items(), key=lambda x: -x[1]):
            f.write(f"| {rn} | {cnt} |\n")
        f.write("\n## Regiones Detalladas\n\n")
        f.write("| Rango | Bytes |\n|-------|-------|\n")
        for start, end in regions[:100]:
            n = sum(1 for c in changes if start <= c <= end)
            f.write(f"| `0x{start:06X}`–`0x{end:06X}` | {n} |\n")
        if len(regions) > 100:
            f.write(f"\n... y {len(regions) - 100} regiones más.\n")
    print(f"Reporte guardado: {filepath}")


# ===========================================================================
# HEATMAP mode
# ===========================================================================

def cmd_heatmap(args):
    """Analyze a directory of sequential snapshots to find dynamic regions."""
    snap_dir = args.snapshots_dir
    files = sorted([f for f in os.listdir(snap_dir) if f.endswith(".bin")])
    if len(files) < 2:
        print("ERROR: Se necesitan al menos 2 snapshots para análisis.")
        return

    print(f"=== Heatmap de Cambios en Memoria ===")
    print(f"  Directorio: {snap_dir}")
    print(f"  Snapshots: {len(files)}")
    print()

    # Load first snapshot as reference
    ref_data = load_snapshot(os.path.join(snap_dir, files[0]))
    data_size = len(ref_data)

    # Track change count per byte
    change_counts = bytearray(data_size)
    comparisons = 0

    for i in range(1, len(files)):
        curr_data = load_snapshot(os.path.join(snap_dir, files[i]))
        if len(curr_data) != data_size:
            print(f"  ADVERTENCIA: {files[i]} tiene tamaño diferente, omitiendo.")
            continue
        for j in range(data_size):
            if ref_data[j] != curr_data[j]:
                if change_counts[j] < 255:
                    change_counts[j] += 1
        ref_data = curr_data
        comparisons += 1
        if (i % 10) == 0:
            print(f"  Procesados {i}/{len(files)}...")

    print(f"  Comparaciones realizadas: {comparisons}")

    # Analyze results
    hot_bytes = sum(1 for c in change_counts if c > 0)
    very_hot = sum(1 for c in change_counts if c > comparisons * 0.5)
    print(f"  Bytes que cambiaron al menos una vez: {hot_bytes}")
    print(f"  Bytes que cambiaron >50% de las veces: {very_hot}")

    # Group into hot regions (64-byte granularity)
    block_size = 64
    block_heats = []
    for block_start in range(0, data_size, block_size):
        block_end = min(block_start + block_size, data_size)
        block_heat = sum(change_counts[block_start:block_end])
        if block_heat > 0:
            block_heats.append((block_start, block_heat))

    block_heats.sort(key=lambda x: -x[1])

    print(f"\n  Top 30 bloques más activos (bloques de {block_size} bytes):")
    print(f"  {'Offset':<12} {'Calor':<8} {'Por comparación':<16}")
    for offset, heat in block_heats[:30]:
        avg = heat / max(comparisons, 1)
        bar = "█" * min(int(avg), 50)
        print(f"  0x{offset:06X}    {heat:<8} {avg:<16.1f} {bar}")

    # Save report
    if args.output:
        _save_heatmap_report(args.output, files, comparisons,
                            change_counts, block_heats, data_size)


def _save_heatmap_report(filepath, files, comparisons, change_counts,
                         block_heats, data_size):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("# Memory Heatmap Analysis\n\n")
        f.write(f"- Snapshots analizados: {len(files)}\n")
        f.write(f"- Comparaciones: {comparisons}\n")
        hot = sum(1 for c in change_counts if c > 0)
        f.write(f"- Bytes dinámicos: {hot} / {data_size}\n\n")
        f.write("## Top Bloques Más Activos\n\n")
        f.write("| Offset | Calor Total | Promedio por Frame |\n")
        f.write("|--------|------------|-------------------|\n")
        for offset, heat in block_heats[:100]:
            avg = heat / max(comparisons, 1)
            f.write(f"| `0x{offset:06X}` | {heat} | {avg:.1f} |\n")
    print(f"Reporte guardado: {filepath}")


# ===========================================================================
# SCAN mode
# ===========================================================================

def cmd_scan(args):
    """Search for patterns in a RAM snapshot."""
    data = load_snapshot(args.snapshot)
    print(f"=== Scan: {os.path.basename(args.snapshot)} ===")
    print(f"  Tamaño: {len(data)} bytes")

    if args.pattern:
        # String search
        pattern = args.pattern.encode("ascii")
        results = []
        pos = 0
        while pos < len(data):
            idx = data.find(pattern, pos)
            if idx < 0:
                break
            results.append(idx)
            pos = idx + 1
        print(f"\n  Buscando string '{args.pattern}': {len(results)} resultados")
        for r in results[:50]:
            context = data[r:r+len(pattern)+16]
            printable = "".join(
                chr(b) if 32 <= b < 127 else "." for b in context)
            print(f"    0x{r:06X}: {context[:16].hex()} ... {printable}")

    if args.hex_pattern:
        # Hex byte pattern search
        pattern = bytes.fromhex(args.hex_pattern.replace(" ", ""))
        results = []
        pos = 0
        while pos < len(data):
            idx = data.find(pattern, pos)
            if idx < 0:
                break
            results.append(idx)
            pos = idx + 1
        print(f"\n  Buscando hex '{args.hex_pattern}': {len(results)} resultados")
        for r in results[:50]:
            print(f"    0x{r:06X}")

    if args.u8 is not None:
        val = args.u8
        print(f"\n  Buscando u8 == {val} (0x{val:02X}):")
        count = 0
        for i in range(len(data)):
            if data[i] == val:
                count += 1
        print(f"    {count} ocurrencias")

    if args.u16 is not None:
        val = args.u16
        pattern = st.pack("<H", val)
        results = []
        for i in range(0, len(data) - 1):
            if data[i:i+2] == pattern:
                results.append(i)
        print(f"\n  Buscando u16 == {val} (0x{val:04X}): {len(results)} resultados")
        for r in results[:30]:
            print(f"    0x{r:06X}")

    if args.u32 is not None:
        val = args.u32
        pattern = st.pack("<I", val)
        results = []
        for i in range(0, len(data) - 3):
            if data[i:i+4] == pattern:
                results.append(i)
        print(f"\n  Buscando u32 == {val} (0x{val:08X}): {len(results)} resultados")
        for r in results[:30]:
            print(f"    0x{r:06X}")


# ===========================================================================
# GAMESTATE mode
# ===========================================================================

# Known indicators for different game states (heuristic)
def cmd_gamestate(args):
    """Attempt to identify the current game state from a snapshot."""
    data = load_snapshot(args.snapshot)
    print(f"=== Detección de Game State: {os.path.basename(args.snapshot)} ===")
    print(f"  Tamaño: {len(data)} bytes")
    print()

    if len(data) < SH4_RAM_SIZE:
        print("ADVERTENCIA: Snapshot más pequeño que 16 MB. "
              "Algunos análisis pueden fallar.")

    # Check if camera has valid values
    cam = extract_camera(data) if len(data) >= CAMERA_PTR + 8 else None
    if cam:
        print(f"  Camera: X={cam['posX']} Y={cam['posY']}")
    else:
        print("  Camera: No accesible")

    # Check team structs
    teams_valid = True
    for side in range(2):
        if len(data) < TEAM_PTRS[side] + TEAM_SIZE:
            teams_valid = False
            break
        team = extract_team_fields(data, TEAM_PTRS[side])
        pe0 = team["playerExtra"][0]
        if pe0["charID"] == 0 and pe0["health"] == 0:
            teams_valid = False

    # Heuristics for game state identification
    indicators = {}

    # Check for character string presence in known areas
    # During fight, team structs have valid character data
    if teams_valid:
        indicators["teams_populated"] = True
        # During fight, players have positions near center screen
        for side in range(2):
            team = extract_team_fields(data, TEAM_PTRS[side])
            for i in range(3):
                player_off = resolve_player_offset(data, TEAM_PTRS[side], i)
                if player_off is not None:
                    pf = extract_player_fields(data, player_off)
                    if pf.get("position") and isinstance(pf["position"], tuple):
                        indicators[f"p{side+1}_player_resolved"] = True
                        break
    else:
        indicators["teams_populated"] = False

    # Try to detect game state based on indicators
    state = "unknown"
    confidence = "low"

    if not indicators.get("teams_populated"):
        # Teams not populated: likely title screen, menus, or loading
        # Check for specific UI strings
        if data.find(b"PRESS START") >= 0:
            state = "title_screen"
            confidence = "medium"
        elif data.find(b"SELECT") >= 0 or data.find(b"PLAYER") >= 0:
            state = "character_select"
            confidence = "low"
        else:
            state = "menu_or_loading"
            confidence = "low"
    else:
        # Teams populated: fight-related state
        team1 = extract_team_fields(data, TEAM_PTRS[0])
        team2 = extract_team_fields(data, TEAM_PTRS[1])
        pe1 = team1["playerExtra"][0]
        pe2 = team2["playerExtra"][0]

        if pe1["health"] <= 0 or pe2["health"] <= 0:
            state = "ko_or_win"
            confidence = "medium"
        elif cam and cam["posX"] > 0 and cam["posY"] > 0:
            # Check if player is doing anything
            for side in range(2):
                team = extract_team_fields(data, TEAM_PTRS[side])
                for i in range(3):
                    player_off = resolve_player_offset(
                        data, TEAM_PTRS[side], i)
                    if player_off is None:
                        continue
                    pf = extract_player_fields(data, player_off)
                    action = pf.get("actionID", -1)
                    if action == 0:
                        state = "fight_idle"
                        confidence = "medium"
                    elif action in (8, 11, 23):
                        state = "fight_attack"
                        confidence = "medium"
                    elif action == 5:
                        state = "fight_jumping"
                        confidence = "medium"
                    elif action in (89, 90, 92, 93, 95, 96, 98, 99, 160, 194):
                        state = "fight_special_or_super"
                        confidence = "medium"
                    else:
                        state = "fight_active"
                        confidence = "low"
                    break
                if state != "unknown":
                    break
        else:
            state = "fight_or_transition"
            confidence = "low"

    print(f"\n  Estado detectado: {state}")
    print(f"  Confianza: {confidence}")
    print(f"  Indicadores: {json.dumps(indicators, indent=4)}")

    # Detailed struct dump if in fight state
    if state.startswith("fight"):
        print("\n  --- Detalle de structs de combate ---")
        cmd_struct(args)


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Analizador de memoria KOF XI Atomiswave")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # struct
    p_struct = subparsers.add_parser("struct",
        help="Extraer structs de un snapshot")
    p_struct.add_argument("snapshot", help="Archivo .bin del snapshot")

    # diff
    p_diff = subparsers.add_parser("diff",
        help="Comparar dos snapshots")
    p_diff.add_argument("snapshot1", help="Primer snapshot")
    p_diff.add_argument("snapshot2", help="Segundo snapshot")
    p_diff.add_argument("--verbose", "-v", action="store_true")
    p_diff.add_argument("--focus", type=str, default=None,
        help="Offset hex para detalle (ej: 0x27CB50)")
    p_diff.add_argument("--focus-size", type=int, default=256)
    p_diff.add_argument("--output", "-o", type=str, default=None,
        help="Guardar reporte en archivo .md")

    # heatmap
    p_heat = subparsers.add_parser("heatmap",
        help="Analizar serie de snapshots")
    p_heat.add_argument("snapshots_dir", help="Directorio con snapshots")
    p_heat.add_argument("--output", "-o", type=str, default=None)

    # scan
    p_scan = subparsers.add_parser("scan",
        help="Buscar patrones en un snapshot")
    p_scan.add_argument("snapshot", help="Archivo .bin del snapshot")
    p_scan.add_argument("--pattern", "-p", type=str, default=None,
        help="String ASCII a buscar")
    p_scan.add_argument("--hex-pattern", type=str, default=None,
        help="Patrón hexadecimal a buscar")
    p_scan.add_argument("--u8", type=int, default=None,
        help="Buscar valor u8")
    p_scan.add_argument("--u16", type=int, default=None,
        help="Buscar valor u16 (little-endian)")
    p_scan.add_argument("--u32", type=int, default=None,
        help="Buscar valor u32 (little-endian)")

    # gamestate
    p_gs = subparsers.add_parser("gamestate",
        help="Detectar estado del juego")
    p_gs.add_argument("snapshot", help="Archivo .bin del snapshot")

    args = parser.parse_args()
    commands = {
        "struct": cmd_struct,
        "diff": cmd_diff,
        "heatmap": cmd_heatmap,
        "scan": cmd_scan,
        "gamestate": cmd_gamestate,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
