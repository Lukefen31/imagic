#define MyAppName "imagic Desktop"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#ifndef SourceDist
  #define SourceDist "..\\..\\build\\windows\\pyinstaller\\dist\\imagic"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\\..\\dist\\windows"
#endif
#ifndef IncludeRawTherapee
  #define IncludeRawTherapee "0"
#endif
#if IncludeRawTherapee == "1"
  #ifndef RawTherapeePayloadDir
    #error RawTherapeePayloadDir must be defined when IncludeRawTherapee=1
  #endif
  #define OutputBaseFilename "imagic-desktop-recommended-rawtherapee-setup"
#else
  #define OutputBaseFilename "imagic-desktop-setup"
#endif

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

[Types]
#if IncludeRawTherapee == "1"
Name: "recommended"; Description: "Recommended: imagic + bundled RawTherapee for the full RAW workflow"
Name: "imagiconly"; Description: "imagic only (not recommended for RAW-heavy workflows)"
Name: "custom"; Description: "Custom installation"; Flags: iscustom
#else
Name: "standard"; Description: "Standard installation"
#endif

[Components]
#if IncludeRawTherapee == "1"
Name: "main"; Description: "imagic Desktop"; Types: recommended imagiconly custom; Flags: fixed
Name: "rawtherapee"; Description: "Bundled RawTherapee integration (highly recommended)"; Types: recommended custom
#else
Name: "main"; Description: "imagic Desktop"; Types: standard; Flags: fixed
#endif

[Files]
Source: "{#SourceDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: main
Source: "..\..\packaging\windows\branding\imagic-wide-installer-logo.bmp"; Flags: dontcopy
#if IncludeRawTherapee == "1"
Source: "{#RawTherapeePayloadDir}\*"; DestDir: "{app}\RawTherapee"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: rawtherapee
#endif

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
#if IncludeRawTherapee == "1"
  BundleNoteLabel: TNewStaticText;
#endif

procedure AddBrandLogo();
begin
  ExtractTemporaryFile('imagic-wide-installer-logo.bmp');

  BrandLogoImage := TBitmapImage.Create(WizardForm.SelectComponentsPage);
  BrandLogoImage.Parent := WizardForm;
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

procedure InitializeWizard();
#if IncludeRawTherapee == "1"
var
  ExistingInstall: string;
#endif
begin
#if IncludeRawTherapee == "1"
  WizardForm.ComponentsList.Height := WizardForm.ComponentsList.Height - ScaleY(34);
#endif
  AddBrandLogo();

#if IncludeRawTherapee == "1"
  ExistingInstall := DetectExistingRawTherapee();

  BundleNoteLabel := TNewStaticText.Create(WizardForm.SelectComponentsPage);
  BundleNoteLabel.Parent := WizardForm.ComponentsList.Parent;
  BundleNoteLabel.Left := WizardForm.ComponentsList.Left;
  BundleNoteLabel.Top := WizardForm.ComponentsList.Top + WizardForm.ComponentsList.Height + ScaleY(8);
  BundleNoteLabel.Width := WizardForm.ComponentsList.Width;
  BundleNoteLabel.Height := ScaleY(72);
  BundleNoteLabel.WordWrap := True;

  if ExistingInstall <> '' then
    BundleNoteLabel.Caption :=
      'RawTherapee was detected at ' + ExistingInstall +
      '. The bundled component remains preselected because it is the recommended zero-setup path for imagic and guarantees a known-good RAW workflow.'
  else
    BundleNoteLabel.Caption :=
      'Highly recommended: keep the RawTherapee component selected so imagic can process RAW files immediately after install, with no manual CLI setup.';
#endif
end;