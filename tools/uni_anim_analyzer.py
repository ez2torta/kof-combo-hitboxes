"""
UNI Animation Sequence Analyzer
================================
Analiza en profundidad las secciones 3 (secuencias de animación) y 4
(definiciones de movimiento) para entender cómo se componen las
animaciones completas a partir de frames individuales.

Genera reporte en markdown.
"""
import struct
import sys
import os
from collections import defaultdict

def parse_sections(data):
    """Parse UNI section table."""
    entry_count = struct.unpack_from('<I', data, 0)[0]
    sections = {}
    for i in range(entry_count - 1):
        packed = struct.unpack_from('<I', data, 8 + i * 4)[0]
        if packed == 0xFFFFFFFF:
            break
        sid = (packed >> 24) & 0xFF
        off = packed & 0x00FFFFFF
        sections[sid] = off
    return sections


def get_section_range(sections, sid):
    """Get (start, end) of a section."""
    if sid not in sections:
        return None, None
    start = sections[sid]
    # Find next section offset
    all_offsets = sorted(sections.values())
    idx = all_offsets.index(start)
    if idx + 1 < len(all_offsets):
        end = all_offsets[idx + 1]
    else:
        end = len(data)
    return start, end


def parse_sub_table(data, section_off, section_size):
    """Parse a section that starts with u16 count + u16[] sub-offsets."""
    count = struct.unpack_from('<H', data, section_off)[0]
    offsets = []
    for i in range(count):
        off = struct.unpack_from('<H', data, section_off + 2 + i * 2)[0]
        offsets.append(off)
    return count, offsets


def parse_frame_header(data, offset):
    """Parse a 32-byte frame header from section 10 data area."""
    if offset + 32 > len(data):
        return None
    raw = data[offset:offset+32]
    return {
        'duration': raw[0],
        'flags': raw[1],
        'state_type': raw[2],
        'unknown_03': raw[3],
        'unknown_04': raw[4],
        'unknown_05': raw[5],
        'unknown_06': raw[6],
        'hitbox_flags': raw[7],
        'grid_w': raw[8],
        'grid_h': raw[9],
        'unknown_10': raw[10],
        'unknown_11': raw[11],
        'tile_ref': struct.unpack_from('<H', raw, 12)[0],
        'unknown_14': raw[14],
        'unknown_15': raw[15],
        'sprite_idx': raw[16],
        'unknown_17': raw[17],
        'unknown_18': raw[18],
        'unknown_19': raw[19],
        'hb1': {
            'half_w': raw[20], 'box_id': raw[21], 'half_h': raw[22],
            'y_off': struct.unpack_from('b', raw, 23)[0],
            'extra1': raw[24], 'extra2': raw[25],
        },
        'hb2': {
            'half_w': raw[26], 'box_id': raw[27], 'half_h': raw[28],
            'y_off': struct.unpack_from('b', raw, 29)[0],
            'extra1': raw[30], 'extra2': raw[31],
        },
        'raw': raw.hex(),
    }


def analyze_section3_bytecode(data, s3_off, s3_size, s3_offsets):
    """Decode section 3 animation sequence bytecode."""
    results = []
    for i, rel_off in enumerate(s3_offsets):
        abs_off = s3_off + rel_off
        # Determine entry size
        if i + 1 < len(s3_offsets):
            next_off = s3_offsets[i + 1]
        else:
            next_off = s3_size
        entry_size = next_off - rel_off
        if entry_size <= 0 or entry_size > 0x2000:
            results.append({'index': i, 'offset': rel_off, 'size': 0,
                            'valid': False, 'instructions': []})
            continue

        raw = data[abs_off:abs_off + entry_size]
        instructions = decode_bytecode(raw, entry_size)
        results.append({
            'index': i,
            'offset': rel_off,
            'abs_offset': abs_off,
            'size': entry_size,
            'valid': True,
            'instructions': instructions,
            'raw_hex': raw.hex(),
        })
    return results


def decode_bytecode(raw, size):
    """Attempt to decode bytecode from section 3 or 4 entries.
    
    Strategy: scan for patterns. Known observation:
    - Values like 0x07, 0x08, 0x0A, 0x0C appear as potential opcodes
    - Some values reference frame indices or section IDs
    - Look for u16 LE values that could be frame reference numbers
    """
    instructions = []
    pos = 0
    while pos < size:
        opcode = raw[pos]
        
        # Heuristic: try to identify instruction boundaries
        # A common pattern in animation bytecode is: opcode + operands
        # We'll decode each byte and look for u16 values that
        # reference frame indices (0..1026 range for section 10)
        
        # Check if we can read a u16 at pos
        if pos + 1 < size:
            word = struct.unpack_from('<H', raw, pos)[0]
        else:
            word = None
        
        instructions.append({
            'pos': pos,
            'byte': opcode,
            'word': word,
        })
        pos += 1
    
    return instructions


def find_frame_references(raw, max_frame=1027):
    """Find potential frame index references in bytecode data.
    
    Look for u16 LE values in the 0-1026 range throughout the data.
    """
    refs = []
    for pos in range(len(raw) - 1):
        val = struct.unpack_from('<H', raw, pos)[0]
        if 0 < val < max_frame:
            refs.append((pos, val))
    return refs


def analyze_section4_moves(data, s4_off, s4_size, s4_offsets):
    """Analyze section 4 move definitions looking for animation refs."""
    results = []
    for i, rel_off in enumerate(s4_offsets):
        abs_off = s4_off + rel_off
        if i + 1 < len(s4_offsets):
            next_off = s4_offsets[i + 1]
        else:
            next_off = s4_size
        entry_size = next_off - rel_off
        
        if entry_size <= 0 or abs_off + entry_size > len(data):
            results.append({'index': i, 'valid': False})
            continue
        
        raw = data[abs_off:abs_off + entry_size]
        
        # Look for 0xA0-prefixed patterns (identified in prior analysis)
        a0_patterns = []
        for pos in range(len(raw) - 1):
            if raw[pos + 1] == 0xA0:  # Second byte is 0xA0 in u16 LE
                cmd = struct.unpack_from('<H', raw, pos)[0]
                a0_patterns.append((pos, cmd))
        
        # Look for frame references
        frame_refs = find_frame_references(raw)
        
        results.append({
            'index': i,
            'offset': rel_off,
            'abs_offset': abs_off,
            'size': entry_size,
            'valid': True,
            'a0_cmds': a0_patterns,
            'frame_refs': frame_refs,
            'raw_hex': raw[:64].hex(),
        })
    return results


def main():
    uni_path = sys.argv[1] if len(sys.argv) > 1 else \
        'aw_data/rom_samples/personajes/0004_0000.UNI'
    
    with open(uni_path, 'rb') as f:
        data = f.read()
    
    sections = parse_sections(data)
    print(f"Archivo: {uni_path} ({len(data):,} bytes)")
    print(f"Secciones: {sorted(sections.keys())}")
    
    # ===== Section 10: Parse all frame headers =====
    s10_off, s10_end = get_section_range(sections, 10)
    s10_count = struct.unpack_from('<I', data, s10_off)[0]
    frame_offsets = []
    for i in range(s10_count):
        off = struct.unpack_from('<I', data, s10_off + 4 + i * 4)[0]
        frame_offsets.append(off)
    
    frames = {}
    valid_frames = 0
    for i, foff in enumerate(frame_offsets):
        abs_off = s10_off + foff
        fh = parse_frame_header(data, abs_off)
        if fh and fh['duration'] > 0 and fh['flags'] != 0x32:
            frames[i] = fh
            valid_frames += 1
    
    print(f"\nSection 10: {s10_count} frame offsets, {valid_frames} valid frames")
    
    # ===== Section 3: Animation Sequences =====
    s3_off, s3_end = get_section_range(sections, 3)
    s3_size = s3_end - s3_off
    s3_count, s3_offsets = parse_sub_table(data, s3_off, s3_size)
    
    print(f"\nSection 3: {s3_count} animation sequences, {s3_size} bytes")
    
    # Decode each sequence
    seq_results = analyze_section3_bytecode(data, s3_off, s3_size, s3_offsets)
    
    # For section 3, try a different approach: scan for u16 values
    # that reference section 10 frame indices
    print("\n--- Section 3 Detailed Analysis ---")
    for seq in seq_results:
        if not seq['valid']:
            print(f"  [{seq['index']:2d}] INVALID")
            continue
        
        raw = data[seq['abs_offset']:seq['abs_offset'] + seq['size']]
        frame_refs = find_frame_references(raw, s10_count)
        
        # Show the raw data in a readable format
        hex_line = " ".join(f"{b:02X}" for b in raw[:min(48, len(raw))])
        more = "..." if len(raw) > 48 else ""
        print(f"  [{seq['index']:2d}] size={seq['size']:3d}B "
              f"refs={len(frame_refs)} | {hex_line}{more}")
        
        # Try to decode as structured data
        # Pattern hypothesis: pairs of (u16 frame_idx, u16 duration/flags)?
        if seq['size'] >= 4:
            u16_vals = []
            for p in range(0, seq['size'] - 1, 2):
                v = struct.unpack_from('<H', raw, p)[0]
                u16_vals.append(v)
            
            # Filter to show only u16 values in frame index range
            in_range = [(j, v) for j, v in enumerate(u16_vals)
                        if 0 < v < s10_count]
            if in_range:
                frame_indices = [v for _, v in in_range]
                print(f"         u16 in frame range: {frame_indices}")
        
        seq['frame_refs_found'] = frame_refs
    
    # ===== Section 4: Move Definitions =====
    s4_off, s4_end = get_section_range(sections, 4)
    s4_size = s4_end - s4_off
    s4_count, s4_offsets = parse_sub_table(data, s4_off, s4_size)
    
    print(f"\nSection 4: {s4_count} move definitions, {s4_size} bytes")
    
    move_results = analyze_section4_moves(data, s4_off, s4_size, s4_offsets)
    
    print("\n--- Section 4 Detailed Analysis (first 40 moves) ---")
    for mv in move_results[:40]:
        if not mv['valid']:
            print(f"  [{mv['index']:3d}] INVALID")
            continue
        
        a0_str = ", ".join(f"0x{c:04X}" for _, c in mv['a0_cmds'][:5])
        fr_vals = [v for _, v in mv['frame_refs'] if v < s10_count]
        fr_str = str(fr_vals[:8]) if fr_vals else "none"
        
        print(f"  [{mv['index']:3d}] size={mv['size']:4d}B "
              f"a0_cmds=[{a0_str}] "
              f"frame_refs={fr_str}")
    
    # ===== Section 17: Timing data =====
    s17_off, s17_end = get_section_range(sections, 17)
    s17_size = s17_end - s17_off
    s17_count, s17_offsets = parse_sub_table(data, s17_off, s17_size)
    
    print(f"\nSection 17: {s17_count} timing entries, {s17_size} bytes")
    for i, rel_off in enumerate(s17_offsets[:20]):
        abs_off = s17_off + rel_off
        if i + 1 < len(s17_offsets):
            entry_size = s17_offsets[i + 1] - rel_off
        else:
            entry_size = s17_size - rel_off
        raw = data[abs_off:abs_off + min(32, entry_size)]
        hex_str = " ".join(f"{b:02X}" for b in raw)
        
        # Try parsing as u16 pairs
        u16_vals = []
        for p in range(0, min(entry_size, 32) - 1, 2):
            v = struct.unpack_from('<H', data, abs_off + p)[0]
            u16_vals.append(v)
        
        print(f"  [{i:2d}] +0x{rel_off:04X} size={entry_size:3d}B "
              f"u16={u16_vals[:8]} | {hex_str}")
    
    # ===== Cross-reference: try to link section 4 entries to frames =====
    print("\n--- Cross-Reference: Section 4 → Section 10 Frame Sequences ---")
    
    # Common action IDs from the game: 0=idle, 1=walk fwd, etc.
    # Let's see if section 4 entry[action_id] references a specific frame range
    for mv in move_results[:20]:
        if not mv['valid']:
            continue
        
        raw = data[mv['abs_offset']:mv['abs_offset'] + mv['size']]
        
        # New approach: check if the entry contains direct frame indices
        # by looking at every u16 and checking if it's a valid section 10 index
        frame_indices = set()
        for pos in range(0, len(raw) - 1, 2):
            v = struct.unpack_from('<H', raw, pos)[0]
            if 0 < v < s10_count:
                frame_indices.add(v)
        
        if frame_indices:
            sorted_frames = sorted(frame_indices)
            # Check if these frames form a contiguous sequence
            is_contiguous = all(
                sorted_frames[j+1] - sorted_frames[j] == 1
                for j in range(len(sorted_frames) - 1)
            ) if len(sorted_frames) > 1 else True
            
            # Get frame details for the referenced frames
            frame_info = []
            for fi in sorted_frames[:10]:
                if fi in frames:
                    fh = frames[fi]
                    frame_info.append(
                        f"f{fi}(dur={fh['duration']},hb={fh['hitbox_flags']:02X})")
            
            contig_str = "contiguous" if is_contiguous else "scattered"
            print(f"  action[{mv['index']:3d}] → {len(frame_indices)} frames "
                  f"({contig_str}): {sorted_frames[:15]}")
            if frame_info:
                print(f"              {', '.join(frame_info[:6])}")
    
    # ===== Generate markdown report =====
    report_path = os.path.splitext(uni_path)[0] + '_animation_analysis.md'
    if uni_path.startswith('aw_data'):
        report_path = os.path.join('aw_data', 'animation_sequence_analysis.md')
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("# Análisis de Secuencias de Animación — UNI\n\n")
        f.write(f"Archivo: `{uni_path}`\n\n")
        f.write(f"## Resumen\n\n")
        f.write(f"- Section 10: {s10_count} frame entries ({valid_frames} valid)\n")
        f.write(f"- Section 3: {s3_count} animation sequences\n")
        f.write(f"- Section 4: {s4_count} move/action definitions\n")
        f.write(f"- Section 17: {s17_count} timing entries\n\n")
        
        f.write("## Section 3 — Secuencias de Animación\n\n")
        f.write("Cada entrada parece ser una secuencia de bytecode que controla "
                "la progresión de frames de una animación.\n\n")
        f.write("| # | Offset | Size | Potential Frame Refs | Raw (first 32B) |\n")
        f.write("|---|--------|------|---------------------|------------------|\n")
        for seq in seq_results:
            if not seq['valid']:
                f.write(f"| {seq['index']} | - | 0 | - | - |\n")
                continue
            raw = data[seq['abs_offset']:seq['abs_offset'] + min(32, seq['size'])]
            hex_str = " ".join(f"{b:02X}" for b in raw)
            refs = seq.get('frame_refs_found', [])
            ref_vals = sorted(set(v for _, v in refs if v < s10_count))[:8]
            f.write(f"| {seq['index']} | 0x{seq['offset']:04X} | {seq['size']} "
                    f"| {ref_vals} | `{hex_str}` |\n")
        
        f.write("\n## Section 4 — Definiciones de Movimientos\n\n")
        f.write("Cada entrada define un movimiento/acción. Contiene opcodes "
                "0xA0-prefixed y posibles referencias a frames.\n\n")
        f.write("| Action | Size | 0xA0 Commands | Frame Refs |\n")
        f.write("|--------|------|---------------|------------|\n")
        for mv in move_results:
            if not mv['valid']:
                f.write(f"| {mv['index']} | - | - | - |\n")
                continue
            a0_str = ", ".join(f"0x{c:04X}" for _, c in mv['a0_cmds'][:5])
            fr_vals = sorted(set(v for _, v in mv['frame_refs'] if v < s10_count))[:10]
            f.write(f"| {mv['index']} | {mv['size']} | {a0_str} | {fr_vals} |\n")
        
        f.write(f"\n## Section 10 — Frame Summary\n\n")
        f.write("| Frame | Duration | Flags | HitboxFlags | GridWxH | HB1 | HB2 |\n")
        f.write("|-------|----------|-------|-------------|---------|-----|-----|\n")
        for fi in sorted(frames.keys())[:50]:
            fh = frames[fi]
            hb1 = fh['hb1']
            hb2 = fh['hb2']
            f.write(f"| {fi} | {fh['duration']} | 0x{fh['flags']:02X} "
                    f"| 0x{fh['hitbox_flags']:02X} "
                    f"| {fh['grid_w']}x{fh['grid_h']} "
                    f"| {hb1['half_w']}x{hb1['half_h']}@{hb1['box_id']:02X} "
                    f"| {hb2['half_w']}x{hb2['half_h']}@{hb2['box_id']:02X} |\n")
        if len(frames) > 50:
            f.write(f"| ... | ({len(frames)-50} more) | | | | | |\n")
    
    print(f"\nReporte: {report_path}")


if __name__ == '__main__':
    main()
