# KOF XI Atomiswave — Análisis Profundo de Framecap

**Sesión analizada**: `framecap_20260408_201428`  
**Duración**: 4800 frames (~160 segundos reales, ~29.9 FPS)  
**Contenido**: 2 peleas completas (carga → pelea → win → carga → pelea → win)  
**Fecha**: 2026-04-08

---

## Resumen Ejecutivo

Análisis exhaustivo de un framecap de 4800 frames cubriendo dos peleas completas de KOF XI (Atomiswave/Flycast). Se descubrieron y corrigieron múltiples errores en la interpretación de la memoria del juego:

| Métrica | Resultado |
|---------|-----------|
| Escenas detectadas | 16 (loading/fight/win) |
| Quickshifts | 8 (7 P1 + 1 P2/CPU) |
| Combos (heurístico) | 31 (máximo: 15 hits) |
| KOs | 8 (4 por pelea) |
| Activaciones de hitbox | 78 |
| Acciones únicas con hitbox | 299 pares (char, action) |

---

## 1. Descubrimientos Críticos de Ingeniería Inversa

### 1.1 El campo `point` NO es un índice de slot directo

**El error**: Se asumía que `team.point` (offset +0x003) era un índice directo al slot del personaje activo (0, 1, o 2).

**La realidad**: `team.point` es un valor de `teamPosition`, no un índice de slot. El juego localiza al personaje activo así:

```
1. Leer team.point (ej. 0)
2. Buscar en playerExtra[0..2].teamPosition el que coincida con team.point
3. Ese slot contiene al personaje activo
```

**Evidencia**: El código Lua del proyecto lo confirma en `game.lua`:
```lua
for e = 0, 2 do
    if team.p[e].teamPosition == team.point then
        entryIdx = e; break
    end
end
```

**Impacto**: Sin esta corrección, el análisis reportaba al personaje equivocado como "point character" después de cada quickshift.

### 1.2 Action 87 = Estado "Benched/Standby"

**Descubrimiento**: `actionID = 87` no significa "idle". Significa que el personaje está **en la banca** (no está en pantalla). Los personajes no-activos del equipo permanecen en action=87 mientras no son el point character.

**Uso para detección de quickshifts**: El único slot con `action != 87` es el personaje activo (excepto durante intros/wins donde múltiples slots tienen `action != 87`).

**Acciones de quickshift observadas**:
| Action | Significado |
|--------|-------------|
| 83 | Tag-in (personaje entrando al campo) |
| 85 | Tag-out preparación |
| 86 | Tag-out (saliendo) |
| 64 | Entrada forzada (partner KO'd) |
| 110 | Ataque de entrada (tag attack) |

### 1.3 rotación de `teamPosition` durante quickshifts

Los valores de `teamPosition` de cada slot **rotan** cuando ocurre un quickshift. Los entry pointers (team+0x144) NO cambian — siempre apuntan a los mismos objetos. Lo que cambia es qué `teamPosition` tiene cada slot.

**Ejemplo de rotación (Pelea 1 - P1)**:

| Frame | Evento | s0 (Ash) tPos | s1 (Oswald) tPos | s2 (Shen Woo) tPos | Activo |
|-------|--------|:---:|:---:|:---:|--------|
| 186 | Round start | 2 | **0** | 1 | Oswald (s1) |
| 629 | QS Oswald→Shen | 2 | 1 | **0** | Shen Woo (s2) |
| 1050 | QS Shen→Oswald | 2 | **0** | 1 | Oswald (s1) |
| 1266 | Oswald KO → Ash | **0** | 2 | 1 | Ash (s0) |
| 1418 | QS Ash→Shen | 1 | 2 | **0** | Shen Woo (s2) |

El que tiene `teamPosition = team.point = 0` es el activo (marcado en **negrita**).

### 1.4 El struct de equipo P2 está parcialmente vacío

Los primeros ~0x40 bytes del team struct de P2 (base `0x27CD48`) son **todos cero** durante toda la sesión en modo 1P vs CPU.

| Offset | Campo esperado | Valor P1 | Valor P2 |
|--------|---------------|----------|----------|
| +0x003 | point | 0 | 0 (indistinguible) |
| +0x007 | comboCounter | 0 siempre | 0 |
| +0x028 | timer | 99→45 ✓ | 0 siempre |
| +0x030 | power | 0→4975 ✓ | 0 siempre |
| +0x038 | super | 0 | 0 |

**Sin embargo**, los campos desde +0x144 (entry pointers) y +0x150 (playerExtra incluyendo charID, HP, teamPosition) **sí funcionan correctamente** para P2. Resolución de player structs vía entry pointers también funciona.

**Hipótesis**: En modo 1P vs CPU, el juego no mantiene los campos de equipo del CPU (timer, gauges, combo count) ya que solo se muestran en pantalla para P1.

---

## 2. Campos del Team Struct Mapeados

### Base conocida
- P1 Team: RAM offset `0x27CB50` (SH-4: `0x8C27CB50`)
- P2 Team: RAM offset `0x27CD48` (SH-4: `0x8C27CD48`)
- Tamaño: `0x1F8` bytes

### Mapa de campos P1

| Offset | Tipo | Campo | Estado | Notas |
|--------|------|-------|--------|-------|
| +0x001 | u8 | `leader` | ✅ Confirmado | Líder seleccionado (0/1/2) |
| +0x003 | u8 | `point` | ⚠️ Redefinido | Es un valor teamPosition, NO slot index |
| +0x007 | u8 | `comboCounter` | ❌ Siempre 0 | Puede ser incorrecto para Atomiswave o este modo |
| +0x008 | u8 | `comboCounter2` | ❌ Siempre 0 | Mismo problema |
| +0x028 | u16 | **timer** | ✅ Confirmado | Countdown del round (99→45 en pelea 2) |
| +0x02A | u16 | timer_sub | 🔍 Parcial | Varía rápido, posible subtimer/ticks |
| +0x02C | u16 | timer_initial | ✅ | Constante = 90 (0x5A) |
| +0x02E | u16 | timer_mirror | 🔍 | Parece duplicar +0x028 |
| +0x030 | u32 | **power** | 🔍 Nuevo | Acumulador progresivo (0→3510 pelea 1, 0→4975 pelea 2) |
| +0x034 | u32 | counter2 | 🔍 Nuevo | Otro acumulador (2245→6347 pelea 1) |
| +0x038 | u32 | `super` | ❌ Siempre 0 | Definido como super meter pero jamás cambia |
| +0x03C | u32 | `skillStock` | 🔍 | 0 durante casi toda la pelea, ~700 al final |
| +0x044 | u32 | tick_counter | 🔍 Nuevo | Valor grande creciente (~32M→85M), tick global |
| +0x0C0 | ptr[16] | projectiles | 📝 Definido | Punteros indirectos a proyectiles |
| +0x144 | ptr[3] | entries | ✅ Confirmado | Punteros SH-4 a entradas del obj pool |
| +0x150 | struct[3] | playerExtra | ✅ Confirmado | charID, HP, teamPosition etc. |

### Power Gauge (+0x030) — Análisis

El valor crece linealmente durante la pelea y se resetea entre peleas:

```
Pelea 1: 0 → 3510 (en 1677 frames)     ≈ 2.09 unidades/frame
Pelea 2: 0 → 4975 (en 2256 frames)     ≈ 2.20 unidades/frame
```

La tasa es bastante constante (~2.1/frame), lo que sugiere que es un **contador basado en el tiempo** y no en acciones de combate. Posibilidades:
- Timer interno de super gauge fill rate
- Scoring counter
- Match duration in game-internal units

### Super (+0x038) — ¿Por qué es 0?

Posibilidades:
1. **El offset es incorrecto para la versión Atomiswave** — la versión PS2 usa los mismos offsets pero el layout podría diferir
2. **El jugador gasta las barras inmediatamente** — 1 barra = 0x70 (112), si se usa al instante, el frame delta no captura el cambio
3. **El modo CPU vs 1P no acumula super** de la misma manera

---

## 3. Detección de Quickshifts

### Método que funciona

```python
# Para cada frame, encontrar el ÚNICO slot con action != 87
# Si ese slot cambia respecto al frame anterior → quickshift
active_slot = None
for slot in range(3):
    action = player[slot].actionID
    if action != 87:
        if active_slot is None:
            active_slot = slot
        else:
            active_slot = None  # Múltiples activos = intro/win, ignorar
            break
```

### Quickshifts detectados

**Pelea 1: Ash/Oswald/Shen Woo (P1) vs Ash/Oswald/Shen Woo (P2-CPU)**

| Frame | Transición | Slots | Contexto |
|-------|-----------|-------|----------|
| 629 | Oswald → Shen Woo | s1→s2 | Tag voluntario |
| 1061 | Shen Woo → Oswald | s2→s1 | Tag voluntario |
| 1268 | Oswald → Ash | s1→s0 | Entrada forzada: Oswald KO'd (act=67→87, Ash act=64) |
| 1418 | Ash → Shen Woo | s0→s2 | Tag voluntario |

**Pelea 2: P1 (mismos) vs B. Jenet/Tizoc·Griffon/Gato (P2-CPU)**

| Frame | Transición | Slots | Lado |
|-------|-----------|-------|------|
| 2484 | B. Jenet → Tizoc/Griffon | s0→s2 | P2 (CPU tag) |
| 2933 | Shen Woo → Oswald | s2→s1 | P1 |
| 3418 | Oswald → Ash | s1→s0 | P1 |
| 3774 | Ash → Shen Woo | s0→s2 | P1 |

**Notas**:
- La CPU (P2) también hace quickshifts — f2484 muestra a B. Jenet (CPU) tageando a Tizoc/Griffon
- Los quickshifts forzados (por KO) tienen animaciones diferentes: act=64 (entrada forzada) en vez de act=83/110 (tag voluntario)

---

## 4. Detección de Combos

### Método heurístico

Al no encontrar el campo `comboCounter` funcional, se implementó detección heurística: una secuencia de caídas de HP en el oponente con **≤20 frames entre cada drop** se considera un combo.

### Resultados destacados

| Pelea | Combos P1 | Combos P2 | Combo más largo P1 | Combo más largo P2 |
|-------|:---------:|:---------:|:-------------------:|:-------------------:|
| 1 | 10 | 2 | 9 hits (f599-f682) | 4 hits (f459-f478) |
| 2 | 16 | 3 | **15 hits** (f3387-f3529) | 3 hits (f3352-f3368) |

### Combo de 15 hits (f3387-f3529) — Análisis

- Duración: 142 frames (~4.7 segundos)
- Personaje activo P1: Oswald → Ash (quickshift mid-combo@ f3418)
- La presencia de un quickshift DENTRO del combo sugiere un "dream cancel" o tag combo avanzado
- El oponente era B. Jenet (HP: ~63 → 3, luego KO en f3650)

### Limitaciones del método heurístico

- No distingue entre hits de combo real y hits individuales espaciados
- Window de 20 frames puede ser demasiado amplia para ataques lentos
- No detecta combos donde un solo golpe es un super/special de múltiples hits simultáneos
- Los drops de HP ocurren en el frame del impacto, no necesariamente en el último active frame

---

## 5. Estructura de Escenas

El framecap capturó el ciclo completo de 2 peleas:

```
f0-47     [LOADING_STATIC]  Sin actividad — menú/idle
f48-163   [LOADING]          4 sub-scenes de carga (bursts de 150-704 páginas)
                             Camera pan: (214,90) → (312,133)
f164-1840 [FIGHT 1]          Ash team vs Ash team (CPU)
                             1677 frames, timer 99→61
f1841-2463 [WIN+LOADING]     Win screen + transición + carga para pelea 2
                             Muchas sub-scenes, camera continúa panning
f2464-4719 [FIGHT 2]         Ash team vs MOTW team (B.Jenet/Tizoc/Gato)
                             2256 frames, timer 99→45
f4720-4799 [WIN]             Win quote final
```

### Patrones de transición

- **Carga → Pelea**: Burst de >150 páginas, seguido de cambio de entries_valid (null→válido)
- **Pelea → Win**: Timer se congela, entries pasan a null eventualmente
- **Entre peleas**: Camera panning lento (1 pixel/frame), sin entries válidos
- **Pelea 2 más larga**: 2256 vs 1677 frames — la segunda pelea duró ~34% más

### Actividad de memoria por fase

| Fase | Avg pág/frame | Región dominante |
|------|:-------------:|-----------------|
| Loading estático | 0 | - |
| Loading activo | 50-120 | gfx_lo, gfx_hi, decomp |
| Peleas | 70-76 | obj_pool, heap_low, gfx_lo |
| Win | ~50 | gfx_lo, obj_pool |

---

## 6. Análisis de Hitboxes

### Valores de `hitboxesActive` observados

El campo `hitboxesActive` (player struct +0x258, u8) funciona como bitmask:

| Bit | Valor | Significado probable |
|-----|-------|---------------------|
| 0 | 0x01 | Raro, visto en pocos frames |
| 1 | 0x02 | Attack hitbox activo |
| 2 | 0x04 | Vulnerability box extended |
| 3 | 0x08 | Vulnerability box type 2 |
| 4 | 0x10 | Visto en combos largos |
| 5 | 0x20 | Raro |
| 6 | 0x40 | Collision override |
| 7 | 0x80 | Special state (Oswald act=99: 0x8D, 0xCD) |

### Top acciones por tiempo en hitbox activo

| Personaje | ActionID | Frames con hitbox | Valores hbActive |
|-----------|----------|:-----------------:|-----------------|
| Shen Woo | 219 | 261 | 0x08, 0x0C, 0x0D |
| Oswald | 0 (idle) | 157 | 0x08, 0x0C |
| Shen Woo | 0 (idle) | 154 | 0x0C |
| Shen Woo | 94 | 143 | 0x02, 0x0C, 0x0D, 0x1C |
| Tizoc/Griffon | 0 | 125 | 0x0C |
| B. Jenet | 68 | 124 | 0x02 |
| Ash | 0 (idle) | 115 | 0x06, 0x0C |

**Nota**: `hbActive != 0` en action=0 (idle) con valor 0x0C indica que los vulnerability boxes están siempre presentes, aun en idle — esto es consistente con juegos de pelea donde siempre puedes ser golpeado.

---

## 7. Player Struct — Offsets Confirmados

Resolución del player struct:
```
entry_ptr = team.entries[slot]        # Puntero SH-4 (ej. 0x8C190BB4)
ram_off   = (entry_ptr & 0x1FFFFFFF) - 0x0C000000  # → offset RAM
data_ptr  = read_u32(ram, ram_off + 0x10)           # Siguiente puntero SH-4
player    = (data_ptr & 0x1FFFFFFF) - 0x0C000000 - 0x614  # Base del player struct
```

| Offset | Tipo | Campo | Estado |
|--------|------|-------|--------|
| +0x160 | u32 | posX | ✅ |
| +0x164 | u32 | posY | ✅ |
| +0x258 | u8 | hitboxesActive | ✅ |
| +0x264 | u16 | actionID | ✅ |
| +0x266 | u16 | prevActionID | ✅ |
| +0x26E | u16 | animFrameIndex | ✅ |
| +0x2B4 | u8 | facing | ✅ (bitmask, bit 1 = dirección) |

### Action IDs significativos

| ActionID | Significado |
|----------|-------------|
| 0 | Idle/neutral |
| 1-5 | Intro animations |
| 6-8 | Walk/movement |
| 27, 30 | Crouch/crouch transitions |
| 39, 42, 44 | Jump variants |
| 48-53 | Normal attacks (standing) |
| 64 | Forced tag-in (partner KO'd) |
| 66-67 | KO animation |
| 83 | Tag-in voluntary |
| 85-86 | Tag-out animation |
| 87 | **Benched/standby** (no activo) |
| 90-95 | Special moves |
| 99 | Oswald special (hbActive incluye bit 7) |
| 110 | Tag attack (entrando con ataque) |
| 126-136 | Hitstun variants |
| 146-148 | More hitstun (heavier hits) |
| 176 | Projectile-related |
| 188-199 | Special/super moves |
| 202-208 | Command normals/specials |
| 219 | Shen Woo charge move |

---

## 8. Facing Field — Análisis de Bitmask

Valores observados del campo `facing` (+0x2B4, u8):

| Valor | Hex | Binario | Frecuencia | Interpretación |
|-------|-----|---------|:----------:|----------------|
| 0 | 0x00 | 00000000 | Alta | Facing left, ground |
| 2 | 0x02 | 00000010 | Alta | Facing right, ground |
| 34 | 0x22 | 00100010 | Media | Facing right + ???  |
| 42 | 0x2A | 00101010 | Media | Facing right + crouch? |
| 62 | 0x3E | 00111110 | Alta (intros) | Facing right + intro state |

**Bit 1** (0x02) = dirección de mirada (0=izquierda, 1=derecha)  
**Bits 2-5** parecen indicar estados adicionales (airborne, crouching, intro, etc.)

---

## 9. Observaciones sobre el Matchup CPU

### Pelea 1: Mirror match (Ash vs Ash)
- Timer consume 38 unidades de 99 a 61 (~5.7s/unidad de timer)
- P1 pierde solo a Oswald (KO f1211, hp=32→0)
- P2 pierde a los 3 (Ash f738, Oswald f1323, Shen Woo f1630)

### Pelea 2: vs Mark of the Wolves team  
- Timer consume 54 unidades de 99 a 45 (pelea más larga, ~4.7s/unidad)
- P1 pierde a Shen Woo (KO f4079)
- P2 pierde a los 3 (Tizoc f3137, B.Jenet f3650, Gato f4494)
- CPU hace un quickshift propio en f2484 (B.Jenet → Tizoc/Griffon)

### Comportamiento del CPU
- Solo 1 quickshift del CPU detectado en 2 peleas
- Solo 5 combos de P2 (CPU) vs 26 de P1 → la CPU es mucho menos agresiva
- Combo máximo del CPU: 4 hits vs 15 hits de P1

---

## 10. Problemas Abiertos / TODO

### Combo Counter no encontrado
- `team+0x007` (`comboCounter`) lee 0 en TODOS los frames, incluso durante combos confirmados
- `team+0x008` (`comboCounter2`) igual
- Exploración de offsets de player struct (0x0F0-0x110, 0x380-0x3B0, 0x560-0x590) no encontró candidato
- **Teoría**: El combo counter puede estar en una estructura completamente diferente (ej. HUD state, global game state) fuera del team/player struct

### Super Meter (+0x038) siempre 0
- Posibles causas: offset incorrecto para Atomiswave, o el jugador gasta las barras entre frames delta
- Necesita validación con la captura Lua en vivo (el Lua lee +0x038 directamente)

### Campo +0x030 sin identificar completamente
- Crece linealmente (~2.1/frame) independientemente de acciones
- Se resetea entre peleas
- ¿Timer interno? ¿Score? ¿Power gauge accumulation rate?

### PlayerExtra campos sin explorar
- `stun` y `guard` gauges son campos conocidos en la versión PS2 pero no verificados en Atomiswave
- La versión Flycast de types.lua NO los incluye (solo PS2)

### Scene detection granularidad
- Los 8 sub-scenes de "loading_active" entre peleas podrían ser:
  - Win pose screen
  - Winquote text
  - Stage transition
  - Character intro
  - Cada uno con su propio patrón de carga gráfica

---

## 11. Metodología

### Herramientas utilizadas

| Herramienta | Propósito |
|-------------|----------|
| `tools/analyze_framecap_full.py` | Análisis completo: escenas, eventos, hitboxes, reportes |
| `tools/analysis_utils.py` | Constantes y funciones de extracción de memoria |
| `tools/_debug_combo.py` | Investigación de team struct bytes en HP drops |
| `tools/_debug_active.py` | Detección de quickshifts via action!=87 |
| `tools/_debug_qs.py` | Verificación de point/teamPosition durante QS |

### Proceso de análisis

1. **Carga**: FCAP (20-byte header + u32 frame + u16 page_count + pages de 4096 bytes)
2. **Reconstrucción RAM**: Aplicar deltas secuencialmente sobre frame_base.bin
3. **Extracción de estado**: Por cada frame, leer team structs, resolver player structs vía entry pointers, extraer todos los campos
4. **Detección de escenas**: Bursts de páginas >150, transiciones de entries valid/null, resets de HP, jumps de cámara
5. **Detección de eventos**: Quickshifts (action!=87 transitions), combos (HP drops consecutivos ≤20f), KOs (HP>0 → HP≤0)
6. **Agregación de hitboxes**: Mapeo (personaje, actionID) → valores de hbActive y muestras de boxes

### Tasa de procesamiento

- 4800 frames procesados en ~35 segundos
- ~138 frames/segundo de análisis
- RAM snapshot: 16 MB reconstruido incrementalmente

---

## 12. Datos Crudos

El reporte auto-generado completo con timelines, tablas de hitbox, y análisis de memoria está en:

→ [`docs/framecap_full_analysis.md`](framecap_full_analysis.md)
