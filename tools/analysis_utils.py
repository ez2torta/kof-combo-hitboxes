"""
Shared utilities for KOF XI Atomiswave memory analysis tools.
Provides Windows process memory access, SH-4 address translation,
and struct parsing helpers.
"""
import ctypes
import ctypes.wintypes as wt
import struct
import os
import json
from datetime import datetime

# ===========================================================================
# Windows API bindings
# ===========================================================================

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_size_t),
        ("AllocationBase", ctypes.c_size_t),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]

MEM_COMMIT = 0x1000
MEM_MAPPED = 0x40000
SH4_RAM_SIZE = 0x01000000  # 16 MB


def open_process(pid):
    """Open a process handle with VM_READ | QUERY_INFORMATION."""
    access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        raise OSError(f"OpenProcess failed for PID {pid}: "
                      f"error {ctypes.get_last_error()}")
    return handle


def close_process(handle):
    kernel32.CloseHandle(handle)


def read_process_memory(handle, address, size):
    """Read `size` bytes from `address` in the target process."""
    buf = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        handle, ctypes.c_size_t(address), buf, size,
        ctypes.byref(bytes_read))
    if not ok:
        raise OSError(f"ReadProcessMemory failed at 0x{address:X}: "
                      f"error {ctypes.get_last_error()}")
    return buf.raw[:bytes_read.value]


def enum_memory_regions(handle, filter_fn=None):
    """Enumerate memory regions of a process.
    filter_fn(mbi) -> bool to include a region.
    """
    regions = []
    address = 0
    mbi = MEMORY_BASIC_INFORMATION()
    mbi_size = ctypes.sizeof(mbi)
    while True:
        result = kernel32.VirtualQueryEx(
            handle, ctypes.c_size_t(address),
            ctypes.byref(mbi), mbi_size)
        if result == 0:
            break
        if filter_fn is None or filter_fn(mbi):
            regions.append({
                "base": mbi.BaseAddress,
                "size": mbi.RegionSize,
                "state": mbi.State,
                "type": mbi.Type,
                "protect": mbi.Protect,
            })
        next_addr = mbi.BaseAddress + mbi.RegionSize
        if next_addr <= address:
            break
        address = next_addr
    return regions


# ===========================================================================
# Flycast / SH-4 RAM helpers
# ===========================================================================

# Known byte patterns in the KOF XI binary for RAM discovery.
GAME_SIGNATURES = [
    {"pattern": b"MUTEKI",     "offset": 0x10FF50},
    {"pattern": b"Debug Menu", "offset": 0x11034C},
]

VERIFICATION_STRINGS = [
    {"pattern": b"sx_System", "offset": 0x103A0D + 0xFF00},
    {"pattern": b"ADELHIDE",  "offset": 0x100024 + 0xFF00},
]


def find_flycast_pid():
    """Find the PID of a running flycast.exe process."""
    import subprocess
    result = subprocess.run(
        ["tasklist", "/fi", "imagename eq flycast.exe", "/fo", "csv", "/nh"],
        capture_output=True, text=True)
    for line in result.stdout.strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and parts[0].lower() == "flycast.exe":
            return int(parts[1])
    return None


def find_ram_base(handle):
    """Scan Flycast process memory to find the SH-4 16 MB main RAM base.
    Returns the base address or None.
    """
    def is_16mb_mapped(mbi):
        return (mbi.RegionSize == SH4_RAM_SIZE
                and mbi.Type == MEM_MAPPED
                and mbi.State == MEM_COMMIT)

    regions = enum_memory_regions(handle, is_16mb_mapped)
    for sig in GAME_SIGNATURES:
        for region in regions:
            try:
                data = read_process_memory(
                    handle, region["base"] + sig["offset"],
                    len(sig["pattern"]))
                if data == sig["pattern"]:
                    return region["base"]
            except OSError:
                continue
    return None


def verify_kofxi(handle, ram_base):
    """Verify the found RAM contains KOF XI by checking secondary strings."""
    for sig in VERIFICATION_STRINGS:
        addr = ram_base + sig["offset"]
        try:
            data = read_process_memory(handle, addr, len(sig["pattern"]))
            if data == sig["pattern"]:
                return True
        except OSError:
            continue
    return False


def dump_full_ram(handle, ram_base):
    """Read the entire 16 MB SH-4 RAM as a bytes object."""
    # Read in 64 KB chunks for reliability
    chunks = []
    chunk_size = 0x10000  # 64 KB
    for offset in range(0, SH4_RAM_SIZE, chunk_size):
        data = read_process_memory(handle, ram_base + offset, chunk_size)
        chunks.append(data)
    return b"".join(chunks)


def sh4_to_ram_offset(sh4_addr):
    """Convert SH-4 cached pointer (0x8Cxxxxxx) to RAM offset."""
    if sh4_addr == 0:
        return None
    phys = sh4_addr & 0x1FFFFFFF
    off = phys - 0x0C000000
    if 0 <= off < SH4_RAM_SIZE:
        return off
    return None


# ===========================================================================
# KOF XI Memory addresses (RAM offsets relative to 0x0C000000)
# ===========================================================================

CAMERA_PTR = 0x27CAA8
TEAM_PTRS = [0x27CB50, 0x27CD48]
TEAM_SIZE = 0x1F8
PLAYER_STRUCT_SIZE = 0x584

# Player struct key offsets
PLAYER_OFFSETS = {
    "position":       (0x000, 4, "2H"),    # X,Y as u16
    "velocity":       (0x018, 8, "2i"),    # X,Y as s32 (16.16 fixed)
    "facing":         (0x08C, 1, "B"),
    "actionID":       (0x0EC, 1, "B"),
    "prevActionID":   (0x0EE, 1, "B"),
    "actionSignal":   (0x0F2, 2, "h"),
    "animDataPtr":    (0x200, 2, "H"),
    "actionCategory": (0x204, 1, "B"),
    "charBankSelector": (0x226, 1, "B"),
    "animFrameIndex": (0x2A4, 1, "B"),
    "animPlayFlag":   (0x2A5, 1, "B"),
    "spriteOffsetX":  (0x2A8, 2, "h"),
    "spriteOffsetY":  (0x2AA, 2, "h"),
    "animPropertyA":  (0x2B2, 1, "B"),
    "animPropertyB":  (0x2B3, 1, "B"),
    "animPhaseToggle":(0x2B4, 1, "B"),
    "hitboxesActive": (0x39E, 1, "B"),
    "stunTimer":      (0x582, 2, "h"),
}

# Hitbox struct: 10 bytes each, 7 slots starting at +314h
HITBOX_OFFSET = 0x314
HITBOX_SIZE = 10
HITBOX_COUNT = 7

# Team struct key offsets
TEAM_OFFSETS = {
    "leader":        (0x001, 1, "B"),
    "point":         (0x003, 1, "B"),
    "comboCounter":  (0x007, 1, "B"),
    "super":         (0x038, 4, "I"),
    "skillStock":    (0x03C, 4, "I"),
}

# PlayerExtra sub-struct (3 per team at +0x150, each 0x20 bytes)
PLAYER_EXTRA_OFFSET = 0x150
PLAYER_EXTRA_SIZE = 0x20
PLAYER_EXTRA_FIELDS = {
    "charID":       (0x001, 1, "B"),
    "health":       (0x008, 2, "h"),
    "visibleHealth":(0x00A, 2, "h"),
    "maxHealth":    (0x00C, 2, "h"),
    "teamPosition": (0x010, 1, "B"),
}

# Entry pointers at team+0x144 (3 × 4-byte SH-4 pointers)
TEAM_ENTRIES_OFFSET = 0x144
ENTRY_DATA_PTR_OFFSET = 0x10      # entry+0x10 = SH-4 ptr to data block
PLAYER_DATA_BACKOFF = 0x614       # player_struct = data_ptr - 0x614


# ===========================================================================
# Struct extraction from RAM dump
# ===========================================================================

def extract_field(data, base_offset, field_offset, size, fmt):
    """Extract a field from a RAM dump."""
    start = base_offset + field_offset
    raw = data[start:start + size]
    if len(raw) < size:
        return None
    return struct.unpack("<" + fmt, raw)


def extract_player_fields(data, player_offset):
    """Extract all known fields from a player struct in a RAM dump."""
    result = {}
    for name, (off, size, fmt) in PLAYER_OFFSETS.items():
        val = extract_field(data, player_offset, off, size, fmt)
        if val is not None:
            result[name] = val[0] if len(val) == 1 else val
    return result


def extract_hitboxes(data, player_offset):
    """Extract hitbox data from a player struct."""
    hitboxes = []
    for i in range(HITBOX_COUNT):
        off = player_offset + HITBOX_OFFSET + i * HITBOX_SIZE
        raw = data[off:off + HITBOX_SIZE]
        if len(raw) < HITBOX_SIZE:
            break
        pos_x, pos_y = struct.unpack_from("<hh", raw, 0)
        box_id = raw[4]
        width = raw[7]
        height = raw[8]
        hitboxes.append({
            "slot": i,
            "posX": pos_x, "posY": pos_y,
            "boxID": box_id,
            "width": width, "height": height,
        })
    return hitboxes


def extract_team_fields(data, team_offset):
    """Extract all known fields from a team struct."""
    result = {}
    for name, (off, size, fmt) in TEAM_OFFSETS.items():
        val = extract_field(data, team_offset, off, size, fmt)
        if val is not None:
            result[name] = val[0] if len(val) == 1 else val

    # Extract playerExtra for all 3 slots
    extras = []
    for i in range(3):
        pe_off = team_offset + PLAYER_EXTRA_OFFSET + i * PLAYER_EXTRA_SIZE
        pe = {}
        for name, (foff, fsize, fmt) in PLAYER_EXTRA_FIELDS.items():
            val = extract_field(data, pe_off, foff, fsize, fmt)
            if val is not None:
                pe[name] = val[0] if len(val) == 1 else val
        extras.append(pe)
    result["playerExtra"] = extras
    return result


def extract_camera(data):
    """Extract camera struct from RAM dump."""
    raw = data[CAMERA_PTR:CAMERA_PTR + 8]
    if len(raw) < 8:
        return None
    pos_x, pos_y = struct.unpack_from("<HH", raw, 0)
    restrictor = struct.unpack_from("<f", raw, 4)[0]
    return {"posX": pos_x, "posY": pos_y, "restrictor": restrictor}


def resolve_player_offset(data, team_offset, slot_index):
    """Resolve the player struct offset from a team entry pointer.
    Returns the RAM offset of the player struct, or None.
    """
    entry_ptr_off = team_offset + TEAM_ENTRIES_OFFSET + slot_index * 4
    raw = data[entry_ptr_off:entry_ptr_off + 4]
    if len(raw) < 4:
        return None
    entry_sh4 = struct.unpack("<I", raw)[0]
    entry_off = sh4_to_ram_offset(entry_sh4)
    if entry_off is None:
        return None

    # Read data pointer at entry+0x10
    dp_off = entry_off + ENTRY_DATA_PTR_OFFSET
    raw2 = data[dp_off:dp_off + 4]
    if len(raw2) < 4:
        return None
    data_sh4 = struct.unpack("<I", raw2)[0]
    data_off = sh4_to_ram_offset(data_sh4)
    if data_off is None:
        return None

    player_off = data_off - PLAYER_DATA_BACKOFF
    if 0 <= player_off < SH4_RAM_SIZE:
        return player_off
    return None


# ===========================================================================
# Character roster
# ===========================================================================

ROSTER = {
    0x00: "Ash", 0x01: "Oswald", 0x02: "Shen Woo", 0x03: "Elisabeth",
    0x04: "Duo Lon", 0x05: "Benimaru", 0x06: "Terry", 0x07: "Kim",
    0x08: "Duck King", 0x09: "Ryo", 0x0A: "Yuri", 0x0B: "King",
    0x0C: "B. Jenet", 0x0D: "Gato", 0x0E: "Tizoc/Griffon",
    0x0F: "Ralf", 0x10: "Clark", 0x11: "Whip", 0x12: "Athena",
    0x13: "Kensou", 0x14: "Momoko", 0x15: "Vanessa", 0x16: "Mary",
    0x17: "Ramon", 0x18: "Malin", 0x19: "Kasumi", 0x1A: "Eiji",
    0x1B: "K'", 0x1C: "Kula", 0x1D: "Maxima", 0x1E: "Kyo",
    0x1F: "Iori", 0x20: "Shingo", 0x21: "Gai", 0x22: "Hayate",
    0x23: "Adelheid", 0x24: "Silver/Silber", 0x25: "Jazu/Jyazu",
    0x26: "Shion", 0x27: "Magaki", 0x29: "Mai", 0x2A: "Robert",
    0x2B: "Mr. Big", 0x2C: "Geese", 0x2D: "Hotaru",
    0x2E: "Tung Fu Rue", 0x2F: "Kyo (EX)",
}


def char_name(char_id):
    return ROSTER.get(char_id, f"Unknown(0x{char_id:02X})")


# ===========================================================================
# I/O helpers
# ===========================================================================

def save_snapshot(data, filepath, metadata=None):
    """Save a RAM dump to a .bin file with optional .json metadata."""
    with open(filepath, "wb") as f:
        f.write(data)
    if metadata:
        json_path = os.path.splitext(filepath)[0] + ".json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)


def load_snapshot(filepath):
    """Load a RAM dump from a .bin file."""
    with open(filepath, "rb") as f:
        return f.read()


def load_metadata(filepath):
    """Load metadata JSON for a snapshot."""
    json_path = os.path.splitext(filepath)[0] + ".json"
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def timestamp_str():
    return datetime.now().strftime("%Y%m%d_%H%M%S")
