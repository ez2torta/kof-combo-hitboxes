# KOF XI Player Struct Memory Map

Reverse engineering notes for The King of Fighters XI (PCSX2).
All offsets are relative to the start of the `player` struct in PS2 emulated RAM.

## Known Struct Layout

| Offset | Size | Type | Field | Notes |
|--------|------|------|-------|-------|
| `+000h` | 4 | `coordPair` | `position` | World X (2B) + Y (2B). Ground Y = 0x02A0 (672). |
| `+004h` | 4 | `float` | `unknown01` | Unknown float. |
| `+008h` | 16 | — | `padding01` | Unknown. |
| `+018h` | 8 | `fixedPair` | `velocity` | 16.16 fixed-point. +X = forward (scale by facing). +Y = up. |
| `+020h` | 108 | — | `padding02` | **anim_block_1** captures 128 bytes starting here (overlaps into padding03). |
| `+024h` | 4 | `s32 LE` | `moveDisplacement` | Large negative values during special attacks (actions 11, 194). Zero during idle. |
| `+030h` | 1 | `u8` | `battleState` | Toggles 1↔2. `2` during active actions, `1` during idle/recovery. |
| `+034h` | 2 | `u16 LE` | `battleStateAux` | Co-toggles with `battleState`. Values `0` or `0xDAAAh`. |
| `+03Ch` | 2 | `u16 LE` | `prevPosX` | Previous-frame X position (shadow). |
| `+03Dh` | 1 | `u8` | `stunCountdown` | Counts down 5→4→…→0 during hit stun phases. |
| `+03Eh` | 2 | `u16 LE` | `prevPosY` | Previous-frame Y position (shadow). |
| `+03Fh` | 1 | `u8` | `posPhase` | Values 1 or 2. Lags behind `battleState`. |
| `+048h` | 2 | `u16 LE` | `effectDataA` | High-cardinality; changes rapidly only during action 194 (cinematic super). |
| `+04Dh` | 1 | `u8` | `effectDataB` | 84 unique values; changes during action 194 animations. |
| `+054h` | 4 | `s32 LE` | `gravityOrVelocity` | Small negative values (−7, −16, −17) near attack ends. Possible vertical velocity. |
| `+08Ch` | 1 | `byte` | `facing` | `0x00` = left, `0x02` = right. |
| `+08Dh` | 128 | — | `padding03` | **anim_block_2** captures 128 bytes starting here. |
| `+0ECh` | 1 | `u8` | `actionID` | Current move/action ID. See Action ID table below. |
| `+0EEh` | 1 | `u8` | `prevActionID` | Previous action ID. Stores last action when a new one starts. |
| `+0F2h` | 2 | `s16 LE` | `actionSignal` | Default `0xFFFF`. Flashes 0 or 5 for 1 frame at action transitions. |
| `+0F6h` | 1 | `u8` | `superSignal` | Transition signal. Only fires during action 194. Values 0 or 2. |
| `+10Dh` | 128 | — | `padding03b` | **anim_block_4** captures 128 bytes here. **ALL STATIC** — no bytes change during gameplay. |
| `+1C0h` | 2 | `uword` | `unknown02` | Unknown status word. |
| `+1C2h` | 166 | — | `padding04` | **anim_block_3** captures 128 bytes starting here. |
| `+200h` | 2 | `u16 LE` | `animDataPtr` | Animation data table pointer/index. Banks by character. +2 per phase. |
| `+204h` | 1 | `u8` | `actionCategory` | Maps action IDs to animation categories. 1:1 with actions. |
| `+226h` | 1 | `u8` | `charBankSelector` | Character/animation bank index. Changes only at tag-ins. |
| `+268h` | 35+ | `playerFlags` | `flags` | Status flags. `+268h+01Bh` = `collisionActive` (bit 4). |
| `+28Bh` | 128 | — | `padding05` | **anim_block_5** captures 128 bytes starting here. |
| `+2A4h` | 1 | `u8` | `animFrameIndex` | **Animation frame counter.** Increments +1 per anim frame (~5 game frames). Resets on action change. |
| `+2A5h` | 1 | `u8` | `animPlayFlag` | 0 or 1. Related to animation playback state. |
| `+2A8h` | 2 | `s16 LE` | `spriteOffsetX` | Sprite/hitbox offset X. Range −101 to −9. Changes per anim frame. |
| `+2AAh` | 2 | `s16 LE` | `spriteOffsetY` | Sprite/hitbox offset Y. Range −158 to −51. Changes at action transitions. |
| `+2AEh` | 1 | `u8` | `superStateFlag` | 0 or 16. Set to 16 during action 160 (super attack). |
| `+2B0h` | 1 | `u8` | `hitContactFlag` | 0 or 1. Toggles during contact actions (11, 194). |
| `+2B2h` | 1 | `u8` | `animPropertyA` | Values 6–19 (per-action). Possibly sprite width unit. |
| `+2B3h` | 1 | `u8` | `animPropertyB` | Values 7–18 (per-action). Possibly sprite height unit. |
| `+2B4h` | 1 | `u8` | `animPhaseToggle` | 0 or 1. Flips at animation frame boundaries. |
| `+314h` | 70 | `hitbox[7]` | `hitboxes` | 7 hitbox slots, 10 bytes each. |
| `+35Ah` | 68 | — | `padding06` | **NOT CAPTURED** by current logger. |
| `+39Eh` | 1 | `byte` | `hitboxesActive` | Bitmask for slots 0–5. |
| `+39Fh` | 337 | — | `padding07` | **NOT CAPTURED** by current logger. |
| `+4F0h` | 1 | `ubyte` | `unknown03` | Unknown status byte. |
| `+4F1h` | 145 | — | `padding08` | **NOT CAPTURED** by current logger. |
| `+582h` | 2 | `word` | `stunTimer` | Stun timer. `-1` = not stunned. |

Total known struct span: at least `0x584` (1412) bytes.

## Hitbox Sub-Struct (10 bytes each)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| `+0h` | 2 | `coordPair.x` | X offset from player origin |
| `+2h` | 2 | `coordPair.y` | Y offset from player origin |
| `+4h` | 1 | `byte` | `boxID` — type identifier |
| `+5h` | 2 | — | Unknown padding |
| `+7h` | 1 | `ubyte` | `width` — half-width |
| `+8h` | 1 | `ubyte` | `height` — half-height |
| `+9h` | 1 | — | Unknown padding |

Slots: 0 = attackBox, 1–3 = vulnBox 1–3, 4 = grabBox, 5 = hb6, 6 = collisionBox.

## Action ID Table (Observed)

Compiled across v1 + v2 captures. Action IDs are per-action, **not** per-character.

| Action ID | Category Code (+204h) | Behavior (inferred) |
|-----------|----------------------|---------------------|
| 0 | 0 | Idle (standing neutral) |
| 1 | 1 | Walk backward |
| 2 | 2 | Walk forward |
| 4 | 4 | Crouch / jump startup |
| 5 | 3 | Jumping (airborne) |
| 6 | 5 | Landing recovery |
| 8 | 32 | Attack startup (grounded) |
| 11 | 33 | Attack active / mid-combo |
| 23 | 35 | Attack recovery |
| 89 | 53 | Special move A |
| 90 | 73 | Special move B |
| 92 | 53 | Special move C |
| 93 | 75 | Special move D |
| 95 | 53/122 | EX/super startup |
| 96 | 73/122 | Super active |
| 98 | 53/122/255 | Special move (multi-phase) |
| 99 | 75/122 | Special move (multi-phase) |
| 160 | 13/122 | Super attack |
| 194 | 57/120 | Cinematic / tag super |

### Animation Frame Ranges per Action

Each action uses a specific sub-range of the `animFrameIndex` (+2A4h):

| Action | Frame Range | Loop? |
|--------|------------|-------|
| 0 (idle) | 0–9 | Yes (cycles) |
| 1 (walk back) | 10–21 | Yes |
| 2 (walk fwd) | 22–29 | Yes |
| 4 (crouch/jump) | 58–60 | No |
| 5 (jumping) | 64–69 | Partial |
| 8 (atk startup) | 30–31 | No |
| 11 (atk active) | 32–39 | Yes |
| 23 (atk recov) | 30–31 | No |
| 89 (special A) | 180–187 | — |
| 90 (special B) | 196–200 | — |
| 92 (special C) | 145–153 | — |
| 93 (special D) | 219–223 | — |
| 95 (super start) | 231–238 | — |
| 160 (super atk) | 28–37 | — |

## Team Struct

Addresses (NTSC-U): P1 = `0x008A9690`, P2 = `0x008A98D8`.

| Offset | Size | Type | Field |
|--------|------|------|-------|
| `+001h` | 1 | `byte` | `leader` (0/1/2) |
| `+003h` | 1 | `byte` | `point` — active character index |
| `+007h` | 1 | `byte` | `comboCounter` |
| `+01Ah` | 1 | `byte` | `tagCounter` |
| `+028h` | 3 | `byte[3]` | `teamPositions` |
| `+038h` | 4 | `udword` | `super` — super meter (0x70 = 1 bar) |
| `+03Ch` | 4 | `udword` | `skillStock` (0x70 = 1 stock) |
| `+0C0h` | 64 | `intptr_t[16]` | `projectiles` — indirect pointers |
| `+150h` | 96 | `playerExtra[3]` | Per-character roster data |
| `+240h` | 2 | `word` | `currentX` |

## PlayerExtra Sub-Struct (32 bytes each)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| `+000h` | 1 | `byte` | `charID` (see roster.lua) |
| `+001h` | 1 | `byte` | `isSelected` (-1 = no, 0 = yes) |
| `+004h` | 2 | `word` | `health` (0x70 = full) |
| `+006h` | 2 | `word` | `visibleHealth` |
| `+008h` | 2 | `word` | `stun` (0x70 = full, 0 = stunned) |
| `+00Ah` | 2 | `word` | `guard` (0x70 = full, 0 = guard crushed) |
| `+00Ch` | 1 | `byte` | `teamPosition` |

## Global Pointers (NTSC-U)

| Address | Type | Description |
|---------|------|-------------|
| `0x008A26E0` | `playerTable` | 2×3 array of pointers to `player` structs |
| `0x008A9660` | `camera` | Camera position + restrictor |
| `0x008A9690` | `team` | Team 1 struct |
| `0x008A98D8` | `team` | Team 2 struct |

## Other Revisions

| Revision | teamPtrs | playerTablePtr | cameraPtr |
|----------|----------|----------------|-----------|
| NTSC-J | `0x009BDB50`, `0x009BDD98` | `0x009B6BC0` | `0x009BDB20` |
| NTSC-U | `0x008A9690`, `0x008A98D8` | `0x008A26E0` | `0x008A9660` |
| PAL | `0x008EF810`, `0x008EFA58` | `0x008E8860` | `0x008EF7E0` |

## Memory Logger Capture Regions

The `memlogger.lua` (F10 toggle) captures three 128-byte raw regions per frame:

| Region | Player Offset | Byte Range | Coverage |
|--------|--------------|------------|----------|
| `anim_block_1` | `+020h` | `+020h` to `+09Fh` | padding02 (post-velocity) |
| `anim_block_2` | `+08Dh` | `+08Dh` to `+10Ch` | padding03 (post-facing) |
| `anim_block_3` | `+1C2h` | `+1C2h` to `+241h` | padding04 (pre-flags) |

## Memory Logger Capture Regions (v2)

The `memlogger.lua` (F10 toggle) captures six 128-byte raw regions per frame:

### Player Struct Regions

| Region | Player Offset | Byte Range | Coverage |
|--------|--------------|------------|----------|
| `anim_block_1` | `+020h` | `+020h` to `+09Fh` | padding02 (post-velocity) |
| `anim_block_2` | `+08Dh` | `+08Dh` to `+10Ch` | padding03 (post-facing) |
| `anim_block_3` | `+1C2h` | `+1C2h` to `+241h` | padding04 (pre-flags) |
| `anim_block_4` | `+10Dh` | `+10Dh` to `+18Ch` | **NEW** Gap A (post-block2) |
| `anim_block_5` | `+28Bh` | `+28Bh` to `+30Ah` | **NEW** Gap C (post-flags → hitboxes) |

### Team Struct Region

| Region | Team Offset | Byte Range | Coverage |
|--------|------------|------------|----------|
| `team_block` | `+040h` | `+040h` to `+0BFh` | **NEW** padding05 (candidate input) |

**Overlap notes:**
- block1/block2 overlap at `+08Dh`–`+09Fh` (19 bytes).
- block2 end (`+10Ch`) is contiguous with block4 start (`+10Dh`).
- block5 extends into the hitbox region: `+28Bh`–`+30Ah` covers the pre-hitbox gap AND the first ~6 hitbox slots.

### Remaining Uncaptured Gaps

| Gap | Range | Size | Notes |
|-----|-------|------|-------|
| Pre-block | `+000h` – `+01Fh` | 32 bytes | Position, velocity (already known) |
| Gap B2 | `+18Dh` – `+1C1h` | 53 bytes | Between block4 end and unknown02. **Frame timer candidate.** |
| Gap D2 | `+242h` – `+28Ah` | 73 bytes | Between block3 end and block5 start. |
| Gap post-b5 | `+30Bh` – `+313h` | 9 bytes | Between block5 end and hitboxes start. |
| Gap D3 | `+35Ah` – `+39Dh` | 68 bytes | Post-hitboxes, pre-hitboxesActive. |
| Gap E | `+39Fh` – `+4EFh` | 337 bytes | Large gap, likely gameplay state. |
| Gap F | `+4F1h` – `+581h` | 145 bytes | Between unknown03 and stunTimer. |

**Note:** `anim_block_4` (`+10Dh`–`+18Ch`) is **entirely static** — 0 bytes changed across 1546 frames.
This region likely contains character constants (move properties, hitbox definitions) loaded once.
It can be deprioritized or replaced in future captures.

## Coordinate System

- **World X:** Positive = right. Origin is offscreen left.
- **World Y:** Ground = `0x02A0` (672). Y decreases as characters move upward.
- **Facing multiplier:** Hitbox X offsets are multiplied by `+1` (right) or `-1` (left).
- **Camera:** `camera.position.x` = left edge of visible screen. `camera.position.y` = top edge. Ground-level camera Y = `0x00E0` (224).
- **Fixed-point velocity:** Stored as 16.16 fixed-point (`value / 65536.0` for float).
