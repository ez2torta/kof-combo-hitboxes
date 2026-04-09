# Guía de Workflow: Pipeline de Análisis de RAM

## Resumen

Este documento explica cómo usar el pipeline de herramientas para analizar la
memoria de KOF XI ejecutándose en Flycast. El objetivo es que el usuario solo
tenga que ejecutar el emulador con savestates específicos mientras las
herramientas se encargan de capturar y analizar datos automáticamente.

## Herramientas Disponibles

| Herramienta | Archivo | Propósito |
|-------------|---------|-----------|
| **RAM Watch** | `tools/ram_watch.py` | Monitoreo continuo, snapshots, comparación, rastreo LZ, captura de animaciones |
| **LZ Tool** | `tools/lz_tool.py` | Descomprimir/comprimir archivos .lz del ROM |
| **UNI Parser** | `tools/uni_parser.py` | Parsear archivos UNI de personaje (frames, hitboxes) |
| **UNI Anim Analyzer** | `tools/uni_anim_analyzer.py` | Análisis de secuencias de animación dentro de UNI |
| **RAM Dumper** | `tools/flycast_ram_dumper.py` | Dumps de RAM crudos |
| **Memory Analyzer** | `tools/memory_analyzer.py` | Análisis offline de snapshots |
| **Analysis Utils** | `tools/analysis_utils.py` | Utilidades compartidas (lectura de RAM, structs) |

## Requisitos Previos

1. **Flycast** ejecutándose con la ROM de KOF XI (Atomiswave)
2. **Python 3.8+** (sin dependencias externas — solo stdlib)
3. **Savestate de referencia**: Con configuración P1=Ash/Kyo/Iori, P2=Terry/Ryo/Kim
4. Archivo `.nvmem` con todo desbloqueado (opcional, facilita acceso a personajes)

## Flujos de Trabajo

### 1. Monitoreo Continuo (Watch Mode)

El modo más completo. Detecta cambios en la RAM automáticamente y los registra.

```powershell
# Iniciar monitoreo (0.5s entre lecturas, regiones clave)
python tools/ram_watch.py watch

# Monitoreo rápido (0.2s, toda la RAM)
python tools/ram_watch.py watch --interval 0.2 --full
```

**Procedimiento:**
1. Ejecutar `ram_watch.py watch` en una terminal
2. Abrir Flycast y cargar KOF XI
3. Navegar por los menús → la herramienta registra cambios
4. Entrar a una pelea, realizar movimientos, etc.
5. Presionar Ctrl+C para detener

**Salida:**
- `aw_data/sessions/session_YYYYMMDD_HHMMSS/watch_log.ndjson` — log temporal en formato NDJSON
- `aw_data/sessions/session_*/auto_*.bin` — snapshots automáticos en transiciones grandes
- `aw_data/sessions/session_*/session_summary.md` — resumen en markdown

### 2. Snapshots Nombrados

Para capturar estados específicos del juego.

```powershell
# Capturar estado actual
python tools/ram_watch.py snap --name titulo

# Con descripción
python tools/ram_watch.py snap --name pelea_idle --description "Ash vs Terry, ambos idle"
```

**Savestates recomendados:**
1. `titulo` — Pantalla de título
2. `menu_principal` — Menú principal
3. `seleccion_personaje` — Pantalla de selección de personaje
4. `pelea_idle` — En pelea, ambos idle (sin tocar controles)
5. `pelea_movimiento` — P1 caminando hacia adelante
6. `pelea_ataque` — P1 ejecutando un golpe
7. `pelea_especial` — P1 ejecutando un movimiento especial
8. `pelea_super` — P1 ejecutando un super
9. `pelea_tag` — Momento de tag/cambio de personaje
10. `pelea_ko` — Momento de KO
11. `victoria` — Pantalla de victoria

### 3. Comparar Snapshots

```powershell
# Comparar dos snapshots
python tools/ram_watch.py compare aw_data/snapshots/titulo.bin aw_data/snapshots/pelea_idle.bin

# Con reporte markdown
python tools/ram_watch.py compare aw_data/snapshots/titulo.bin aw_data/snapshots/pelea_idle.bin -o aw_data/analysis/titulo_vs_pelea.md
```

### 4. Rastreo de Actividad LZ

Detecta cuándo archivos .lz son descomprimidos en RAM durante la ejecución.

```powershell
python tools/ram_watch.py lztrack
```

**Procedimiento:**
1. Ejecutar `lztrack` ANTES de iniciar una pelea
2. En Flycast, navegar: título → selección → pelea
3. Observar los eventos que aparecen (cada uno indica una descompresión)
4. La herramienta guarda los datos descomprimidos para comparación con archivos del ROM

### 5. Captura de Animación

Captura frame por frame los datos de animación de un personaje.

```powershell
# Capturar 300 frames de P1
python tools/ram_watch.py anim --player 1 --frames 300

# Capturar 600 frames de P2
python tools/ram_watch.py anim --player 2 --frames 600

# Captura lenta (para análisis detallado)
python tools/ram_watch.py anim --player 1 --frames 120 --interval 0.033
```

**Genera:**
- `anim_log.ndjson` — datos frame-by-frame (acción, animFrame, hitboxes, posición)
- `anim_analysis.md` — análisis de secuencias detectadas con tablas de timing

### 6. Análisis de Archivos UNI

```powershell
# Info general de un archivo UNI
python tools/uni_parser.py info aw_data/rom_samples/personajes/0004_0000.UNI

# Listar frames con hitboxes
python tools/uni_parser.py hitboxes aw_data/rom_samples/personajes/0004_0000.UNI

# Análisis de secuencias de animación
python tools/uni_anim_analyzer.py aw_data/rom_samples/personajes/0004_0000.UNI
```

### 7. Descompresión LZ

```powershell
# Descomprimir un archivo
python tools/lz_tool.py decompress archivo.lz

# Info de un .lz
python tools/lz_tool.py info archivo.lz

# Descomprimir todos los .lz de un directorio
python tools/lz_tool.py batch-decompress aw_data/rom_samples/graficos/

# Identificar tipo de contenido
python tools/lz_tool.py identify archivo.dec
```

## Escenario Completo: Sesión de Análisis

```powershell
# 1. Preparación
# (Abrir Flycast, cargar KOF XI, tener savestate listo)

# 2. Capturar estado base (título)
python tools/ram_watch.py snap --name titulo

# 3. Avanzar en Flycast a selección de personaje
python tools/ram_watch.py snap --name seleccion

# 4. Entrar a pelea (Ash vs Terry)
python tools/ram_watch.py snap --name pelea_inicio

# 5. Iniciar captura de animación (idle de Ash)
python tools/ram_watch.py anim --player 1 --frames 120

# 6. Ejecutar un combo con Ash
python tools/ram_watch.py anim --player 1 --frames 300

# 7. Comparar estados
python tools/ram_watch.py compare aw_data/snapshots/titulo.bin aw_data/snapshots/pelea_inicio.bin -o aw_data/analysis/titulo_vs_pelea.md

# 8. Rastrear archivos LZ cargados durante transición
python tools/ram_watch.py lztrack
# (En Flycast: regresar a título, volver a pelea para ver qué se carga)
```

## Estructura de Archivos Generados

```
aw_data/
  snapshots/           # Snapshots de RAM nombrados
    titulo.bin         # 16MB dump
    titulo.json        # Metadatos
    pelea_idle.bin
    pelea_idle.json
  sessions/            # Sesiones de watch/lztrack/anim
    session_YYYYMMDD_HHMMSS/
      watch_log.ndjson
      auto_0001.bin    # Snapshots automáticos
      session_summary.md
    lztrack_YYYYMMDD_HHMMSS/
      lz_events.ndjson
      lz_0001_decomp_buf_1_0x300000.bin
      lztrack_summary.md
    anim_YYYYMMDD_HHMMSS/
      anim_log.ndjson
      anim_analysis.md
  analysis/            # Reportes de comparación
    titulo_vs_pelea.md
  animation_sequence_analysis.md  # Análisis UNI estático
```

## Correlación: RAM ↔ Archivos UNI

La clave del pipeline es correlacionar lo que vemos en RAM con la estructura
estática de los archivos UNI:

| RAM Field | Offset en Player Struct | Correlación con UNI |
|-----------|------------------------|---------------------|
| `actionID` | +0x0EC (u16) | Índice en Section 4 (definiciones de movimiento) |
| `animFrameIndex` | +0x2A4 (u16) | Índice en Section 10 (tabla de frames) |
| `spriteOffsetX/Y` | +0x2A8/+0x2AA (s16) | Offsets de sprite del frame actual |
| `hitboxes[7]` | +0x314 (70 bytes) | Hitboxes del frame actual de Section 10 |
| `hitboxesActive` | +0x39E (u8) | Bitmask de hitboxes activos |

### Mapeo actionID → Frames

Section 4 entry `[actionID]` contiene la lista de **frame start indices**
dentro de Section 10. Cada par de start indices define una sub-animación:

```
actionID = 0: frames [2, 14, 26, 38, 56, 74]
  Sub-anim 0: frames 2-13   (idle phase)
  Sub-anim 1: frames 14-25  (transition 1)
  Sub-anim 2: frames 26-37  (transition 2)
  Sub-anim 3: frames 38-55  (extended phase)
  Sub-anim 4: frames 56-73  (extended phase)
  Sub-anim 5: frames 74-85  (until next action starts)

actionID = 1: frames [86, 98, 110, 122, 134, 166]
  ...y así sucesivamente
```

### Opcodes 0xA0 en Section 4 (Entries 22+)

Las entradas 22 en adelante de Section 4 usan un formato de bytecode con
opcodes 0xA0-prefixed. Estos probablemente controlan:

| Opcode | Frecuencia | Hipótesis |
|--------|------------|-----------|
| `0xA001` | Muy común | Goto/Jump a otra acción |
| `0xA010` | Moderada | Set animation property |
| `0xA015` | Moderada | Set velocity/movement |
| `0xA019` | Rara | Flag toggle |
| `0xA01A` | Rara | Condition check |
| `0xA01C` | Moderada | Load sub-sequence |
| `0xA028` | Muy común | Set frame range |
| `0xA029` | Muy común | Call sub-routine |
| `0xA02C` | Moderada | Return/End |
| `0xA05C` | Común | Trigger effect/sound |

**Para decodificar estos opcodes se necesita correlación RAM**: observar qué
cambia en el player struct cuando el juego ejecuta cada opcode.
