local commontypes = require("game.commontypes")
local types = commontypes:new()

-- Game-specific struct definitions for KOF XI on Atomiswave (Flycast).
-- Little-endian, SH-4 CPU, 4-byte pointers.
--
-- Address references below are given as RAM offsets (relative to physical
-- 0x0C000000).  Add the dynamically-discovered Flycast_Common.RAMbase to
-- get the real absolute address in Flycast's process memory space.
--
-- Player struct offsets are identical to the PS2 version.
-- Team struct layout shares key offsets with PS2 (+003h point, +038h super,
-- +150h playerExtra) but uses entry pointers at +144h instead of a separate
-- playerTable.  Team spacing is 0x1F8 (vs PS2's 0x248).

types.typedefs = [[
#pragma pack(push, 1) /* DO NOT REMOVE THIS */
static const int PLAYERS = 2;
static const int CHARS_PER_TEAM = 3;
static const int BOXCOUNT = 7;

typedef struct {
	coordPair position;       // +000h: X/Y world position (4 bytes)
	float restrictor;         // +004h: Always equal to +1.0?
} camera;

typedef struct {
	coordPair position;       // +000h: X/Y offset from player origin
	byte boxID;               // +004h: Hitbox type
	byte padding01[0x002];    // +005h to +007h: Unknown
	ubyte width;              // +007h: Hitbox half-width
	ubyte height;             // +008h: Hitbox half-height
	byte padding02[0x001];    // +009h: Unknown (DO NOT REMOVE)
} hitbox;

typedef struct {
	byte padding01[0x01B];    // +000h to +01Bh: Unknown
	byte collisionActive;     // +01Bh: Collision box active?
	byte padding02[0x007];    // +01Ch to +023h: Unknown
} playerFlags;

// Player struct offsets verified identical to PS2 version.
typedef struct {
	coordPair position;       // +000h: X/Y world position (4 bytes)
	float unknown01;          // +004h: Scale/unknown float
	byte padding01[0x010];    // +008h to +018h: Unknown
	fixedPair velocity;       // +018h: X/Y velocity (8 bytes)
	byte padding02[0x06C];    // +020h to +08Ch: Unknown
	byte facing;              // +08Ch: Facing (00h = left, 02h = right)
	byte padding03[0x133];    // +08Dh to +1C0h: Unknown
	uword unknown02;          // +1C0h: Unknown status word
	byte padding04[0x0A6];    // +1C2h to +268h: Unknown
	playerFlags flags;        // +268h: Various status flags
	byte padding05[0x089];    // +28Bh to +314h: Unknown
	union {
		struct {
			hitbox attackBox;    // +314h: Attack hitbox
			hitbox vulnBox1;     // +31Eh: Vulnerable hitbox
			hitbox vulnBox2;     // +328h: Vulnerable hitbox
			hitbox vulnBox3;     // +332h: Vulnerable hitbox
			hitbox grabBox;      // +33Ch: Grab "attack" hitbox
			hitbox hb6;          // +34Ch: Unused?
			hitbox collisionBox; // +350h: Collision hitbox
		};
		hitbox hitboxes[7]; // +314h: Hitboxes list
	};
	byte padding06[0x044];    // +35Ah to +39Eh: Unknown
	byte hitboxesActive;      // +39Eh: Hitbox active state flags
	byte padding07[0x151];    // +39Fh to +4F0h: Unknown
	ubyte unknown03;          // +4F0h: Unknown status byte
	byte padding08[0x091];    // +4F1h to +582h: Unknown
	word stunTimer;           // +582h: Stun state timer (-1 = not stunned)
} player;
typedef player projectile;

// Atomiswave playerExtra layout (0x20 bytes, shifted by 1 byte from PS2).
typedef struct {
	byte unknown01;           // +000h: Unknown (always 0?)
	byte charID;              // +001h: Character ID (see roster.lua)
	byte padding01[0x002];    // +002h to +004h: Unknown flags
	byte charID2;             // +004h: Character ID (duplicate)
	byte padding02[0x002];    // +005h to +007h: Unknown flags
	byte marker;              // +007h: Always 0xFF
	word health;              // +008h: HP (0x70 = full HP, -1 = KO'd)
	word visibleHealth;       // +00Ah: Visible HP
	word maxHealth;           // +00Ch: Max HP (0x70)
	word maxHealth2;          // +00Eh: Max HP (duplicate)
	byte teamPosition;        // +010h: Current team position (0, 1 or 2)
	byte marker2;             // +011h: Always 0xFF
	byte padding03[0x00E];    // +012h to +020h: Padding
} playerExtra;

// Atomiswave team struct (0x1F8 bytes, spacing between P1 and P2).
// Shares key offsets with PS2 (+003h point, +038h super, +150h playerExtra)
// but uses entry pointers at +144h instead of a separate playerTable.
typedef struct {
	byte unknown01;           // +000h: Unknown
	byte leader;              // +001h: Selected leader (0, 1 or 2)
	byte unknown02;           // +002h: Unknown
	byte point;               // +003h: Current "point" character (0/1/2)
	byte padding01[0x003];    // +004h to +007h: Unknown
	byte comboCounter;        // +007h: Combo counter
	byte comboCounter2;       // +008h: Combo counter (duplicate?)
	byte padding02[0x02F];    // +009h to +038h: Unknown
	udword super;             // +038h: Super meter (0x70 = 1 full bar)
	udword skillStock;        // +03Ch: Skill stock (0x70 = 1 full stock)
	byte padding03[0x080];    // +040h to +0C0h: Unknown
	intptr_t projectiles[16]; // +0C0h: Indirect pointers to projectiles
	byte padding04[0x040];    // +100h to +140h: Unknown
	intptr_t unusedPtr;       // +140h: Always 0
	intptr_t entries[CHARS_PER_TEAM]; // +144h: SH-4 ptrs to obj pool entries
	playerExtra p[CHARS_PER_TEAM]; // +150h: "playerExtra" struct instances
} team;

#pragma pack(pop)
]]

return types
