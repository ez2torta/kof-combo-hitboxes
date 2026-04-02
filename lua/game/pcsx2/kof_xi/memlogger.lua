-- KOF XI high-fidelity memory logger for animation reverse engineering.
-- Writes one NDJSON line per frame (newline-delimited JSON).
-- Toggle recording with F10.  Output file: kof_xi_memlog_<timestamp>.ndjson
--
-- Unlike the standard hitbox logger (F9), this logger captures RAW memory
-- regions from the player struct in addition to structured data.  It is
-- designed to maximise signal for a downstream analysis model to infer
-- animation IDs, frame indices, timers, and state-machine transitions.
--
-- Capture regions (v2):
--   anim_block_1  player+020h  128B  post-velocity padding
--   anim_block_2  player+08Dh  128B  post-facing padding
--   anim_block_3  player+1C2h  128B  pre-flags padding
--   anim_block_4  player+10Dh  128B  Gap A (post-block2 → unknown02)
--   anim_block_5  player+28Bh  128B  Gap C (post-flags → pre-hitboxes)
--   team_block    team+040h    128B  candidate input / game-state region

local ffi = require("ffi")
local bit = require("bit")

local MemLogger = {}

--------------------------------------------------------------------
-- constants
--------------------------------------------------------------------

-- Raw byte buffer type (128 bytes)
local rawBlock = ffi.typeof("uint8_t[128]")
local BLOCK_SIZE = 128

-- Memory region offsets relative to the player struct base
local ANIM_BLOCK_1_OFFSET = 0x020 -- padding02 region (unknown state)
local ANIM_BLOCK_2_OFFSET = 0x08D -- padding03 region (post-facing)
local ANIM_BLOCK_3_OFFSET = 0x1C2 -- padding04 region (pre-flags)
local ANIM_BLOCK_4_OFFSET = 0x10D -- Gap A: between block2 end and unknown02
local ANIM_BLOCK_5_OFFSET = 0x28B -- Gap C: between playerFlags and hitboxes
-- Offset within the team struct for candidate input / game-state data
local TEAM_BLOCK_OFFSET   = 0x040 -- team padding05 (128B gap before projectiles)

-- Slot name lookup (matches hitbox struct layout in types.lua)
local SLOT_NAMES = {
	[0] = "attackBox",
	[1] = "vulnBox1",
	[2] = "vulnBox2",
	[3] = "vulnBox3",
	[4] = "grabBox",
	[5] = "hb6",
	[6] = "collisionBox",
}

--------------------------------------------------------------------
-- helpers
--------------------------------------------------------------------

-- Convert a 128-byte FFI buffer to a JSON array of ints (0–255).
local function bytesToJSON(buf)
	local t = {}
	for i = 0, BLOCK_SIZE - 1 do
		t[i + 1] = tostring(buf[i])
	end
	return "[" .. table.concat(t, ",") .. "]"
end

-- Returns the current working directory as an absolute path.
local function getCWD()
	local f = io.popen("cd")
	if not f then return "." end
	local cwd = f:read("*l") or "."
	f:close()
	return cwd
end

--------------------------------------------------------------------
-- constructor
--------------------------------------------------------------------

function MemLogger:new()
	local o = {
		file       = nil,
		frameCount = 0,
		enabled    = false,
		path       = nil,
		-- target player to capture (1 or 2); default to player 1
		targetPlayer = 1,
		-- pre-allocated raw byte buffers (reused every frame)
		block1 = rawBlock(),
		block2 = rawBlock(),
		block3 = rawBlock(),
		block4 = rawBlock(), -- Gap A
		block5 = rawBlock(), -- Gap C
		teamBlock = rawBlock(), -- team struct candidate input region
	}
	setmetatable(o, self)
	self.__index = self
	return o
end

--------------------------------------------------------------------
-- open / close / toggle
--------------------------------------------------------------------

function MemLogger:open()
	local ts   = os.date("%Y%m%d_%H%M%S")
	local name = string.format("kof_xi_memlog_%s.ndjson", ts)
	local f, err = io.open(name, "w")
	if f then
		self.file       = f
		self.path       = getCWD() .. "\\" .. name
		self.frameCount = 0
		self.enabled    = true
		print("[MemLogger] Recording started (player "
			.. self.targetPlayer .. "). Press F10 to stop.")
		print("[MemLogger] Saving to: " .. self.path)
	else
		print("[MemLogger] ERROR: Could not open log file: "
			.. (err or "unknown"))
	end
end

function MemLogger:close()
	if self.file then
		self.file:flush()
		self.file:close()
		self.file = nil
		print(string.format(
			"[MemLogger] Stopped. Wrote %d frames to:", self.frameCount))
		print(string.format("[MemLogger]   %s", self.path))
	end
	self.enabled = false
end

function MemLogger:toggle()
	if self.enabled then
		self:close()
	else
		self:open()
	end
end

--------------------------------------------------------------------
-- per-frame capture
--------------------------------------------------------------------

-- Emit a single hitbox slot as a JSON object.  Always emitted regardless
-- of whether the slot is active.
local function hitboxJSON(player, slot, isActive, facing, px, py)
	local hb = player.hitboxes[slot]
	-- world-space centre of the hitbox
	local wx = px + (hb.position.x * 2 * facing)
	local wy = py - (hb.position.y * 2)
	return string.format(
		'{"slot":%d,"box_id":%d,"active":%s,'
		.. '"x":%.1f,"y":%.1f,"half_w":%d,"half_h":%d}',
		slot,
		hb.boxID,
		isActive and "true" or "false",
		wx, wy,
		hb.width,
		hb.height)
end

-- Write one NDJSON line for the current frame.
--   game        – the KOF_XI game object (has :read(), players, teams, etc.)
function MemLogger:logFrame(game)
	if not (self.enabled and self.file) then return end

	local which  = self.targetPlayer
	local team   = game.teams[which]
	local player = game.players[which]

	-- Resolve the PS2 RAM address of the active player struct.
	local playerPtr = game.playerTable.p[which - 1][team.point]

	-- Read five 128-byte raw memory regions from the player struct.
	-- game:read() adds RAMbase and reads via ReadProcessMemory.
	local ok1 = pcall(game.read, game, playerPtr + ANIM_BLOCK_1_OFFSET, self.block1)
	local ok2 = pcall(game.read, game, playerPtr + ANIM_BLOCK_2_OFFSET, self.block2)
	local ok3 = pcall(game.read, game, playerPtr + ANIM_BLOCK_3_OFFSET, self.block3)
	local ok4 = pcall(game.read, game, playerPtr + ANIM_BLOCK_4_OFFSET, self.block4)
	local ok5 = pcall(game.read, game, playerPtr + ANIM_BLOCK_5_OFFSET, self.block5)
	if not (ok1 and ok2 and ok3 and ok4 and ok5) then return end

	-- Read 128 bytes from team struct (candidate input / game-state region).
	local teamPtr = game.teamPtrs[which]
	local ok6 = pcall(game.read, game, teamPtr + TEAM_BLOCK_OFFSET, self.teamBlock)
	if not ok6 then return end

	local facing = game:facingMultiplier(player)
	local px, py = player.position.x, player.position.y

	-- Velocity: 16.16 fixed-point → float
	local vx = player.velocity.x.value / 65536.0
	local vy = player.velocity.y.value / 65536.0

	-------------------------------------------------------------
	-- Build the JSON line
	-------------------------------------------------------------
	local parts = {}

	-- frame + timestamp
	parts[#parts + 1] = string.format(
		'{"frame":%d,"timestamp":%.6f', self.frameCount, os.clock())

	-- player envelope
	parts[#parts + 1] = string.format(
		',"player":{"base_address":"0x%08X"', playerPtr)

	-- position
	parts[#parts + 1] = string.format(
		',"position":{"x":%d,"y":%d}', px, py)

	-- velocity
	parts[#parts + 1] = string.format(
		',"velocity":{"x":%.6f,"y":%.6f}', vx, vy)

	-- facing
	parts[#parts + 1] = string.format(',"facing":%d', facing)

	-- input ----------------------------------------------------------
	-- KOF XI does not expose a conveniently mapped input struct at a
	-- known, stable address.  The team_block capture below covers the
	-- most likely candidate region (team+040h).  We emit a placeholder
	-- input block here to keep the schema stable; the downstream model
	-- should derive input from team_block and/or memory_regions.
	--
	-- Button mapping (user config):
	--   Square=A(LP), Cross=B(LK), Triangle=C(HP), Circle=D(HK), R1=E(tag)
	-- PS2 pad word (active-LOW): byte0=[Sel,L3,R3,Start,Up,Rt,Dn,Lt]
	--                            byte1=[L2,R2,L1,R1,Tri,Cir,Crs,Sq]
	parts[#parts + 1] = ',"input":'
		.. '{"up":false,"down":false,"left":false,"right":false,'
		.. '"a":false,"b":false,"c":false,"d":false,"e":false}'

	-- hitboxes (all 7 slots, always) ---------------------------------
	parts[#parts + 1] = ',"hitboxes":['
	local hbParts = {}
	for slot = 0, 6 do
		local isActive
		if slot <= 5 then
			isActive = bit.band(
				bit.rshift(player.hitboxesActive, slot), 1) == 1
		else
			-- collision box: active when bit 4 of collisionActive is CLEAR
			isActive = bit.band(player.flags.collisionActive, 0x10) == 0
		end
		hbParts[#hbParts + 1] = hitboxJSON(
			player, slot, isActive, facing, px, py)
	end
	parts[#parts + 1] = table.concat(hbParts, ",")
	parts[#parts + 1] = "]"

	-- memory regions -------------------------------------------------
	parts[#parts + 1] = ',"memory_regions":{'
	parts[#parts + 1] = '"anim_block_1":' .. bytesToJSON(self.block1)
	parts[#parts + 1] = ',"anim_block_2":' .. bytesToJSON(self.block2)
	parts[#parts + 1] = ',"anim_block_3":' .. bytesToJSON(self.block3)
	parts[#parts + 1] = ',"anim_block_4":' .. bytesToJSON(self.block4)
	parts[#parts + 1] = ',"anim_block_5":' .. bytesToJSON(self.block5)
	parts[#parts + 1] = ',"team_block":'   .. bytesToJSON(self.teamBlock)
	parts[#parts + 1] = "}"

	-- close player + root
	parts[#parts + 1] = "}}\n"

	self.file:write(table.concat(parts))
	self.frameCount = self.frameCount + 1
end

return MemLogger
