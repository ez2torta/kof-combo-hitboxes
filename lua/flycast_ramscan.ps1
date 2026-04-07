# flycast_ramscan.ps1
# Scans a 64-bit Flycast process for the SH-4 main RAM (16 MB MEM_MAPPED
# region) containing a known byte pattern at a specific offset.
#
# This script exists because the hitbox viewer is a 32-bit process and
# cannot enumerate or read memory above 4 GB in a 64-bit target process
# using standard Win32 APIs.  PowerShell runs as a native 64-bit process
# and can scan the full address space.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File flycast_ramscan.ps1 PID HexPattern Offset
#
# Output: decimal base address on success, or -1 / -2 on failure.
param(
    [Parameter(Mandatory=$true, Position=0)][int]$ProcessId,
    [Parameter(Mandatory=$true, Position=1)][string]$PatternHex,
    [Parameter(Mandatory=$true, Position=2)][long]$PatternOffset
)

$csSource = @"
using System;
using System.Runtime.InteropServices;

public class FlycastRamScanner
{
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, int pid);

    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr h);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool ReadProcessMemory(
        IntPtr hProcess, IntPtr baseAddr, byte[] buffer,
        int size, out int bytesRead);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern int VirtualQueryEx(
        IntPtr hProcess, IntPtr addr,
        out MEMORY_BASIC_INFORMATION mbi, int mbiSize);

    [StructLayout(LayoutKind.Sequential)]
    struct MEMORY_BASIC_INFORMATION
    {
        public IntPtr BaseAddress;
        public IntPtr AllocationBase;
        public uint AllocationProtect;
        public IntPtr RegionSize;
        public uint State;
        public uint Protect;
        public uint Type;
    }

    public static long Scan(int pid, byte[] pattern, long patternOffset)
    {
        IntPtr h = OpenProcess(0x0410u, false, pid);
        if (h == IntPtr.Zero) return -2;
        try
        {
            MEMORY_BASIC_INFORMATION mbi;
            int mbiLen = Marshal.SizeOf(typeof(MEMORY_BASIC_INFORMATION));
            long addr = 0;
            while (true)
            {
                if (VirtualQueryEx(h, (IntPtr)addr, out mbi, mbiLen) == 0)
                    break;
                long bAddr = mbi.BaseAddress.ToInt64();
                long rSize = mbi.RegionSize.ToInt64();
                if (rSize == 0x1000000L
                    && mbi.Type == 0x40000u
                    && mbi.State == 0x1000u)
                {
                    byte[] buf = new byte[pattern.Length];
                    int rd;
                    if (ReadProcessMemory(
                            h, (IntPtr)(bAddr + patternOffset),
                            buf, buf.Length, out rd)
                        && rd == buf.Length)
                    {
                        bool ok = true;
                        for (int i = 0; i < pattern.Length; i++)
                            if (buf[i] != pattern[i]) { ok = false; break; }
                        if (ok) return bAddr;
                    }
                }
                long next = bAddr + rSize;
                if (next <= addr) break;
                addr = next;
            }
            return -1;
        }
        finally { CloseHandle(h); }
    }
}
"@

try { Add-Type -TypeDefinition $csSource -ErrorAction Stop }
catch { if ($_.Exception.Message -notmatch "already exists") { throw } }

$bytes = [byte[]]::new($PatternHex.Length / 2)
for ($i = 0; $i -lt $bytes.Length; $i++) {
    $bytes[$i] = [Convert]::ToByte($PatternHex.Substring($i * 2, 2), 16)
}

[FlycastRamScanner]::Scan($ProcessId, $bytes, $PatternOffset)
