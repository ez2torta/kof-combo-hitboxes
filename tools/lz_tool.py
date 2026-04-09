"""
KOF XI LZSS Tool — Descomprimir/comprimir archivos .lz del Atomiswave.

Parámetros LZSS confirmados (12/12 archivos verificados, 301/301 roundtrip):
  - Ventana deslizante: 4096 bytes
  - Posición inicial: 0xFEE
  - Flag byte: LSB-first
  - bit=1 → byte literal
  - bit=0 → back-reference (2 bytes: offset absoluto 12 bits + longitud 4 bits + 3)

Uso:
    python lz_tool.py decompress archivo.lz                    # → archivo.dec
    python lz_tool.py decompress archivo.lz -o salida.bin      # → salida.bin
    python lz_tool.py compress archivo.dec                     # → archivo.lz
    python lz_tool.py info archivo.lz                          # Mostrar info
    python lz_tool.py batch-decompress directorio/             # Todos los .lz
    python lz_tool.py identify archivo.dec                     # Identificar tipo
"""
import argparse
import os
import sys
import struct as st
import hashlib

# ===========================================================================
# LZSS Parameters (KOF XI Atomiswave)
# ===========================================================================

WINDOW_SIZE = 4096
WINDOW_INIT_POS = 0xFEE
MIN_MATCH = 3
MAX_MATCH = 18  # (0x0F & 0x0F) + 3


def decompress_lzss(data):
    """Descomprime datos LZSS del formato KOF XI Atomiswave.

    Args:
        data: bytes — archivo .lz completo (header + datos comprimidos)

    Returns:
        bytes — datos descomprimidos
    """
    if len(data) < 4:
        raise ValueError("Archivo demasiado pequeño para ser .lz")

    # Header: u32 LE = tamaño del buffer de descompresión
    buffer_size = st.unpack_from("<I", data, 0)[0]

    # Inicializar ventana deslizante
    window = bytearray(WINDOW_SIZE)
    win_pos = WINDOW_INIT_POS

    output = bytearray()
    pos = 4  # Skip header

    while pos < len(data):
        # Leer flag byte (8 operaciones, LSB-first)
        flags = data[pos]
        pos += 1

        for bit in range(8):
            if pos >= len(data):
                break
            # Stop when we've filled the buffer
            if buffer_size > 0 and len(output) >= buffer_size:
                break

            if (flags >> bit) & 1:
                # bit=1: literal byte
                byte = data[pos]
                pos += 1
                output.append(byte)
                window[win_pos] = byte
                win_pos = (win_pos + 1) & 0xFFF
            else:
                # bit=0: back-reference (2 bytes)
                if pos + 1 >= len(data):
                    break
                b0 = data[pos]
                b1 = data[pos + 1]
                pos += 2

                # offset = b0 | ((b1 & 0xF0) << 4)  — 12-bit absolute
                offset = b0 | ((b1 & 0xF0) << 4)
                # length = (b1 & 0x0F) + 3  — range 3–18
                length = (b1 & 0x0F) + MIN_MATCH

                for _ in range(length):
                    if buffer_size > 0 and len(output) >= buffer_size:
                        break
                    byte = window[offset & 0xFFF]
                    output.append(byte)
                    window[win_pos] = byte
                    win_pos = (win_pos + 1) & 0xFFF
                    offset = (offset + 1) & 0xFFF

        if buffer_size > 0 and len(output) >= buffer_size:
            break

    return bytes(output)


def compress_lzss(data):
    """Comprime datos en formato LZSS de KOF XI Atomiswave.

    Genera un archivo .lz con header (u32 buffer_size) + datos comprimidos.
    Implementa búsqueda de matches con soporte para back-references
    overlapping (cuando el match se extiende más allá del write pointer).

    Args:
        data: bytes — datos sin comprimir

    Returns:
        bytes — archivo .lz completo
    """
    # Header: buffer size = tamaño original (puede ser mayor que datos reales)
    header = st.pack("<I", len(data))

    window = bytearray(WINDOW_SIZE)
    win_pos = WINDOW_INIT_POS

    output = bytearray()
    pos = 0
    data_len = len(data)

    while pos < data_len:
        # Construimos un flag byte y hasta 8 operaciones
        flag_byte = 0
        operations = bytearray()
        ops_count = 0

        for bit in range(8):
            if pos >= data_len:
                break

            # Buscar el match más largo en la ventana
            best_offset = 0
            best_length = 0

            if pos + MIN_MATCH <= data_len:
                for search_off in range(WINDOW_SIZE):
                    if search_off == win_pos:
                        continue

                    match_len = 0
                    while (match_len < MAX_MATCH
                           and pos + match_len < data_len):
                        # Soporte overlapping: leer de la ventana
                        # considerando que ya escribimos bytes previos
                        win_idx = (search_off + match_len) & 0xFFF
                        if window[win_idx] == data[pos + match_len]:
                            match_len += 1
                        else:
                            break

                    if match_len > best_length:
                        best_length = match_len
                        best_offset = search_off

            if best_length >= MIN_MATCH:
                # Emitir back-reference
                b0 = best_offset & 0xFF
                b1 = ((best_offset >> 4) & 0xF0) | ((best_length - MIN_MATCH) & 0x0F)
                operations.append(b0)
                operations.append(b1)
                # Flag bit = 0 (ya es 0 por defecto)

                # Actualizar ventana
                for j in range(best_length):
                    window[win_pos] = data[pos + j]
                    win_pos = (win_pos + 1) & 0xFFF
                pos += best_length
            else:
                # Emitir literal
                flag_byte |= (1 << bit)
                operations.append(data[pos])
                window[win_pos] = data[pos]
                win_pos = (win_pos + 1) & 0xFFF
                pos += 1

            ops_count += 1

        output.append(flag_byte)
        output.extend(operations)

    return header + bytes(output)


# ===========================================================================
# File identification
# ===========================================================================

KNOWN_SIGNATURES = [
    (b"GBIX",  "PVR texture (GBIX header)"),
    (b"PVRT",  "PVR texture (PVRT direct)"),
    (b"PVPL",  "PVP palette"),
    (b"SOSB",  "SOSB sprite container"),
    (b"SOSP",  "SOSP sprite page"),
    (b"ENDP",  "ENDP sprite terminator"),
    (b"maz",   "MAZ multi-texture container"),
]

def identify_content(data):
    """Identify the type of decompressed content."""
    if len(data) < 4:
        return "unknown (too small)"

    for sig, desc in KNOWN_SIGNATURES:
        if data[:len(sig)] == sig:
            return desc

    # Check for zero-header pattern (common in textures/sprites)
    zero_run = 0
    for b in data[:64]:
        if b == 0:
            zero_run += 1
    if zero_run > 50:
        return f"zero-header texture/sprite (first {zero_run} bytes are 0x00)"

    # Check first byte
    first_byte = data[0]
    if first_byte == 0x6D:
        return "MAZ container (starts with 0x6D)"

    return f"unknown (starts with 0x{data[:4].hex()})"


# ===========================================================================
# Commands
# ===========================================================================

def cmd_decompress(args):
    """Descomprimir un archivo .lz."""
    with open(args.input, "rb") as f:
        data = f.read()

    result = decompress_lzss(data)
    buffer_size = st.unpack_from("<I", data, 0)[0]

    out_path = args.output
    if not out_path:
        base = os.path.splitext(args.input)[0]
        out_path = base + ".dec"

    with open(out_path, "wb") as f:
        f.write(result)

    content_type = identify_content(result)
    ratio = len(data) / len(result) if result else 0

    print(f"Descomprimido: {args.input}")
    print(f"  Comprimido:     {len(data):,} bytes")
    print(f"  Descomprimido:  {len(result):,} bytes")
    print(f"  Buffer header:  {buffer_size:,} bytes")
    print(f"  Ratio:          {1/ratio:.2f}x" if ratio > 0 else "")
    print(f"  Tipo contenido: {content_type}")
    print(f"  Salida:         {out_path}")


def cmd_compress(args):
    """Comprimir un archivo a formato .lz."""
    with open(args.input, "rb") as f:
        data = f.read()

    result = compress_lzss(data)

    out_path = args.output
    if not out_path:
        base = os.path.splitext(args.input)[0]
        out_path = base + ".lz"

    with open(out_path, "wb") as f:
        f.write(result)

    ratio = len(result) / len(data) if data else 0

    print(f"Comprimido: {args.input}")
    print(f"  Original:   {len(data):,} bytes")
    print(f"  Comprimido: {len(result):,} bytes")
    print(f"  Ratio:      {ratio:.2f}x")
    print(f"  Salida:     {out_path}")


def cmd_info(args):
    """Mostrar información de un archivo .lz."""
    with open(args.input, "rb") as f:
        data = f.read()

    if len(data) < 4:
        print("ERROR: Archivo demasiado pequeño.")
        return

    buffer_size = st.unpack_from("<I", data, 0)[0]
    # Quick decompress to get actual size
    result = decompress_lzss(data)
    content_type = identify_content(result)

    print(f"Archivo: {args.input}")
    print(f"  Tamaño comprimido:   {len(data):,} bytes")
    print(f"  Header (buffer):     {buffer_size:,} bytes (0x{buffer_size:X})")
    print(f"  Tamaño real decomp:  {len(result):,} bytes")
    print(f"  Buffer vs real:      {'EXACTO' if buffer_size == len(result) else f'buffer {buffer_size - len(result):+,} mayor'}")
    print(f"  Ratio compresión:    {len(data)/len(result):.3f}x ({len(result)/len(data):.1f}:1)")
    print(f"  Tipo contenido:      {content_type}")
    print(f"  SHA256 (comprimido): {hashlib.sha256(data).hexdigest()[:32]}...")
    print(f"  SHA256 (decomp):     {hashlib.sha256(result).hexdigest()[:32]}...")


def cmd_batch_decompress(args):
    """Descomprimir todos los .lz en un directorio."""
    input_dir = args.input_dir
    output_dir = args.output_dir or input_dir

    lz_files = sorted([f for f in os.listdir(input_dir)
                       if f.lower().endswith(".lz")])
    if not lz_files:
        print(f"No se encontraron archivos .lz en {input_dir}")
        return

    print(f"Descomprimiendo {len(lz_files)} archivos .lz de {input_dir}...")
    os.makedirs(output_dir, exist_ok=True)

    stats = {"ok": 0, "error": 0, "types": {}}

    for lz_file in lz_files:
        in_path = os.path.join(input_dir, lz_file)
        out_name = os.path.splitext(lz_file)[0] + ".dec"
        out_path = os.path.join(output_dir, out_name)

        try:
            with open(in_path, "rb") as f:
                data = f.read()
            result = decompress_lzss(data)
            with open(out_path, "wb") as f:
                f.write(result)

            content_type = identify_content(result)
            stats["types"].setdefault(content_type, 0)
            stats["types"][content_type] += 1
            stats["ok"] += 1

            ratio = len(data) / len(result) if result else 0
            print(f"  ✓ {lz_file}: {len(data):,} → {len(result):,} ({content_type})")
        except Exception as e:
            stats["error"] += 1
            print(f"  ✗ {lz_file}: {e}")

    print(f"\nResumen: {stats['ok']} OK, {stats['error']} errores")
    print(f"\nTipos de contenido:")
    for ctype, count in sorted(stats["types"].items(), key=lambda x: -x[1]):
        print(f"  {count:3d} × {ctype}")


def cmd_identify(args):
    """Identificar el tipo de un archivo descomprimido."""
    with open(args.input, "rb") as f:
        data = f.read()

    content_type = identify_content(data)
    print(f"Archivo: {args.input}")
    print(f"  Tamaño:  {len(data):,} bytes")
    print(f"  Tipo:    {content_type}")
    print(f"  Header:  {data[:16].hex()}")
    print(f"  ASCII:   {''.join(chr(b) if 32 <= b < 127 else '.' for b in data[:16])}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="KOF XI LZSS — Herramienta de compresión/descompresión")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # decompress
    p_dec = subparsers.add_parser("decompress",
        help="Descomprimir un archivo .lz")
    p_dec.add_argument("input", help="Archivo .lz a descomprimir")
    p_dec.add_argument("-o", "--output", help="Archivo de salida")

    # compress
    p_comp = subparsers.add_parser("compress",
        help="Comprimir a formato .lz")
    p_comp.add_argument("input", help="Archivo a comprimir")
    p_comp.add_argument("-o", "--output", help="Archivo de salida")

    # info
    p_info = subparsers.add_parser("info",
        help="Mostrar información de un archivo .lz")
    p_info.add_argument("input", help="Archivo .lz")

    # batch-decompress
    p_batch = subparsers.add_parser("batch-decompress",
        help="Descomprimir todos los .lz en un directorio")
    p_batch.add_argument("input_dir", help="Directorio con archivos .lz")
    p_batch.add_argument("-o", "--output-dir",
        help="Directorio de salida (default: mismo)")

    # identify
    p_id = subparsers.add_parser("identify",
        help="Identificar tipo de archivo descomprimido")
    p_id.add_argument("input", help="Archivo descomprimido")

    args = parser.parse_args()
    commands = {
        "decompress": cmd_decompress,
        "compress": cmd_compress,
        "info": cmd_info,
        "batch-decompress": cmd_batch_decompress,
        "identify": cmd_identify,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
