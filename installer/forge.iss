#define MyAppId "{{3E4D7F95-3BC3-4A59-92B0-0C493B4C07E2}"
#define MyAppName "FORGE"
#define MyAppVersion "1.1.1"
#define MyAppPublisher "TREN Studio"
#define MyAppURL "https://www.trenstudio.com/FORGE/"
#define MyAppExeName "FORGE-Desktop.exe"
#define MyAppFolder "..\\dist\\FORGE-Desktop-App"
#define MyIconFile "..\\assets\\forge-desktop-icon.ico"
#define MyLicenseFile "..\\LICENSE"
#define MyWizardImage "assets\\forge-wizard.bmp"
#define MyWizardSmallImage "assets\\forge-wizard-small.bmp"
#define MyOutputDir "..\\release-assets\\installer-output"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\FORGE
DefaultGroupName=FORGE
DisableProgramGroupPage=yes
LicenseFile={#MyLicenseFile}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
WizardImageFile={#MyWizardImage}
WizardSmallImageFile={#MyWizardSmallImage}
Compression=lzma2/ultra64
SolidCompression=yes
ChangesAssociations=no
OutputDir={#MyOutputDir}
OutputBaseFilename=FORGE-Setup-{#MyAppVersion}
SetupIconFile={#MyIconFile}
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=FORGE Desktop Installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#MyAppFolder}\FORGE-Desktop-App.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion
Source: "{#MyAppFolder}\*"; DestDir: "{app}"; Excludes: "FORGE-Desktop-App.exe"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\FORGE"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\FORGE"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch FORGE Desktop"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=This setup installs FORGE Desktop on this PC.%n%nFORGE is an English-first desktop operator with multilingual chat support, live model routing, and a local launcher designed for serious execution.
SelectDirDesc=Choose where FORGE should be installed.
ReadyLabel1=Setup is ready to install FORGE Desktop on your computer.
FinishedHeadingLabel=FORGE installation complete
FinishedLabel=FORGE Desktop was installed successfully. You can launch it now from the desktop shortcut or Start menu.
InstallingLabel=Installing FORGE Desktop files and local runtime...
ClickNext=Click Next to continue.
