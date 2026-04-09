-- KOF XI Atomiswave — Full-struct frame capture logger.
-- Toggle recording with F9.  Output: kof_xi_aw_capture_<timestamp>.ndjson
--
-- Unlike the PS2 memlogger that captures selected 128-byte regions, this
-- logger captures COMPLETE player structs (0x584 bytes × 2), complete team
-- structs (0x1F8 bytes × 2), the camera struct, and additional game-state
-- regions.  This provides maximum data for offline analysis.
--
-- The capture uses the same entry-chain resolution as game.lua to find
-- the player struct addresses dynamically.
--
-- Design: capture-everything-once, analyze-offline.  Each NDJSON line
-- contains enough data to reconstruct the full game state at that frame.

local ffi = require("ffi")
local bit = require("bit")

local FrameCapture = {}

--------------------------------------------------------------------
-- constants
--------------------------------------------------------------------

local PLAYER_STRUCT_SIZE = 0x584   -- full player struct
local TEAM_STRUCT_SIZE   = 0x1F8   -- full team struct (AW)
local CAMERA_STRUCT_SIZE = 8       -- camera (posX, posY, float)
local BLOCK_SIZE         = 128     -- for extra memory regions

-- Additional memory regions to capture (RAM offsets, not player-relative)
-- These capture game-state data outside the player/team structs
local EXTRA_REGIONS = {
	-- Possible game state indicators near the team structs
	{ name = "pre_camera",   offset = 0x27CA00, size = 168 },  -- 168B before camera
	{ name = "post_teams",   offset = 0x27CF40, size = 256 },  -- after team P2
	-- Object pool header area
	{ name = "objpool_head", offset = 0x200000, size = 256 },
}

-- Pre-allocate buffers
local playerBuf = ffi.typeof("uint8_t[?]")
local teamBuf   = ffi.typeof("uint8_t[?]")
local cameraBuf = ffi.typeof("uint8_t[?]")
local extraBuf  = ffi.typeof("uint8_t[?]")

--------------------------------------------------------------------
-- helpers
--------------------------------------------------------------------

local function bufToHex(buf, size)
	local t = {}
	for i = 0, size - 1 do
		t[i + 1] = string.format("%02X", buf[i])
	end
	return table.concat(t)
end

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

function FrameCapture:new()
	local o = {
		file       = nil,
		frameCount = 0,
		enabled    = false,
		path       = nil,
		-- Pre-allocated buffers (reused every frame)
		pBuf1 = playerBuf(PLAYER_STRUCT_SIZE),
		pBuf2 = playerBuf(PLAYER_STRUCT_SIZE),
		tBuf1 = teamBuf(TEAM_STRUCT_SIZE),
		tBuf2 = teamBuf(TEAM_STRUCT_SIZE),
		cBuf  = cameraBuf(CAMERA_STRUCT_SIZE),
		eBufs = {},  -- extra region buffers
	}
	for i, region in ipairs(EXTRA_REGIONS) do
		o.eBufs[i] = extraBuf(region.size)
	end
	setmetatable(o, self)
	self.__index = self
	return o
end

--------------------------------------------------------------------
-- open / close / toggle
--------------------------------------------------------------------

function FrameCapture:open()
	local ts = os.date("%Y%m%d_%H%M%S")
	local name = string.format("kof_xi_aw_capture_%s.ndjson", ts)
	local f, err = io.open(name, "w")
	if f then
		self.file       = f
		self.path       = getCWD() .. "\\" .. name
		self.frameCount = 0
		self.enabled    = true
		print("[FrameCapture] Recording started. Press F9 to stop.")
		print("[FrameCapture] Saving to: " .. self.path)
	else
		print("[FrameCapture] ERROR: Could not open file: "
			.. (err or "unknown"))
	end
end

function FrameCapture:close()
	if self.file then
		self.file:flush()
		self.file:close()
		self.file = nil
		print(string.format(
			"[FrameCapture] Stopped. Wrote %d frames to:", self.frameCount))
		print(string.format("[FrameCapture]   %s", self.path))
	end
	self.enabled = false
end

function FrameCapture:toggle()
	if self.enabled then
		self:close()
	else
		self:open()
	end
end

--------------------------------------------------------------------
-- per-frame capture
--------------------------------------------------------------------

-- Resolve the RAM offset of the active player struct for a side (1 or 2).
-- Uses the same entry-chain logic as game.lua:capturePlayerState().
-- Returns playerOffset (RAM offset) or nil.
local function resolvePlayerOffset(game, which)
	local team = game.teams[which]
	-- Find entry whose teamPosition matches team.point
	local entryIdx = nil
	for e = 0, 2 do
		if team.p[e].teamPosition == team.point then
			entryIdx = e
			break
		end
	end
	if not entryIdx then return nil end

	local entryPtr = team.entries[entryIdx]
	local entryOff = game:sh4ToRAMOffset(entryPtr)
	if not entryOff then return nil end

	local dataPtr = game:readPtr(entryOff + 0x10)
	local dataOff = game:sh4ToRAMOffset(dataPtr)
	if not dataOff then return nil end

	local playerOff = dataOff - 0x614
	if playerOff < 0 or playerOff >= 0x01000000 then return nil end
	return playerOff
end

-- Write one NDJSON line capturing the full game state.
function FrameCapture:logFrame(game)
	if not (self.enabled and self.file) then return end
	if not game.RAMbase or game.RAMbase == 0 then return end

	local parts = {}

	-- Frame header
	parts[#parts + 1] = string.format(
		'{"frame":%d,"timestamp":%.6f', self.frameCount, os.clock())

	-- Camera
	local camOk = pcall(game.read, game, game.cameraPtr, self.cBuf)
	if camOk then
		parts[#parts + 1] = ',"camera":"' .. bufToHex(self.cBuf, CAMERA_STRUCT_SIZE) .. '"'
	end

	-- Teams and players
	local teamPtrs = game.teamPtrs
	local playerBufs = { self.pBuf1, self.pBuf2 }
	local teamBufs = { self.tBuf1, self.tBuf2 }

	for side = 1, 2 do
		local sideKey = string.format('"p%d"', side)

		-- Read team struct
		local teamOk = pcall(game.read, game, teamPtrs[side], game.teams[side])
		if not teamOk then
			parts[#parts + 1] = "," .. sideKey .. ":null"
			goto continue
		end

		-- Also read raw team bytes
		local tRawOk = pcall(game.read, game, teamPtrs[side], teamBufs[side])

		-- Start side object
		parts[#parts + 1] = "," .. sideKey .. ":{"

		-- Team raw hex
		if tRawOk then
			parts[#parts + 1] = '"team":"'
				.. bufToHex(teamBufs[side], TEAM_STRUCT_SIZE) .. '"'
		end

		-- Team structured data (quick summary)
		local team = game.teams[side]
		parts[#parts + 1] = string.format(
			',"point":%d,"leader":%d,"combo":%d,"super":%d,"skill":%d',
			team.point, team.leader, team.comboCounter,
			team.super, team.skillStock)

		-- Player extras
		parts[#parts + 1] = ',"roster":['
		local rosterParts = {}
		for e = 0, 2 do
			local pe = team.p[e]
			rosterParts[#rosterParts + 1] = string.format(
				'{"slot":%d,"charID":%d,"hp":%d,"teamPos":%d}',
				e, pe.charID, pe.health, pe.teamPosition)
		end
		parts[#parts + 1] = table.concat(rosterParts, ",") .. "]"

		-- Resolve and capture player struct
		local playerOff = resolvePlayerOffset(game, side)
		if playerOff then
			local pOk = pcall(game.read, game, playerOff, playerBufs[side])
			if pOk then
				parts[#parts + 1] = ',"player_offset":"0x'
					.. string.format("%06X", playerOff) .. '"'
				parts[#parts + 1] = ',"player":"'
					.. bufToHex(playerBufs[side], PLAYER_STRUCT_SIZE) .. '"'

				-- Quick structured summary of key fields
				local player = game.players[side]
				-- Re-read via the typed struct for field access
				local pOk2 = pcall(game.read, game, playerOff, player)
				if pOk2 then
					local facing = game:facingMultiplier(player)
					parts[#parts + 1] = string.format(
						',"pos":[%d,%d],"facing":%d,"action":%d'
						.. ',"prevAction":%d,"animFrame":%d,"hbActive":%d',
						player.position.x, player.position.y,
						facing,
						-- Read action bytes from raw buffer since padding covers them
						playerBufs[side][0x0EC],  -- actionID
						playerBufs[side][0x0EE],  -- prevActionID
						playerBufs[side][0x2A4],  -- animFrameIndex
						player.hitboxesActive)
				end
			end
		else
			parts[#parts + 1] = ',"player":null'
		end

		parts[#parts + 1] = "}"
		::continue::
	end

	-- Extra memory regions
	parts[#parts + 1] = ',"extra":{'
	local extraParts = {}
	for i, region in ipairs(EXTRA_REGIONS) do
		local eOk = pcall(game.read, game, region.offset, self.eBufs[i])
		if eOk then
			extraParts[#extraParts + 1] = string.format(
				'"%s":"', region.name)
				.. bufToHex(self.eBufs[i], region.size) .. '"'
		end
	end
	parts[#parts + 1] = table.concat(extraParts, ",")
	parts[#parts + 1] = "}"

	-- Close root
	parts[#parts + 1] = "}\n"

	self.file:write(table.concat(parts))
	self.frameCount = self.frameCount + 1
end

return FrameCapture
