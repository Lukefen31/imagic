#define MyAppName "imagic Desktop"
#ifndef MyAppVersion
  #define MyAppVersion "0.4.6"
#endif
#ifndef SourceDist
  #define SourceDist "..\\..\\build\\windows\\pyinstaller\\dist\\imagic"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\\..\\dist\\windows"
#endif
#ifndef RawTherapeePayloadDir
  #error RawTherapeePayloadDir must be defined.
#endif
#define OutputBaseFilename "imagic-desktop-setup"

[Setup]
AppId={{0D8CCF1C-D4D1-42B5-9686-162A0E37CB70}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=imagic
DefaultDirName={autopf}\imagic
DefaultGroupName=imagic
OutputDir={#InstallerOutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\imagic.exe
SetupIconFile=..\..\packaging\windows\branding\imagic-app-icon.ico
LicenseFile=..\..\packaging\EULA.txt

[Types]
Name: "full"; Description: "Full installation (imagic + RawTherapee)"

[Components]
Name: "main"; Description: "imagic Desktop"; Types: full; Flags: fixed
Name: "rawtherapee"; Description: "Bundled RawTherapee"; Types: full; Flags: fixed

[Files]
Source: "{#SourceDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: main
Source: "..\..\packaging\windows\branding\imagic-wide-installer-logo.bmp"; Flags: dontcopy
Source: "{#RawTherapeePayloadDir}\*"; DestDir: "{app}\RawTherapee"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: rawtherapee

[Icons]
Name: "{group}\imagic"; Filename: "{app}\imagic.exe"
Name: "{group}\Uninstall imagic"; Filename: "{uninstallexe}"
Name: "{autodesktop}\imagic"; Filename: "{app}\imagic.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\imagic.exe"; Description: "Launch imagic"; Flags: nowait postinstall skipifsilent

[Code]
var
  BrandLogoImage: TBitmapImage;

procedure AddBrandLogo();
begin
  ExtractTemporaryFile('imagic-wide-installer-logo.bmp');

  BrandLogoImage := TBitmapImage.Create(WizardForm);
  BrandLogoImage.Parent := WizardForm.NextButton.Parent;
  BrandLogoImage.AutoSize := True;
  BrandLogoImage.Bitmap.LoadFromFile(ExpandConstant('{tmp}\imagic-wide-installer-logo.bmp'));
  WizardForm.WizardSmallBitmapImage.Visible := False;
  BrandLogoImage.Left := ScaleX(18);
  BrandLogoImage.Top := WizardForm.NextButton.Top +
    ((WizardForm.NextButton.Height - BrandLogoImage.Bitmap.Height) div 2);
end;

function DetectExistingRawTherapee(): string;
var
  Candidate: string;
begin
  Result := '';

  Candidate := ExpandConstant('{pf}\RawTherapee\5.11\rawtherapee-cli.exe');
  if FileExists(Candidate) then begin
    Result := ExpandConstant('{pf}\RawTherapee\5.11');
    exit;
  end;

  Candidate := ExpandConstant('{pf}\RawTherapee\rawtherapee-cli.exe');
  if FileExists(Candidate) then begin
    Result := ExpandConstant('{pf}\RawTherapee');
    exit;
  end;

  Candidate := ExpandConstant('{pf32}\RawTherapee\5.11\rawtherapee-cli.exe');
  if FileExists(Candidate) then begin
    Result := ExpandConstant('{pf32}\RawTherapee\5.11');
    exit;
  end;

  Candidate := ExpandConstant('{pf32}\RawTherapee\rawtherapee-cli.exe');
  if FileExists(Candidate) then begin
    Result := ExpandConstant('{pf32}\RawTherapee');
    exit;
  end;
end;

function GetPreviousUninstallString(): string;
var
  UninstallKey: string;
  UninstallStr: string;
begin
  Result := '';
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1';
  if RegQueryStringValue(HKLM, UninstallKey, 'UninstallString', UninstallStr) then
    Result := RemoveQuotes(UninstallStr)
  else if RegQueryStringValue(HKCU, UninstallKey, 'UninstallString', UninstallStr) then
    Result := RemoveQuotes(UninstallStr);
end;

function PrepareToInstall(var NeedsRestart: Boolean): string;
var
  UninstallStr: string;
  ResultCode: Integer;
begin
  Result := '';
  UninstallStr := GetPreviousUninstallString();
  if UninstallStr <> '' then begin
    if FileExists(UninstallStr) then begin
      Log('Previous installation detected. Running uninstaller: ' + UninstallStr);
      Exec(UninstallStr, '/SILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Log('Uninstaller exited with code: ' + IntToStr(ResultCode));
    end;
  end;
end;

procedure InitializeWizard();
begin
  AddBrandLogo();
end;