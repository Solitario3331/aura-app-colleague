param([switch]$IfMissing)
# Puts an "Aura" icon on the desktop (and in the Start menu) that opens the app directly.
# The shortcut carries the Aura icon AND the app id "Aura.App" (same id the app sets on itself),
# so the taskbar shows the Aura icon and groups the running window with this shortcut.
# -IfMissing: only create missing shortcuts, and repair old ones that lack the id or point elsewhere.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root '.venv\Scripts\pythonw.exe'
$app = Join-Path $root 'app\app.py'
$ico = Join-Path $root 'app\aura_icon4.ico'
# the professional look (theme.txt says hermes) has its own icon
$theme = ''
try { $theme = (Get-Content (Join-Path $root 'theme.txt') -ErrorAction Stop | Select-Object -First 1).Trim() } catch { }
if ($theme -eq 'hermes') { $ico = Join-Path $root 'app\aura_pro3.ico' }
$appId = 'Aura.App'
if ($theme -eq 'hermes') { $appId = 'Aura.App.Pro' }  # the professional look has its own taskbar identity
if (-not (Test-Path $py)) { exit 1 }

$code = @'
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct AuraPropKey { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Explicit)]
public struct AuraPropVariant {
  [FieldOffset(0)] public ushort vt;
  [FieldOffset(8)] public IntPtr p;
  [FieldOffset(16)] public IntPtr pad;
}
[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAuraPropertyStore {
  void GetCount(out uint count);
  void GetAt(uint index, out AuraPropKey key);
  void GetValue(ref AuraPropKey key, ref AuraPropVariant pv);
  void SetValue(ref AuraPropKey key, ref AuraPropVariant pv);
  void Commit();
}
public static class AuraLnk {
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr pbc, int flags, ref Guid iid,
    [MarshalAs(UnmanagedType.Interface)] out IAuraPropertyStore store);
  [DllImport("ole32.dll")] static extern int PropVariantClear(ref AuraPropVariant pv);
  [DllImport("shell32.dll")] static extern void SHChangeNotify(int eventId, uint flags, IntPtr a, IntPtr b);
  static AuraPropKey Key() {
    AuraPropKey k; k.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"); k.pid = 5; return k;
  }
  static IAuraPropertyStore Open(string lnk, int flags) {
    Guid iid = typeof(IAuraPropertyStore).GUID; IAuraPropertyStore s;
    SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, flags, ref iid, out s); return s;
  }
  public static string GetId(string lnk) {
    IAuraPropertyStore s = Open(lnk, 0);
    try {
      AuraPropKey k = Key(); AuraPropVariant v = new AuraPropVariant();
      s.GetValue(ref k, ref v);
      string r = (v.vt == 31 && v.p != IntPtr.Zero) ? Marshal.PtrToStringUni(v.p) : "";
      PropVariantClear(ref v); return r;
    } finally { Marshal.ReleaseComObject(s); }
  }
  public static void SetId(string lnk, string id) {
    IAuraPropertyStore s = Open(lnk, 2);  // GPS_READWRITE
    try {
      AuraPropKey k = Key(); AuraPropVariant v = new AuraPropVariant();
      v.vt = 31; v.p = Marshal.StringToCoTaskMemUni(id);  // VT_LPWSTR
      s.SetValue(ref k, ref v); s.Commit(); PropVariantClear(ref v);
    } finally { Marshal.ReleaseComObject(s); }
  }
  public static void RefreshIcons() { SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero); }  // SHCNE_ASSOCCHANGED
}
'@
$haveId = $true
try { Add-Type -TypeDefinition $code -ErrorAction Stop } catch { $haveId = $false }

$shell = New-Object -ComObject WScript.Shell
$changed = $false
$places = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($dir in $places) {
  if (-not $dir -or -not (Test-Path $dir)) { continue }
  $lnk = Join-Path $dir 'Aura.lnk'
  if ($IfMissing -and (Test-Path $lnk)) {
    $ok = $false
    try {
      $old = $shell.CreateShortcut($lnk)
      $ok = ($old.TargetPath -ieq $py) -and ($old.IconLocation -like ($ico + '*'))
      if ($ok -and $haveId) { $ok = ([AuraLnk]::GetId($lnk) -eq $appId) }
    } catch { $ok = $true }  # cannot read it: leave the user's shortcut alone
    if ($ok) { continue }
  }
  $s = $shell.CreateShortcut($lnk)
  $s.TargetPath = $py
  $s.Arguments = '"' + $app + '"'
  $s.WorkingDirectory = $root
  $s.IconLocation = $ico + ',0'
  $s.Description = 'Aura'
  $s.Save()
  if ($haveId) {
    try { [AuraLnk]::SetId($lnk, $appId) } catch { Write-Output ('Could not set the app id on ' + $lnk) }
  }
  $changed = $true
}
if ($changed -and $haveId) { try { [AuraLnk]::RefreshIcons() } catch { } }
exit 0
