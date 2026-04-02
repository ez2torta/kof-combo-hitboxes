# KoF Combo Hitbox Viewer

**Version 1.0.3** — https://github.com/odabugs/kof-combo-hitboxes

Overlay that displays hitboxes in real time for various KoF and Guilty Gear titles.

---

## Juegos soportados

**Steam / PC:**
- King of Fighters '98 Ultimate Match Final Edition (Steam y GOG)
- King of Fighters 2002 Unlimited Match (Steam y GOG)
- Guilty Gear XX #Reload *(solo versión Steam)*
- Guilty Gear XX Accent Core +R

**PlayStation 2 (vía PCSX2 1.4.0):**
- King of Fighters XI
- King of Fighters '98 Ultimate Match
- King of Fighters 2002 Unlimited Match (versión original y Tougeki Ver.)
- King of Fighters NeoWave
- NeoGeo Battle Coliseum
- Capcom vs. SNK 2 *(solo NTSC-U)*
- Capcom Fighting Evolution *(solo NTSC-U)*

---

## Uso

1. Inicia el juego deseado.
2. Ejecuta `kof-hitboxes.exe`.
3. El programa detecta el juego automáticamente e imprime instrucciones adicionales en la consola.

**Requisitos:** Windows Vista o superior con Windows Aero (DWM) habilitado.

---

## Compilar desde el código fuente

### Prerequisitos

Solo necesitas **MinGW** (incluye `gcc` y `mingw32-make`). Instálalo con winget:

```powershell
winget install -e --id MSYS2.MSYS2
```

> Si ya tienes MinGW instalado (`gcc --version` responde sin error), puedes saltarte este paso.

### Pasos

```powershell
# 1. Clona el repositorio con el submodule de LuaJIT
git clone --recurse-submodules https://github.com/odabugs/kof-combo-hitboxes.git
cd kof-combo-hitboxes

# Si ya clonaste sin --recurse-submodules, inicializa el submodule así:
git submodule update --init

# 2. Compila LuaJIT (solo la primera vez)
.\make.bat lua

# 3. Compila el ejecutable principal
.\make.bat
```

El resultado es `kof-hitboxes.exe` en la raíz del repositorio.

### Limpiar la build

```powershell
# Limpia solo el ejecutable y los .o del proyecto
.\make.bat clean

# Limpia también los archivos compilados de LuaJIT
.\make.bat luaclean
```

---

## Logger de hitboxes (KOF XI / PCSX2)

Presiona **F9** mientras el overlay está corriendo para iniciar/detener la grabación de datos de hitboxes. Se genera un archivo `.ndjson` en la misma carpeta que el ejecutable con el nombre `kof_xi_hitboxes_<timestamp>.ndjson`.

El formato es **NDJSON** (newline-delimited JSON): cada línea es un objeto JSON independiente que representa un frame. Esto lo hace fácil de procesar tanto por scripts como por un LLM.

### Estructura del archivo

```jsonc
// Una línea por frame:
{
  "frame": 42,
  "camera_x": 256, "camera_y": 224,   // borde izquierdo/superior de pantalla en coords mundo
  "players": [
    {
      "player": 1,
      "char_id": "0x1E", "char_name": "Kyo",
      "world_x": 512, "world_y": 672,  // posición origen del personaje (suelo = 0x02A0)
      "facing": 1,                     // +1 = derecha, -1 = izquierda
      "health": 112, "stun_gauge": 112, "guard_gauge": 112,
      "super_meter": 112, "stun_timer": -1,  // -1 = no está en stun
      "hitboxes_active": "0x07",       // bitmask de slots activos (bits 0-5)
      "hitboxes": [
        {
          "slot": 0, "slot_name": "attackBox",
          "box_id": "0x21", "box_type": "attack",
          "rel_x": 40,  "rel_y": 80,   // offset desde el origen del personaje (px reales)
          "half_w": 15, "half_h": 20,  // valores tal como están en memoria
          "full_w": 30, "full_h": 40,  // tamaño real de la caja
          "world_cx": 552, "world_cy": 592  // centro de la caja en coords mundo
        }
        // ...más hitboxes
      ]
    },
    { "player": 2, ... }
  ]
}
```

### Slots de hitboxes

| Slot | Nombre | Descripción |
|---|---|---|
| 0 | `attackBox` | Caja de ataque |
| 1 | `vulnBox1` | Caja vulnerable 1 |
| 2 | `vulnBox2` | Caja vulnerable 2 |
| 3 | `vulnBox3` | Caja vulnerable 3 |
| 4 | `grabBox` | Caja de agarre (siempre tipo `throw`) |
| 5 | `hb6` | Sin uso conocido |
| 6 | `collisionBox` | Caja de colisión del personaje |

Los tipos de caja (`box_type`) posibles son: `attack`, `vulnerable`, `counterVuln`, `anywhereVuln`, `otgVuln`, `guard`, `throw`, `throwable`, `projAttack`, `projVuln`, `collision`, `clash`, `dummy`.

### Coordenadas

- `world_x / world_y`: posición del origen del personaje. El suelo está en Y = `0x02A0` (672). Y **decrece** al subir.
- `rel_x / rel_y`: offset de la caja relativo al origen. `rel_x` positivo apunta hacia adelante (según `facing`). `rel_y` positivo apunta hacia arriba.
- `full_w / full_h`: la caja se extiende `half_w` píxeles hacia cada lado del centro, por lo que el ancho total es `full_w`.

---

## Configuración

Edita `default.ini` para ajustar el comportamiento del overlay:

| Opción | Descripción |
|---|---|
| `boxEdgeOpacity` / `boxFillOpacity` | Opacidad del borde y relleno de las cajas (0–255) |
| `drawProjectiles` | Mostrar cajas de proyectiles |
| `drawBoxFill` | Mostrar relleno de cajas |
| `drawPlayerPivot` / `drawBoxPivot` | Mostrar puntos de pivote |
| `drawGauges` | Mostrar barra de aturdimiento y guardia (KOF '98UMFE / '02UM) |
| `[player1|2] enabled` | Activar/desactivar cajas por jugador |
| `[player1|2] drawRangeMarker` | Marcador de rango de normales cercanos (`A`, `B`, `C`, `D`, `none`) |

Los colores se configuran en la sección `[colors]` en formato `(R, G, B)` o `(R, G, B, A)`.

---

## Créditos

- **PhoenixNL** — testing, recolección de IDs de hitboxes
- **Jesuszilla** — ingeniería inversa de CvS2 y Capcom Fighting Evolution
- **Pasky** — ingeniería inversa de Guilty Gear XX #Reload

LuaJIT embebido versión 2.0.5, compilado para 32-bit — http://luajit.org/
