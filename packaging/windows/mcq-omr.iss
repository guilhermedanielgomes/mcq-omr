; Inno Setup script: a normal Windows installer for MCQ-OMR, listed in Settings > Apps so it can be
; uninstalled like any other program. Installs for the current user only (no administrator needed).
;   iscc /DAppVersion=1.0.0 /DDistDir=..\..\dist packaging\windows\mcq-omr.iss
; Uninstalling removes the program but keeps the data (exams, scans, students) in %APPDATA%\MCQ-OMR.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir "..\..\dist"
#endif

[Setup]
AppId={{6F3C2B1E-8D4A-4C5B-9E7F-2A1D3C4B5E6F}
AppName=MCQ-OMR
AppVersion={#AppVersion}
AppPublisher=GGSDev
AppPublisherURL=https://guilherme.ie
AppSupportURL=https://github.com/guilhermedanielgomes/mcq-omr
AppUpdatesURL=https://github.com/guilhermedanielgomes/mcq-omr/releases
AppCopyright=Copyright (c) 2026 Guilherme Daniel Gomes Silva (GGSDev)
LicenseFile=..\..\LICENSE
DefaultDirName={autopf}\MCQ-OMR
DefaultGroupName=MCQ-OMR
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#DistDir}
OutputBaseFilename=MCQ-OMR-{#AppVersion}-windows-setup
SetupIconFile=..\icons\icon.ico
UninstallDisplayIcon={app}\MCQ-OMR.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
; the installer's own screens; Chinese Windows gets the English screens (Inno Setup has no official
; Chinese translation), but the language page below still offers Chinese for MCQ-OMR itself
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[CustomMessages]
english.LangTitle=Language
english.LangDesc=The language of MCQ-OMR. It can be changed later in Settings.
brazilianportuguese.LangTitle=Idioma
brazilianportuguese.LangDesc=O idioma do MCQ-OMR. Pode ser alterado depois nas Configurações.
spanish.LangTitle=Idioma
spanish.LangDesc=El idioma de MCQ-OMR. Se puede cambiar más tarde en Configuración.
french.LangTitle=Langue
french.LangDesc=La langue de MCQ-OMR. Elle peut être modifiée plus tard dans les Paramètres.
german.LangTitle=Sprache
german.LangDesc=Die Sprache von MCQ-OMR. Sie kann später in den Einstellungen geändert werden.
italian.LangTitle=Lingua
italian.LangDesc=La lingua di MCQ-OMR. Può essere cambiata in seguito nelle Impostazioni.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#DistDir}\MCQ-OMR\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MCQ-OMR"; Filename: "{app}\MCQ-OMR.exe"
Name: "{group}\{cm:UninstallProgram,MCQ-OMR}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\MCQ-OMR"; Filename: "{app}\MCQ-OMR.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MCQ-OMR.exe"; Description: "{cm:LaunchProgram,MCQ-OMR}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; stop a running copy first so its files can be removed
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM MCQ-OMR.exe"; Flags: runhidden; RunOnceId: "StopMCQOMR"

[Code]
var
  LangPage: TWizardPage;
  LangCombo: TNewComboBox;

function LangCode(Index: Integer): String;
begin
  case Index of
    1: Result := 'pt_BR';
    2: Result := 'es';
    3: Result := 'fr';
    4: Result := 'de';
    5: Result := 'it';
    6: Result := 'zh_CN';
  else
    Result := 'en';
  end;
end;

function DefaultLangIndex(): Integer;
var
  L: String;
begin
  L := ActiveLanguage();
  if L = 'brazilianportuguese' then Result := 1
  else if L = 'spanish' then Result := 2
  else if L = 'french' then Result := 3
  else if L = 'german' then Result := 4
  else if L = 'italian' then Result := 5
  else if (GetUILanguage() and $3FF) = $04 then Result := 6   { Chinese Windows }
  else Result := 0;
end;

procedure InitializeWizard();
begin
  LangPage := CreateCustomPage(wpSelectTasks, CustomMessage('LangTitle'), CustomMessage('LangDesc'));
  LangCombo := TNewComboBox.Create(LangPage);
  LangCombo.Parent := LangPage.Surface;
  LangCombo.Style := csDropDownList;
  LangCombo.Width := ScaleX(280);
  LangCombo.Items.Add('English');
  LangCombo.Items.Add('Português (Brasil)');
  LangCombo.Items.Add('Español');
  LangCombo.Items.Add('Français');
  LangCombo.Items.Add('Deutsch');
  LangCombo.Items.Add('Italiano');
  LangCombo.Items.Add('中文（简体）');
  LangCombo.ItemIndex := DefaultLangIndex();
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir: String;
begin
  { MCQ-OMR reads this file on its first start (when no language has been chosen in it yet) }
  if CurStep = ssPostInstall then
  begin
    Dir := ExpandConstant('{userappdata}\MCQ-OMR');
    ForceDirectories(Dir);
    SaveStringToFile(Dir + '\initial-language.txt', LangCode(LangCombo.ItemIndex), False);
  end;
end;
