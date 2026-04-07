param([Parameter(Mandatory=$true)][int]$ProcessId)

$csSource = @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class MemDiag
{
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern int VirtualQueryEx(
        IntPtr hProcess, IntPtr addr,
        out MEMORY_BASIC_INFORMATION mbi, int mbiSize);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool ReadProcessMemory(
        IntPtr hProcess, IntPtr baseAddr, byte[] buffer,
        int size, out int bytesRead);

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

    public static string[] Enumerate(int pid)
    {
        var results = new List<string>();
        IntPtr h = OpenProcess(0x0410u, false, pid);
        if (h == IntPtr.Zero) { results.Add("ERROR: OpenProcess failed"); return results.ToArray(); }
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
                if (rSize >= 0x100000 && mbi.State == 0x1000)
                {
                    string typeStr;
                    switch (mbi.Type)
                    {
                        case 0x20000u: typeStr = "PRIVATE"; break;
                        case 0x40000u: typeStr = "MAPPED"; break;
                        case 0x1000000u: typeStr = "IMAGE"; break;
                        default: typeStr = string.Format("0x{0:X}", mbi.Type); break;
                    }
                    // For regions 12-20 MB, try reading "MUTEKI" at offset 0x10FF50
                    string marker = "";
                    if (rSize >= 0xC00000 && rSize <= 0x1400000)
                    {
                        byte[] buf = new byte[6];
                        int rd;
                        if (ReadProcessMemory(h, (IntPtr)(bAddr + 0x10FF50), buf, 6, out rd) && rd == 6)
                        {
                            string s = System.Text.Encoding.ASCII.GetString(buf);
                            if (s == "MUTEKI") marker = " *** MUTEKI FOUND ***";
                        }
                    }
                    results.Add(string.Format("Base=0x{0:X12} Size={1,8:F1}MB Type={2,-8} Protect=0x{3:X}{4}",
                        bAddr, rSize / (1024.0 * 1024.0), typeStr, mbi.Protect, marker));
                }
                long next = bAddr + rSize;
                if (next <= addr) break;
                addr = next;
            }
        }
        finally { CloseHandle(h); }
        return results.ToArray();
    }
}
"@

try { Add-Type -TypeDefinition $csSource -ErrorAction Stop }
catch { if ($_.Exception.Message -notmatch "already exists") { throw } }

$lines = [MemDiag]::Enumerate($ProcessId)
foreach ($line in $lines) { Write-Output $line }
