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
| `+08Ch` | 1 | `byte` | `facing` | `0x00` = left, `0x02` = right. |
| `+08Dh` | 307 | — | `padding03` | **anim_block_2** captures 128 bytes starting here. |
| `+1C0h` | 2 | `uword` | `unknown02` | Unknown status word. |
| `+1C2h` | 166 | — | `padding04` | **anim_block_3** captures 128 bytes starting here. |
| `+268h` | 35+ | `playerFlags` | `flags` | Status flags. `+268h+01Bh` = `collisionActive` (bit 4). |
| `+28Bh` | 137 | — | `padding05` | **NOT CAPTURED** by current logger. |
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
| Gap B2 | `+18Dh` – `+1BFh` | 51 bytes | Between block4 end and unknown02 |
| Gap D2 | `+30Bh` – `+39Dh` | 147 bytes | Between block5 end and hitboxesActive |
| Gap E | `+39Fh` – `+4EFh` | 337 bytes | Between hitboxesActive and unknown03 |
| Gap F | `+4F1h` – `+581h` | 145 bytes | Between unknown03 and stunTimer |

## Coordinate System

- **World X:** Positive = right. Origin is offscreen left.
- **World Y:** Ground = `0x02A0` (672). Y decreases as characters move upward.
- **Facing multiplier:** Hitbox X offsets are multiplied by `+1` (right) or `-1` (left).
- **Camera:** `camera.position.x` = left edge of visible screen. `camera.position.y` = top edge. Ground-level camera Y = `0x00E0` (224).
- **Fixed-point velocity:** Stored as 16.16 fixed-point (`value / 65536.0` for float).
