# KOF XI Animation System — Memlog Analysis Report

Analysis of `kof_xi_memlog_20260402_000502.ndjson` (1148 frames, ~17.7 seconds).
Capture sampled at ~15 ms/frame (≈66 Hz), which is slightly faster than the game's
60 fps tick rate — some game frames were double-sampled.

---

## 1. Dataset Overview

| Metric | Value |
|--------|-------|
| Total frames captured | 1,148 |
| Frame continuity gaps | 0 (perfect) |
| Duration | 17.73 s |
| Sample interval | avg 15.5 ms, min 6 ms, max 17 ms |
| Unique base addresses | 3 (`0x0081FB84`, `0x0081EBC4`, `0x00820B44`) |
| Unique positions | 222 |
| Facing changes | 4 |
| Hitbox-active transitions | 90 |

The three distinct base addresses correspond to three **different characters** being
on point at different times:

| Frames | Base Address | Character (inferred) |
|--------|-------------|---------------------|
| 0–340 | `0x0081FB84` | Character A |
| 341–819 | `0x0081EBC4` | Character B (tag-in at frame 341) |
| 820–1147 | `0x00820B44` | Character C (tag-in at frame 820) |

---

## 2. Key Discoveries

### 2.1 Animation Data Pointer — `player+0x200h`

**Location:** `anim_block_3[62:64]` → absolute offset `player+0x200h` (u16 LE).

This 16-bit value acts as a **pointer or index into the animation data table**.
It changes exactly at animation boundaries and exhibits small increments of +2
between sequential animation phases within the same move.

**Evidence:**
- 78 distinct transitions across 1,148 frames
- Values cluster by character: Char A uses `0x4952`–`0x4BA2`, Char B uses
  `0x1CD2`–`0x1EF5`, Char C uses `0x0052`–`0x02B0`
- Sequential animation phases increment by 2 (e.g., `0x498A` → `0x498C` → `0x498E`)
- Returns to a "home" value when idle (e.g., `0x4952` for Char A)

**Interpretation:** This is likely an **offset into a per-character animation data
bank** in PS2 RAM. The +2 increments between phases suggest each entry in the
animation table is 2 bytes (possibly a pointer to frame data). The different value
ranges per character confirm each has its own animation bank.

### 2.2 Move / Action ID — `player+0x0ECh`

**Location:** `anim_block_2[95]` → absolute offset `player+0x0ECh` (u8).

This single byte is a **move or action identifier**. It has a perfect 1:1
correspondence with the animation pointer at `+0x200h` — whenever the animation
pointer changes, this byte changes to a new value.

**Evidence:**
- 26 unique values observed: `{0, 1, 2, 4, 5, 6, 24, 29, 30, 31, 83, 85, 89, 90, 94, 95, 99, 107, 110, 185, 189, 194, 204, 208, 218, 219}`
- Value `0` = idle state
- Values are small integers, NOT pointers
- Changes synchronize exactly with animation pointer transitions

**Partial action ID map (Char A, inferred from movement patterns):**

| Action ID | Animation Ptr | Behavior (observed) |
|-----------|--------------|---------------------|
| 0 | `0x4952` | Idle (standing) |
| 1 | `0x4954` | Walk forward (short) |
| 2 | `0x4956` | Walk backward |
| 4 | `0x495A` | Walk animation phase 1 |
| 5 | `0x495C` | Walk animation phase 2 |
| 6 | `0x495E` | Walk animation phase 3 |
| 29 | `0x498A` | Dash/run startup |
| 30 | `0x498C` | Dash/run loop |
| 31 | `0x498E` | Dash/run brake |
| 89 | `0x4A02` | Pre-fight intro / taunt? |
| 99 | `0x4A20` | Jump startup? |
| 189 | `0x4B63` | Jump ascending |
| 204 | `0x4B96` | Jump descending |
| 208 | `0x4BA2` | Landing recovery |

### 2.3 Animation Phase Property — `player+0x204h`

**Location:** `anim_block_3[66]` → absolute offset `player+0x204h` (u8).

This byte remains **constant within each animation segment** and changes to a new
value when the animation pointer changes. It appears to be a **property of the
current animation phase** rather than a per-frame counter.

**Observed values and contexts:**

| Value | Context |
|-------|---------|
| 0 | Idle |
| 1–7 | Walking / light movement |
| 38, 97 | Attack animations (Char B) |
| 53 | Intro animation |
| 79, 178 | Jump animations |
| 122, 132 | Jump/landing phases |

**Hypothesis:** This may encode the total duration or a frame-data offset for
the current animation phase. More data (with controlled inputs) is needed to
confirm.

### 2.4 Character / Animation Bank Selector — `player+0x226h`

**Location:** `anim_block_3[64]` → absolute offset `player+0x226h` (u8).

Only 3 values observed, changing exclusively at character tag-in transitions:

| Value | Frames | Character |
|-------|--------|-----------|
| 28 | 0–340 | Character A |
| 3 | 341–819 | Character B |
| 51 | 820–1147 | Character C |

This is likely a **character ID** or **animation bank index**. Cross-reference
with `roster.lua` char IDs to confirm.

### 2.5 Binary Battle State — `player+0x030h`

**Location:** `anim_block_1[16]` → absolute offset `player+0x030h` (u8).

Toggles between values `1` and `2`. Transitions to `2` during active move
execution (attacks, dashes), returns to `1` during idle or recovery. Could be
a **"in action" flag** or **animation-is-interruptible** state.

### 2.6 Hit/Block Stun Countdown — `player+0x03Dh`

**Location:** `anim_block_1[29]` → absolute offset `player+0x03Dh` (u8).

4 unique values: `{0, 1, 2, 3}`. During attack sequences, this counts down
from 3 → 2 → 1 → 0 across sequential frames. Likely a **hit stun level** or
**multi-hit phase counter**.

### 2.7 Sub-frame Interpolation Phase — `player+0x04Ch`

**Location:** `anim_block_1[44]` → absolute offset `player+0x04Ch` (u8).

Cycles through `{0, 64, 128, 192}` during active animations. These are 4
evenly spaced values in a byte: `0/4, 64/4, 128/4, 192/4` = `{0, 16, 32, 48}`
quarter-values, suggesting this is a **sub-frame phase counter for animation
interpolation** (4 ticks per animation frame).

### 2.8 Attack Timer — `player+0x06Ch`

**Location:** `anim_block_1[108]` → absolute offset `player+0x06Ch` (u8).

Appears only during attack/special move sequences. Takes value `60` during
attacks and `0` otherwise. May encode the **total active frames** of the
current attack or a **hitstop duration**.

### 2.9 Previous-frame Position Shadow — `player+0x03Ch`

**Location:** `anim_block_1[28:32]` → absolute offset `player+0x03Ch`.

`[28:30]` as u16LE = X position, `[30:32]` as u16LE = Y position. These mirror
`player.position` but stored deeper in the struct. Likely a **previous-frame
position** used for interpolation or collision detection.

---

## 3. Correlation Matrix

Events at the same frames confirm relationships between discovered fields:

| Frame | Event | `+0ECh` | `+200h` | `+204h` | `+030h` | `+03Dh` |
|-------|-------|---------|---------|---------|---------|---------|
| 85 | Intro anim start | 0→89 | `4952`→`4A02` | 0→53 | — | — |
| 103 | Return to idle | 89→0 | `4A02`→`4952` | 53→0 | — | — |
| 104 | Walk forward | 0→1 | `4952`→`4954` | 0→1 | — | — |
| 114 | Dash startup | 0→29 | `4952`→`498A` | 0→7 | — | — |
| 341 | **TAG-IN** (Char B) | — | `49FA`→`1D76` | 126→38 | 1→2 | 0→3 |
| 820 | **TAG-IN** (Char C) | — | `1D7A`→`00F6` | — | — | — |

---

## 4. What's Missing — The Frame Timer

**No per-frame incrementing counter was found** in any of the three captured
memory regions. The animation system must store its frame timer/countdown in one
of the **uncaptured gaps** of the player struct.

**v2 logger now covers two of the highest-priority gaps:**

| New Region | Offset Range | Size | Priority |
|-----------|-------------|------|----------|
| `anim_block_4` | `+10Dh` – `+18Ch` | 128 B | **HIGH** — covers most of Gap A |
| `anim_block_5` | `+28Bh` – `+30Ah` | 128 B | **HIGH** — covers Gap C + early hitbox overlap |
| `team_block` | team `+040h` – `+0BFh` | 128 B | **MEDIUM** — candidate input/game-state region |

Remaining uncovered candidate for the frame timer:

| Gap | Range | Size | Notes |
|-----|-------|------|-------|
| Gap B2 | `+18Dh` – `+1BFh` | 51 B | Tail of old Gap A, not yet covered |
| Gap D2 | `+30Bh` – `+39Dh` | 147 B | Post-hitbox, pre-hitboxesActive |
| Gap E | `+39Fh` – `+4EFh` | 337 B | Large gap, likely gameplay state |

---

## 5. Input State — Strategy

KOF XI on PCSX2 does not have a known, stable input buffer address. The game
reads PS2 pad data via the IOP subsystem and copies it into its own RAM.

**Current approach (v2):** The logger captures `team_block` (team struct
`+040h`, 128 bytes) which is a large unexplored region in the per-player team
struct. This is a strong candidate for containing processed input state,
since fighting games commonly store per-player input near team/player data.

**PS2 Pad Data Format (for reference):**
```
Byte 0 (active-LOW): [Select, L3, R3, Start, Up, Right, Down, Left]
Byte 1 (active-LOW): [L2, R2, L1, R1, Triangle, Circle, Cross, Square]
```
Buttons active-LOW means 0 = pressed, 1 = released.

**User's button mapping:**
| PS2 Button | KOF Button | Pad Byte 1 Bit |
|-----------|-----------|----------------|
| Square (□) | A (LP) | bit 0 |
| Cross (×) | B (LK) | bit 1 |
| Triangle (△) | C (HP) | bit 3 |
| Circle (○) | D (HK) | bit 2 |
| R1 | E (tag) | bit 4 |

**To locate the input buffer:** Run the v2 logger and perform:
1. Hold a single button for several seconds, then release
2. Repeat for each button individually
3. Tap each direction individually

Then scan `team_block` and all `anim_block_*` regions for bytes that flip
synchronously with button presses. Active-LOW 2-byte pad words (`0xFF7F` with
one bit cleared) are the signature to look for.

---

## 6. Next Steps

1. **Run v2 logger** with the new capture regions and perform controlled actions
   to find the frame timer in blocks 4/5 and input state in team_block.

2. **Controlled input test** — hold each button one at a time for 2-3 seconds
   to create clear on/off signatures in the raw data.

3. **Controlled animation test** — perform single known moves (stand A, crouch B,
   236A, etc.) with pauses between them to isolate animation segments.

4. **Two-player capture** — set `memlogger.targetPlayer = 2` to capture P2 data
   and correlate team_block contents between players.

5. **Correlate `+0x226h` with roster.lua** — verify the character bank selector
   against known char IDs.

6. **Longer capture with varied gameplay** — blocking, getting hit, throws, supers,
   and round transitions to fill out the action ID table.
