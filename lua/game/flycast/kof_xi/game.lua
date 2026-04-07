local ffi = require("ffi")
local ffiutil = require("ffiutil")
local luautil = require("luautil")
local winprocess = require("winprocess")
local winutil = require("winutil")
local hk = require("hotkey")
local colors = require("render.colors")
local types = require("game.flycast.kof_xi.types")
local boxtypes = require("game.flycast.kof_xi.boxtypes")
local BoxSet = require("game.boxset")
local BoxList = require("game.boxlist")
local Flycast_Common = require("game.flycast.common")
local KOF_Common = require("game.kof_common")
local KOF_XI_AW = KOF_Common:new({ whoami = "KOF_XI_AW" })

-- ========================================================================
-- KOF XI Atomiswave (Flycast) hitbox viewer
--
-- Flycast process detection and SH-4 RAM discovery are handled by
-- Flycast_Common.  Player data is accessed via object pool entries
-- referenced from the team struct, rather than the flat playerTable
-- used by the PS2 version.
-- ========================================================================

KOF_XI_AW.configSection = "kof_xi"
KOF_XI_AW.requiresProcessBase = false
KOF_XI_AW.basicWidth = 640
KOF_XI_AW.basicHeight = 480
KOF_XI_AW.absoluteYOffset = 0  -- TODO: determine correct value
KOF_XI_AW.boxesPerLayer = 20
-- game-specific constants
KOF_XI_AW.boxtypes = boxtypes
KOF_XI_AW.projCount = 16 -- per player (team)
KOF_XI_AW.playersPerTeam = 3

-- Known byte patterns in the KOF XI Atomiswave binary and their RAM offsets.
-- The BIOS copies the EPR flash (starting at offset 0x100) to main RAM
-- starting at physical address 0x0C010000.  This means:
--   EPR[X] -> RAM[X + 0xFF00]  (for X >= 0x100)
--
-- These signatures are used to:
-- 1. Find the 16 MB RAM base address in Flycast's process space
-- 2. Confirm that KOF XI (and not some other Atomiswave game) is loaded
KOF_XI_AW.gameSignatures = {
	-- "MUTEKI" at EPR offset 0x100050 -> RAM offset 0x10FF50
	{ pattern = "MUTEKI", offset = 0x10FF50 },
	-- "Debug Menu" at EPR offset 0x10044C -> RAM offset 0x11034C
	{ pattern = "Debug Menu", offset = 0x11034C },
}

-- Secondary verification strings (checked after RAM base is found)
KOF_XI_AW.verificationStrings = {
	-- "sx_System" module signature at EPR offset 0x103A0D
	{ pattern = "sx_System", offset = 0x103A0D + 0xFF00 },
	-- "ADELHIDE" character name at EPR offset 0x100024
	{ pattern = "ADELHIDE", offset = 0x100024 + 0xFF00 },
}

-- ========================================================================
-- MEMORY ADDRESSES (RAM offsets relative to SH-4 physical 0x0C000000)
-- ========================================================================
KOF_XI_AW.cameraPtr = 0x27CAA8
KOF_XI_AW.teamPtrs = { 0x27CB50, 0x27CD48 } -- P1 team, P2 team (spacing 0x1F8)

function KOF_XI_AW:extraInit(noExport)
	if not noExport then
		types:export(ffi)
		self.boxset = BoxSet:new(self.boxtypes.order, self.boxesPerLayer,
			self.boxSlotConstructor, self.boxtypes)
	end
	self.players = ffiutil.ntypes("player", 2, 1)
	self.teams = ffiutil.ntypes("team", 2, 1)
	self.camera = ffi.new("camera")
	self.projBuffer = ffi.new("projectile")
	self.pivots = BoxList:new(
		"pivots", (self.projCount + 1) * 2, self.pivotSlotConstructor)
	-- Used to dereference SH-4 pointers from team entry list
	self.entryBuf = ffi.new("uint8_t[32]")
end

-- Override the base class constructor to handle RAM discovery.
function KOF_XI_AW:new(source)
	local instance = KOF_Common.new(self, source)
	if instance.gameHandle then
		-- Detect if we're 32-bit (WoW64) talking to a 64-bit Flycast
		instance.useWow64 = winprocess.isWow64()
		if instance.useWow64 then
			print("Flycast: WoW64 detected (32-bit viewer, 64-bit emulator).")
		end

		print("Flycast: Scanning for SH-4 main RAM...")
		local ramBase = Flycast_Common.findRAMBase(
			Flycast_Common, instance.gameHandle, self.gameSignatures)
		-- If standard scan failed and we're WoW64, try the external scanner
		if not ramBase and instance.useWow64 then
			local pid = winprocess.getProcessId(instance.gameHandle)
			ramBase = Flycast_Common:findRAMBase_External(
				pid, self.gameSignatures)
		end
		if ramBase then
			instance.RAMbase = ramBase
			instance.RAMlimit = ramBase + Flycast_Common.SH4_RAM_SIZE - 1
			print(string.format(
				"Flycast: SH-4 main RAM found at process address 0x%X",
				ramBase))
			if instance:verifyGame() then
				print("Flycast: KOF XI (Atomiswave) confirmed.")
			else
				print("WARNING: RAM found but game verification failed!")
			end
		else
			print("ERROR: Could not find SH-4 main RAM in Flycast process!")
		end
	end
	return instance
end

-- Verify that the found RAM region actually contains KOF XI.
-- Uses self:read() which routes through the WoW64 path if needed.
function KOF_XI_AW:verifyGame()
	local buf = ffi.new("uint8_t[32]")
	for _, sig in ipairs(self.verificationStrings) do
		if sig.offset >= 0
			and sig.offset + 32 <= Flycast_Common.SH4_RAM_SIZE then
			local ok = pcall(self.read, self, sig.offset, buf)
			if ok then
				if ffi.string(buf, #sig.pattern) == sig.pattern then
					return true
				end
			end
		end
	end
	return false
end

-- Convert SH-4 cached pointer (0x8Cxxxxxx) to a RAM offset suitable for read().
-- Returns nil if the pointer is null or out of range.
function KOF_XI_AW:sh4ToRAMOffset(ptr)
	if ptr == 0 then return nil end
	local phys = bit.band(ptr, 0x1FFFFFFF)
	local off = phys - 0x0C000000
	if off >= 0 and off < Flycast_Common.SH4_RAM_SIZE then
		return off
	end
	return nil
end

-- Override read()/readPtr() to use 64-bit memory access when running as
-- a WoW64 (32-bit) process against a 64-bit Flycast.  The standard
-- ReadProcessMemory path truncates addresses above 4 GB to 32 bits.
function KOF_XI_AW:read(address, buffer)
	if self.useWow64 then
		local absAddr = address + self.RAMbase
		winprocess.read64(self.gameHandle, absAddr, buffer)
		return buffer, address
	end
	return KOF_Common.read(self, address, buffer)
end

function KOF_XI_AW:readPtr(address, buffer)
	if self.useWow64 then
		local absAddr = address + self.RAMbase
		buffer = buffer or self.pointerBuf
		winprocess.read64(self.gameHandle, absAddr, buffer,
			ffi.sizeof(buffer))
		return buffer.i, address
	end
	return KOF_Common.readPtr(self, address, buffer)
end

-- ========================================================================
-- CAPTURE PIPELINE
-- ========================================================================

function KOF_XI_AW:captureWorldState()
	self:read(self.cameraPtr, self.camera)
end

function KOF_XI_AW:captureEntity(target, facing, isProjectile)
	local boxset, boxAdder = self.boxset, self.addBox
	local bt, boxtype = self.boxtypes, "dummy"
	local boxstate, i, boxesDrawn = target.hitboxesActive, 0, 0
	local haveDrawnAttackBox, hitbox = false, nil
	while boxstate ~= 0 and i <= 5 do
		if bit.band(boxstate, 1) ~= 0 then
			hitbox = target.hitboxes[i]
			if i == 4 then
				boxtype = "throw"
			else
				boxtype = bt:typeForID(hitbox.boxID)
				if isProjectile then
					boxtype = bt:asProjectile(boxtype)
				end
			end
			if not (boxtype == "throw" and haveDrawnAttackBox) then
				if boxtype == "attack" then
					haveDrawnAttackBox = true
				end
				boxset:add(boxtype, boxAdder, self, self:deriveBoxPosition(
					target, hitbox, facing))
				boxesDrawn = boxesDrawn + 1
			end
		end
		boxstate = bit.rshift(boxstate, 1)
		i = i + 1
	end
	if not isProjectile then
		if bit.band(target.flags.collisionActive, 0x10) == 0 then
			hitbox = target.collisionBox
			boxset:add("collision", boxAdder, self, self:deriveBoxPosition(
				target, hitbox, facing))
		end
		self.pivots:add(self.addPivot, self.pivotColor, self:worldToScreen(
			target.position.x, target.position.y))
	elseif boxesDrawn > 0 then
		self.pivots:add(self.addPivot, self.projectilePivotColor,
			self:worldToScreen(target.position.x, target.position.y))
	end
end

function KOF_XI_AW:capturePlayerProjectiles(which, facing)
	local projBuffer = self.projBuffer
	local projPtrs = self.teams[which].projectiles
	for i = 0, self.projCount - 1 do
		local target = projPtrs[i]
		if target ~= 0 then
			local entryOff = self:sh4ToRAMOffset(target)
			if entryOff then
				local innerPtr = self:readPtr(entryOff + 0x10)
				local innerOff = self:sh4ToRAMOffset(innerPtr)
				if innerOff then
					self:read(innerOff, projBuffer)
					self:captureEntity(projBuffer, facing, true)
				end
			end
		end
	end
end

-- On Atomiswave, there is no flat playerTable.  Instead, each team struct
-- contains 3 SH-4 pointers to object pool entries at +144h.  Each entry
-- has a "data pointer" at entry+10h.  The player struct (position, hitboxes,
-- etc.) lives at (data_ptr - 0x614) in RAM.
--
-- Access path:  team.entries[team.point] -> entry+10h -> data-0x614 -> player
function KOF_XI_AW:capturePlayerState(which)
	local team, player = self.teams[which], self.players[which]
	self:read(self.teamPtrs[which], team)
	local entryPtr = team.entries[team.point]
	local entryOff = self:sh4ToRAMOffset(entryPtr)
	if not entryOff then return end
	local dataPtr = self:readPtr(entryOff + 0x10)
	local dataOff = self:sh4ToRAMOffset(dataPtr)
	if not dataOff then return end
	local playerOff = dataOff - 0x614
	if playerOff < 0 or playerOff >= Flycast_Common.SH4_RAM_SIZE then return end
	self:read(playerOff, player)
	local facing = self:facingMultiplier(player)
	self:captureEntity(player, facing, false)
	if self.projectilesEnabled then
		self:capturePlayerProjectiles(which, facing)
	end
end

function KOF_XI_AW:captureState()
	if not self.RAMbase or self.RAMbase == 0 or not self.RAMlimit
		or self.RAMlimit <= self.RAMbase then
		return
	end
	self.boxset:reset()
	self.pivots:reset()
	self:captureWorldState()
	for i = 1, 2 do
		if self.playersEnabled[i] then
			self:capturePlayerState(i)
		end
	end
end

function KOF_XI_AW:facingMultiplier(player)
	return ((player.facing == 0) and -1) or 1
end

function KOF_XI_AW:worldToScreen(x, y)
	local cam = self.camera.position
	return x - cam.x, y - cam.y
end

function KOF_XI_AW:getPlayerPosition(player)
	return player.position.x, player.position.y
end

function KOF_XI_AW:deriveBoxPosition(player, hitbox, facing)
	local playerX, playerY = self:getPlayerPosition(player)
	local centerX, centerY = hitbox.position.x * 2, hitbox.position.y * 2
	centerX = playerX + (centerX * facing)
	centerY = playerY - centerY
	local w, h = hitbox.width * 2, hitbox.height * 2
	return centerX, centerY, w, h
end

-- ========================================================================
-- DEBUG (press F8 to dump diagnostic info to the console)
-- ========================================================================

function KOF_XI_AW:checkInputs()
	if hk.pressed(hk.VK_F8) then
		self:debugDump()
	end
end

function KOF_XI_AW:debugDump()
	print("========== KOF XI AW DEBUG DUMP ==========")
	-- 1. RAM state
	print(string.format("RAMbase=0x%X  RAMlimit=0x%X",
		self.RAMbase or 0, self.RAMlimit or 0))
	if not self.RAMbase or self.RAMbase == 0 then
		print("  ** RAM NOT FOUND — capture is disabled **")
		print("============================================")
		return
	end

	-- 2. Camera
	local camOk, camErr = pcall(self.read, self, self.cameraPtr, self.camera)
	if camOk then
		local cam = self.camera
		print(string.format("Camera: X=%d Y=%d  float=%.3f",
			cam.position.x, cam.position.y, cam.restrictor))
	else
		print("Camera READ FAILED: " .. tostring(camErr))
	end

	-- 3. Per-player chain
	for side = 1, 2 do
		print(string.format("--- Player %d (teamPtr=0x%X) ---",
			side, self.teamPtrs[side]))
		local team = self.teams[side]
		local teamOk, teamErr = pcall(self.read, self,
			self.teamPtrs[side], team)
		if not teamOk then
			print("  team READ FAILED: " .. tostring(teamErr))
			goto continue
		end
		local point = team.point
		print(string.format("  team.point=%d  team.super=0x%X",
			point, team.super))

		-- Show all 3 entry pointers
		for e = 0, 2 do
			print(string.format("  entries[%d]=0x%08X", e, team.entries[e]))
		end

		local entryPtr = team.entries[point]
		local entryOff = self:sh4ToRAMOffset(entryPtr)
		if not entryOff then
			print(string.format(
				"  ** entryPtr 0x%08X -> sh4ToRAMOffset returned nil **",
				entryPtr))
			goto continue
		end
		print(string.format("  entryOff=0x%X (RAM offset)", entryOff))

		local dataPtrOk, dataPtr = pcall(self.readPtr, self, entryOff + 0x10)
		if not dataPtrOk then
			print("  readPtr(entry+0x10) FAILED: " .. tostring(dataPtr))
			goto continue
		end
		local dataOff = self:sh4ToRAMOffset(dataPtr)
		if not dataOff then
			print(string.format(
				"  ** dataPtr 0x%08X -> sh4ToRAMOffset returned nil **",
				dataPtr))
			goto continue
		end

		local playerOff = dataOff - 0x614
		print(string.format(
			"  dataPtr=0x%08X  dataOff=0x%X  playerOff=0x%X",
			dataPtr, dataOff, playerOff))

		if playerOff < 0 or playerOff >= Flycast_Common.SH4_RAM_SIZE then
			print("  ** playerOff out of range **")
			goto continue
		end

		local player = self.players[side]
		local pOk, pErr = pcall(self.read, self, playerOff, player)
		if not pOk then
			print("  player READ FAILED: " .. tostring(pErr))
			goto continue
		end

		local px, py = player.position.x, player.position.y
		local facing = player.facing
		local hbAct = player.hitboxesActive
		local scaleF = player.unknown01
		print(string.format(
			"  pos=(%d,%d)  facing=0x%02X  hitboxesActive=0x%02X  float=%.3f",
			px, py, facing, hbAct, scaleF))

		-- Show playerExtra for this side
		for e = 0, 2 do
			local pe = team.p[e]
			print(string.format(
				"  extra[%d]: charID=%d  health=%d  teamPos=%d",
				e, pe.charID, pe.health, pe.teamPosition))
		end

		-- World-to-screen
		local sx, sy = self:worldToScreen(px, py)
		print(string.format("  screen=(%d,%d)", sx, sy))

		-- Hitbox details
		if hbAct ~= 0 then
			local bs = hbAct
			for i = 0, 6 do
				if bit.band(bs, 1) ~= 0 then
					local hb = player.hitboxes[i]
					print(string.format(
						"    hb[%d]: pos=(%d,%d) id=%d w=%d h=%d",
						i, hb.position.x, hb.position.y,
						hb.boxID, hb.width, hb.height))
				end
				bs = bit.rshift(bs, 1)
				if bs == 0 then break end
			end
		end
		::continue::
	end

	-- 4. Overlay / rendering info
	print(string.format("Window: %dx%d  scale=(%.2f,%.2f)  offset=(%d,%d)",
		self.width, self.height,
		self.xScale, self.yScale,
		self.xOffset, self.yOffset))
	print(string.format("playersEnabled: P1=%s P2=%s",
		tostring(self.playersEnabled[1]),
		tostring(self.playersEnabled[2])))
	print("============================================")
end

return KOF_XI_AW
