; installer.iss — Inno Setup script for CTR Rosales QC
;
; Design contract: docs/WINDOWS-INSTALLER.md §2.5
;
; Key decisions:
;   PrivilegesRequired=lowest  → per-user install, NO admin prompt.
;   InstallDir under {localappdata}\Programs\  → user-writable, no UAC.
;   Desktop + Start Menu shortcuts to ctr-rosales-qc.exe.
;   Uninstaller removes program files; leaves {localappdata}\ctr-rosales-qc\
;   data directory (runs, sunat-cache, secrets) so the user's work is preserved.
;
; AppId GUID:
;   IMPORTANT — this GUID is FIXED for production and MUST stay stable across all
;   future releases.  Changing it causes Windows to treat a new install as a
;   completely different application (the old one is orphaned and the uninstaller
;   no longer replaces it).  Do NOT regenerate it.
;
; Build command (from repo root):
;   iscc packaging\windows\installer.iss
;
; Output: dist\CTR-Rosales-QC-Setup-v1.0.0.exe

#define MyAppName      "CTR Rosales QC"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "CTR Rosales"
#define MyAppURL       "https://github.com/kozz36/ctr-rosales-qc"
#define MyAppExeName   "ctr-rosales-qc.exe"
#define MyBundleDir    "..\..\dist\ctr-rosales-qc"

[Setup]
; IMPORTANT: Keep AppId stable across releases — see note above. Do NOT change.
AppId={{E785A512-FCF1-447C-9DD2-51E2F1C14E99}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Per-user install: no administrator elevation required.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=

; Install under %LOCALAPPDATA%\Programs\CTR Rosales QC\
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}

; Allow the user to choose a custom location.
DisableDirPage=no

; Output setup executable
OutputDir=..\..\dist
OutputBaseFilename=CTR-Rosales-QC-Setup-v{#MyAppVersion}

; Compression
Compression=lzma2/ultra64
SolidCompression=yes

; Architecture: x64 only (PyInstaller bundle targets 64-bit).
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

; Wizard appearance
WizardStyle=modern
WizardSmallImageFile=

; Minimum Windows version: Windows 10 (10.0)
MinVersion=10.0

; Uninstall log: enable for clean removal.
Uninstallable=yes
UninstallDisplayName={#MyAppName}

; Do NOT create an application ID entry in Add/Remove Programs that triggers
; a "repair" on every update — the default installer behavior is sufficient.
CloseApplications=yes
CloseApplicationsFilter=*{#MyAppExeName}*

[Languages]
Name: "spanish";  MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english";  MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";   Description: "Crear un icono en el &Escritorio";    GroupDescription: "Iconos adicionales:"; Flags: checked
Name: "startmenuicon"; Description: "Crear entrada en el &Menú de Inicio"; GroupDescription: "Iconos adicionales:"; Flags: checked

[Files]
; Copy the entire one-dir PyInstaller bundle.
; {#MyBundleDir} is relative to the .iss file location (packaging\windows\).
; The actual path resolves to: dist\ctr-rosales-qc\*
Source: "{#MyBundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Desktop shortcut (created only if the user selected that task)
Name: "{autodesktop}\{#MyAppName}";    Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; Start Menu shortcut (created only if the user selected that task)
Name: "{group}\{#MyAppName}";          Filename: "{app}\{#MyAppExeName}"; Tasks: startmenuicon

; Uninstaller entry in Start Menu
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; Offer to launch the application after installation completes.
Filename: "{app}\{#MyAppExeName}"; Description: "Iniciar {#MyAppName}"; \
  Flags: nowait postinstall skipifsilent unchecked

[UninstallRun]
; Nothing special to run on uninstall.

[UninstallDelete]
; Leave {localappdata}\ctr-rosales-qc\ (runs, sunat-cache, secrets) intact.
; The user's work is in those directories — never delete it automatically.
; If the user explicitly wants to remove all data they must delete that
; directory manually after uninstalling.
;
; Only clean up files that the installer itself placed inside {app}:
; (Inno Setup handles {app} removal automatically via its own uninstall log.)

[Code]
// Optional: add custom Pascal script here for advanced install logic.
// Currently not needed for the per-user deterministic install.
