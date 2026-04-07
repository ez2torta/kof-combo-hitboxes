# Flycast (Atomiswave) — KOF XI Compatibility: Findings & Progress

## Estado del Proyecto

**Plataforma objetivo:** Atomiswave (emulada por Flycast)  
**Juego objetivo:** The King of Fighters XI  
**Estado:** Implementación completa. Todas las direcciones de memoria descubiertas y verificadas.

---

## 1. Detección del Emulador Flycast

### Proceso y Ventana

| Campo | Valor |
|-------|-------|
| Nombre del proceso | `flycast.exe` |
| Título de ventana | `Flycast Dojo` (puede variar: `Flycast`, etc.) |
| Detección | Pattern match en título + verificación de nombre de proceso |

**Nota importante:** A diferencia de PCSX2, Flycast **no incluye el nombre del juego** en el título de la ventana. La identificación del juego se realiza mediante escaneo de patrones en la RAM después de conectar al proceso.

### Método de detección implementado

```
1. EnumWindows → buscar título que contenga "Flycast"
2. Verificar nombre de proceso = "flycast.exe"
3. Abrir handle con PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
4. Escanear RAM para encontrar base + verificar juego cargado
```

---

## 2. Mapeo de Memoria del SH-4 en Flycast

### Cómo Flycast asigna la memoria emulada

Flycast utiliza **`VirtualAlloc2` / `MapViewOfFile`** para crear regiones de memoria mapeada (`MEM_MAPPED`, tipo `0x40000`) que representan el espacio de direcciones físico del SH-4.

La RAM principal de 16 MB se mapea como **4 regiones independientes** de exactamente `0x1000000` bytes (16 MB cada una), correspondientes a los 4 espejos del hardware:

| Región | Dirección física SH-4 | Tamaño |
|--------|------------------------|--------|
| RAM original | `0x0C000000 - 0x0CFFFFFF` | 16 MB |
| Espejo 1 | `0x0D000000 - 0x0DFFFFFF` | 16 MB |
| Espejo 2 | `0x0E000000 - 0x0EFFFFFF` | 16 MB |
| Espejo 3 | `0x0F000000 - 0x0FFFFFFF` | 16 MB |

Adicionalmente existe una quinta región de ~15.3 MB (`0xEF7000`) que es una vista parcial con un `allocBase` ligeramente diferente.

### Direcciones encontradas (sesión actual)

| Región del proceso | Tamaño | Tipo | Contenido |
|-------------------|--------|------|-----------|
| `0x7FF4631E9000` | `0xEF7000` (15.3 MB) | MEM_MAPPED | RAM principal (parcial) |
| `0x7FF4640E0000` | `0x1000000` (16 MB) | MEM_MAPPED | RAM espejo completo |
| `0x7FF4650E0000` | `0x1000000` (16 MB) | MEM_MAPPED | RAM espejo completo |
| `0x7FF4660E0000` | `0x1000000` (16 MB) | MEM_MAPPED | RAM espejo completo |

**IMPORTANTE:** Estas direcciones cambian en cada ejecución de Flycast. El módulo utiliza escaneo dinámico de patrones para encontrarlas.

### Otras regiones de interés

| Dirección | Tamaño | Descripción probable |
|-----------|--------|---------------------|
| `0x19380009000` | 327 MB | Espacio ROM completo del cartucho (0x14000000) |
| `0x7FF45D0E0000` | 8 MB × 2 | VRAM (Video RAM) |
| `0x7FF4578E0000` | 2 MB × 4 | BIOS ROM / Flash mirrors |
| `0x193E50C0000` | ~372 KB | Buffer ROM del cartucho (contiene `SYSTEM_X_APP`) |

---

## 3. Carga del Programa en RAM

### Comportamiento del BIOS de Atomiswave

El BIOS real de Atomiswave (`awbios.zip`) carga el programa del cartucho EPR flash a la RAM principal:

```
EPR Flash (8 MB)                    Main RAM (16 MB)
┌──────────────────┐                ┌──────────────────────────┐
│ 0x000-0x0FF      │ NO se copia    │ 0x00000-0x0FFFF          │ Datos BIOS/inicialización
│ (Boot header)    │ ──────────/    │                          │
├──────────────────┤                ├──────────────────────────┤
│ 0x100            │ ────────────── │ 0x10000 (= 0x8C010000)  │ ← gamePC (punto de entrada)
│ (Código programa)│                │ (Código del juego)       │
│ ...              │                │ ...                      │
│ 0x122920         │ ────────────── │ 0x132820                 │ ← Fin del programa activo
├──────────────────┤                ├──────────────────────────┤
│ 0x7FFFFF         │                │ 0x0FFFFF                 │ (resto de RAM libre)
└──────────────────┘                └──────────────────────────┘
```

### Fórmula de mapeo EPR → RAM

```
Para cualquier offset X en el EPR (X >= 0x100):
  RAM_offset = X - 0x100 + 0x10000 = X + 0xFF00

Para leer desde el proceso Flycast:
  process_addr = RAMbase + RAM_offset
```

### Verificación del mapeo

| Contenido | Offset EPR | Offset RAM | Verificado |
|-----------|-----------|-----------|------------|
| Instrucción D202 (entry point) | `0x100` | `0x10000` | ✅ |
| String "MUTEKI" | `0x100050` | `0x10FF50` | ✅ |
| String "Debug Menu" | `0x10044C` | `0x11034C` | ✅ (inferido) |
| Boot header "SYSTEM_X_APP" | `0x000` | N/A | ❌ NO se copia a RAM |

### Lo que NO se copia a RAM

- El boot header (offsets 0x00-0xFF) que contiene:
  - `SYSTEM_X_APP` (identificador)
  - `THE KING OF FIGHTERS XI` (título)
  - `SNK-PLAYMORE` (publisher)
  - `gamePC`, `testPC`, `mpr_offset`

Estos datos permanecen solo en el buffer ROM del cartucho (región separada en el proceso).

---

## 4. Traduccción de Direcciones SH-4

### Del código del juego al proceso Flycast

El código del juego usa direcciones virtuales SH-4 en el espacio P1 (cached):

```
Dirección SH-4 (P1):    0x8C XXYYZZ
                         ↓ AND 0x1FFFFFFF
Dirección física:        0x0C XXYYZZ
                         ↓ - 0x0C000000
Offset en RAM:           0x00 XXYYZZ
                         ↓ + RAMbase (dinámico)
Dirección en proceso:    RAMbase + XXYYZZ
```

### Ejemplos

| SH-4 Address | Physical | RAM Offset | Descripción |
|-------------|----------|-----------|-------------|
| `0x8C000000` | `0x0C000000` | `0x000000` | Inicio de RAM |
| `0x8C010000` | `0x0C010000` | `0x010000` | Entry point (gamePC) |
| `0x8C10FF50` | `0x0C10FF50` | `0x10FF50` | String "MUTEKI" |

---

## 5. Contenido de la RAM (Inicio)

Los primeros bytes de la RAM principal (`0x0C000000`):

```
0C000000: 09 00 09 00 09 00 1B 00 FD AF 09 00 00 00 00 00
0C000010: 09 00 09 00 2B 00 09 00 09 00 09 00 0B 00 09 00
0C000020: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
  ... (ceros hasta 0x10000)
```

Los primeros 64 KB (`0x00000-0x0FFFF`) contienen datos del BIOS o inicialización; el código del juego comienza en `0x10000`.

Bytes en el entry point (`0x8C010000` = RAM[0x10000]):

```
0C010000: 02 D2 02 93 32 22 03 A0 09 00 FF 00 1C 81 5F A0
```

Esto decodifica como instrucciones SH-4:
- `D202` → `MOV.L @(PC+8), R2` — Carga constante desde pool de literales
- `9302` → `MOV.W @(PC+4), R3` — Carga constante de 16 bits
- `2232` → `MOV.L R3, @R2` — Almacena en memoria

---

## 6. Strings del Debug Encontrados en RAM

Las siguientes strings del menú debug del juego fueron verificadas en la RAM:

| String | EPR Offset | RAM Offset | SH-4 Address |
|--------|-----------|-----------|-------------|
| MUTEKI | `0x100050` | `0x10FF50` | `0x8C10FF50` |
| No Life | `0x100060` | `0x10FF60` | `0x8C10FF60` |
| Death | `0x100070` | `0x10FF70` | `0x8C10FF70` |
| Rect | `0x100100` | `0x110000` | `0x8C110000` |
| Debug Menu | `0x10044C` | `0x11034C` | `0x8C11034C` |
| sx_System | `0x103A0D` | `0x11390D` | `0x8C11390D` |

---

## 7. Direcciones de Memoria Verificadas

### Direcciones principales (RAM offsets)

| Dato | PS2 (NTSC-U) | Atomiswave | Estado |
|------|:--------:|:----------:|--------|
| Camera position | `0x008A9660` | `0x27CAA8` | ✅ Verificado |
| Team P1 | `0x008A9690` | `0x27CB50` | ✅ Verificado |
| Team P2 | `0x008A98D8` | `0x27CD48` | ✅ Verificado |
| Player table | `0x008A26E0` | **N/A** | ✅ No existe en AW |

**Nota importante:** En Atomiswave **no existe** un `playerTable` (array plano de punteros a jugadores). En su lugar, cada team struct contiene punteros a entries del object pool.

### Acceso a structs de jugadores (entry chain)

A diferencia de PS2 que usa `playerTable.p[side][team.point]` para obtener directamente la dirección del player struct, Atomiswave requiere una cadena de indirección:

```
team.point (byte @ team+0x003)
  → team.entries[point] (SH-4 ptr @ team+0x144 + point*4)
    → entry.data (SH-4 ptr @ entry+0x10)
      → player_struct = entry.data - 0x614
```

**Fórmula:** `player_struct = entry.data_ptr - 0x614`

El offset 0x614 surge de la estructura del object pool:
- Stride de allocación: 0xE04
- Offset del data pointer dentro de la allocación: 0x7F0
- `0xE04 - 0x7F0 = 0x614`

### Object pool entries

Los entries son nodos de 0x34 bytes en una lista doblemente enlazada:

| Offset | Tipo | Descripción |
|--------|------|-------------|
| +0x04 | byte | Tipo (0x35 = player/projectile) |
| +0x08 | ptr | VTable |
| +0x10 | ptr | Data pointer (SH-4 address) |
| +0x14 | ptr | Next entry |
| +0x18 | ptr | Prev entry |

### Diferencias con PS2

| Aspecto | PS2 | Atomiswave |
|---------|-----|------------|
| Team spacing | 0x248 | 0x1F8 |
| Team struct size | ~0x242 bytes | 0x1F8 bytes |
| Player access | playerTable flat array | Entry chain (3 indirections) |
| Player struct offsets | — | Idénticos a PS2 |
| Camera struct | — | Idéntico a PS2 |
| Hitbox struct | — | Idéntico a PS2 |
| playerExtra.charID | +0x000 | +0x001 (shifted by 1 byte) |
| playerExtra size | 0x20 | 0x20 (same) |
| team.entries | N/A | +0x144 (3 × SH-4 pointers) |
| team.point | +0x003 | +0x003 (same) |
| team.super | +0x038 | +0x038 (same) |
| team.playerExtra | +0x150 | +0x150 (same) |

### Estructuras verificadas

| Estructura | Tamaño | Estado |
|-----------|--------|--------|
| player | ~0x584 bytes | ✅ Idéntico a PS2 |
| team | 0x1F8 bytes | ✅ Verificado (menor que PS2) |
| hitbox | 10 bytes | ✅ Idéntico a PS2 |
| camera | 8 bytes | ✅ Idéntico a PS2 |
| playerExtra | 0x20 bytes | ✅ Verificado (charID shifted) |

---

## 8. Archivos Implementados

| Archivo | Descripción |
|---------|-------------|
| `lua/game/flycast/common.lua` | Clase base Flycast con descubrimiento de RAM |
| `lua/game/flycast/kof_xi/game.lua` | Módulo KOF XI Atomiswave (pipeline de captura completo) |
| `lua/game/flycast/kof_xi/types.lua` | Definiciones de structs (verificadas contra RAM en vivo) |
| `lua/game/flycast/kof_xi/boxtypes.lua` | Tabla de tipos de hitbox (misma que PS2) |
| `lua/game/flycast/kof_xi/roster.lua` | Roster de personajes (mismo que PS2) |
| `lua/detectgame.lua` | Modificado: agregada entrada `FlycastGame` para detección |
| `lua/winprocess.lua` | Modificado: agregados `VirtualQueryEx` y `scanMemory()` |

---

## 9. Arquitectura de Soporte Flycast

```
Game_Common (base: ventanas + lectura de memoria)
    ↓
KOF_Common (serie KOF: facing, rendering de hitboxes, config)
    ↓
KOF_XI_AW (game-specific: direcciones de memoria Atomiswave)
    └── usa Flycast_Common para descubrimiento de RAM

Flycast_Common (módulo auxiliar, no herencia directa)
    ├── findRAMBase(): escanea patrones en memoria del proceso
    ├── SH4_RAM_SIZE: 16 MB (0x1000000)
    └── ramRegionFilter(): filtra regiones MEM_MAPPED de 16 MB
```

### Flujo de inicialización

```
1. detectgame.lua → match "Flycast" en título de ventana
2. KOF_XI_AW:new() → constructor con gameHandle
3. Flycast_Common:findRAMBase() → escanea "MUTEKI" en regiones de 16 MB
4. KOF_XI_AW:verifyGame() → verifica strings "sx_System", "ADELHIDE"
5. KOF_XI_AW:dumpRAMInfo() → imprime info de diagnóstico
6. Si direcciones configuradas → pipeline de captura activo
   Si no → overlay vacío, mensaje de advertencia
```

---

## 10. Próximos Pasos

1. **Buscar direcciones de player/camera/team** usando scanning en vivo durante gameplay
2. **Verificar struct layouts** comparando valores leídos con lo esperado
3. **Implementar logger/memlogger** para la versión Atomiswave
4. **Verificar boxtypes** si los IDs de hitbox difieren del PS2
5. **Probar con múltiples juegos Atomiswave** para generalizar la detección
