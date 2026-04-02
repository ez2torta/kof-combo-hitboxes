-- KOF XI hitbox logger
-- Writes one NDJSON line per frame (newline-delimited JSON).
-- Toggle recording with F9.  Output file: kof_xi_hitboxes_<timestamp>.ndjson
-- The file is saved next to kof-hitboxes.exe (the working directory at launch).
--
-- Each line is a self-contained JSON object with this shape:
--   {
--     "frame":    <int>        -- 0-based frame counter since logging started
--     "camera_x": <int>,       -- left edge of visible screen in world coords
--     "camera_y": <int>,       -- top edge of visible screen in world coords
--     "players":  [ <player>, <player> ]
--   }
--
-- Each <player> object:
--   {
--     "player":       1 | 2
--     "point":        0 | 1 | 2    -- index of the active team member
--     "team": [                    -- all 3 characters in team order
--       {
--         "slot":      0 | 1 | 2
--         "char_id":   "0xNN"
--         "char_name": "<string>"
--         "health":    <int>       -- current HP  (full = 0x70 = 112)
--         "stun":      <int>       -- stun gauge  (full = 0x70)
--         "guard":     <int>       -- guard gauge (full = 0x70)
--         "active":    true|false  -- true = this char is currently on point
--       }
--     ]
--     "char_id":          "0xNN"   -- active character ID (shortcut)
--     "char_name":        "<string>"
--     "world_x":          <int>    -- active player origin X in world space
--     "world_y":          <int>    -- active player origin Y
--                                  --  (ground level = 0x02A0 = 672)
--     "facing":           1 | -1   -- +1 = right, -1 = left
--     "super_meter":      <int>    -- super meter (full bar = 0x70)
--     "stun_timer":       <int>    -- -1 = not stunned
--     "hitboxes_active":  "0xNN"   -- bitmask, 1 bit per slot (slots 0-5)
--     "hitboxes":         [ <hitbox>, ... ]
--   }
--
-- Each <hitbox> object:
--   {
--     "slot":       <int>        -- struct slot index (0-6)
--     "slot_name":  "<string>"   -- human-readable slot name
--     "box_id":     "0xNN"       -- raw byte ID from memory (see boxtypes.lua)
--     "box_type":   "<string>"   -- resolved type: "attack","vulnerable",etc.
--     "rel_x":      <int>        -- X offset from player origin (world pixels,
--                                --   positive = forward in facing direction)
--     "rel_y":      <int>        -- Y offset from player origin (world pixels,
--                                --   positive = upward)
--     "half_w":     <int>        -- half-width as stored in memory
--     "half_h":     <int>        -- half-height as stored in memory
--     "full_w":     <int>        -- full width  (half_w * 2)
--     "full_h":     <int>        -- full height (half_h * 2)
--     "world_cx":   <int>        -- hitbox center X in world space
--     "world_cy":   <int>        -- hitbox center Y in world space
--   }
--
-- NOTE: "rel_x" and "rel_y" are already scaled (raw struct value * 2).
-- NOTE: Projectile hitboxes are not included in this log; only the two
--   active characters are captured per frame.

local roster = require("game.pcsx2.kof_xi.roster")
local Logger = {}

local SLOT_NAMES = {
	[0] = "attackBox",
	[1] = "vulnBox1",
	[2] = "vulnBox2",
	[3] = "vulnBox3",
	[4] = "grabBox",
	[5] = "hb6",
	[6] = "collisionBox",
}

-- Returns the current working directory as an absolute path.
local function getCWD()
	local f = io.popen("cd")
	if not f then return "." end
	local cwd = f:read("*l") or "."
	f:close()
	return cwd
end

function Logger:new()
	local o = { file = nil, frameCount = 0, enabled = false, path = nil }
	setmetatable(o, self)
	self.__index = self
	return o
end

function Logger:open()
	local ts   = os.date("%Y%m%d_%H%M%S")
	local name = string.format("kof_xi_hitboxes_%s.ndjson", ts)
	local f, err = io.open(name, "w")
	if f then
		self.file       = f
		self.path       = getCWD() .. "\\" .. name
		self.frameCount = 0
		self.enabled    = true
		print(string.format("[Logger] Recording started. Press F9 to stop."))
		print(string.format("[Logger] Saving to: %s", self.path))
	else
		print(string.format("[Logger] ERROR: Could not open log file: %s", err or "unknown"))
	end
end

function Logger:close()
	if self.file then
		self.file:flush()
		self.file:close()
		self.file = nil
		print(string.format("[Logger] Stopped. Wrote %d frames to:", self.frameCount))
		print(string.format("[Logger]   %s", self.path))
	end
	self.enabled = false
end

function Logger:toggle()
	if self.enabled then
		self:close()
	else
		self:open()
	end
end

-- Serialise one hitbox slot to a JSON object string.
local function hitbox_json(hbox, slot, facing, px, py, boxtypes)
	local rel_x  = hbox.position.x * 2
	local rel_y  = hbox.position.y * 2
	local full_w = hbox.width  * 2
	local full_h = hbox.height * 2
	local wcx    = px + (rel_x * facing)
	local wcy    = py - rel_y
	-- Slot 4 is always "throw" regardless of boxID (same logic as captureEntity).
	local btype  = (slot == 4) and "throw" or boxtypes:typeForID(hbox.boxID)
	return string.format(
		'{"slot":%d,"slot_name":"%s","box_id":"0x%02X","box_type":"%s",'
		.. '"rel_x":%d,"rel_y":%d,"half_w":%d,"half_h":%d,'
		.. '"full_w":%d,"full_h":%d,"world_cx":%d,"world_cy":%d}',
		slot, SLOT_NAMES[slot] or "unknown",
		hbox.boxID, btype,
		rel_x, rel_y,
		hbox.width, hbox.height,
		full_w, full_h,
		wcx, wcy)
end

-- Serialise the full 3-character team roster.
local function team_json(team)
	local members = {}
	for i = 0, 2 do
		local extra    = team.p[i]
		local charID   = extra.charID
		local charName = roster[charID]
			or string.format("Unknown_0x%02X", charID)
		local active   = (i == team.point) and "true" or "false"
		members[i + 1] = string.format(
			'{"slot":%d,"char_id":"0x%02X","char_name":"%s",'
			.. '"health":%d,"stun":%d,"guard":%d,"active":%s}',
			i, charID, charName,
			extra.health, extra.stun, extra.guard, active)
	end
	return "[" .. table.concat(members, ",") .. "]"
end

-- Write one NDJSON line for the current frame.
function Logger:logFrame(game)
	if not (self.enabled and self.file) then return end

	local cam   = game.camera.position
	local parts = {}

	parts[#parts + 1] = string.format(
		'{"frame":%d,"camera_x":%d,"camera_y":%d,"players":[',
		self.frameCount, cam.x, cam.y)

	for which = 1, 2 do
		if which > 1 then parts[#parts + 1] = "," end

		local team   = game.teams[which]
		local player = game.players[which]
		local extra  = team.p[team.point]
		local charID = extra.charID
		local charName = roster[charID]
			or string.format("Unknown_0x%02X", charID)
		local facing = game:facingMultiplier(player)
		local px, py = player.position.x, player.position.y

		parts[#parts + 1] = string.format(
			'{"player":%d,"point":%d,"team":%s,'
			.. '"char_id":"0x%02X","char_name":"%s",'
			.. '"world_x":%d,"world_y":%d,"facing":%d,'
			.. '"super_meter":%d,"stun_timer":%d,'
			.. '"hitboxes_active":"0x%02X","hitboxes":[',
			which, team.point, team_json(team),
			charID, charName,
			px, py, facing,
			team.super, player.stunTimer,
			player.hitboxesActive)

		-- Active hitboxes (slots 0-5) from bitmask.
		local hboxes   = {}
		local boxstate = player.hitboxesActive
		for i = 0, 5 do
			if bit.band(boxstate, 1) ~= 0 then
				hboxes[#hboxes + 1] = hitbox_json(
					player.hitboxes[i], i, facing, px, py, game.boxtypes)
			end
			boxstate = bit.rshift(boxstate, 1)
		end

		-- Collision box (slot 6) â€” active when bit 4 of collisionActive is CLEAR.
		if bit.band(player.flags.collisionActive, 0x10) == 0 then
			local cb     = player.collisionBox
			local rel_x  = cb.position.x * 2
			local rel_y  = cb.position.y * 2
			local full_w = cb.width  * 2
			local full_h = cb.height * 2
			hboxes[#hboxes + 1] = string.format(
				'{"slot":6,"slot_name":"collisionBox","box_id":"0x%02X","box_type":"collision",'
				.. '"rel_x":%d,"rel_y":%d,"half_w":%d,"half_h":%d,'
				.. '"full_w":%d,"full_h":%d,"world_cx":%d,"world_cy":%d}',
				cb.boxID,
				rel_x, rel_y,
				cb.width, cb.height,
				full_w, full_h,
				px + (rel_x * facing), py - rel_y)
		end

		parts[#parts + 1] = table.concat(hboxes, ",")
		parts[#parts + 1] = "]}"
	end

	parts[#parts + 1] = "]}\n"
	self.file:write(table.concat(parts))
	self.frameCount = self.frameCount + 1
end

return Logger
