existe la posibilidad que empieces a trabajar en compatibilidad de este software que ve hitboxes y logging de memoria para el emulador flycast?
tengo un documento de la versión de atomiswave donde podrías empezar a buscar strings (por ejemplo como parte el ejecutable SYSTEM_X_APP)
de esta manera podrías llegar a ver donde está el juego en RAM para ver sus alrededores y poder sacar mejores conclusiones.
te pasaré adicionalmente información sobre el ejecutable y otras cosas, para ver si puedes integrar algunas cosas en flycast. 

cualquier duda o consulta por favor hazla saber.
adicionalmente deja documentado cosas que vayas viendo de flycast en un archivo markdown por favor. gracias!


# SH-4 Boot Analysis: Atomiswave / KOFXI

This document describes how the Flycast emulator loads and boots Atomiswave games—specifically **The King of Fighters XI (KOFXI)**—from ROM data into SH-4 main memory.

---

## 1. Platform Detection

When a ROM is loaded, Flycast determines the platform from the game database:

**`core/hw/naomi/naomi_cart.cpp` → `naomi_cart_GetPlatform()`**

```cpp
if (game->cart_type == AW)
    return DC_PLATFORM_ATOMISWAVE;
```

KOFXI's database entry (`core/hw/naomi/naomi_roms.cpp`):

| Field        | Value        |
|--------------|--------------|
| `name`       | `"kofxi"`    |
| `cart_type`  | `AW`         |
| `bios`       | `"awbios"`   |
| `key`        | `0xD3`       |
| `size`       | `0x14000000` (320 MB total ROM space) |

Key helper functions in `core/types.h`:
```cpp
bool isNaomi()       → true for Naomi1/Naomi2 ONLY
bool isAtomiswave()  → true for DC_PLATFORM_ATOMISWAVE
```

**`isNaomi()` returns `false` for Atomiswave.** This is critical for boot path selection.

---

## 2. Boot Path Selection: Real BIOS vs REIOS

The SH-4 CPU starts execution after reset at PC = `0xA0000000` (uncached BIOS ROM area).

### REIOS (HLE BIOS Replacement)

In `core/reios/reios.cpp`, the hook at `0xA0000000` calls `reios_boot()`:

```cpp
static void reios_boot() {
    // ...
    if (settings.platform.isConsole()) {
        // Dreamcast path
        return;
    }
    verify(settings.platform.isNaomi());  // ← CRASHES for Atomiswave!
    // ... Naomi-only boot code
}
```

Since `isNaomi()` returns false for Atomiswave, **REIOS cannot boot Atomiswave games**. The `verify()` assertion would fail.

### Real BIOS (awbios.zip) — REQUIRED for Atomiswave

Atomiswave games **must** use the real Atomiswave BIOS firmware (`awbios.zip`). The BIOS is loaded into `sys_rom` memory:

**`core/hw/flashrom/nvmem.cpp` → `init()`:**
```cpp
case DC_PLATFORM_ATOMISWAVE:
    sys_rom = new DCFlashChip(settings.platform.bios_size,
                              settings.platform.bios_size / 2);
    // Note: DCFlashChip is WRITABLE (unlike RomChip for Naomi)
```

The BIOS is loaded via `loadBios()` in `naomi_cart.cpp`, which reads `awbios.zip` and copies the firmware into `sys_rom->data` (mapped at physical address `0x00000000`, accessible at `0xA0000000` in the SH-4 P2 uncached area).

The SH-4 CPU then executes the **actual BIOS firmware code**, which handles everything:
1. Hardware initialization
2. Reading the boot header from the cartridge
3. Copying program code from EPR to main RAM
4. Jumping to the game entry point

---

## 3. AWCartridge: The Hardware Interface

The BIOS accesses the Atomiswave cartridge through a memory-mapped register interface. The `AWCartridge` class (`core/hw/naomi/awcartridge.cpp`) emulates the ROM board hardware.

### Register Interface

| Register | Address | Description |
|----------|---------|-------------|
| `AW_EPR_OFFSETL` | `0x5F7000` | EPR offset low word |
| `AW_EPR_OFFSETH` | `0x5F7004` | EPR offset high word |
| `AW_MPR_RECORD_INDEX` | `0x5F700C` | MPR filesystem record index |
| `AW_MPR_FIRST_FILE_INDEX` | `0x5F7010` | First file record index |
| `AW_MPR_FILE_OFFSETL` | `0x5F7014` | MPR file offset low word |
| `AW_MPR_FILE_OFFSETH` | `0x5F7018` | MPR file offset high word |
| `AW_PIO_DATA` | `0x5F7080` | Direct read/write to ROM board |

The BIOS writes to these registers (via `AWCartridge::WriteMem()`) to select which region of the ROM to read, then reads decrypted data via DMA (`GetDmaPtr()`).

### Three Access Modes

```cpp
void AWCartridge::recalc_dma_offset(int mode) {
    switch(mode) {
    case EPR:           // Program code + header
        dma_offset = epr_offset * 2;
        dma_limit  = mpr_offset;
        break;
    case MPR_RECORD:    // Filesystem record table
        dma_offset = mpr_offset + mpr_record_index * 0x40;
        break;
    case MPR_FILE:      // Individual file data
        // Calculates absolute offset from filesystem record
        break;
    }
}
```

### On-the-fly Decryption

All data returned by the cartridge is **automatically decrypted** using the Atomiswave cipher. The encryption key for KOFXI is `0xD3`, which selects:
- Permutation table index: 3
- S-box set index: 1
- XOR value: `0x4BE3`

```cpp
void *AWCartridge::GetDmaPtr(u32 &size) {
    for (u32 i = 0; i < size / 2; i++)
        decrypted_buf[i] = decrypt16(offset + i);
    return decrypted_buf;
}
```

---

## 4. Boot Header: AtomiswaveBootID

The BIOS reads the boot header from the beginning of the EPR flash. Flycast mirrors this in `AWCartridge::GetBootId()`:

```cpp
struct AtomiswaveBootID {
    char  boardName[16];    // 0x00: "SYSTEM_X_APP"
    char  vendorName[32];   // 0x10
    char  gameTitle[32];    // 0x30
    char  year[4];          // 0x50
    char  month[2];         // 0x54
    char  day[2];           // 0x56
    u32   _unkn0;           // 0x58: mpr_offset
    u32   _unkn1;           // 0x5C
    u32   _unkn2;           // 0x60
    u32   gamePC;           // 0x64: game entry point
    u32   _unkn3;           // 0x68
    u32   testPC;           // 0x6C: test mode entry point
};
```

### KOFXI Header Values

| Field       | Offset | Value          | Meaning |
|-------------|--------|----------------|---------|
| `boardName` | 0x00   | `SYSTEM_X_APP` | Atomiswave identifier |
| `_unkn0`    | 0x58   | `0x02000000`   | MPR offset (where mask ROM data begins) |
| `gamePC`    | 0x64   | `0x8C010000`   | Game entry point (SH-4 P1 cached address) |
| `testPC`    | 0x6C   | `0x8C010000`   | Test mode entry point (same as gamePC) |

The `_unkn0` field at offset `0x58` is the `mpr_offset`—used by `AWCartridge::Init()`:
```cpp
void AWCartridge::Init(...) {
    mpr_offset = decrypt16(0x58 / 2) | (decrypt16(0x5a / 2) << 16);
}
```

---

## 5. SH-4 Memory Map

The SH-4 CPU uses a 32-bit address space with multiple regions:

### Physical Memory Areas

| Physical Address | Size | Description |
|-----------------|------|-------------|
| `0x00000000` | 2 MB | BIOS ROM (awbios) |
| `0x00200000` | 128 KB | Flash memory / SRAM |
| `0x005F7000` | — | G1 bus registers (cartridge interface) |
| `0x0C000000` | 16 MB | **Main system RAM** (Area 3) |
| `0x10000000` | 8 MB | Video RAM |

### SH-4 Virtual Address Translation

The SH-4 uses the top 3 bits of the 32-bit address to select the memory region:

| Virtual Range | Name | Translation |
|---------------|------|-------------|
| `0x00000000–0x7FFFFFFF` | U0/P0 | Through TLB (user space) |
| `0x80000000–0x9FFFFFFF` | **P1** | **Cached**: `physical = virtual & 0x1FFFFFFF` |
| `0xA0000000–0xBFFFFFFF` | P2 | Uncached: `physical = virtual & 0x1FFFFFFF` |
| `0xC0000000–0xDFFFFFFF` | P3 | Through TLB (kernel space) |
| `0xE0000000–0xFFFFFFFF` | P4 | Control registers |

**Address translation examples for KOFXI:**

| SH-4 Address | Mask `& 0x1FFFFFFF` | Physical | Location |
|-------------|---------------------|----------|----------|
| `0x8C010000` (gamePC) | `0x0C010000` | Main RAM + 0x10000 | 64 KB into RAM |
| `0x8C000000` | `0x0C000000` | Main RAM base | Start of RAM |
| `0xA0000000` | `0x00000000` | BIOS ROM base | Start of BIOS |
| `0xA05F7000` | `0x005F7000` | G1 bus regs | Cartridge registers |

### Memory Map Definition in Flycast

`core/hw/mem/addrspace.cpp`:
```cpp
{0x0C000000, 0x10000000, MAP_RAM_START_OFFSET, RAM_SIZE, true},  // Area 3 (main RAM + 3 mirrors)
```

Main RAM occupies physical `0x0C000000–0x0CFFFFFF` (16 MB) and is mirrored 3 additional times up to `0x0FFFFFFF`.

---

## 6. Program Loading and Execution

### Boot Sequence (Real BIOS)

The actual Atomiswave BIOS firmware performs these steps:

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. SH-4 Reset                                                  │
│    └─ PC = 0xA0000000 (BIOS ROM, uncached)                     │
│                                                                 │
│ 2. BIOS Initialization                                          │
│    └─ Hardware setup, interrupt vectors, etc.                   │
│                                                                 │
│ 3. Read Boot Header from Cartridge                              │
│    ├─ Write EPR offset registers (0x5F7000/0x5F7004)            │
│    ├─ DMA reads decrypted AtomiswaveBootID from EPR             │
│    └─ Extract: gamePC = 0x8C010000, mpr_offset = 0x02000000    │
│                                                                 │
│ 4. Copy Program Code: EPR → Main RAM                            │
│    ├─ Source: EPR flash (8 MB, decrypted on-the-fly)            │
│    ├─ Destination: SH-4 main RAM (0x0C000000)                   │
│    └─ DMA transfer via G1 bus (27 cycles/word for Atomiswave)   │
│                                                                 │
│ 5. Jump to Game Entry Point                                     │
│    └─ PC = gamePC = 0x8C010000 (physical 0x0C010000)            │
│         = Main RAM + 64 KB, P1 cached region                    │
└─────────────────────────────────────────────────────────────────┘
```

### What the REIOS Path Would Do (Naomi only, NOT Atomiswave)

For reference, the REIOS path (`core/reios/reios.cpp`) for Naomi games:

```cpp
// Read program size from ROM header offset 0x368
u32* sz = (u32*)CurrentCartridge->GetPtr(0x368, data_size);
const u32 size = *sz;

// Copy ROM to RAM at fixed address 0x0C020000
WriteMemBlock_nommu_ptr(0x0c020000, (u32*)CurrentCartridge->GetPtr(0, data_size), size);

// Set PC to fixed address 0x0C021000
reios_setup_naomi(0x0c021000);
```

This path uses hardcoded addresses (`0x0C020000` load base, `0x0C021000` entry point) and ignores the `gamePC`/`gameLoad` fields from the boot header.

**This path is NOT available for Atomiswave games** due to the `verify(settings.platform.isNaomi())` check.

### RomBootID.gamePC and gameLoad: Used or Not?

The `RomBootID` struct defines two key fields for program loading:

```cpp
u32  gameLoad[8][3];  // ROM→RAM copy regions: {offset, RAM_addr, length} × 8
u32  gamePC;          // Game entry point
```

**Finding**: In Flycast, `gamePC` and `gameLoad` are **populated** by `GetBootId()` but **never consumed by the emulator's C++ code** for actual program loading. They exist for metadata purposes only:

- `gamePC` is assigned from the Atomiswave header but never read to set the CPU's PC
- `gameLoad` entries are not set at all for Atomiswave (zeroed by memset)

The real BIOS firmware running on the emulated SH-4 handles the actual loading internally—Flycast just provides the hardware emulation layer.

---

## 7. ROM Board Memory Layout (KOFXI)

KOFXI uses a **Type 2** Atomiswave ROM board:

```
ROM Address Space (Type 2 board)
┌──────────────────────────────────┐ 0x00000000
│ FMEM1 (EPR flash)               │ 8 MB  ← ax3201p01.fmem1
│ Contains: boot header + code    │
├──────────────────────────────────┤ 0x00800000
│ Mirror of FMEM1                 │ 8 MB
├──────────────────────────────────┤ 0x01000000
│ (unused in KOFXI)               │
├──────────────────────────────────┤ 0x02000000  ← mpr_offset
│ MROM1 (mask ROM)                │ 32 MB ← ax3201m01.mrom1
├──────────────────────────────────┤ 0x04000000
│ MROM2                           │ 32 MB ← ax3202m01.mrom2
├──────────────────────────────────┤ 0x06000000
│ MROM3                           │ 32 MB ← ax3203m01.mrom3
├──────────────────────────────────┤ 0x08000000
│ (gap: address space hole)       │
├──────────────────────────────────┤ 0x0A000000
│ MROM4                           │ 32 MB ← ax3204m01.mrom4
├──────────────────────────────────┤ 0x0C000000
│ MROM5                           │ 32 MB ← ax3205m01.mrom5
├──────────────────────────────────┤ 0x0E000000
│ MROM6                           │ 32 MB ← ax3206m01.mrom6
├──────────────────────────────────┤ 0x10000000
│ (gap: address space hole)       │
├──────────────────────────────────┤ 0x12000000
│ MROM7                           │ 32 MB ← ax3207m01.mrom7
└──────────────────────────────────┘ 0x14000000
```

The MPR mask ROMs contain the **G1ROM Filesystem** with game assets (sprites, sounds, etc.). The EPR flash contains the executable program code.

---

## 8. Summary

| Aspect | Value | Source |
|--------|-------|--------|
| **Platform** | `DC_PLATFORM_ATOMISWAVE` | `naomi_cart.cpp:naomi_cart_GetPlatform()` |
| **BIOS** | `awbios.zip` (real firmware, required) | `naomi_roms.cpp` |
| **BIOS load address** | Physical `0x00000000` / Virtual `0xA0000000` | `nvmem.cpp:init()` |
| **SH-4 reset PC** | `0xA0000000` | `sh4_interpreter.cpp:sh4_cpu_Reset()` |
| **Main RAM** | Physical `0x0C000000` (16 MB) | `addrspace.cpp` |
| **Program load destination** | `0x0C000000+` (main RAM) | BIOS firmware (opaque) |
| **Game entry point (gamePC)** | `0x8C010000` = physical `0x0C010000` | EPR header offset `0x64` |
| **Entry point offset in RAM** | 64 KB (`0x10000`) from RAM base | `0x0C010000 - 0x0C000000` |
| **REIOS support** | **NO** — `verify(isNaomi())` blocks it | `reios.cpp:reios_boot()` |
| **Encryption key** | `0xD3` | `naomi_roms.cpp` |
| **MPR offset** | `0x02000000` | EPR header offset `0x58` |

### Key Source Files

| File | Role |
|------|------|
| `core/hw/naomi/awcartridge.cpp` | AWCartridge: cipher, DMA, register interface, GetBootId() |
| `core/hw/naomi/naomi_cart.cpp` | ROM loading, BIOS loading, platform detection |
| `core/hw/naomi/naomi_cart.h` | RomBootID struct definition |
| `core/hw/naomi/naomi_roms.cpp` | Game database (ROM file specs, keys, BIOS assignments) |
| `core/hw/flashrom/nvmem.cpp` | BIOS/Flash memory initialization |
| `core/hw/mem/addrspace.cpp` | SH-4 memory map (RAM at 0x0C000000) |
| `core/hw/sh4/sh4_mem.cpp` | WriteMemBlock_nommu_ptr (RAM block writes) |
| `core/hw/sh4/interpr/sh4_interpreter.cpp` | SH-4 CPU reset (initial PC = 0xA0000000) |
| `core/reios/reios.cpp` | REIOS HLE BIOS (Naomi only, not Atomiswave) |


# Análisis del Ejecutable Atomiswave — KOF XI Program ROM

## Resumen

El archivo ejecutable (EPR flash / FMEM1) contiene el **código de programa principal**
del juego The King of Fighters XI para la plataforma arcade Atomiswave. Este archivo
se carga en la RAM principal del sistema SH-4 durante el arranque y contiene toda la
lógica del juego, los menús de configuración, las estructuras de datos del Test Menu,
y un **menú oculto de Time Release** que controla el desbloqueo de personajes.

## Archivo Analizado

| Campo | Valor |
|-------|-------|
| **Archivo** | `ax3201p01.fmem1.dec_original` |
| **Ubicación** | `rom_samples/ejecutable/` |
| **Tamaño** | 8,388,608 bytes (8 MB exactos) |
| **Tipo** | EPR flash decriptado (programa SH-4) |
| **Plataforma** | Atomiswave (basado en Sega NAOMI / SH-4) |

---

## 1. Cabecera del Boot (Atomiswave Boot Header)

Los primeros 0x100 bytes contienen la cabecera de arranque que el BIOS de Atomiswave
lee para inicializar y cargar el juego.

### Estructura de la Cabecera

| Offset | Tamaño | Valor | Descripción |
|--------|--------|-------|-------------|
| `0x00` | 16 bytes | `SYSTEM_X_APP    ` | Identificador del sistema Atomiswave |
| `0x10` | 16 bytes | `SNK-PLAYMORE    ` | Publisher / Desarrollador |
| `0x20` | 16 bytes | *(espacios)* | Padding |
| `0x30` | 32 bytes | `THE KING OF FIGHTERS XI         ` | Título del juego |
| `0x50` | 8 bytes | `20051025` | Fecha de compilación (25 octubre 2005) |
| `0x58` | 4 bytes | `0x02000000` | MPR offset (inicio de Mask ROMs) |
| `0x5C` | 4 bytes | `0xFFFFFFFF` | Reservado |
| `0x60` | 4 bytes | `0x00000100` | Offset de carga del programa |
| `0x64` | 4 bytes | `0x8C010000` | **gamePC** — Punto de entrada del juego |
| `0x68` | 4 bytes | `0x00122820` | Tamaño del programa (~1,162 KB) |
| `0x6C` | 4 bytes | `0x8C010000` | **testPC** — Punto de entrada modo test |
| `0x70–0xFF` | 144 bytes | `0xFF` (relleno) | Área reservada |

```
Offset  00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F  ASCII
──────  ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──  ────────────────
0x0000  53 59 53 54 45 4D 5F 58 5F 41 50 50 20 20 20 20  SYSTEM_X_APP
0x0010  53 4E 4B 2D 50 4C 41 59 4D 4F 52 45 20 20 20 20  SNK-PLAYMORE
0x0020  20 20 20 20 20 20 20 20 20 20 20 20 20 20 20 20
0x0030  54 48 45 20 4B 49 4E 47 20 4F 46 20 46 49 47 48  THE KING OF FIGH
0x0040  54 45 52 53 20 58 49 20 20 20 20 20 20 20 20 20  TERS XI
0x0050  32 30 30 35 31 30 32 35 00 00 00 02 FF FF FF FF  20051025........
0x0060  00 01 00 00 00 00 01 8C 20 28 12 00 00 00 01 8C  ........ (......
0x0070  00 00 00 00 FF FF FF FF FF FF FF FF FF FF FF FF  ................
```

### Mapeo de Memoria SH-4

El procesador SH-4 del Atomiswave usa un espacio de direcciones de 32 bits. La
traducción de direcciones relevante es:

| Dirección Virtual | Área | Dirección Física | Uso |
|-------------------|------|-------------------|-----|
| `0x8C000000` | P1 (cached) | `0x0C000000` | Base de RAM principal |
| `0x8C010000` | P1 (cached) | `0x0C010000` | Punto de entrada del juego |
| `0xA0000000` | P2 (uncached) | `0x00000000` | BIOS ROM |

**Fórmula:** `dirección_física = dirección_virtual & 0x1FFFFFFF`

El BIOS de Atomiswave copia el contenido del EPR flash a la RAM principal (`0x0C000000`)
y salta al punto de entrada `gamePC = 0x8C010000`. Por lo tanto, el offset `X` dentro
del archivo ROM corresponde a la dirección SH-4 `0x8C000000 + X`.

---

## 2. Distribución del Archivo (Layout)

El ejecutable de 8 MB se divide en secciones funcionales:

```
┌─────────────────────────────────────────────────────────┐
│ 0x000000 - 0x0000FF  Cabecera Atomiswave Boot          │ 512 B
│ 0x000100 - 0x0F8CFF  Código SH-4 (programa principal)  │ ~993 KB
│ 0x0F8D00 - 0x0FFD00  Strings + datos del sistema       │ ~28 KB
│ 0x0FFD00 - 0x100100  Datos del Game Settings / Time Rel.│ ~1 KB
│ 0x100100 - 0x105F00  Strings de debug + menús test      │ ~23 KB
│ 0x105F00 - 0x122900  Código adicional + tablas          │ ~114 KB
│ 0x122900 - 0x7FFFFF  Código/datos (mayormente código)   │ ~5.9 MB
└─────────────────────────────────────────────────────────┘
```

### Tamaño Estimado del Programa Activo

La cabecera indica un tamaño de programa de `0x122820` bytes (**1,189,920 bytes ≈ 1.13 MB**).
Esto sugiere que solo los primeros ~1.13 MB contienen código/datos del juego activos, y el
resto del flash de 8 MB puede contener datos repetidos, padding, o código no utilizado.

---

## 3. Módulos del Sistema (Build Signatures)

El ejecutable contiene firmas de compilación de cada módulo del sistema operativo del
arcade (framework `sx_*` de SNK). Estas firmas revelan la arquitectura modular del
software:

| Módulo | Versión | Fecha de Build | Offset |
|--------|---------|----------------|--------|
| `sx_SramUtl` | 0.90 | Jun 10 2005 15:09:48 | `0x1038D9` |
| `sx_Coin` | 0.91 | Apr 08 2005 16:40:24 | `0x103921` |
| `sx_Input` | 0.90 | Apr 08 2005 16:40:26 | `0x103975` |
| `sx_Int` | 0.91 | Apr 08 2005 16:40:26 | `0x1039A9` |
| `sx_System` | 0.90 | Jun 10 2005 15:09:49 | `0x103A0D` |
| `sx_Credit` | 0.90 | Apr 08 2005 16:40:25 | `0x103E79` |
| `sx_SM_System` | 0.90 | Feb 22 2005 13:55:01 | `0x103EAD` |
| `sx_SystemSetting` | 0.90 | Feb 22 2005 13:55:02 | `0x104299` |
| `sx_SystemMenu` | 0.90 | Feb 22 2005 13:55:03 | `0x1045A1` |
| `sx_TestMenu` | 0.90 | Feb 22 2005 13:55:13 | `0x10469D` |
| `sx_CoinSetting` | 0.90 | Feb 22 2005 14:23:19 | `0x104BAD` |
| `sx_ConfigMenu` | 0.90 | Feb 22 2005 13:55:27 | `0x104D11` |
| `sx_ClearMenu` | 0.90 | Feb 22 2005 13:55:30 | `0x10503D` |
| `sx_LongBook` | 0.90 | Dec 08 2003 15:55:19 | `0x105091` |
| `sx_BackupSrv` | 0.90 | Mar 04 2004 17:16:16 | `0x1050C9` |
| `sx_Sram` | 0.90 | Jun 10 2005 15:09:46 | `0x105101` |
| `sx_Gun` | 0.90 | Apr 08 2005 16:40:26 | `0x1051DD` |
| `sx_SystemBackup` | 0.90 | Apr 08 2005 16:40:37 | `0x1054C5` |
| `sx_Output` | 0.90 | Apr 08 2005 16:40:27 | `0x105501` |
| `sx_ClockSetting` | 0.90 | Feb 22 2005 13:55:05 | `0x1056A9` |
| `sx_ColorTest` | 0.90 | Feb 22 2005 13:55:07 | `0x105795` |
| `sx_CrossHatch` | 0.90 | Feb 22 2005 13:55:08 | `0x10582D` |
| `sx_SoundTest` | 0.90 | Feb 22 2005 13:55:11 | `0x105961` |
| `sx_MemoryTest` | 0.90 | Feb 22 2005 13:55:23 | `0x105B39` |
| `sx_Bookkeeping` | 0.90 | Feb 22 2005 13:55:20 | `0x105E49` |

**Observaciones:**
- El framework `sx_*` fue construido en **3 fases**: diciembre 2003, febrero-abril 2005,
  y junio 2005 (actualizaciones finales a SRAM y System).
- El módulo más antiguo es `sx_LongBook` (diciembre 2003), posiblemente heredado de un
  juego anterior.
- La compilación final del juego fue el **25 de octubre de 2005** (fecha en la cabecera).

---

## 4. Jerarquía de Menús del Test Mode

El Test Mode del arcade tiene una estructura jerárquica de menús. A continuación se
documenta la estructura completa extraída del ejecutable:

### 4.1 SYSTEM MENU (Menú Principal del Test Mode)

**Módulo:** `sx_SystemMenu` | **Strings en:** `0x104530`

```
SYSTEM MENU
├── TEST MODE
├── COIN SETTINGS
├── CONFIGURATION ──────────┐
├── BOOKKEEPING             │
├── BACKUP CLEAR            │
├── NETWORK SETTINGS        │
└── EXIT                    │
                            │
    CONFIGURATION ◄─────────┘
    ├── SYSTEM SETTINGS
    ├── CLOCK SETTING
    ├── GAME SETTINGS ──────────┐
    ├── COMMUNICATION SETTINGS  │
    └── EXIT                    │
                                │
        GAME SETTINGS ◄─────────┘
        │
        │  ┌─ Página 1 ────────────────────┐
        ├── >NEXT PAGE
        ├── PLAY TIME        [SLOW ... FAST]
        ├── HOW TO PLAY      [OFF / ON]
        ├── DIFFICULTY        [1 ... 8]
        ├── VERSUS LIMIT     [WITHOUT / BEAT BY 10-30]
        ├── CONTINUE         [OFF / ON]
        ├── CONT.SERVICE     [OFF / ON]
        ├── BLOOD            [OFF / ON]
        ├── SAVE&EXIT
        │  └────────────────────────────────┘
        │
        │  ┌─ Página 2 ────────────────────┐
        ├── FLASH
        ├── VS MODE
        ├── BGM VOLUME
        ├── SE VOLUME
        ├── BUTTON SETTINGS ──────────────────┐
        ├── RETURN TO FACTORY SETTINGS        │
        ├── SAVE&EXIT                         │
        │  └────────────────────────────────┘ │
        │                                     │
        │     BUTTON SETTINGS ◄───────────────┘
        │     ├── A/LIGHT PUNCH         : [SHOT 1-5]
        │     ├── B/LIGHT KICK          : [SHOT 1-5]
        │     ├── C/STRONG PUNCH        : [SHOT 1-5]
        │     ├── D/STRONG KICK         : [SHOT 1-5]
        │     ├── E/SPECIAL ATTACK      : [SHOT 1-5]
        │     └── SAVE&EXIT
        │
        │  ┌─ OCULTO ─────────────────────────────────┐
        ├── TIME RELEASE SETTINGS  ◄── ¡MENÚ OCULTO!  │
        │   ├── RELEASE TYPE : [TYPE 0 ... TYPE 5]    │
        │   ├── RELEASE CHARA                         │
        │   └── NO SAVE&EXIT                          │
        │  └───────────────────────────────────────────┘
        │
        └── (fin del sub-menú)
```

### 4.2 TEST MODE (Sub-menú de Diagnóstico)

**Módulo:** `sx_TestMenu` | **Strings en:** `0x10464C`

| Ítem | Offset |
|------|--------|
| TEST MODE (título) | `0x10464C` |
| COLOR TEST | `0x104658` |
| CROSS HATCH | `0x104664` |
| I/O TEST | `0x104670` |
| SOUND TEST | `0x10467C` |
| MEMORY TEST | `0x10468C` |
| EXIT | `0x104694` |

---

## 5. Time Release Settings — El Menú Oculto

### 5.1 Ubicación en el ROM

El menú **TIME RELEASE SETTINGS** y todos sus datos se encuentran en una región
contigua del ejecutable:

| Contenido | Offset Inicio | Offset Fin | Tamaño |
|-----------|---------------|------------|--------|
| Título "TIME RELEASE SETTINGS" | `0x0FFFA0` | `0x0FFFB5` | 21 bytes |
| Opciones del menú | `0x0FFFB8` | `0x100007` | ~112 bytes |
| Nombres de personajes | `0x100008` | `0x10004B` | 67 bytes |
| Ajustes de debug | `0x100050` | `0x1000F0` | 160 bytes |

### 5.2 Estructura del Menú Time Release

```
Offset    Bytes                                          ASCII
────────  ───────────────────────────────────────────    ─────────────────────
0x0FFFA0  54 49 4D 45 20 52 45 4C 45 41 53 45 20 53 45  TIME RELEASE SET
0x0FFFB0  54 54 49 4E 47 53 00 00 52 45 4C 45 41 53 45  TINGS...RELEASE
0x0FFFC0  20 54 59 50 45 20 3A 00 00 4E 4F 20 53 41 56   TYPE :..NO SAV
0x0FFFD0  45 26 45 58 49 54 00 00 00 00 20 54 59 50 45  E&EXIT.... TYPE
0x0FFFE0  20 30 00 20 54 59 50 45 20 31 00 20 54 59 50   0. TYPE 1. TYP
0x0FFFF0  45 20 32 00 20 54 59 50 45 20 33 00 20 54 59  E 2. TYPE 3. TY
0x100000  50 45 20 34 00 20 54 59 50 45 20 35 00 52 45  PE 4. TYPE 5.RE
0x100010  4C 45 41 53 45 20 43 48 41 52 41 00 00 00 4E  LEASE CHARA...N
0x100020  4F 20 43 48 41 52 41 00 00 00 00 41 44 45 4C  O CHARA....ADEL
0x100030  48 49 44 45 00 00 00 00 47 41 49 00 53 49 4C  HIDE....GAI.SIL
0x100040  42 45 52 00 00 4A 41 5A 55 00 00 00 00 48 41  BER..JAZU....HA
0x100050  59 41 54 45 00 00 3F                           YATE..?
```

### 5.3 Release Types (Tipos de Desbloqueo)

El menú ofrece **7 opciones** para el tipo de desbloqueo temporal:

| Opción | Offset | Descripción Probable |
|--------|--------|---------------------|
| **RELEASE TYPE** | `0x0FFFB8` | Selector principal |
| TYPE 0 | `0x0FFFD8` | Sin desbloqueo temporal |
| TYPE 1 | `0x0FFFE0` | Desbloqueo progresivo lento |
| TYPE 2 | `0x0FFFE8` | Desbloqueo progresivo medio |
| TYPE 3 | `0x0FFFF0` | Desbloqueo progresivo rápido |
| TYPE 4 | `0x0FFFF8` | Desbloqueo acelerado |
| TYPE 5 | `0x100000` | Todos desbloqueados inmediatamente |

### 5.4 Personajes Desbloqueables (Release Characters)

El menú lista **6 personajes ocultos** que se desbloquean mediante el sistema de
Time Release, más un personaje desconocido `?`:

| Personaje | Offset | Notas |
|-----------|--------|-------|
| NO CHARA | `0x100018` | Sin personaje (deshabilitado) |
| **ADELHIDE** | `0x100024` | Adelheid Bernstein |
| **GAI** | `0x100030` | Gai Tendo |
| **SILBER** | `0x100034` | Silber |
| **JAZU** | `0x10003C` | Jazu |
| **HAYATE** | `0x100044` | Hayate |
| **?** | `0x10004C` | Personaje desconocido / reservado |

Estos son los personajes "boss" y secretos que normalmente se desbloquean
automáticamente tras cierto tiempo de operación del arcade (time release) o
al cumplir condiciones específicas.

### 5.5 Ajustes de Debug Asociados

Inmediatamente después de los datos de Time Release hay una tabla extensa de
opciones de debug que controlan el comportamiento del juego en modo desarrollo:

| Opción | Offset | Función |
|--------|--------|---------|
| MUTEKI | `0x100050` | Invencibilidad (無敵 = "invencible" en japonés) |
| No Life | `0x100060` | Sin barra de vida |
| Death | `0x100070` | Muerte instantánea |
| Undead | `0x100080` | No se puede morir |
| Time Stop | `0x100090` | Detener el temporizador |
| Time Down | `0x1000A0` | Temporizador acelerado hacia abajo |
| Time Over | `0x1000B0` | Tiempo agotado instantáneo |
| Wait | `0x1000C0` | Pausar acción |
| Pause | `0x1000D0` | Pausa del juego |
| Still | `0x1000E0` | Congelar animación |
| CP | `0x1000F0` | Control Player (modo CPU) |
| Rect | `0x100100` | Mostrar hitboxes/rectángulos de colisión |
| P Recovery | `0x100110` | Recuperación de power gauge |
| P Max | `0x100120` | Power gauge al máximo |
| S Max | `0x100130` | Super gauge al máximo |
| Seq Stop | `0x100140` | Detener secuencias |
| Watch | `0x100150` | Modo observador |
| Soft Reset | `0x100160` | Reset por software |
| Vs Reset | `0x100170` | Reset del modo versus |
| Cpu Select | `0x100180` | Selección de CPU oponente |
| Stage Select | `0x100190` | Selección de escenario |
| Sound Test | `0x1001A0` | Test de sonido integrado |
| Hori | `0x1001B0` | Horizontal (¿flip de pantalla?) |
| Status | `0x1001C0` | Mostrar estado del juego |
| COMMAND BUFF | `0x1001D0` | Buffer de comandos/inputs |
| Debug Disp0 | `0x1001E0` | Display de debug #0 |
| F PARA Disp | `0x1001F0` | Display de parámetros de frame |

### 5.6 Menú Debug Extendido

Existe un menú de debug aún más extenso (para desarrollo) con funciones avanzadas.

> **Nota:** Los strings se transcriben exactamente como aparecen en el ROM. Algunas
> palabras contienen errores ortográficos del equipo de desarrollo original
> (ej: "BILINAER" en vez de "BILINEAR", "COKPIT" en vez de "COCKPIT").

| Opción | Offset | Función |
|--------|--------|---------|
| ACTION TOOL | `0x100200` | Herramienta de edición de acciones |
| CONVERSION | `0x100210` | Conversión de datos |
| COKPIT OFF | `0x100220` | Desactivar cockpit/HUD (sic: "COCKPIT") |
| DIST ON | `0x100230` | Activar display de distancia |
| TASK ON | `0x100240` | Activar monitor de tareas |
| SEQ DISP | `0x100250` | Display de secuencias |
| SEQ TOOL | `0x100260` | Herramienta de secuencias |
| PAL TOOL | `0x100270` | Herramienta de paletas |
| FRONT DISP | `0x100280` | Display frontal |
| SHUTTER OFF | `0x100290` | Desactivar shutter |
| SURVIVAL | `0x1002A0` | Modo supervivencia |
| SHADOW | `0x1002B0` | Sombras |
| MEM VIEW | `0x1002C0` | Visor de memoria |
| QUICK FREE | `0x1002D0` | Free play rápido |
| SAVING FREE | `0x1002E0` | Guardado libre |
| NET ST DISP | `0x1002F0` | Display de estado de red |
| L CMD FREE | `0x100300` | Comandos largos libres |
| MODE TIMEOFF | `0x100310` | Desactivar tiempo de modo |
| DIR SPRITE | `0x100320` | Sprites directos |
| PS GAI | `0x100330` | Player Select: GAI |
| PS HAYATE | `0x100340` | Player Select: HAYATE |
| PS ADELHIDE | `0x100350` | Player Select: ADELHIDE |
| PS SILBER | `0x100360` | Player Select: SILBER |
| PS JAZU | `0x100370` | Player Select: JAZU |
| PS SHION | `0x100380` | Player Select: SHION |
| PS MAGAKI | `0x100390` | Player Select: MAGAKI |
| PS SAME | `0x1003A0` | Player Select: SAME (¿mismo personaje?) |
| DAMAGE | `0x1003B0` | Control de daño |
| BILINAER 1P | `0x1003C0` | Filtro bilineal jugador 1 (sic: "BILINEAR") |
| BILINAER 2P | `0x1003D0` | Filtro bilineal jugador 2 (sic: "BILINEAR") |
| BEAR REVISE | `0x1003E0` | Revisión de bear (¿hitbox?) |
| PIYO MUTEKI | `0x1003F0` | Invencible durante dizzy/stun |
| GRAD MUTEKI | `0x100400` | Invencible durante grab/agarre |
| FIX OFF | `0x100410` | Desactivar correcciones |
| COMMAND DISP | `0x100420` | Display de comandos |
| COMMAND LIST | `0x100430` | Lista de comandos |
| Debug Dip | `0x100441` | DIP switches de debug |
| Debug Menu | `0x10044C` | Menú de debug principal |

**Nota sobre PS (Player Select):** Las entradas `PS GAI`, `PS HAYATE`, `PS ADELHIDE`,
`PS SILBER`, `PS JAZU` corresponden a los mismos personajes desbloqueables del Time
Release. Adicionalmente aparecen `PS SHION` y `PS MAGAKI` (bosses del juego), lo que
confirma que estos personajes tenían funcionalidad de selección en el debug.

---

## 6. Estructura de Datos de los Menús

### 6.1 CONFIGURATION Menu (sx_ConfigMenu)

**Módulo:** `sx_ConfigMenu Ver 0.90` (Build: Feb 22 2005 13:55:27)

El menú CONFIGURATION tiene **6 strings** consecutivos alineados a 16 bytes en:

| # | Offset | String | Tipo |
|---|--------|--------|------|
| 0 | `0x104CB0` | `CONFIGURATION` | Título del menú |
| 1 | `0x104CC0` | `SYSTEM SETTINGS` | Sub-menú de sistema |
| 2 | `0x104CD0` | `CLOCK SETTING` | Ajuste de reloj |
| 3 | `0x104CE0` | `GAME SETTINGS` | Sub-menú de juego |
| 4 | `0x104CF0` | `COMMUNICATION SETTINGS` | Configuración de red |
| 5 | `0x104D08` | `EXIT` | Salir del menú |

Los datos de configuración del menú se encuentran en `0x104C60–0x104CAF` como
registros de 16 bytes que definen la apariencia y comportamiento:

```
Offset    Datos (hex)                               Interpretación
────────  ──────────────────────────────────────    ──────────────────
0x104C60  06 00 0D 00 C0 4B 11 8C 08 00 00 00      row=6, col=13, ptr=0x8C114BC0
0x104C6C  FF 60 60 60                               color=RGBA(96,96,96,255)
0x104C70  08 00 0D 00 D0 4B 11 8C 08 00 00 00      row=8, col=13, ptr=0x8C114BD0
0x104C7C  FF 60 60 60                               color=RGBA(96,96,96,255)
0x104C80  0A 00 0D 00 E0 4B 11 8C 08 00 00 00      row=10, col=13
0x104C8C  FF 60 60 60
0x104C90  0C 00 0D 00 F0 4B 11 8C 08 00 00 00      row=12, col=13
0x104C9C  FF C0 C0 C0                               color=RGBA(192,192,192,255)
0x104CA0  0E 00 0D 00 08 4C 11 8C 06 00 00 00      row=14, count=6
0x104CAC  10 4C 11 8C                               ptr=0x8C114C10
```

**Valor clave:** En `0x104CA8` se encuentra el valor `0x06` que corresponde al
**número total de ítems** del menú CONFIGURATION (6 strings incluyendo título y EXIT).

### 6.2 GAME SETTINGS (Ajustes de Juego)

**Strings en:** `0x0FFD60–0x0FFDF0` (Página 1) y `0x0FFE84–0x0FFEE0` (Página 2)

Los ajustes del juego se organizan en **dos páginas** con un sistema de navegación
`>NEXT PAGE`. Las opciones y sus valores posibles son:

#### Página 1 — Records en `0x0FF574`, SRAM `0x179229–0x17922F`

| Opción | Str offset | SRAM | Valores Posibles |
|--------|-----------|------|-----------------|
| PLAY TIME | `0x0FFD80` | `0x179229` | SLOW, LITTLE SLOW, NORMAL, LITTLE FAST, FAST |
| HOW TO PLAY | `0x0FFD90` | `0x17922A` | OFF, ON |
| DIFFICULTY | `0x0FFDA0` | `0x17922B` | 1, 2, 3, 4, 5, 6, 7, 8 |
| VERSUS LIMIT | `0x0FFDB0` | `0x17922C` | WITHOUT, BEAT BY 10, BEAT BY 20, BEAT BY 30 |
| CONTINUE | `0x0FFDC0` | `0x17922D` | OFF, ON |
| CONT.SERVICE | `0x0FFDD0` | `0x17922E` | OFF, ON |
| BLOOD | `0x0FFDE0` | `0x17922F` | OFF, ON |

#### Página 2 — Records en `0x0FF7C4`, SRAM `0x179230–0x179233`

| Opción | Str offset | SRAM | Valores Posibles |
|--------|-----------|------|-----------------|
| FLASH | `0x0FFE84` | `0x179230` | OFF, ON |
| VS MODE | `0x0FFE94` | `0x179231` | OFF, ON |
| BGM VOLUME | `0x0FFEA4` | `0x179232` | 0, 1, 2, 3 |
| SE VOLUME | `0x0FFEB4` | `0x179233` | 0, 1, 2, 3 |
| BUTTON SETTINGS | `0x0FFEC4` | — | (sub-menú de botones) |
| RETURN TO FACTORY | `0x0FFED4` | — | (restaurar valores de fábrica) |

#### Button Settings — Records en `0x0FF928`, SRAM `0x179234–0x17923C`

| Opción | Str offset | SRAM | Valores Posibles |
|--------|-----------|------|-----------------|
| A/LIGHT PUNCH | `0x0FFEF4` | `0x179234` | SHOT 1–5 |
| B/LIGHT KICK | `0x0FFF0C` | `0x179236` | SHOT 1–5 |
| C/STRONG PUNCH | `0x0FFF24` | `0x179238` | SHOT 1–5 |
| D/STRONG KICK | `0x0FFF3C` | `0x17923A` | SHOT 1–5 |
| E/SPECIAL ATTACK | `0x0FFF54` | `0x17923C` | SHOT 1–5 |

> **Nota sobre strings compartidos:** Los valores OFF/ON (`0x0FFE30`/`0x0FFE34`)
> son compartidos por P1 (HOW TO PLAY, CONTINUE, CONT.SERVICE, BLOOD) y P2
> (FLASH, VS MODE). Los strings "1"-"3" del DIFFICULTY (`0x0FFE38`-`0x0FFE40`)
> son reutilizados por BGM/SE VOLUME. El "0" de BGM/SE VOLUME tiene su propio
> string en `0x0FFEF0`. Los 5 botones comparten los mismos SHOT 1-5
> (`0x0FFF78`-`0x0FFF98`).

### 6.3 Estructura de Registros y Tabla Descriptor

La zona `0x0FF574–0x0FFB7C` contiene los **registros de configuración de página**
y la zona `0x0FFD60–0x100050` contiene los **datos de strings**. La tabla
descriptor en `0x04BEF0–0x04BFC8` enlaza cada ítem seleccionable con su dirección
SRAM y su lista de valores.

#### Formato de registro (16 bytes)

Todos los registros (título, ítems, valores) usan el mismo formato de 16 bytes:

```
Bytes 0-3:   recsize (0x10 = título, 0x08 = ítem/valor)
Bytes 4-7:   Color RGBA (ej: 0x0000FF00 = verde = título)
Bytes 8-9:   Fila (row) en pantalla
Bytes 10-11: Columna (col) en pantalla
Bytes 12-15: Puntero SH-4 a string (0x8C10FDxx → offset 0x0FFDxx)
```

#### Estructura de página

Cada página sigue esta estructura:

```
[record título]  (recsize=0x10, color verde)
[record ítem 1]  (recsize=0x08, color gris)
[record ítem 2]  ...
[record ítem N]
[u32 page_entry_count]   ← = N+1 (título + ítems)
[records valor-list 1]   ← valores para ítem seleccionable 1
[u32 trailing_count_1]   ← número de records en valor-list 1
[records valor-list 2]   ← valores para ítem seleccionable 2
[u32 trailing_count_2]   ← número de records en valor-list 2
...
```

#### Tabla descriptor (`0x04BEF0`)

La tabla descriptor contiene pares de u32 (SH-4 pointers) que enlazan cada
página e ítem seleccionable:

```
Por página:
  [ptr → page_entry_count] [ptr → primer record de la página]

Por ítem seleccionable:
  [SRAM address]           [ptr → primer record de valores]
```

| Página | Records | Entries | Descriptor |
|--------|---------|---------|------------|
| GS Página 1 | `0x0FF574` | 10 | `0x04BEF0` |
| GS Página 2 | `0x0FF7C4` | 9 | `0x04BF08` |
| Button Settings | `0x0FF928` | 7 | `0x04BF30` |
| Time Release | `0x0FFB40` | 4 | `0x04BF88` |

#### Punteros globales de página (`0x04BD18`)

```
0x04BD18: GS P1 → 0x0FF614 (page_entry_count = 10)
0x04BD20: GS P2 → 0x0FF854 (page_entry_count = 9)
0x04BD28: BS   → 0x0FF998 (page_entry_count = 7)
0x04BD30: TR   → 0x0FFB80 (page_entry_count = 4)
```

---

## 7. El Menú Oculto: Análisis del Mecanismo de Ocultamiento

### 7.1 ¿Cómo Se Oculta TIME RELEASE?

Basado en el análisis del ejecutable y la información de investigación comunitaria,
el menú **TIME RELEASE SETTINGS** está oculto mediante un mecanismo simple pero
efectivo:

1. **Las strings del menú están completas** — Todo el texto del menú Time Release
   existe intacto en el ROM (offset `0x0FFFA0–0x10004B`).

2. **El código funcional está presente** — Las opciones de RELEASE TYPE (TYPE 0-5),
   RELEASE CHARA, y los nombres de personajes están todos implementados.

3. **La entrada está debajo de EXIT** — El menú Time Release aparece **después** de
   la opción SAVE&EXIT / EXIT en la estructura de datos. Esto significa que el cursor
   del menú nunca puede alcanzarlo, ya que EXIT es el último ítem navegable.

4. **El conteo de ítems del menú excluye TIME RELEASE** — El valor del contador de
   ítems del menú no incluye la entrada de Time Release, haciendo que el sistema
   de navegación del cursor la ignore.

### 7.2 El Cheat de MAME

Según la investigación de la comunidad, existe un cheat de MAME que permite acceder
al menú oculto modificando la configuración del menú en runtime:

> *"Here is the MAME cheat to modify the menu configuration and allow the cursor
> to move to the blank entry and select Time Release"*

El cheat funciona modificando un valor en la **RAM del sistema** (dirección
`0x0C000000+`) durante la ejecución, aumentando el conteo de ítems del menú
para que el cursor pueda bajar hasta la entrada de Time Release que está debajo
de EXIT.

**Principio del cheat:**
```
Valor original: count = N      (EXIT es el último ítem navegable)
Valor modificado: count = N+1  (TIME RELEASE se vuelve navegable)
```

El hecho de que el menú funcione perfectamente cuando se accede confirma que
**todo el código está implementado** — solo fue ocultado en la interfaz.

### 7.3 Modificación Directa del ROM

Para hacer el menú accesible de forma permanente **sin** necesidad de cheats,
se necesitaría:

1. **Modificar la tabla descriptor o los punteros globales de página** para que
   TIME RELEASE sea alcanzable desde la navegación del GAME SETTINGS.

2. **Candidatos identificados:**
   - Punteros globales de página en `0x04BD18`: actualmente define 4 páginas
     (GS P1, GS P2, BS, TR). El código que procesa estas 4 entradas podría
     limitar la navegación.
   - El `page_entry_count` de GS Página 2 en `0x0FF854` (valor = 9): 
     no incluye TIME RELEASE como ítem de navegación.
   - La tabla descriptor de TIME RELEASE en `0x04BF88` ya tiene toda la
     configuración necesaria (RELEASE TYPE + RELEASE CHARA slots).

3. **La estructura de TIME RELEASE ya funciona** — El menú tiene su propio
   bloque de records en `0x0FFB40` con título verde, 4 entries, y toda la
   lógica de RELEASE TYPE (6 tipos) y RELEASE CHARA (slots progresivos con
   2-6 opciones). El SRAM para RELEASE TYPE está en `0x179360`.

**Nota:** La modificación del ROM requiere re-encriptar el ejecutable con la
herramienta de este repositorio (`unpack.py pack`) para generar un archivo
compatible con el emulador Flycast.

---

## 8. SYSTEM SETTINGS (Ajustes de Sistema)

**Módulo:** `sx_SystemSetting Ver 0.90` | **Strings en:** `0x10416C`

| Opción | Offset | Valores |
|--------|--------|---------|
| AREA | `0x10417C` | JAPAN, NORTH AMERICA, EUROPE, OTHER |
| LANGUAGE | `0x104184` | JAPANESE, ENGLISH, SPANISH, PORTUGUESE, ITALIAN, CHINESE SIMPLE, CHINESE TRAD |
| ADVERTISE SOUND | `0x104190` | ON, OFF |
| AUDIO MODE | `0x1041A0` | MONO, STEREO |
| SOUND VOLUME | `0x1041AC` | (nivel numérico) |
| SAVE&EXIT | `0x1041BC` | |

---

## 9. Otros Strings y Datos de Interés

### 9.1 Strings del Sistema de Créditos/Monedas

| String | Offset |
|--------|--------|
| `GAME MODE       NORMAL` | `0x1049C4` |
| `GAME MODE       FREE PLAY` | `0x104ACC` |
| `COIN CHUTE TYPE COMMON` | `0x1049DC` |
| `COIN CHUTE TYPE INDIVIDUAL` | `0x104A7C` |

### 9.2 Datos de Bookkeeping

| String | Offset |
|--------|--------|
| `DAILY COIN DATA` | `0x105434` |
| `MONTHLY COIN DATA` | `0x105444` |
| `MONTHLY PLAY DATA` | `0x105420` |
| `BOOKKEEPING CLEAR` | `0x104F64` |

### 9.3 Strings de Audio/Sonido

| String | Offset |
|--------|--------|
| `&BankSize` | `0x10046C` |
| `BANK SIZE` | `0x100478` |
| `MidiDrmBank` | `0x100488` |
| `MidiSeqBank` | `0x100498` |
| `MidiPrgBank` | `0x1004A4` |
| `OneShotBank` | `0x1004B0` |
| `PCM-RingBuf` | `0x1004BC` |
| `FX-OutBank` | `0x1004C8` |
| `FX-PrgBank` | `0x1004D4` |
| `FX-PrgWork` | `0x1004E0` |

---

## 10. Análisis de Arquitectura del Código

### 10.1 CPU y Conjunto de Instrucciones

| Aspecto | Detalle |
|---------|---------|
| **CPU Principal** | Hitachi SH-4 (SuperH-4) a 200 MHz |
| **Instrucciones** | 16 bits (formato fijo) |
| **Endianness** | Little-endian (datos y instrucciones) |
| **RAM Principal** | 16 MB en dirección física `0x0C000000` |
| **VRAM** | 8 MB en dirección física `0x10000000` |

### 10.2 Punto de Entrada y Boot

```
SH-4 Reset
  │
  ▼
PC = 0xA0000000 (BIOS ROM, uncached)
  │
  ▼
BIOS lee cabecera del cartucho
  │
  ▼
DMA copia EPR flash → RAM principal (0x0C000000)
  │
  ▼
Salto a gamePC = 0x8C010000 (= phys 0x0C010000)
  │
  ▼
Inicio del juego (código SH-4 en P1 cached)
```

### 10.3 Código SH-4 en el Punto de Entrada (0x100)

```asm
; Offset 0x100 (dirección SH-4: 0x8C000100)
8C000100:  D202     MOV.L @(0x8C00010C,PC),R2    ; Cargar constante
8C000106:  A003     BRA   0x8C00016A              ; Salto incondicional
8C000108:  0009     NOP                           ; Delay slot

; Inicialización de registros generales
8C00019A:  6103     MOV R0,R1                     ; R1 = 0
8C00019C:  6203     MOV R0,R2                     ; R2 = 0
8C00019E:  6303     MOV R0,R3                     ; ...
8C0001A0:  6403     MOV R0,R4
8C0001A2:  6503     MOV R0,R5
8C0001A4:  6603     MOV R0,R6
; ... (inicializa R0-R15 a cero)

8C0001B6:  D006     MOV.L @(0x8C0001D0,PC),R0    ; Cargar stack pointer
8C0001B8:  6F03     MOV R0,R15                    ; R15 = SP (stack pointer)
8C0001C2:  D002     MOV.L @(0x8C0001CC,PC),R0    ; Cargar dirección de main()
8C0001C4:  400B     JSR @R0                       ; Llamar a main()
```

### 10.4 Desensamblado Parcial

Se realizó un desensamblado básico del punto de entrada usando un decodificador SH-4
personalizado. El código muestra el patrón típico de arranque de un programa SH-4:
1. Carga de constantes desde el literal pool (instrucciones `MOV.L @(disp,PC),Rn`)
2. Inicialización de todos los registros generales a cero
3. Configuración del stack pointer (R15)
4. Salto a la función principal del juego

**Limitaciones del desensamblado:**
- No se cuenta con un desensamblador SH-4 completo en el entorno actual.
- El código mixto (código + datos inline) dificulta el análisis automático.
- Para un análisis más profundo se recomienda usar herramientas como **Ghidra**
  (que soporta SH-4) o **radare2**.

---

## 11. Recomendaciones para Análisis Avanzado

### 11.1 Uso de Emuladores para Análisis

Con el código fuente de emuladores como **Flycast** o **MAME**, se podrían:

- **Agregar breakpoints** en las direcciones donde se leen los contadores de menú.
- **Trazar la ejecución** para encontrar exactamente qué instrucción decide el número
  de ítems del menú.
- **Monitorear accesos a memoria** en el rango `0x8C0FFFA0–0x8C10004B` (área de
  Time Release) para identificar qué código lee estos datos.

### 11.2 Herramientas Recomendadas

| Herramienta | Uso | Notas |
|-------------|-----|-------|
| **Ghidra** | Desensamblado/decompilación SH-4 | Soporte nativo para SuperH |
| **radare2** | Análisis binario | Plugin sh disponible |
| **MAME debugger** | Debug en runtime | `-debug` flag al ejecutar |
| **Flycast** | Emulación + posible debug | Código fuente disponible |
| **Python struct** | Análisis de estructuras | Ya usado en este repositorio |

### 11.3 Modificaciones Posibles sin Decompilación Completa

1. **Habilitar Time Release permanente:** Modificar el byte del contador de ítems
   del menú para incluir la entrada oculta.

2. **Cambiar configuración predeterminada:** Los valores por defecto de GAME SETTINGS
   se almacenan en la SRAM del Atomiswave, pero los valores iniciales/de fábrica
   podrían estar en el ROM.

3. **Desbloquear personajes directamente:** Si se identifica la estructura en SRAM
   que almacena el estado de desbloqueo, se podría pre-configurar para tener todos
   los personajes disponibles.

4. **Activar menú debug:** Las strings del menú Debug están presentes (`Debug Menu`,
   `Debug Dip`, etc.) con opciones extensas. La activación probablemente requiere
   un DIP switch o flag en memoria.

---

## 12. Contexto en el Repositorio

| Elemento | Ubicación |
|----------|-----------|
| Archivo ejecutable | `rom_samples/ejecutable/ax3201p01.fmem1.dec_original` |
| Análisis del boot SH-4 | `aw_boot_analysis.md` |
| Herramienta de decrypt/encrypt | `aw_crypto.py` |
| Workflow completo (unpack/repack) | `unpack.py` |
| Este análisis | `docs/analisis_ejecutable.md` |

Para re-encriptar un ejecutable modificado:
```bash
# 1. Desempaquetar el ROM original
python3 unpack.py

# 2. Modificar el ejecutable en la carpeta extraída
#    (editar el archivo fmem1 decriptado)

# 3. Re-empaquetar y encriptar
python3 unpack.py pack

# 4. El archivo ZIP resultante es compatible con Flycast
```


