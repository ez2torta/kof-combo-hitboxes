local ffi = require("ffi")
local winprocess = require("winprocess")
local winutil = require("winutil")
local Game_Common = require("game.common")
local Flycast_Common = Game_Common:new()

Flycast_Common.whoami = "Flycast_Common"
-- Flycast does NOT use a fixed RAM base address; it allocates the SH-4 main
-- RAM dynamically via VirtualAlloc2/MapViewOfFile.  We must scan process
-- memory at startup to find the 16 MB main RAM region.
Flycast_Common.RAMbase = 0 -- set at runtime by findRAMBase()
Flycast_Common.RAMlimit = 0 -- set at runtime by findRAMBase()
-- SH-4 main RAM size: 16 MB
Flycast_Common.SH4_RAM_SIZE = 0x01000000

-- Filter for memory scanner: only look at exactly 16 MB MEM_MAPPED regions.
-- Flycast maps the SH-4 main RAM as MEM_MAPPED (type 0x40000) regions of
-- exactly 16 MB (0x1000000), with 4 mirrors in the virtual address space
-- corresponding to physical addresses 0x0C000000-0x0FFFFFFF.
local MEM_MAPPED = 0x40000
local function ramRegionFilter(mbi)
	return tonumber(mbi.RegionSize) == 0x1000000
		and mbi.Type == MEM_MAPPED
end

-- The string "MUTEKI" exists in the KOF XI game binary at a known offset.
-- After the Atomiswave BIOS loads the EPR flash into RAM, this string ends
-- up at a predictable offset within the 16 MB main RAM region.
-- EPR layout: the BIOS copies starting at EPR offset 0x100 (program load
-- offset) to RAM starting at physical 0x0C010000 (the gamePC entry point).
-- So any byte at EPR[X] maps to RAM[X - 0x100 + 0x10000] = RAM[X + 0xFF00].
--
-- We use "MUTEKI" as a search anchor because:
-- 1. It's a unique ASCII string within the game code section
-- 2. It exists in the EPR at a known, stable offset (0x100050)
-- 3. After loading, it always appears at RAM offset 0x10FF50
-- 4. The boot header (containing "SYSTEM_X_APP") is NOT copied to RAM,
--    but program code strings like "MUTEKI" always are
local MUTEKI_RAM_OFFSET = 0x10FF50

-- Scan Flycast's process memory to find the base address of the SH-4 16 MB
-- main RAM region.  Returns the base address or nil if not found.
-- "gameSignatures" is a table of { pattern = "...", offset = N } entries;
-- each entry's "offset" is the expected RAM offset of the pattern.
function Flycast_Common:findRAMBase(handle, gameSignatures)
	for _, sig in ipairs(gameSignatures) do
		local results = winprocess.scanMemory(
			handle, sig.pattern, ramRegionFilter)
		for _, r in ipairs(results) do
			local candidateBase = r.address - sig.offset
			-- Sanity check: the candidate base should be within the region
			if candidateBase >= r.regionBase
				and candidateBase + self.SH4_RAM_SIZE
					<= r.regionBase + r.regionSize
			then
				return candidateBase
			end
		end
	end
	return nil
end

-- Scan a 64-bit Flycast process using the external PowerShell helper.
-- Required when the hitbox viewer is 32-bit (WoW64) and Flycast is 64-bit,
-- because VirtualQueryEx from a 32-bit process cannot enumerate memory
-- regions above 4 GB in the target's address space.
function Flycast_Common:findRAMBase_External(pid, gameSignatures)
	print("Flycast: Using 64-bit external scanner (this may take a moment)...")
	for _, sig in ipairs(gameSignatures) do
		local hex = {}
		for i = 1, #sig.pattern do
			hex[i] = string.format("%02X", string.byte(sig.pattern, i))
		end
		local cmd = string.format(
			'%s\\WindowsPowerShell\\v1.0\\powershell.exe'
			.. ' -NoProfile -ExecutionPolicy Bypass'
			.. ' -File lua\\flycast_ramscan.ps1 %d %s %d',
			"C:\\Windows\\Sysnative",
			pid, table.concat(hex), sig.offset)
		local ok, f = pcall(io.popen, cmd)
		if ok and f then
			local output = f:read("*a")
			f:close()
			local addr = tonumber(output:match("(-?%d+)"))
			if addr and addr >= 0 then
				print(string.format(
					"Flycast: External scan found RAM at 0x%X", addr))
				return addr
			end
		end
	end
	return nil
end

-- for cases where we want to export some values from this class into others
-- without directly inheriting from this class
function Flycast_Common:export(target)
	target.RAMbase, target.RAMlimit = self.RAMbase, self.RAMlimit
end

-- After successfully finding the RAM base, update the address bounds.
function Flycast_Common:setRAMBounds(base)
	self.RAMbase = base
	self.RAMlimit = base + self.SH4_RAM_SIZE - 1
	print(string.format("Flycast: SH-4 main RAM found at process address 0x%X",
		base))
	print(string.format("Flycast: RAM range 0x%X - 0x%X",
		self.RAMbase, self.RAMlimit))
end

return Flycast_Common
