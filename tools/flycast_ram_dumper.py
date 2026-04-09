"""
Flycast RAM Dumper — Capture full 16 MB SH-4 RAM snapshots from a running
Flycast emulator with KOF XI loaded.

Usage:
    python flycast_ram_dumper.py                          # Single snapshot
    python flycast_ram_dumper.py --continuous --count 60   # 60 snapshots
    python flycast_ram_dumper.py --continuous --duration 5  # 5 seconds
    python flycast_ram_dumper.py --name fight_idle         # Custom name
    python flycast_ram_dumper.py --outdir path/to/dir      # Custom output dir
    python flycast_ram_dumper.py --regions                 # Dump only key regions (smaller files)
"""
import argparse
import os
import sys
import time
import json

# Add parent tools dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis_utils import (
    find_flycast_pid, open_process, close_process, find_ram_base,
    verify_kofxi, dump_full_ram, read_process_memory, save_snapshot,
    timestamp_str, SH4_RAM_SIZE, CAMERA_PTR, TEAM_PTRS, TEAM_SIZE,
    extract_camera, extract_team_fields, resolve_player_offset,
    extract_player_fields, extract_hitboxes, char_name,
)

DEFAULT_OUTDIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "aw_data", "snapshots")

# Key memory regions for smaller dumps (offset, size, label)
KEY_REGIONS = [
    (0x000000, 0x000100, "bios_vectors"),
    (0x200000, 0x080000, "working_ram"),      # 512 KB of heap/object pool
    (0x27CA00, 0x000800, "game_state"),        # Camera + teams + nearby
    (0x280000, 0x080000, "gameplay_data"),      # Post-team data
    (0x300000, 0x100000, "loaded_data"),        # LZ decompress target area
]


def attach_to_flycast():
    """Find and attach to a running Flycast process. Returns (handle, pid, ram_base)."""
    print("Buscando proceso flycast.exe...")
    pid = find_flycast_pid()
    if pid is None:
        print("ERROR: No se encontró flycast.exe en ejecución.")
        print("  Asegúrate de que Flycast está corriendo con KOF XI cargado.")
        sys.exit(1)
    print(f"  Flycast encontrado: PID {pid}")

    handle = open_process(pid)
    print("  Proceso abierto exitosamente.")

    print("Escaneando RAM del SH-4 (buscando 'MUTEKI')...")
    ram_base = find_ram_base(handle)
    if ram_base is None:
        close_process(handle)
        print("ERROR: No se pudo encontrar la RAM del SH-4.")
        print("  ¿Está KOF XI cargado en el emulador?")
        sys.exit(1)
    print(f"  RAM base encontrada: 0x{ram_base:X}")

    if verify_kofxi(handle, ram_base):
        print("  KOF XI (Atomiswave) confirmado.")
    else:
        print("  ADVERTENCIA: RAM encontrada pero verificación del juego falló.")

    return handle, pid, ram_base


def capture_single(handle, pid, ram_base, name, outdir, regions_only=False):
    """Capture a single RAM snapshot."""
    ts = timestamp_str()
    label = name or ts

    if regions_only:
        # Smaller dump: only key regions
        filename = f"kofxi_{label}.regions.bin"
        filepath = os.path.join(outdir, filename)
        region_data = {}
        total_size = 0
        for offset, size, rlabel in KEY_REGIONS:
            data = read_process_memory(handle, ram_base + offset, size)
            region_data[rlabel] = {
                "offset": offset,
                "size": size,
                "data": data,
            }
            total_size += len(data)
        # Save as concatenated binary with JSON index
        with open(filepath, "wb") as f:
            idx = {}
            file_offset = 0
            for rlabel in [r[2] for r in KEY_REGIONS]:
                rd = region_data[rlabel]
                f.write(rd["data"])
                idx[rlabel] = {
                    "ram_offset": rd["offset"],
                    "file_offset": file_offset,
                    "size": rd["size"],
                }
                file_offset += rd["size"]
        metadata = {
            "timestamp": ts,
            "pid": pid,
            "ram_base": f"0x{ram_base:X}",
            "label": label,
            "type": "regions",
            "regions": idx,
        }
    else:
        # Full 16 MB dump
        filename = f"kofxi_{label}.bin"
        filepath = os.path.join(outdir, filename)
        print(f"Capturando 16 MB de RAM completa...")
        t0 = time.perf_counter()
        data = dump_full_ram(handle, ram_base)
        elapsed = time.perf_counter() - t0
        print(f"  Captura completada en {elapsed:.2f}s ({len(data)} bytes)")
        with open(filepath, "wb") as f:
            f.write(data)
        metadata = {
            "timestamp": ts,
            "pid": pid,
            "ram_base": f"0x{ram_base:X}",
            "label": label,
            "type": "full",
            "size": len(data),
        }

    # Save metadata
    meta_path = os.path.splitext(filepath)[0] + ".json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"  Guardado: {filepath}")
    return filepath


def quick_status(handle, ram_base):
    """Print a quick status of the current game state."""
    print("\n--- Estado Actual del Juego ---")

    cam = None
    try:
        cam_data = read_process_memory(handle, ram_base + CAMERA_PTR, 8)
        import struct
        pos_x, pos_y = struct.unpack_from("<HH", cam_data, 0)
        restrictor = struct.unpack_from("<f", cam_data, 4)[0]
        cam = {"posX": pos_x, "posY": pos_y}
        print(f"  Cámara: X={pos_x} Y={pos_y} float={restrictor:.3f}")
    except Exception as e:
        print(f"  Cámara: ERROR ({e})")

    for side in range(2):
        try:
            team_data = read_process_memory(
                handle, ram_base + TEAM_PTRS[side], TEAM_SIZE)
            # Create a mini "RAM image" with team at the right offset
            # We need to read team fields from the raw bytes
            import struct as st
            point = team_data[0x003]
            leader = team_data[0x001]
            combo = team_data[0x007]
            super_val = st.unpack_from("<I", team_data, 0x038)[0]
            print(f"  P{side+1}: point={point} leader={leader} "
                  f"combo={combo} super=0x{super_val:X}")
            for i in range(3):
                pe_off = 0x150 + i * 0x20
                char_id = team_data[pe_off + 0x001]
                health = st.unpack_from("<h", team_data, pe_off + 0x008)[0]
                team_pos = team_data[pe_off + 0x010]
                is_point = " [POINT]" if team_pos == point else ""
                print(f"    slot[{i}]{is_point}: {char_name(char_id)} "
                      f"(0x{char_id:02X}) HP={health}")
        except Exception as e:
            print(f"  P{side+1}: ERROR ({e})")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Captura snapshots de RAM del SH-4 desde Flycast (KOF XI)")
    parser.add_argument("--name", "-n", type=str, default=None,
                        help="Nombre/etiqueta para el snapshot")
    parser.add_argument("--outdir", "-o", type=str, default=DEFAULT_OUTDIR,
                        help="Directorio de salida")
    parser.add_argument("--continuous", "-c", action="store_true",
                        help="Modo de captura continua")
    parser.add_argument("--count", type=int, default=0,
                        help="Número de snapshots en modo continuo (0=infinito)")
    parser.add_argument("--duration", "-d", type=float, default=0,
                        help="Duración en segundos en modo continuo")
    parser.add_argument("--interval", "-i", type=float, default=0.016,
                        help="Intervalo entre capturas en modo continuo (s)")
    parser.add_argument("--regions", "-r", action="store_true",
                        help="Capturar solo regiones clave (archivos más pequeños)")
    parser.add_argument("--status", "-s", action="store_true",
                        help="Solo mostrar estado actual del juego, sin capturar")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    handle, pid, ram_base = attach_to_flycast()

    try:
        quick_status(handle, ram_base)

        if args.status:
            return

        if args.continuous:
            print("=== Modo de captura continua ===")
            count = 0
            t_start = time.perf_counter()
            try:
                while True:
                    snap_name = f"{args.name or 'seq'}_{count:06d}"
                    capture_single(handle, pid, ram_base, snap_name,
                                   args.outdir, args.regions)
                    count += 1
                    if args.count > 0 and count >= args.count:
                        break
                    elapsed = time.perf_counter() - t_start
                    if args.duration > 0 and elapsed >= args.duration:
                        break
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nCaptura interrumpida por el usuario.")
            print(f"Total: {count} snapshots capturados.")
        else:
            capture_single(handle, pid, ram_base, args.name,
                           args.outdir, args.regions)
    finally:
        close_process(handle)
        print("Proceso cerrado.")


if __name__ == "__main__":
    main()
