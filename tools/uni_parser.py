"""
KOF XI UNI File Parser — Parse character data files and correlate with RAM.

The UNI format contains all character data: sprites, animation tables,
hitbox definitions, move commands, and more. This parser extracts the
sections relevant to runtime memory correlation.

Usage:
    python uni_parser.py info FILE.UNI                  # Show section map
    python uni_parser.py frames FILE.UNI                # List animation frames
    python uni_parser.py frames FILE.UNI --id 74        # Show frame 74 detail
    python uni_parser.py hitboxes FILE.UNI              # List hitbox definitions
    python uni_parser.py correlate FILE.UNI snapshot.bin # Correlate with RAM
"""
import argparse
import os
import sys
import struct as st
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis_utils import (
    load_snapshot, extract_camera, extract_team_fields,
    extract_player_fields, extract_hitboxes, resolve_player_offset,
    char_name, CAMERA_PTR, TEAM_PTRS, TEAM_SIZE,
    PLAYER_OFFSETS, SH4_RAM_SIZE,
)


# ===========================================================================
# UNI File Structure
# ===========================================================================

SECTION_COUNT = 22  # Maximum number of sections in header

# Section descriptions
SECTION_NAMES = {
    1:  "Frame attribute table (lookups)",
    2:  "Hitbox/collision parameters (256B)",
    3:  "Animation sequence descriptors",
    4:  "Move/command definitions",
    5:  "Sub-header + padding",
    6:  "Additional move data",
    7:  "Short config",
    8:  "Move parameters (256B)",
    9:  "Short config",
    10: "Animation frame offset table (1027 entries)",
    11: "Sprite tile metadata",
    12: "Sprite config header",
    13: "SOSB container #1",
    14: "Sprite coordinate/transform table",
    15: "SOSB container #2",
    16: "Sprite coordinate/transform #2",
    17: "Animation timing data",
    18: "Extended move/frame data",
    19: "Additional animation configs",
    20: "Small config block",
    22: "Raw sprite pixel data (4bpp)",
}

# Frame header: 32 bytes per animation frame (section 10 entries)
FRAME_HEADER_SIZE = 32
FRAME_HITBOX_SIZE = 6  # 2 hitboxes of 6 bytes each embedded in header


class UNIFile:
    """Parser for KOF XI .UNI character data files."""

    def __init__(self, filepath):
        with open(filepath, "rb") as f:
            self.data = f.read()
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self._parse_header()

    def _parse_header(self):
        """Parse the file header and section table."""
        # First u32: number of entries in section table
        self.entry_count = st.unpack_from("<I", self.data, 0)[0]
        # Second u32: offset to config block
        self.config_offset = st.unpack_from("<I", self.data, 4)[0]

        # Section table: packed u32 entries (section_id in bits 31-24, offset in bits 23-0)
        self.sections = {}
        for i in range(self.entry_count - 1):  # -1 because first entry is count
            packed = st.unpack_from("<I", self.data, 8 + i * 4)[0]
            if packed == 0xFFFFFFFF:
                break
            section_id = (packed >> 24) & 0xFF
            offset = packed & 0x00FFFFFF
            self.sections[section_id] = offset

        # Calculate section sizes (difference between consecutive offsets)
        sorted_sections = sorted(self.sections.items(), key=lambda x: x[1])
        self.section_sizes = {}
        for i, (sid, off) in enumerate(sorted_sections):
            if i + 1 < len(sorted_sections):
                next_off = sorted_sections[i + 1][1]
                self.section_sizes[sid] = next_off - off
            else:
                self.section_sizes[sid] = len(self.data) - off

    def get_section_data(self, section_id):
        """Get raw bytes for a section."""
        if section_id not in self.sections:
            return None
        off = self.sections[section_id]
        size = self.section_sizes.get(section_id, 0)
        return self.data[off:off + size]

    def info(self):
        """Print section map."""
        print(f"=== UNI File: {self.filename} ===")
        print(f"  Tamaño total: {len(self.data)} bytes ({len(self.data)/1024:.1f} KB)")
        print(f"  Entradas en header: {self.entry_count}")
        print(f"  Config offset: 0x{self.config_offset:X}")
        print(f"  Secciones encontradas: {len(self.sections)}")
        print()
        print(f"  {'ID':<4} {'Offset':<10} {'Tamaño':<10} {'Descripción'}")
        print(f"  {'─'*4} {'─'*10} {'─'*10} {'─'*40}")
        for sid in sorted(self.sections.keys()):
            off = self.sections[sid]
            size = self.section_sizes.get(sid, 0)
            desc = SECTION_NAMES.get(sid, "Desconocido")
            size_str = f"{size} B" if size < 1024 else f"{size/1024:.1f} KB"
            print(f"  {sid:<4} 0x{off:06X}  {size_str:<10} {desc}")

    def parse_input_table(self):
        """Parse the input combination table at offset 0x74 (80 bytes)."""
        table = self.data[0x74:0x74 + 80]
        return list(table)

    def parse_frame_offsets(self):
        """Parse section 10: animation frame offset table (1027 × u32)."""
        sec_data = self.get_section_data(10)
        if sec_data is None:
            return []
        frame_count = min(len(sec_data) // 4, 1027)
        offsets = []
        for i in range(frame_count):
            off = st.unpack_from("<I", sec_data, i * 4)[0]
            offsets.append(off)
        return offsets

    def parse_frame_header(self, frame_idx):
        """Parse a frame header from section 10.

        Returns dict with frame data, or None if invalid.
        The frame header is 32 bytes, found by indexing into the frame
        offset table and then reading from that offset within section 10.
        """
        sec_data = self.get_section_data(10)
        if sec_data is None:
            return None

        offsets = self.parse_frame_offsets()
        if frame_idx >= len(offsets):
            return None

        # The offset is relative to the start of section 10
        frame_off = offsets[frame_idx]
        if frame_off + FRAME_HEADER_SIZE > len(sec_data):
            return None

        raw = sec_data[frame_off:frame_off + FRAME_HEADER_SIZE]

        header = {
            "frame_idx": frame_idx,
            "offset_in_section": frame_off,
            "duration": raw[0],      # Duration in game ticks
            "flags": raw[1],         # Frame flags
            "state_type": raw[2],    # Animation state type
            "hitbox_flags": raw[7],  # Bitmask of active hitboxes
            "grid_w": raw[8],        # Sprite grid columns
            "grid_h": raw[9],        # Sprite grid rows
            "tile_ref": st.unpack_from("<H", raw, 12)[0],  # Tile descriptor ref
            "sprite_idx": raw[16],   # Sprite sequence index
        }

        # Parse embedded hitboxes (2 × 6 bytes at offset 20-31)
        hitboxes = []
        for hb_idx in range(2):
            hb_off = 20 + hb_idx * FRAME_HITBOX_SIZE
            hb_raw = raw[hb_off:hb_off + FRAME_HITBOX_SIZE]
            if len(hb_raw) < FRAME_HITBOX_SIZE:
                break
            hitboxes.append({
                "half_width": hb_raw[0],
                "box_id": hb_raw[1],
                "half_height": hb_raw[2],
                "y_offset": st.unpack_from("<b", hb_raw, 3)[0],  # signed
                "extra1": hb_raw[4],
                "extra2": hb_raw[5],
            })
        header["hitboxes"] = hitboxes

        # Calculate actual dimensions
        for hb in hitboxes:
            hb["real_width"] = hb["half_width"] * 2
            hb["real_height"] = hb["half_height"] * 2

        return header

    def get_all_frame_headers(self):
        """Parse all 1027 frame headers."""
        headers = []
        offsets = self.parse_frame_offsets()
        for i in range(len(offsets)):
            hdr = self.parse_frame_header(i)
            if hdr:
                headers.append(hdr)
        return headers

    def classify_frame(self, header):
        """Classify a frame based on its flags."""
        flags = header["flags"]
        if flags & 0x80:  # bit 7
            return "end_of_sequence"
        elif flags & 0x40:  # bit 6
            return "special"
        elif flags & 0x20:  # bit 5
            return "normal"
        return f"unknown(0x{flags:02X})"

    def classify_hitbox(self, box_id):
        """Classify hitbox type by box_id."""
        if 0x01 <= box_id <= 0x02:
            return "vulnerable"
        elif 0x03 <= box_id <= 0x04:
            return "counterVuln"
        elif 0x05 <= box_id <= 0x0D:
            return "vulnerable"
        elif 0x0F <= box_id <= 0x1B:
            return "projVuln"
        elif 0x1C <= box_id <= 0x1E:
            return "guard"
        elif 0x20 <= box_id <= 0x5F:
            return "attack"
        elif box_id == 0x60:
            return "attack"
        elif 0x61 <= box_id <= 0x66:
            return "vulnerable"
        elif 0x80 <= box_id <= 0x83:
            return "clash"
        return f"unknown(0x{box_id:02X})"


# ===========================================================================
# Commands
# ===========================================================================

def cmd_info(args):
    """Show UNI file section map."""
    uni = UNIFile(args.uni_file)
    uni.info()


def cmd_frames(args):
    """List animation frames from a UNI file."""
    uni = UNIFile(args.uni_file)
    print(f"=== Animation Frames: {uni.filename} ===")

    if args.id is not None:
        # Show single frame detail
        hdr = uni.parse_frame_header(args.id)
        if hdr is None:
            print(f"  Frame {args.id}: no encontrado o inválido")
            return
        print(f"\n  Frame {args.id}:")
        print(f"    Offset en sección: 0x{hdr['offset_in_section']:X}")
        print(f"    Duración: {hdr['duration']} ticks")
        print(f"    Flags: 0x{hdr['flags']:02X} ({uni.classify_frame(hdr)})")
        print(f"    State type: {hdr['state_type']}")
        print(f"    Hitbox flags: 0x{hdr['hitbox_flags']:02X}")
        print(f"    Grid: {hdr['grid_w']}×{hdr['grid_h']}")
        print(f"    Tile ref: 0x{hdr['tile_ref']:04X}")
        print(f"    Sprite idx: {hdr['sprite_idx']}")
        print(f"    Hitboxes:")
        for i, hb in enumerate(hdr["hitboxes"]):
            htype = uni.classify_hitbox(hb["box_id"])
            print(f"      [{i}]: half_w={hb['half_width']} "
                  f"id=0x{hb['box_id']:02X}({htype}) "
                  f"half_h={hb['half_height']} y_off={hb['y_offset']:+d} "
                  f"→ rect {hb['real_width']}×{hb['real_height']}px")
        return

    # List all frames
    headers = uni.get_all_frame_headers()
    print(f"  Total frames parseados: {len(headers)}")
    print()

    if args.summary:
        # Summary by classification
        by_class = {}
        for hdr in headers:
            cls = uni.classify_frame(hdr)
            by_class.setdefault(cls, []).append(hdr["frame_idx"])
        print("  Resumen por tipo de frame:")
        for cls, frames in sorted(by_class.items()):
            print(f"    {cls}: {len(frames)} frames")
            if len(frames) <= 10:
                print(f"      IDs: {frames}")
        print()

        # Summary by hitbox types present
        hb_types = {}
        for hdr in headers:
            for hb in hdr["hitboxes"]:
                if hb["box_id"] != 0:
                    htype = uni.classify_hitbox(hb["box_id"])
                    hb_types.setdefault(htype, 0)
                    hb_types[htype] += 1
        print("  Hitbox types en todos los frames:")
        for htype, count in sorted(hb_types.items(), key=lambda x: -x[1]):
            print(f"    {htype}: {count}")
    else:
        # Table of first N frames
        limit = args.limit or 50
        print(f"  {'Idx':<5} {'Dur':<4} {'Flags':<6} {'HBFlags':<8} "
              f"{'Grid':<6} {'SprIdx':<7} {'Tipo'}")
        print(f"  {'─'*5} {'─'*4} {'─'*6} {'─'*8} {'─'*6} {'─'*7} {'─'*12}")
        for hdr in headers[:limit]:
            cls = uni.classify_frame(hdr)
            print(f"  {hdr['frame_idx']:<5} {hdr['duration']:<4} "
                  f"0x{hdr['flags']:02X}  0x{hdr['hitbox_flags']:02X}    "
                  f"{hdr['grid_w']}×{hdr['grid_h']:<3} "
                  f"{hdr['sprite_idx']:<7} {cls}")
        if len(headers) > limit:
            print(f"\n  ... mostrando {limit} de {len(headers)} "
                  f"(usa --limit para ver más)")


def cmd_hitboxes(args):
    """List hitbox definitions from animation frames."""
    uni = UNIFile(args.uni_file)
    print(f"=== Hitbox Definitions: {uni.filename} ===")

    headers = uni.get_all_frame_headers()
    attack_frames = []
    vuln_frames = []
    for hdr in headers:
        for hb in hdr["hitboxes"]:
            if hb["box_id"] == 0:
                continue
            htype = uni.classify_hitbox(hb["box_id"])
            entry = {
                "frame": hdr["frame_idx"],
                "duration": hdr["duration"],
                "type": htype,
                "box_id": hb["box_id"],
                "half_w": hb["half_width"],
                "half_h": hb["half_height"],
                "y_off": hb["y_offset"],
                "real_w": hb["real_width"],
                "real_h": hb["real_height"],
            }
            if "attack" in htype:
                attack_frames.append(entry)
            else:
                vuln_frames.append(entry)

    print(f"\n  Attack hitboxes: {len(attack_frames)}")
    print(f"  Vulnerable hitboxes: {len(vuln_frames)}")
    print(f"  Total: {len(attack_frames) + len(vuln_frames)}")

    if args.attacks_only or not args.vuln_only:
        print(f"\n  --- Attack Hitboxes ---")
        print(f"  {'Frame':<6} {'Dur':<4} {'ID':<6} {'W×H':<10} {'Y-off':<6}")
        for e in attack_frames[:args.limit or 100]:
            print(f"  {e['frame']:<6} {e['duration']:<4} "
                  f"0x{e['box_id']:02X}  "
                  f"{e['real_w']}×{e['real_h']:<5} "
                  f"{e['y_off']:+d}")

    if args.vuln_only or not args.attacks_only:
        if not args.attacks_only:
            print(f"\n  --- Vulnerable Hitboxes ---")
            print(f"  {'Frame':<6} {'Dur':<4} {'ID':<6} {'W×H':<10} {'Y-off':<6}")
            for e in vuln_frames[:args.limit or 100]:
                print(f"  {e['frame']:<6} {e['duration']:<4} "
                      f"0x{e['box_id']:02X}  "
                      f"{e['real_w']}×{e['real_h']:<5} "
                      f"{e['y_off']:+d}")


def cmd_correlate(args):
    """Correlate UNI frame data with a RAM snapshot."""
    uni = UNIFile(args.uni_file)
    data = load_snapshot(args.snapshot)

    print(f"=== Correlación UNI ↔ RAM ===")
    print(f"  UNI: {uni.filename}")
    print(f"  Snapshot: {os.path.basename(args.snapshot)}")
    print()

    if len(data) < SH4_RAM_SIZE:
        print("ADVERTENCIA: Snapshot truncado.")

    # Extract current player state
    for side in range(2):
        team = extract_team_fields(data, TEAM_PTRS[side])
        print(f"--- P{side+1} ---")
        for i in range(3):
            pe = team["playerExtra"][i]
            if pe["teamPosition"] != team["point"]:
                continue

            player_off = resolve_player_offset(data, TEAM_PTRS[side], i)
            if player_off is None:
                print(f"  No se pudo resolver player struct")
                continue

            pf = extract_player_fields(data, player_off)
            print(f"  Personaje: {char_name(pe['charID'])} "
                  f"(0x{pe['charID']:02X})")
            print(f"  actionID: {pf.get('actionID', '?')}")
            print(f"  animFrameIndex: {pf.get('animFrameIndex', '?')}")
            print(f"  actionCategory: {pf.get('actionCategory', '?')}")
            print(f"  charBankSelector: {pf.get('charBankSelector', '?')}")
            print(f"  animDataPtr: 0x{pf.get('animDataPtr', 0):04X}")

            # Try to correlate animFrameIndex with UNI frame
            frame_idx = pf.get("animFrameIndex")
            if frame_idx is not None and frame_idx < 1027:
                hdr = uni.parse_frame_header(frame_idx)
                if hdr:
                    print(f"\n  UNI Frame {frame_idx}:")
                    print(f"    Duración: {hdr['duration']} ticks")
                    print(f"    Flags: 0x{hdr['flags']:02X} "
                          f"({uni.classify_frame(hdr)})")
                    print(f"    Hitbox flags UNI: "
                          f"0x{hdr['hitbox_flags']:02X}")
                    print(f"    Hitbox active RAM: "
                          f"0x{pf.get('hitboxesActive', 0):02X}")

                    # Compare hitboxes
                    ram_hbs = extract_hitboxes(data, player_off)
                    print(f"\n    Comparación de Hitboxes:")
                    print(f"    {'Fuente':<8} {'ID':<6} {'W':<5} {'H':<5} "
                          f"{'Y-off':<6}")
                    for j, uhb in enumerate(hdr["hitboxes"]):
                        if uhb["box_id"] == 0:
                            continue
                        utype = uni.classify_hitbox(uhb["box_id"])
                        print(f"    UNI[{j}]  0x{uhb['box_id']:02X}  "
                              f"{uhb['half_width']:<5} "
                              f"{uhb['half_height']:<5} "
                              f"{uhb['y_offset']:+d} ({utype})")
                    active = pf.get("hitboxesActive", 0)
                    for rhb in ram_hbs:
                        if (active >> rhb["slot"]) & 1:
                            print(f"    RAM[{rhb['slot']}]  "
                                  f"0x{rhb['boxID']:02X}  "
                                  f"{rhb['width']:<5} "
                                  f"{rhb['height']:<5} "
                                  f"pos=({rhb['posX']},{rhb['posY']})")
                else:
                    print(f"\n  No se pudo parsear frame {frame_idx} del UNI")
            print()


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Parser de archivos UNI de KOF XI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # info
    p_info = subparsers.add_parser("info", help="Mostrar mapa de secciones")
    p_info.add_argument("uni_file", help="Archivo .UNI")

    # frames
    p_frames = subparsers.add_parser("frames",
        help="Listar frames de animación")
    p_frames.add_argument("uni_file", help="Archivo .UNI")
    p_frames.add_argument("--id", type=int, default=None,
        help="Mostrar detalle de un frame específico")
    p_frames.add_argument("--summary", "-s", action="store_true",
        help="Mostrar resumen en vez de lista")
    p_frames.add_argument("--limit", "-l", type=int, default=None,
        help="Limitar número de resultados")

    # hitboxes
    p_hb = subparsers.add_parser("hitboxes",
        help="Listar definiciones de hitbox")
    p_hb.add_argument("uni_file", help="Archivo .UNI")
    p_hb.add_argument("--attacks-only", "-a", action="store_true")
    p_hb.add_argument("--vuln-only", "-v", action="store_true")
    p_hb.add_argument("--limit", "-l", type=int, default=None)

    # correlate
    p_corr = subparsers.add_parser("correlate",
        help="Correlacionar UNI con snapshot de RAM")
    p_corr.add_argument("uni_file", help="Archivo .UNI")
    p_corr.add_argument("snapshot", help="Archivo .bin del snapshot")

    args = parser.parse_args()
    commands = {
        "info": cmd_info,
        "frames": cmd_frames,
        "hitboxes": cmd_hitboxes,
        "correlate": cmd_correlate,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
