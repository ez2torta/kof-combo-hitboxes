# Análisis de Snapshots de Memoria — KOF XI Atomiswave (Flycast)

**Fecha**: 2026-04-08  
**Snapshots analizados**: 8  
**Fuentes verificadas**: Lua types.lua, game.lua, frame_capture.lua  

---

## 1. Inventario de Snapshots

| # | Nombre | Timestamp | Fase del juego | Datos válidos |
|---|--------|-----------|----------------|---------------|
| 1 | `titulo` | 19:17:57 | Pantalla de título | Solo MUTEKI |
| 2 | `how_to_play` | 19:18:06 | Pantalla "How to Play" | Solo MUTEKI |
| 3 | `char_select_ash` | 19:18:30 | Selección de personaje (Ash) | Solo MUTEKI |
| 4 | `char_select_kyo_16_seconds` | 19:18:41 | Selección (Kyo, 16s después) | Solo MUTEKI |
| 5 | `leader_select_ash` | 19:19:03 | Selección de líder | charIDs asignados (P1+P2) |
| 6 | `order_select_ash_leader` | 19:19:19 | Selección de orden | P2 teamPos asignados |
| 7 | `loading_first_stage` | 19:19:51 | Loading (stage 1) | **Equipos completos + cámara** |
| 8 | `loading_first_stage_2` | 19:20:04 | Loading (13s después) | **Equipos + cámara diferente** |

### Observación clave

Los structs de equipo (`team`) solo se populan completamente a partir de la fase de **loading**. Durante selección de personajes, los campos charID y teamPosition están en `0xFF`. La cámara (`restrictor = 1.0`) solo se activa en loading.

---

## 2. Verificación de Structs — Comparación Lua vs RAM Real

### 2.1 Camera (CONFIRMADO)

| Campo | Offset | Tipo | Loading 1 | Loading 2 | Lua |
|-------|--------|------|-----------|-----------|-----|
| posX | `0x27CAA8` | s16 | 195 | 244 | `coordPair position` ✓ |
| posY | `0x27CAAA` | s16 | 82 | 103 | (parte de coordPair) ✓ |
| restrictor | `0x27CAAC` | float | 1.0 | 1.0 | `float restrictor` ✓ |

El delta de cámara entre loading_1 y loading_2 (posX: 195→244, posY: 82→103) corresponde al paneo de cámara de la intro del stage.

**Datos adicionales** pre-cámara en `0x27CA80`:
```
Loading 1: 52 00 12 02 E3 00 23 03  ← posiblemente otra cámara o bounds
Loading 2: 67 00 27 02 14 01 54 03  ← valores cambian en sincronía
```
Estos 8 bytes en `0x27CA80` parecen ser un **segundo set de coordenadas de cámara** o bounds del viewport. No están definidos en el Lua actual.

### 2.2 Team Struct (CONFIRMADO)

**Dirección base**: P1 = `0x27CB50`, P2 = `0x27CD48` (spacing = `0x1F8` = 504 bytes) ✓

| Campo | Offset | Tipo | Loading (P1) | Lua |
|-------|--------|------|-------------|-----|
| leader | +0x001 | u8 | 0 (=Ash) | ✓ |
| point | +0x003 | u8 | 0 | ✓ |
| comboCounter | +0x007 | u8 | 0 | ✓ |
| comboCounter2 | +0x008 | u8 | 0 | ✓ (confirmado en Lua) |
| super | +0x038 | u32 | 0 | ✓ |
| skillStock | +0x03C | u32 | 0 | ✓ |
| entries[3] | +0x144 | ptr×3 | **NULL ×3** | ✓ (sin player structs durante loading) |
| playerExtra[3] | +0x150 | 0x20×3 | **Populados** | ✓ |

#### Entries NULL durante loading

Esto es crítico: durante la fase de loading, los punteros `entries[3]` en +0x144 son todos NULL. Esto significa que los **player structs no existen aún** — solo se crean cuando empieza la pelea real. **Se necesita un snapshot EN PELEA para verificar los offsets del player struct.**

#### Campos no documentados en team struct

| Offset | Valor en loading | Cambio cronológico | Interpretación |
|--------|-----------------|-------------------|---------------|
| +0x00D | 0x01 | titulo(0)→how_to_play(1), se queda en 1 | Flag "juego inicializado" |
| +0x014 | 0x03 | titulo(1)→..→loading(3) | Número de personajes seleccionados |
| +0x034 (u16) | 0x07C3 | **Monotónicamente creciente** | **Contador global de ticks** |
| +0x044 (u32) | 0x01A5E47C | Cambia cada snapshot | Posible RNG seed o timer alternativo |
| +0x07C-07E | 01 02 00 (P1) | Se pueblan en loading | Orden de entrada (entry→charID mapping?) |
| +0x0A4 | 0x0B | Incrementa en fases de selección | Contador de sub-fase de selección |
| +0x1F4 (u16) | 0x0717 | Cambia cada snapshot | Segundo contador/timer |

#### Contador global (+0x034 como u16 LE)

| Snapshot | Valor u16 | Delta | Tiempo real | ~Ticks/seg |
|----------|-----------|-------|-------------|------------|
| titulo | 0x00D0 | — | — | — |
| how_to_play | 0x0142 | 114 | ~9s | ~13 |
| char_select_ash | 0x0227 | 229 | ~24s | ~10 |
| char_select_kyo_16s | 0x0347 | 288 | ~11s | ~26 |
| leader_select | 0x0510 | 457 | ~22s | ~21 |
| order_select | 0x06E0 | 464 | ~16s | ~29 |
| loading_first_stage | 0x07C3 | 227 | ~32s | ~7 |
| loading_first_stage_2 | 0x082F | 108 | ~13s | ~8 |

La tasa varía (~7-29 ticks/s), probablemente porque el rate depende de la carga de trabajo de la CPU emulada. No corresponde 1:1 con frames de video.

### 2.3 PlayerExtra (CONFIRMADO)

Cada playerExtra es 0x20 bytes, 3 por equipo en +0x150.

Hex verificado del player Ash (P1, slot 0) durante loading:
```
27CCA0: 00 00 00 00 00 00 00 FF 00 00 00 00 FF FF FF FF
27CCB0: 02 FF 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

| Campo | Offset | Tipo | Valor | Lua | Status |
|-------|--------|------|-------|-----|--------|
| unknown01 | +0x000 | u8 | 0x00 | ✓ | |
| charID | +0x001 | u8 | 0x00 (Ash) | ✓ | **CONFIRMADO** |
| charID2 | +0x004 | u8 | 0x00 (Ash) | ✓ | Duplicado confirmado |
| marker | +0x007 | u8 | 0xFF | ✓ | |
| health | +0x008 | s16 | 0 | ✓ | 0 durante loading |
| visibleHealth | +0x00A | s16 | 0 | ✓ | |
| maxHealth | +0x00C | s16 | -1 (0xFFFF) | ✓ | Sin inicializar durante loading |
| maxHealth2 | +0x00E | s16 | -1 (0xFFFF) | ✓ | |
| teamPosition | +0x010 | u8 | 2 | ✓ | **CONFIRMADO** |
| marker2 | +0x011 | u8 | 0xFF | ✓ | |

#### Equipos seleccionados

**P1** (usuario eligió Ash como líder):

| Slot | charID | Personaje | teamPos | Orden de juego |
|------|--------|-----------|---------|---------------|
| 0 | 0x00 | Ash | 2 | **3ro** |
| 1 | 0x01 | Oswald | 0 | **1ro** |
| 2 | 0x02 | Shen Woo | 1 | **2do** |

**P2** (CPU, equipo default):

| Slot | charID | Personaje | teamPos | Orden de juego |
|------|--------|-----------|---------|---------------|
| 0 | 0x00 | Ash | 0 | **1ro** |
| 1 | 0x01 | Oswald | 1 | **2do** |
| 2 | 0x02 | Shen Woo | 2 | **3ro** |

**Nota**: `point = 0` en ambos equipos durante loading. El campo `point` indica cuál teamPosition está activo en combate (0 = el primer personaje en jugar). P1 Oswald tiene teamPos=0, así que Oswald juega primero.

---

## 3. Progresión de Estado del Juego

### 3.1 Timeline de activación de datos

```
titulo           → MUTEKI OK, todo lo demás vacío
  ↓ +0x00D team flag: 0→1 (inicialización)
how_to_play      → Team flag activado
  ↓ (sin cambios en charIDs)
char_select_ash  → +0x014 oscila (selección activa)
  ↓
char_select_kyo  → Solo +0x034 (timer) y +0x044 (seed) cambian
  ↓ charIDs y teamPos SE ASIGNAN AQUÍ
leader_select    → P1 y P2 playerExtra reciben charID/teamPos
                   P1: +0x150 (0xFF→0x00), +0x154 (0xFF→0x00)
                   P2: similar pero con teamPos default (0,1,2)
  ↓ P2 order se asigna
order_select     → P2 +0x07C-07E = 00 01 02 (orden de entrada CPU)
                   P2 +0x160/180/1A0 = 0,1,2 (mapping)
  ↓ P1 charIDs completos + CÁMARA ACTIVA
loading_first    → Camera (195,82) restrictor=1.0
                   P1 playerExtra: charIDs + teamPos finales
                   Entries[3] TODAVÍA NULL
  ↓ Camera se mueve (intro pan)
loading_first_2  → Camera (244,103) — desplazamiento de +49,+21
```

### 3.2 Bytes que distinguen fases del juego

El byte `0x27CB64` (team+0x014) muestra una progresión interesante:

| Fase | Valor | Posible significado |
|------|-------|-------------------|
| titulo | 0x01 | — |
| how_to_play | 0x00 | Menú reseteado |
| char_select | 0x01 | 1er personaje seleccionado |
| leader_select | 0x02 | 2 personajes confirmados |
| order_select | 0x02 | Mismo (eligiendo orden) |
| loading | 0x03 | **3 personajes confirmados** |

Esto sugiere que +0x014 es un **contador de personajes seleccionados** (o fase de selección completada). El salto de 0→1→2→3 correlaciona perfectamente con el flujo de selección.

---

## 4. Mapa de Memoria (Heatmap)

Basado en la comparación de bloques de 64KB entre snapshots:

| Rango | Contenido | Volatilidad | Notas |
|-------|-----------|-------------|-------|
| `0x000000-0x030000` | Vectors SH-4 / BIOS | 0-1.4% | Cambios mínimos (interrupts, stack?) |
| `0x100000-0x130000` | **Código ejecutable** | <0.1% | MUTEKI en 0x10FF50, Debug Menu en 0x11034C |
| `0x130000-0x140000` | Zona mixta | 0.2-0.5% | Transición código→datos |
| `0x140000-0x160000` | **Object pool metadata** | 4-10% | SH-4 pointers, strings "SHOT" |
| `0x180000-0x1A0000` | **Scene objects dinámicos** | 0.4-8% | Strings como "STAGE PANEL" aparecen durante loading |
| `0x190000` | Scene manager? | 20-26% | Alta volatilidad, contiene nombres de escena |
| `0x200000-0x270000` | Object pool (instancias) | ~0% | **Vacío durante loading** — aquí viviran los player structs |
| `0x270000-0x280000` | **Game state central** | 16% | Camera (0x27CAA8), Teams (0x27CB50, 0x27CD48) |
| `0x280000-0x2E0000` | Heap / game data | 22-99% | Altamente volátil entre estados |
| `0x300000-0x3A0000` | **Decomp buffer 1** | ~0.3% | Contenido sparse (~2500 bytes activos) |
| `0x400000-0x500000` | Decomp buffer 2 | 0% | Sin uso detectado |
| `0x500000-0x600000` | **Decomp buffer 3** | 0% normal, actividad puntual | 2 eventos lztrack con 79-106 páginas |
| `0xA80000-0xFF0000` | **VRAM / texturas** | 78-100% | Cambia masivamente entre fases del juego |

### Hallazgos del mapa

1. **0x190000 contiene scene descriptors**: Durante loading aparece el string `"STAGE PANEL"` precedido de punteros SH-4 (`0x8C19...`). Esto confirma que el engine usa un sistema de escenas con descriptores textuales.

2. **0x200000 está vacío durante loading**: Esta es la zona donde deberían estar los player structs (el Lua los resuelve a partir de entries[]→entry+0x10→data-0x614). Su vacío confirma que los objetos de juego se instancian al iniciar la pelea, no durante loading.

3. **VRAM en 0xA80000+**: La zona alta muestra patrones interesantes — `how_to_play` y `titulo` comparten exactamente los mismos datos en 0xA80000-0xBF0000, pero difieren de char_select. Esto refleja que comparten los mismos assets gráficos del menú principal.

---

## 5. Análisis LZ Track

### 5.1 Resumen de sesión

- **122 eventos** detectados en ~47 segundos (19:21:14 a 19:22:01)
- **Intervalo de poll**: 0.2s
- **Regiones activas**: decomp_buf_1 (0x300000-0x400000) = 119 eventos, decomp_buf_3 (0x500000-0x600000) = 3 eventos
- **decomp_buf_2 (0x400000-0x500000)**: Sin actividad

### 5.2 Patrones detectados

| Patrón | Eventos | Rango RAM | Header | Interpretación |
|--------|---------|-----------|--------|---------------|
| Ruido de fondo | ~100 | 0x359000-0x38E000 | NULL | Cambios mínimos en páginas sparse |
| CRI audio | #87-92 | 0x307000-0x38E000 | `0x49524329` = ")CRI" | **Descompresión de audio CRI Middleware** |
| Post-CRI | #93 | 0x308000-0x38E000 | `0xFE39FD83` | Datos descomprimidos (no LZSS) |
| Gráficos buf_3 | #55,57 | 0x549000-0x600000 | NULL | Carga masiva de texturas (79-106 páginas) |
| Burst buf_3 | #81 | 0x547000-0x54B000 | NULL | Micro-actualización de 4 páginas |

### 5.3 Diagnóstico de problemas del LZ Track

**¿Por qué no detecta descompresiones LZSS?**

1. **Granularidad**: El hasheo por páginas de 4KB detecta *cualquier* cambio, no específicamente LZSS. La mayoría de los 122 eventos son cambios de metadata (contadores, flags) en páginas del buffer, no descompresiones enteras.

2. **Buffer sparse**: Solo ~2500 de 1,048,576 bytes son non-zero en el buffer. Los cambios detectados son actualizaciones incremental de pocos bytes en páginas que ya existían.

3. **Timing**: Las descompresiones LZSS probablemente ocurren en un solo frame y completan antes del siguiente poll (0.2s). El tracker captura el "después" pero no puede distinguir LZSS de otros tipos de escritura.

4. **Hallazgo real**: La detección de ")CRI" en eventos #87-92 es valiosa — confirma que el juego usa **CRI Middleware** para audio, y esta descompresión sí ocurre en el rango 0x307000+ del buffer.

### 5.4 Mejoras propuestas para LZ Track

- **Reducir intervalo** a 50ms para capturar eventos individuales
- **Buscar LZSS headers** (4 bytes buffer_size + flag bytes con patrón LSB-first) en los .bin capturados en vez de depender de page hashes
- **Monitorear 0x190000-0x1A0000** porque los scene descriptors con SH-4 pointers sugieren actividad de loading de assets
- **Filtrar ruido**: ignorar cambios de <100 bytes por poll (son contadores, no decompressions)

---

## 6. Discrepancias Encontradas

### 6.1 analysis_utils.py vs Lua (verificado)

| Campo | analysis_utils.py | Lua types.lua | Correcto |
|-------|-------------------|--------------|----------|
| charID offset en playerExtra | +0x001 | +0x001 | ✓ Match |
| health offset | +0x008 | +0x008 | ✓ Match |
| teamPosition offset | +0x010 | +0x010 | ✓ Match |
| playerExtra size | 0x20 | 0x20 | ✓ Match |
| team size | 0x1F8 | 0x1F8 | ✓ Match |

### 6.2 Campos sin verificar (necesitan snapshot EN PELEA)

Estos campos están definidos en analysis_utils.py pero NO pudieron verificarse porque entries[] son NULL durante loading:

| Campo | Offset en player | Tamaño en utils | Tamaño probable | Verificable |
|-------|-----------------|-----------------|-----------------|-------------|
| actionID | +0x0EC | **1 byte (u8)** | ¿u8 o u16? | Necesita fight snapshot |
| prevActionID | +0x0EE | **1 byte (u8)** | ¿u8 o u16? | Necesita fight snapshot |
| animFrameIndex | +0x2A4 | **1 byte (u8)** | ¿u8 o u16? | Necesita fight snapshot |
| hitbox layout | +0x314 | 10 bytes×7 | 10 bytes×7 | Necesita fight snapshot |
| hitboxesActive | +0x39E | **1 byte** | ¿u8 o bitmask u16? | Necesita fight snapshot |
| stunTimer | +0x582 | s16 | s16 | Necesita fight snapshot |

**Nota**: frame_capture.lua lee `playerBuf[0x0EC]` como un byte individual. Pero si actionID fuera u16, byte 0x0ED podría contener bits altos. Los snapshots actuales no permiten resolver esto.

---

## 7. Acciones Siguientes

### Prioridad ALTA: Snapshot en pelea
Se necesitan al mínimo 2 snapshots más:
1. **Pelea en idle** (ambos parados, round recién empezado) — para verificar posiciones, HP, facing, y que entries[] NO sean NULL
2. **Pelea con movimiento** (P1 saltando o golpeando) — para ver actionID, animFrameIndex, hitboxes activos, velocity

Esto desbloquea la verificación del **player struct completo** (0x584 bytes), que es el componente central del hitbox viewer.

### Prioridad MEDIA: Mejorar lztrack
- Reducir poll a 50ms
- Añadir detección de headers LZSS (4-byte size + flag bytes)
- Monitorear zona 0x190000 para scene loading

### Prioridad BAJA: Explorar campos no documentados
- `team+0x034` (u16): confirmar si es global tick counter
- `0x27CA80` (8 bytes): segundo set de coordenadas de cámara
- `team+0x07C-07E`: mapping de entrada → charID
- Zona 0x280000-0x2E0000: heap structures durante pelea

---

## Apéndice: Hex Dumps de Referencia

### A. Team P1 completo durante loading (0x27CB50, 504 bytes)

```
27CB50: 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00
27CB60: 00 00 00 00 03 03 02 03 01 00 00 00 01 00 00 00
27CB70: 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
27CB80: 00 00 00 00 C3 07 00 00 00 00 00 00 00 00 00 00
27CB90: 00 00 00 00 7C E4 A5 01 01 00 00 00 00 00 00 00
27CBA0: 00 00 00 00 00 00 00 00 FF 03 FF 00 00 00 01 00
27CBB0: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
         ... (zeros hasta +0x0C0) ...
         ... (projectiles[16] = NULL ×16) ...
         ... (entries[3] = NULL ×3) ...
27CCA0: 00 00 00 00 00 00 00 FF 00 00 00 00 FF FF FF FF  ← playerExtra[0] Ash
27CCB0: 02 FF 00 00 00 00 00 00 00 00 00 00 00 00 00 00
27CCC0: 00 01 00 00 01 00 00 FF 00 00 00 00 FF FF FF FF  ← playerExtra[1] Oswald
27CCD0: 00 FF 00 00 00 00 00 00 00 00 00 00 00 00 00 00
27CCE0: 00 02 00 00 02 00 00 FF 00 00 00 00 FF FF FF FF  ← playerExtra[2] Shen Woo
27CCF0: 01 FF 00 00 00 00 00 00 00 00 00 00 00 00 00 00
         ... (zeros y 17 07 al final) ...
```

### B. Camera context durante loading (0x27CA70-0x27CAD0)

```
27CA70: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 36 00  ← ¿timer/flags?
27CA80: 52 00 12 02 E3 00 23 03 00 00 00 00 00 00 00 00  ← ¿bounds de cámara?
27CA90: 00 00 00 00 00 00 FF FF 00 00 00 00 00 00 00 00  ← FF FF sentinel
27CAA0: 00 00 00 00 00 00 00 00 C3 00 52 00 00 00 80 3F  ← camera: (195,82) 1.0
27CAB0: 00 00 00 00 52 00 32 02 C3 00 43 03 00 00 00 00  ← ¿datos extra de cámara?
27CAC0: 00 00 00 00 C0 01 E0 00 00 00 00 00 00 00 00 00  ← 0x1C0, 0xE0 = ¿dims?
```

Valores en `0x27CAC0`: `C0 01` = 448, `E0 00` = 224. Esto podría ser el **tamaño de la viewport** de la cámara (448×224 pixels).
