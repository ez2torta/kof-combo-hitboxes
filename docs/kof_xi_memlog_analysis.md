# KOF XI Animation System — Memlog Analysis Report

Analysis of two captures:
- **v1:** `kof_xi_memlog_20260402_000502.ndjson` (1148 frames, ~17.7s, 3 regions)
- **v2:** `kof_xi_memlog_20260402_005007.ndjson` (1546 frames, ~23.9s, 6 regions)

---

## 1. Dataset Overview

### v1 Capture (3 regions)

| Metric | Value |
|--------|-------|
| Total frames captured | 1,148 |
| Frame continuity gaps | 0 (perfect) |
| Duration | 17.73 s |
| Unique base addresses | 3 (character tag-ins) |

### v2 Capture (6 regions)

| Metric | Value |
|--------|-------|
| Total frames captured | 1,546 |
| Frame continuity gaps | 0 (perfect) |
| Duration | 23.9 s |
| Sample interval | avg ~15 ms |
| Unique base addresses | 1 (`0x0081EBC4`) |
| Action transitions | 69 segments, 19 unique action IDs |

---

## 2. Key Discoveries

### 2.1 Animation Frame Index — `player+0x2A4h` ★ NEW in v2

**Location:** `anim_block_5[25]` → absolute offset `player+0x2A4h` (u8).

This is the **animation frame counter** — the primary indexing mechanism for the
animation system. It increments by +1 every ~5 game frames (one animation frame
tick) and resets to the action's base frame when a new action begins.

**Evidence (v2, 1546 frames):**
- 101 unique values observed (range 0–255)
- 350 total changes: delta=+1 accounts for 270 of them (77%)
- Each action ID uses a fixed sub-range of frame indices
- Counter resets on every action transition

**Animation frame ranges per action (v2 data):**

| Action ID | Frame Range | Duration (game frames) | Loop? |
|-----------|------------|----------------------|-------|
| 0 (idle) | 0–9 | ~50 frames per cycle | Yes |
| 1 (walk back) | 10–21 | ~61 frames | Yes |
| 2 (walk fwd) | 22–29 | ~66 frames | Yes |
| 4 (crouch/jump startup) | 58–60 | ~3–4 frames | No |
| 5 (jumping) | 64–69 | ~45 frames | Partial |
| 8 (attack startup) | 30–31 | ~4 frames | No |
| 11 (attack active) | 32–39 | ~38 frames | Yes |
| 23 (attack recovery) | 30–31 | ~2–3 frames | No |
| 89 | 180–187 | 15 frames | — |
| 160 (super) | 28–37 | 42 frames | — |
| 194 (cinematic) | 30–195 | 169 frames | — |

**Tick rate:** The most common spacing between +1 increments is 5 game frames,
but this varies (2–11 frames observed). The animation system does not tick at a
fixed rate relative to the game's 60fps — it appears to be driven by the animation
data itself (variable frame durations per animation phase).

### 2.2 Action ID + Previous Action — `player+0x0ECh` / `player+0x0EEh`

**Location:** `anim_block_2[95]` / `anim_block_2[97]` → offsets `+0ECh` / `+0EEh`.

v2 confirmed `+0ECh` as the current action ID and revealed `+0EEh` as the
**previous action ID**. When action transitions from A→B:
- `+0ECh` changes from A to B
- `+0EEh` changes from (whatever it was) to A

These two bytes have the **exact same set of 19 unique values** but are never
equal at the same frame during an active transition.

**v2 Action ID map (single character, 19 actions observed):**

| ID | Anim Frames | Inferred Meaning |
|----|------------|-----------------|
| 0 | 0–9 | Idle (neutral stand) |
| 1 | 10–21 | Walk backward |
| 2 | 22–29 | Walk forward |
| 4 | 58–60 | Crouch / jump startup (3 frames) |
| 5 | 64–69 | Airborne (jumping) |
| 6 | 61 | Landing (1 frame) |
| 8 | 30–31 | Attack startup |
| 11 | 32–39 | Attack active / mid-combo |
| 23 | 30–31 | Attack recovery |
| 89 | 180–187 | Special move type A |
| 90 | 196–200 | Special move type B |
| 92 | 145–153 | Special move type C |
| 93 | 219–223 | Special move type D |
| 95 | 231–238 | EX/super startup |
| 96 | 0–255 | Super active (uses full range) |
| 98 | 1–12 | Special (multi-phase) |
| 99 | 20–27 | Special (multi-phase) |
| 160 | 28–37 | Super attack |
| 194 | 30–195 | Cinematic / tag super |

### 2.3 Action Transition Signal — `player+0x0F2h`

**Location:** `anim_block_2[101:103]` → offset `+0F2h` (s16 LE).

Default value is `0xFFFF` (−1). Flashes to a different value for exactly **1 frame**
at certain action transitions:
- **Value 5** → appears during action 4 (crouch/jump startup)
- **Value 0** → appears during actions 23, 89, 92, 95, 98 (attack recovery / specials)

The companion byte at `+0F3h` is a sign extension (0xFF when +0F2h is 0xFF, 0x00
otherwise). This is likely a **cancel/interrupt signal**.

### 2.4 Animation Data Pointer — `player+0x200h` (confirmed from v1)

**Location:** `anim_block_3[62:64]` (u16 LE).

Still confirmed: changes exactly at animation boundaries, clusters by character,
+2 increments between sequential phases. In v2 (single character), 26 unique
pointer values observed.

### 2.5 Action Category Code — `player+0x204h` (refined from v1)

**Location:** `anim_block_3[66]` (u8). 17 unique values in v2.

v2 revealed this has a **clean mapping** from action IDs to category codes:
- Actions 0–6 → categories 0–5 (basic movement)
- Actions 8, 11, 23 → categories 32, 33, 35 (attack phases)
- Special moves → categories 53, 73, 75, 122, etc.

This is likely an **animation bank selector** within the character's animation table.

### 2.6 Sprite/Hitbox Offsets — `player+0x2A8h` / `player+0x2AAh` ★ NEW in v2

**Location:** `anim_block_5[29:31]` and `[31:33]` → offsets `+2A8h` / `+2AAh` (s16 LE).

Small signed values that change per-animation-frame:
- **+2A8h** (X offset): range −101 to −9. Changes at anim frame ticks.
- **+2AAh** (Y offset): range −158 to −51. Changes primarily at action transitions.

These do NOT directly match hitbox positions but appear to be **sprite drawing
offsets** — the displacement from the player origin to the sprite anchor point.
Values are always negative, suggesting the sprite origin is above-left of the
player position.

### 2.7 Animation Properties A/B — `player+0x2B2h` / `player+0x2B3h` ★ NEW in v2

**Location:** `anim_block_5[39]` and `[40]` (u8 each).

- **+2B2h**: 13 unique values (6–19). Varies by action and animation frame.
- **+2B3h**: 12 unique values (7–18). Varies by action.

These change per-animation-frame and are action-dependent. Based on the value
ranges and behavior:
- **+2B2h** may encode a sprite or collision **width** in tile units
- **+2B3h** may encode a sprite or collision **height** in tile units

Sample values per action:

| Action | +2B2h values | +2B3h values |
|--------|-------------|-------------|
| 0 (idle) | 8, 10 | 14, 15 |
| 5 (jump) | 10, 11 | 9 |
| 11 (attack) | 6, 8 | 9, 11, 16, 18 |
| 160 (super) | 8, 10, 12, 13, 14 | 13 |

### 2.8 State Flags ★ NEW in v2

| Offset | Field Name | Values | Trigger |
|--------|-----------|--------|---------|
| `+2AEh` | `superStateFlag` | 0 or 16 | Set to 16 during action 160 (super attack), cleared on return to neutral |
| `+2B0h` | `hitContactFlag` | 0 or 1 | Toggles for 1–2 frames during contact in actions 11 (attack active) and 194 |
| `+2B4h` | `animPhaseToggle` | 0 or 1 | 124 changes total. Flips at animation phase boundaries |
| `+2A5h` | `animPlayFlag` | 0 or 1 | Active during certain animation sequences |

### 2.9 Move Displacement / Gravity — `player+0x024h` / `player+0x054h` ★ NEW in v2

**+024h** (s32 LE): Large displacement values, zero during idle. Active only during
specific attack animations (actions 11 and 194). Values observed: 0, −27904
(`0xFFFF9300`), −40448 (`0xFFFF6200`). Likely encodes move-specific displacement
or momentum in fixed-point.

**+054h** (s32 LE): Very small negative values (−7, −16, −17) that appear near the
end of attack segments. Possibly **gravity accumulation** or **landing velocity**.

### 2.10 Battle State / Stun (confirmed from v1)

- **+030h** `battleState`: Toggles 1↔2. `2` = active action, `1` = idle/recovery.
- **+034h** `battleStateAux`: Co-toggles with +030h. Values 0 / 0xDAAA.
- **+03Dh** `stunCountdown`: Counts down during hit stun phases (values 4, 5 in v2).
- **+03Ch/+03Eh** `prevPos`: Previous-frame position shadow (X and Y as u16 LE).

### 2.11 Effect Data During Cinematics — `player+0x048h` / `player+0x04Dh`

These bytes are **entirely static outside action 194** (cinematic super). During
action 194:
- `+048h` (u16 LE): 57 unique values, 63 rapid changes
- `+04Dh` (u8): 84 unique values, 38 changes

These likely encode **camera shake, VFX data, or screen coordinates** specific to
the cinematic super animation.

---

## 3. v2 Region Results Summary

| Region | Offset | Dynamic Bytes | Key Findings |
|--------|--------|--------------|--------------|
| `anim_block_1` | `+020h` | 17/128 | Position shadow, battle state, displacement, effect data |
| `anim_block_2` | `+08Dh` | 5/128 | Action ID, previous action, transition signals |
| `anim_block_3` | `+1C2h` | 3/128 | Anim data pointer, action category, char bank |
| `anim_block_4` | `+10Dh` | **0/128** | **ALL STATIC.** Character constants, no runtime data. |
| `anim_block_5` | `+28Bh` | 9/128 | **Animation frame index**, sprite offsets, properties, flags |
| `team_block` | team `+040h` | 2/128 | Only 2 bytes changed once. No input data found. |

---

## 4. What's Still Missing

### Frame Timer

**No per-frame incrementing counter was found** in any of the six captured regions.
No single byte, u16, or u32 changes every frame. The `animFrameIndex` at +2A4h
ticks every ~5 frames (variable), but is NOT a per-game-frame timer.

The frame timer must reside in one of the uncaptured gaps:
- **Gap B2** (`+18Dh`–`+1C1h`, 53 bytes): Highest priority — between the static
  block4 and block3.
- **Gap D2** (`+242h`–`+28Ah`, 73 bytes): Between block3 and the newly-useful block5.
- **Gaps E/F** (482 bytes total): Large tail section of the player struct.

### Input Buffer

The `team_block` (team `+040h`–`+0BFh`) yielded **almost nothing**: only 2 bytes
changed once across 1546 frames. This region does NOT contain the input buffer.

The input buffer is likely:
1. **In the `playerExtra` struct** (team `+150h`, 32 bytes per character slot)
2. **In a global input array** separate from player/team structs
3. **In IOP-mapped memory** that the EE polls directly

### anim_block_4 Dead Zone

The entire 128 bytes at `+10Dh`–`+18Ch` are **completely static**. This region
likely stores per-character constants (move frame data tables, damage values, etc.)
loaded once when the character initializes. It should be replaced with a more
useful capture target in v3.

---

## 5. Input State — Strategy (unchanged)

KOF XI on PCSX2 does not have a known, stable input buffer address.

**PS2 Pad Data Format (for reference):**
```
Byte 0 (active-LOW): [Select, L3, R3, Start, Up, Right, Down, Left]
Byte 1 (active-LOW): [L2, R2, L1, R1, Triangle, Circle, Cross, Square]
```

**User's button mapping:**
| PS2 Button | KOF Button | Pad Byte 1 Bit |
|-----------|-----------|----------------|
| Square (□) | A (LP) | bit 0 |
| Cross (×) | B (LK) | bit 1 |
| Triangle (△) | C (HP) | bit 3 |
| Circle (○) | D (HK) | bit 2 |
| R1 | E (tag) | bit 4 |

**To locate the input buffer**, a targeted PCSX2 Cheat Engine scan while holding
a button may be more effective than the blind region capture approach.

---

## 6. Next Steps

### v3 Logger Changes (recommended)
1. **Replace `anim_block_4`** (dead zone) with **Gap B2** (`+18Dh`, 53 bytes) —
   highest priority for finding the frame timer.
2. **Add Gap D2** (`+242h`, 73 bytes) if space permits — second timer candidate.
3. Consider a **global address scan region** to hunt for the input buffer outside
   the player struct.

### Analysis Tasks
1. **Controlled animation test** — perform isolated single moves with pauses to
   build a complete action ID → move name mapping.
2. **Cross-reference `+226h`** character bank selector with `roster.lua` char IDs.
3. **Two-player capture** — set `memlogger.targetPlayer = 2` to compare structures.
4. **Longer varied capture** — blocking, getting hit, throws, round transitions
   to discover defensive state fields.
5. **Cheat Engine parallel scan** — scan PCSX2 RAM for a u8 or u16 that increments
   by 1 every frame to definitively locate the frame timer.
