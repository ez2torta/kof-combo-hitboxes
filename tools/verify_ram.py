"""
RAM Verification Script — Zero-Trust Analysis
===============================================
Lee un snapshot .bin y muestra hex crudo de TODAS las regiones de interés
sin asumir que ningún offset es correcto. Para validación manual.

Uso:
    python tools/verify_ram.py aw_data/snapshots/NOMBRE.bin
"""
import struct
import sys
import os


def hexdump(data, offset=0, width=16, max_lines=None):
    """Produce a hex dump of data with address and ASCII columns."""
    lines = []
    for i in range(0, len(data), width):
        if max_lines and len(lines) >= max_lines:
            lines.append(f"  ... ({len(data) - i} bytes restantes)")
            break
        chunk = data[i:i+width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {offset+i:06X}: {hex_part:<{width*3}}  {ascii_part}")
    return "\n".join(lines)


def read_u8(data, off):
    return data[off] if off < len(data) else None

def read_u16(data, off):
    return struct.unpack_from("<H", data, off)[0] if off + 2 <= len(data) else None

def read_s16(data, off):
    return struct.unpack_from("<h", data, off)[0] if off + 2 <= len(data) else None

def read_u32(data, off):
    return struct.unpack_from("<I", data, off)[0] if off + 4 <= len(data) else None

def read_s32(data, off):
    return struct.unpack_from("<i", data, off)[0] if off + 4 <= len(data) else None

def read_float(data, off):
    return struct.unpack_from("<f", data, off)[0] if off + 4 <= len(data) else None

def sh4_to_ram(addr):
    if addr == 0:
        return None
    phys = addr & 0x1FFFFFFF
    off = phys - 0x0C000000
    return off if 0 <= off < 0x01000000 else None


def main():
    if len(sys.argv) < 2:
        print("Uso: python verify_ram.py <snapshot.bin>")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, "rb") as f:
        data = f.read()

    print(f"Archivo: {path}")
    print(f"Tamaño: {len(data):,} bytes ({len(data)/1024/1024:.1f} MB)")
    print(f"Esperado: 16,777,216 bytes (16.0 MB)")
    if len(data) != 0x01000000:
        print(f"  ⚠ TAMAÑO INCORRECTO — esperaba exactamente 16 MB")
    print()

    # =====================================================================
    # 1. GAME SIGNATURE VERIFICATION
    # =====================================================================
    print("=" * 70)
    print("1. VERIFICACIÓN DE FIRMAS DEL JUEGO")
    print("=" * 70)

    sigs = [
        (0x10FF50, b"MUTEKI", "MUTEKI string"),
        (0x11034C, b"Debug Menu", "Debug Menu string"),
    ]
    for off, pattern, desc in sigs:
        if off + len(pattern) <= len(data):
            actual = data[off:off+len(pattern)]
            match = actual == pattern
            ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in actual)
            print(f"  0x{off:06X}: {'OK' if match else 'FAIL'} {desc}")
            print(f"    Esperado: {pattern}")
            print(f"    Actual:   {actual} ({ascii_str})")
    print()

    # =====================================================================
    # 2. CAMERA STRUCT
    # =====================================================================
    CAM_OFF = 0x27CAA8
    print("=" * 70)
    print(f"2. CAMERA STRUCT (esperado en 0x{CAM_OFF:06X})")
    print("=" * 70)
    print(f"  Hex dump (32 bytes desde 0x{CAM_OFF:06X}):")
    print(hexdump(data[CAM_OFF:CAM_OFF+32], CAM_OFF))
    print(f"\n  Interpretaciones posibles:")
    print(f"    +0x00 u16:   {read_u16(data, CAM_OFF)}")
    print(f"    +0x02 u16:   {read_u16(data, CAM_OFF+2)}")
    print(f"    +0x00 s16:   {read_s16(data, CAM_OFF)}")
    print(f"    +0x02 s16:   {read_s16(data, CAM_OFF+2)}")
    print(f"    +0x04 float: {read_float(data, CAM_OFF+4)}")
    print(f"    +0x00 u32:   {read_u32(data, CAM_OFF)}")
    print(f"    +0x04 u32:   {read_u32(data, CAM_OFF+4)}")
    print()

    # Also check nearby area for context
    print(f"  Contexto amplio (0x27CA00 - 0x27CAF0):")
    print(hexdump(data[0x27CA00:0x27CAF0], 0x27CA00))
    print()

    # =====================================================================
    # 3. TEAM STRUCTS
    # =====================================================================
    TEAM_OFFS = [0x27CB50, 0x27CD48]
    for side, team_off in enumerate(TEAM_OFFS):
        print("=" * 70)
        print(f"3.{side+1} TEAM STRUCT P{side+1} (esperado en 0x{team_off:06X}, "
              f"tamaño 0x1F8)")
        print("=" * 70)

        # Team header
        print(f"  Header (primeros 64 bytes):")
        print(hexdump(data[team_off:team_off+64], team_off))

        # Known team fields
        print(f"\n  Campos conocidos (del Lua):")
        print(f"    +0x001 leader (u8):     {read_u8(data, team_off+0x001)}")
        print(f"    +0x003 point (u8):      {read_u8(data, team_off+0x003)}")
        print(f"    +0x007 comboCount (u8): {read_u8(data, team_off+0x007)}")
        print(f"    +0x038 super (u32):     {read_u32(data, team_off+0x038)}")
        print(f"    +0x03C skillStock (u32):{read_u32(data, team_off+0x03C)}")

        # Entries at +0x144 (3 × 4-byte SH-4 pointers)
        print(f"\n  Entries (SH-4 pointers at +0x144):")
        for i in range(3):
            ptr_off = team_off + 0x144 + i * 4
            ptr_val = read_u32(data, ptr_off)
            ram_off = sh4_to_ram(ptr_val) if ptr_val else None
            pv = ptr_val if ptr_val else 0
            if ram_off is not None:
                print(f"    entry[{i}]: 0x{pv:08X} -> RAM 0x{ram_off:06X}")
            else:
                print(f"    entry[{i}]: 0x{pv:08X} -> INVALID")

            # Follow the pointer chain
            if ram_off is not None and ram_off + 0x14 < len(data):
                # Read entry+0x10 = data pointer
                data_ptr = read_u32(data, ram_off + 0x10)
                data_ram = sh4_to_ram(data_ptr) if data_ptr else None
                if data_ram is not None:
                    player_off = data_ram - 0x614
                    print(f"      -> entry+0x10: 0x{data_ptr:08X} -> RAM 0x{data_ram:06X}")
                    print(f"      -> player_struct = data-0x614 = 0x{player_off:06X}")
                else:
                    dp = data_ptr if data_ptr else 0
                    print(f"      -> entry+0x10: 0x{dp:08X} -> INVALID")

        # PlayerExtra at +0x150 (3 × 0x20 bytes)
        print(f"\n  PlayerExtra (3 × 0x20 bytes at +0x150):")
        for i in range(3):
            pe_off = team_off + 0x150 + i * 0x20
            print(f"    playerExtra[{i}] (hex):")
            print(hexdump(data[pe_off:pe_off+0x20], pe_off))
            print(f"      charID (+001h):      {read_u8(data, pe_off+0x001)}")
            print(f"      health (+008h, s16):  {read_s16(data, pe_off+0x008)}")
            print(f"      visibleHP (+00Ah):    {read_s16(data, pe_off+0x00A)}")
            print(f"      maxHealth (+00Ch):    {read_s16(data, pe_off+0x00C)}")
            print(f"      teamPosition (+010h): {read_u8(data, pe_off+0x010)}")
        print()

    # =====================================================================
    # 4. PLAYER STRUCTS
    # =====================================================================
    # Resolve player struct addresses from team entries
    for side, team_off in enumerate(TEAM_OFFS):
        point = read_u8(data, team_off + 0x003)
        if point is None:
            continue

        # Find which entry matches current point character
        for entry_idx in range(3):
            pe_off = team_off + 0x150 + entry_idx * 0x20
            team_pos = read_u8(data, pe_off + 0x010)
            if team_pos != point:
                continue

            # Resolve player offset
            ptr_off = team_off + 0x144 + entry_idx * 4
            entry_ptr = read_u32(data, ptr_off)
            entry_ram = sh4_to_ram(entry_ptr) if entry_ptr else None
            if entry_ram is None:
                continue
            data_ptr = read_u32(data, entry_ram + 0x10)
            data_ram = sh4_to_ram(data_ptr) if data_ptr else None
            if data_ram is None:
                continue

            player_off = data_ram - 0x614
            if player_off < 0 or player_off + 0x584 > len(data):
                continue

            print("=" * 70)
            print(f"4.{side+1} PLAYER STRUCT P{side+1} (resuelto en 0x{player_off:06X})")
            print("=" * 70)

            # Header region (position, etc.)
            print(f"  Offset +000h–+01Fh (position/velocity candidatos):")
            print(hexdump(data[player_off:player_off+0x30], player_off))
            print(f"    +000h coordPair: x={read_s16(data, player_off)}, "
                  f"y={read_s16(data, player_off+2)}")
            print(f"    +000h float: {read_float(data, player_off)}")
            print(f"    +004h float: {read_float(data, player_off+4)}")
            print(f"    +018h fixed x: {read_s32(data, player_off+0x18)}")
            print(f"    +01Ch fixed y: {read_s32(data, player_off+0x1C)}")

            # Facing
            print(f"\n  Offset +08Ch (facing):")
            print(hexdump(data[player_off+0x088:player_off+0x098], player_off+0x088))
            print(f"    +08Ch u8: {read_u8(data, player_off+0x08C)} "
                  f"(Lua: 00h=left, 02h=right)")

            # Action region (+0E0h - +100h)
            print(f"\n  Offset +0E0h–+100h (actionID region):")
            print(hexdump(data[player_off+0x0E0:player_off+0x100], player_off+0x0E0))
            print(f"    +0ECh u8:  {read_u8(data, player_off+0x0EC)}")
            print(f"    +0ECh u16: {read_u16(data, player_off+0x0EC)}")
            print(f"    +0EEh u8:  {read_u8(data, player_off+0x0EE)} (prevActionID)")
            print(f"    +0EEh u16: {read_u16(data, player_off+0x0EE)}")
            print(f"    +0F2h s16: {read_s16(data, player_off+0x0F2)} (actionSignal)")

            # Animation region (+200h - +2C0h)
            print(f"\n  Offset +200h–+2C0h (animación):")
            print(hexdump(data[player_off+0x200:player_off+0x2C0], player_off+0x200))
            print(f"    +200h u16: {read_u16(data, player_off+0x200)} (animDataPtr)")
            print(f"    +204h u8:  {read_u8(data, player_off+0x204)} (actionCategory)")
            print(f"    +226h u8:  {read_u8(data, player_off+0x226)} (charBankSelector)")
            print(f"    +2A4h u8:  {read_u8(data, player_off+0x2A4)} (animFrameIndex)")
            print(f"    +2A4h u16: {read_u16(data, player_off+0x2A4)}")
            print(f"    +2A5h u8:  {read_u8(data, player_off+0x2A5)} (animPlayFlag)")
            print(f"    +2A8h s16: {read_s16(data, player_off+0x2A8)} (spriteOffsetX)")
            print(f"    +2AAh s16: {read_s16(data, player_off+0x2AA)} (spriteOffsetY)")
            print(f"    +2B2h u8:  {read_u8(data, player_off+0x2B2)} (animPropertyA)")
            print(f"    +2B3h u8:  {read_u8(data, player_off+0x2B3)} (animPropertyB)")
            print(f"    +2B4h u8:  {read_u8(data, player_off+0x2B4)} (animPhaseToggle)")

            # Hitboxes (+314h, 7 × 10 bytes)
            print(f"\n  Offset +314h–+360h (hitboxes, 7 × 10B):")
            print(hexdump(data[player_off+0x314:player_off+0x360], player_off+0x314))
            for hb_i in range(7):
                hb_off = player_off + 0x314 + hb_i * 10
                raw = data[hb_off:hb_off+10]
                px, py = struct.unpack_from("<hh", raw, 0)
                box_id = raw[4]
                w, h = raw[7], raw[8]
                print(f"    hb[{hb_i}]: pos=({px},{py}) boxID=0x{box_id:02X} "
                      f"w={w} h={h} raw={raw.hex()}")

            # hitboxesActive
            print(f"\n  Offset +39Eh (hitboxesActive):")
            print(hexdump(data[player_off+0x398:player_off+0x3A8], player_off+0x398))
            print(f"    +39Eh u8: 0x{read_u8(data, player_off+0x39E):02X}")

            # Stun timer
            print(f"\n  Offset +582h (stunTimer):")
            print(hexdump(data[player_off+0x578:player_off+0x588], player_off+0x578))
            print(f"    +582h s16: {read_s16(data, player_off+0x582)}")

            # UNKNOWN GAPS - potential frame timer candidates
            print(f"\n  === EXPLORATORY: Unknown gaps ===")
            # Gap around +18Dh-+1C1h (candidate for frame timer)
            print(f"  Gap B2 (+180h-+1C8h):")
            print(hexdump(data[player_off+0x180:player_off+0x1C8], player_off+0x180))
            # Gap around +242h-+28Ah
            print(f"  Gap D2 (+240h-+290h):")
            print(hexdump(data[player_off+0x240:player_off+0x290], player_off+0x240))

            # Flags region (+268h)
            print(f"\n  Flags region (+268h, playerFlags):")
            print(hexdump(data[player_off+0x268:player_off+0x290], player_off+0x268))

            print()

    # =====================================================================
    # 5. STRING SCAN (find known text to verify ROM identity)
    # =====================================================================
    print("=" * 70)
    print("5. STRING SCAN RÁPIDO")
    print("=" * 70)
    search_strings = [b"KOF", b"KING", b"FIGHT", b"ROUND", b"PERFECT",
                      b"MUTEKI", b"Ash", b"Terry", b"sx_System"]
    for s in search_strings:
        positions = []
        start = 0
        while True:
            pos = data.find(s, start)
            if pos == -1:
                break
            positions.append(pos)
            start = pos + 1
            if len(positions) >= 5:
                break
        if positions:
            locs = ", ".join(f"0x{p:06X}" for p in positions[:5])
            more = f" (+{len(positions)-5} más)" if len(positions) > 5 else ""
            print(f"  '{s.decode('ascii', errors='replace')}': {locs}{more}")

    # =====================================================================
    # 6. DECOMP BUFFER REGION FINGERPRINT
    # =====================================================================
    print(f"\n{'='*70}")
    print("6. HUELLAS DE REGIONES DE DESCOMPRESIÓN")
    print("=" * 70)
    regions = [
        ("decomp_0x300000", 0x300000, 0x100000),
        ("gfx_0x400000",    0x400000, 0x100000),
        ("gfx_0x500000",    0x500000, 0x100000),
    ]
    for name, start, size in regions:
        end = min(start + size, len(data))
        region = data[start:end]
        nonzero = sum(1 for b in region if b != 0)
        unique = len(set(region))
        print(f"  {name}: {nonzero:,}/{len(region):,} non-zero bytes, "
              f"{unique} unique values")
        # Show first 64 bytes
        print(hexdump(region[:64], start, max_lines=4))
        # Check for known headers
        if region[:4] == b"GBIX":
            print(f"    → Contiene textura PVR (GBIX)")
        elif region[:4] == b"PVRT":
            print(f"    → Contiene textura PVR (PVRT)")
        elif region[:3] == b"maz":
            print(f"    → Contiene contenedor MAZ")
        elif region[0] == 0x6D:
            print(f"    → Posible MAZ (empieza con 0x6D)")
    print()

    print("=" * 70)
    print("FIN DE VERIFICACIÓN")
    print("=" * 70)
    print(f"\nPara analizar, copiar la salida completa de este script.")


if __name__ == "__main__":
    main()
