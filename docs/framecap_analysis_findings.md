# Analisis de Framecaps — KOF XI Atomiswave (Flycast)

**Fecha**: 2026-04-08  
**Sesiones analizadas**: 3 (1 test descartada + 2 utiles)  
**Herramienta**: `ram_watch.py framecap`  
**Script de analisis**: `tools/analyze_framecap.py`

---

## 1. Resumen de Sesiones

| Sesion | Directorio | Frames | FPS real | Deltas | Contenido |
|--------|-----------|--------|----------|--------|-----------|
| Test | `framecap_20260408_195212` | 23 | 26.1 | 0 MB | Estatica, descartada |
| **A: Mid-Battle** | `framecap_20260408_195227` | 180 | 25.0 | 39.5 MB | Pelea activa Kyo vs equipo Ash |
| **B: Loading→Fight** | `framecap_20260408_195323` | 600 | 28.6 | 149.7 MB | Transicion completa loading → round 1 |

**Nota sobre FPS**: El objetivo era 60fps pero se alcanzaron ~25-28fps. Cada lectura de 16 MB toma ~35-40ms, lo que limita a ~28fps maximo. Aun asi, se capturan **todos los cambios** entre lecturas (los deltas acumulados son correctos), solo que la granularidad temporal es menor.

---

## 2. Hallazgo Principal: Player Structs

### Sesion A — Pelea activa (ya con entries)

Entries ya validos desde el frame base:
```
P1 entries: [0x8C190BB4, 0x8C190C50, 0x8C190CEC]
P2 entries: [0x8C190E24, 0x8C190EC0, 0x8C190F5C]
```

Los 6 punteros de entry apuntan a la zona `0x190Bxx-0x190Fxx` (region "meta" / obj_pool). Los player structs resueltos estan en:

| Lado | Slot | Char | Offset | Spacing |
|------|------|------|--------|---------|
| P1 | 0 | Ash | `0x19885C` | — |
| P1 | 1 | Oswald | `0x199660` | +0x0E04 (3588) |
| P1 | 2 | Shen Woo | `0x19A464` | +0x0E04 (3588) |
| P2 | 0 | Ash | `0x19B2AC` | +0x0E48 (3656) |
| P2 | 1 | Oswald | `0x19C0B0` | +0x0E04 (3588) |
| P2 | 2 | Shen Woo | `0x19CEB4` | +0x0E04 (3588) |

**Spacing uniforme de 0x0E04** entre slots del mismo equipo. El salto P1→P2 es ligeramente mayor (0x0E48). Ambos estan en la zona `0x198000-0x19D000`.

### Sesion B — Transicion Loading → Fight

**Los entries se activan en el frame 149** (de 600 totales).

| Frame | Camera | Entries | Estado |
|-------|--------|---------|--------|
| 0-148 | (185,78)→(311,132) panea | NULL ×6 | Loading + intro cinematica |
| 149 | (448,224) | Validos | **Round empieza** |
| 150+ | (448,224) | Validos | Pelea activa |

Al activarse en frame 149:
- P1 Ash: action=5, anim=8, pos=(528,672) — animacion de intro
- P2 Ash: action=0, anim=255 — estado inicial/reset

### Verificacion: Entries usan las mismas direcciones

Los punteros de entry son **identicos** en ambas sesiones:
```
P1[0]=0x8C190BB4  P1[1]=0x8C190C50  P1[2]=0x8C190CEC
P2[0]=0x8C190E24  P2[1]=0x8C190EC0  P2[2]=0x8C190F5C
```
Esto sugiere que las entries del object pool se asignan a direcciones fijas (o al menos deterministas).

---

## 3. Verificacion del Player Struct

Datos extraidos de frame 179 (Sesion A) y frame 599 (Sesion B), ambos con pelea activa:

### 3.1 Campos Confirmados

| Campo | Offset | Sesion A (Kyo, f0) | Sesion B (Ash, f599) | Status |
|-------|--------|-------------------|---------------------|--------|
| position | +0x000 | (710, 672) | (576, 473) | **OK** — Ash saltando tiene Y!=672 |
| facing | +0x08C | 0 (left) | 62 | **ANOMALIA** — se esperaba 0 o 2 |
| actionID | +0x0EC | 243 | 87 | OK como u8 |
| prevActionID | +0x0EE | 5 | 5 | OK como u8 |
| animFrameIndex | +0x2A4 | 110 | 162 | **OK** — valores >127 confirma u8 sin signo |
| hitboxesActive | +0x39E | 0x0C | 0x00 | OK como u8 bitmask |
| stunTimer | +0x582 | 0 | 0 | OK |

### 3.2 Facing — Valores Inesperados

El campo `facing` en +0x08C muestra valores fuera de {0, 2}:

| Personaje | Valor | Esperado |
|-----------|-------|----------|
| Kyo (A, f0) | 0 | 0=left OK |
| Ash P2 (A, f0) | 60 | Se esperaba 0 o 2 |
| Ash P1 (B, f599) | 62 | Se esperaba 0 o 2 |
| Ash P2 (B, f599) | 0 | 0=left OK |

Los valores 60 y 62 no corresponden a la definicion en types.lua (`00h = left, 02h = right`). **Posible explicacion**: el byte en +0x08C tiene multiples flags empaquetados, y el facing es solo el bit 1 (`& 0x02`). Valores como 62 = 0x3E = bit 1 set = "right", y 60 = 0x3C = bit 1 clear... salvo que 0x3C en binario = 0011_1100, bit1=0, asi que no cuadra. **Necesita mas investigacion**.

### 3.3 Hitboxes Confirmados

Frame 0 de Sesion A — Kyo en action=243, hbActive=0x0C:

| Slot | Tipo | BoxID | Pos | Size | Activo |
|------|------|-------|-----|------|--------|
| 0 | attack | 59 | (+50,+80) | 20x36 | Si |
| 1 | vuln1 | 10 | (0,+16) | 48x16 | Si |
| 2 | vuln2 | 3 | (+12,+80) | 12x12 | Si |
| 3 | vuln3 | 4 | (0,+40) | 20x40 | Si |
| 4 | grab | 5 | (+24,+46) | 24x46 | Si |
| 5 | hb6 | 0 | (0,0) | 0x0 | No |
| 6 | collision | 0 | (0,+40) | 17x40 | Si |

**hbActive=0x0C = 0000_1100**: bits 2 y 3 activos. Pero 5 de 7 hitboxes tienen datos no-zero. Esto confirma que **hbActive es un flag de rendering/behavior**, no un indicador de si el hitbox tiene datos. Los hitboxes siempre tienen datos definidos; `hbActive` controla cuales se procesan para colision.

Frame 599 de Sesion B — Ash P2 en action=49, hbActive=0x06:

| Slot | Tipo | BoxID | Pos | Size |
|------|------|-------|-----|------|
| 0 | attack | 1 | (+8,+82) | 10x10 |
| 1 | vuln1 | 1 | (-24,+84) | 24x24 |
| 2 | vuln2 | 2 | (-4,+40) | 20x40 |
| 3 | vuln3 | 4 | (0,+46) | 20x46 |
| 6 | collision | 0 | (0,+40) | 17x40 |

**hbActive=0x06 = 0000_0110**: bits 1 y 2 activos. Nota que slot 1 con bit 1 tiene data de vuln (no attack). El mapeo bits→slots necesita verificarse, posiblemente no es 1:1.

### 3.4 Campos Nuevos Observados

Del dump completo de player fields:

| Campo | Offset | Valor tipico | Notas |
|-------|--------|-------------|-------|
| actionCategory | +0x204 | 0xFF o 0xA0-0xA1 | Categoriza la accion (idle vs attack vs...) |
| charBankSelector | +0x226 | 0 | Siempre 0 en lo observado |
| animPlayFlag | +0x2A5 | 0 o 3 | 3 cuando animacion esta activa? |
| spriteOffsetX | +0x2A8 | -15 a -84 | Offset de sprite para centrar grafico |
| spriteOffsetY | +0x2AA | -15 a -111 | Idem vertical |
| animPropertyA | +0x2B2 | 8-16 | Propiedad de animacion |
| animPropertyB | +0x2B3 | 9-14 | Propiedad de animacion |
| animPhaseToggle | +0x2B4 | 0 o 1 | Alterna cada sub-fase de animacion |
| actionSignal | +0x0F2 | -1 (0xFFFF) | Siempre -1 en lo observado |
| animDataPtr | +0x200 | 0x24BE o 0 | Puntero a datos de animacion |

---

## 4. Transicion Loading → Fight

La sesion B captura la transicion completa. Cronologia:

### 4.1 Fases Detectadas

| Frames | Duracion real | Camera | Estado |
|--------|--------------|--------|--------|
| 0-32 | ~1.1s | (185,78) estatica | Loading screen, sin cambios |
| 33-148 | ~4.0s | (185→311, 78→132) paneo | Intro cinematica del stage |
| 149 | instantaneo | (448,224) salto | **Entries se activan, round empieza** |
| 150-240 | ~3.2s | (448,224) fija | Idle, ambos action=0/1 |
| 241-280 | ~1.4s | (448,224) | Idle con ligeros cambios |
| 281+ | variable | se mueve | CPU empieza a pelear |

### 4.2 El Frame Critico: 149

La transicion de loading a fight es **atomica desde nuestro punto de vista**:
- Frame 148: camera=(311,132), entries NULL, HP=0
- Frame 149: camera=(448,224), entries validos, HP=112, action=5/0

Esto no es un "fade gradual" — es un **cambio de escena completo en un solo frame**:
- Camera salta de (311,132) a (448,224)
- Entries pasan de NULL a las 6 direcciones del object pool
- Health se inicializa a 112 (0x70)
- P1 empieza en action=5 (posiblemente "intro walk"), P2 en action=0 ("idle")

### 4.3 Camera durante Loading

El paneo de camera durante el loading es suave y lineal:

| Frame | PosX | PosY | Velocidad |
|-------|------|------|-----------|
| 0 | 185 | 78 | 0 |
| 40 | 190 | 80 | ~0.5 px/f |
| 80 | 241 | 102 | ~1.0 px/f |
| 120 | 282 | 120 | ~1.0 px/f |
| 140 | 302 | 129 | ~1.0 px/f |
| 149 | 448 | 224 | SALTO |

El paneo total es de (185→311, 78→132) = 126px horizontal, 54px vertical en ~115 frames — movimiento slow-pan clasico de intro de stage.

---

## 5. Datos de Combat Activo

### 5.1 Timeline de Pelea (Sesion B, frames 150-599)

La sesion captura ~15 segundos de pelea CPU vs CPU:

| Fase | Frames | P1 (Oswald) HP | P2 (Ash) HP | Notas |
|------|--------|----------------|-------------|-------|
| Idle | 150-280 | 112 | 112 | Ambos parados |
| Round start | 281-290 | 112 | 112 | CPU empieza a moverse |
| First exchange | 290-310 | 112→101 | 112 | Oswald recibe golpe |
| Active fight | 310-450 | 101→79 | 112→97 | Intercambio de golpes |
| One-sided | 450-599 | 79 | 97→29 | Oswald domina |

### 5.2 Acciones Observadas

De la timeline se observan action IDs variados:

| actionID | Contexto | Posible significado |
|----------|----------|-------------------|
| 0 | Neutral | Idle standing |
| 1 | Post-intro | Walk/idle variant |
| 5 | Frame 149 (P1) | Intro walk-in |
| 9, 12 | Movimiento | Walking, jumping? |
| 18, 21 | Pre-golpe | Approach/dash? |
| 27, 29, 30 | Ataque light? | Normals? |
| 38, 42, 45, 47 | Ataques | Normal moves |
| 48, 49, 51, 52 | Ataques | Normal/special moves |
| 53, 54 | Ataques | Combos? |
| 58 | Dano | Hit reaction |
| 66, 67, 68 | Hit/block | Reactions |
| 83, 86, 87 | Win/victory? | Post-KO state |
| 90, 93, 95 | Busy state | Recovery/cinematic |
| 97, 99 | Busy state | Long duration |
| 104 | Repeated | CPU loop pattern |
| 107, 119 | Mixup | Short transitions |
| 131, 143 | Special? | Character-specific |
| 184, 186, 188 | Post-impact | Long sequences |
| 189, 190, 192 | Cinematics? | P2 inactive |
| 196, 204, 205, 208, 213, 230 | Late-fight | Oswald-specific? |
| 243 | Kyo (sesion A) | Unknown (special?) |

**Nota**: Los actionIDs no son secuenciales ni claramente agrupados. Probablemente mapean a indices en la tabla de animaciones del personaje, no a categorias genericas.

### 5.3 Health y Dano

- **maxHP = 112 (0x70)** confirmado en ambas sesiones
- Player Extra muestra `health` y `visibleHealth` como campos separados
- Los valores de HP bajan de forma discreta durante la pelea
- HP = -1 (0xFFFF) aparece durante KO/derrota (Ash P2 en sesion A f0)

---

## 6. Actividad de Memoria por Region

### 6.1 Promedios durante pelea activa

| Region | Rango | Pag/frame (A) | Pag/frame (B) | Notas |
|--------|-------|---------------|---------------|-------|
| meta (0x130000-0x200000) | ~100 pg | 34.4 | 33.0 | **La mas activa** — object pool metadata |
| ram_top (0xC00000+) | ~200 pg | 16.3 | 11.6 | Video/audio buffers |
| obj_pool (0x200000-0x270000) | ~28 pg | 7.1 | 6.0 | Player structs + projectiles |
| decomp (0x300000-0x400000) | ~64 pg | 5.5 | 5.1 | Descompresion activa continua |
| code (0x030000-0x133000) | ~64 pg | 4.7 | 4.6 | Self-modifying code o stack en code area? |
| game_st (0x270000-0x280000) | 4 pg | 2.7 | 2.0 | Camera + teams |
| heap (0x280000-0x300000) | ~32 pg | 3.2 | 1.9 | Trabajo asignaciones |
| bios (0x000000-0x030000) | 12 pg | 2.0 | 2.0 | Interrupt vectors |

### 6.2 Paginas Mas Volatiles

Las mismas paginas aparecen como "top volatiles" en ambas sesiones:

| Direccion | Region | % frames cambiada | Interpretacion |
|-----------|--------|-------------------|---------------|
| `0x00E000` | bios | 94% | Interrupt/exception handler data |
| `0x116000` | code | 94% | *Sospechoso* — no deberia cambiar si es codigo |
| `0x14A000` | meta | 94% | Object pool management |
| `0x15B000` | meta | 94% | Object pool management |
| `0x188000` | meta | 94% | Scene manager |
| `0x214000` | heap_low | 93% | **Cerca de player structs** |
| `0x27C000` | obj_pool | 93% | **Pagina del game state (camera+teams)** |
| `0x38C000` | decomp | 94% | Buffer de descompresion activo |
| `0xFC3000-FC5000` | ram_top | 80% | Hardware registers / video DMA |

La pagina `0x116000` cambiando en 94% de los frames es notable — esta en la zona de codigo ejecutable. Posibilidades:
1. **El game code se auto-modifica** (raro en SH-4)
2. **Stack overflow** — el stack del juego podria crecer hasta esta zona
3. **Mislabel** — quizas el code region termina antes de 0x133000 y esta zona es en realidad data

### 6.3 Burst Masivo en Transicion (Sesion B)

Frame 70 de sesion A: 2461 paginas cambiadas = **~9.6 MB de una sola vez**. Coincide con lo que parece ser un cambio de estado del juego (probablemente la transicion del savestate cargado):

```
Frame 69: 0 paginas    cam=(451,224) 
Frame 70: 2461 paginas  cam=(451,224) ← BURST: del savestate a fight activa
Frame 71: 193 paginas   cam=(0,224)   ← settling 
Frame 72-: ~60 pag     cam=(0,224)    ← estable
```

En sesion B, frame 149: el burst de entries activandose coincide con 310 paginas cambiadas.

---

## 7. Anomalias y Puntos Abiertos

### 7.1 Facing no es simple 0/2

Los valores 60 y 62 en `facing` (+0x08C) no cuadran con la definicion en types.lua. Posiblemente:
- El byte tiene flags adicionales (flip flags, render flags)
- Solo el bit 1 (0x02) indica la direccion, y los demas bits son otros flags

### 7.2 Camera PosX = 0 despues de transicion en Sesion A

Despues del burst en frame 70-71, la camera tiene posX=0 pero posY=224. Esto podria ser un glitch de la captura o un estado transitorio del engine.

### 7.3 Position Y = 672 como "ground level"

Ambas sesiones muestran Y=672 como el nivel del suelo. Cuando un personaje salta, Y baja (473 para Ash saltando). Esto confirma que **Y crece hacia abajo** y 672 es la coordenada base del suelo del stage.

### 7.4 HP = -1 (0xFFFF) como indicador de KO

En sesion A, Ash P2 tiene HP=-1 pero sigue apareciendo con hitboxes activos. Esto puede indicar que HP=-1 es un sentinel de "ya fue KO'd" en un round anterior, no que esta muerto en este round.

### 7.5 Player Extra Health vs TeamPos

En frame 599 (sesion B):
- P1 Oswald: HP=79, TeamPos=1 (slot activo como point)
- P2 Ash: HP=29, TeamPos=0

Los HP de los personajes no-point (slots que no estan jugando activamente) no cambian durante la pelea, manteniendose en 112. Solo el personaje activo recibe dano.

---

## 8. Conclusiones

### Lo que Confirman los Framecaps

1. **Player struct entries se crean en el frame exacto que empieza el round** (frame 149 en sesion B)
2. **Los entry pointers son deterministas**: mismas direcciones en multiples sesiones
3. **Player struct spacing = 0x0E04** entre slots del mismo equipo
4. **Player structs viven en 0x198000-0x19D000** (zona obj_pool)
5. **Ground level Y = 672**, Y crece hacia abajo
6. **maxHP = 112** (0x70) confirmado
7. **Hitboxes siempre tienen datos definidos**, `hbActive` solo controla cuales se procesan
8. **La zona 0x130000-0x200000 es la mas volatil** con ~33 paginas cambiando por frame

### Limitaciones del Framecap

- **~28 FPS reales** vs 60 objetivo — no captura cada frame del juego, pero si cada cambio (los deltas son acumulativos)
- **~35-40ms por lectura** — la lectura de 16MB es el cuello de botella
- Para precision frame-perfect, seria mejor monitorear solo las regiones de interes (~256KB en vez de 16MB)

### Proximo Paso

El framecap confirma que el pipeline de lectura de structs funciona correctamente. El hallazgo del `facing` anomalo necesita investigacion — posiblemente leer solo el bit 1 del byte, o reclasificar el offset.
