; Duet 설치 마법사 — Inno Setup 6
;
; 빌드 순서:
;   uv run --no-sync python scripts\build.py   (검사 → PyInstaller → ISCC /DAppVersion=<버전>)
;
; 관리자 권한을 요구하지 않는다. 사용자 폴더에 설치하므로 UAC 창이 뜨지 않고,
; 회사 PC처럼 권한이 없는 환경에서도 설치된다.
;
; 설치 위치는 AppData 밖(%USERPROFILE%\Duet)이다. Claude 데스크탑 안에서 띄운 터미널은
; AppData 가 앱 전용 폴더로 바뀌어 보여서, 거기 둔 실행 파일이 안 잡히는 일이 있었다.

#define AppName "Duet"
#ifndef AppVersion
#define AppVersion "0.0.0"
#endif
#define AppPublisher "Jonghoon5922"
#define AppURL "https://github.com/Jonghoon5922/duet"
#define AppExe "duet.exe"
#define BoardExe "duet-board.exe"

[Setup]
AppId={{B7E2D4A1-6C3F-4E9B-A2D8-5F1C7E3B9A64}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

PrivilegesRequired=lowest
DefaultDirName={%USERPROFILE}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no

OutputDir=..\dist
OutputBaseFilename=duet-setup-{#AppVersion}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Claude Code 사용자 설정(~\.claude.json)에 항목 하나를 더한다. 모든 프로젝트 창에서 붙는다.
; 남의 설정은 건드리지 않고 고치기 전에 백업을 남긴다.
; 조용한 설치(/VERYSILENT)는 unchecked 를 안 지키고 전부 실행했다 — 실측. 조용히 깔 때는
; /TASKS="claudecode" 처럼 명시하라.
Name: "claudecode"; Description: "Claude Code에 연결합니다 (모든 프로젝트 창에서 Duet이 붙습니다)"; GroupDescription: "연결:"
Name: "claudedesktop"; Description: "Claude Desktop에도 연결합니다"; GroupDescription: "연결:"; Flags: unchecked
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\app\Duet\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
; 창 모드. 시작 메뉴에서 보드를 열 때 검은 창이 안 뜬다.
Source: "..\dist\app\Duet\{#BoardExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\app\Duet\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "사용안내.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 보드. 이미 떠 있으면(Claude 창이 겸하는 중) 브라우저만 열고, 아니면 띄운다. 검은 창 없음.
Name: "{group}\{#AppName} 보드"; Filename: "{app}\{#BoardExe}"
Name: "{group}\사용 안내"; Filename: "{app}\사용안내.txt"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName} 보드"; Filename: "{app}\{#BoardExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "register claude-code"; StatusMsg: "Claude Code에 연결하는 중..."; Flags: runhidden waituntilterminated; Tasks: claudecode
Filename: "{app}\{#AppExe}"; Parameters: "register claude-desktop"; StatusMsg: "Claude Desktop에 연결하는 중..."; Flags: runhidden waituntilterminated; Tasks: claudedesktop

[UninstallRun]
; 제거할 때 등록도 지운다. 안 지우면 앱이 없는 실행 파일을 계속 부른다.
Filename: "{app}\{#AppExe}"; Parameters: "unregister claude-code"; Flags: runhidden waituntilterminated; RunOnceId: "duet_unreg_code"
Filename: "{app}\{#AppExe}"; Parameters: "unregister claude-desktop"; Flags: runhidden waituntilterminated; RunOnceId: "duet_unreg_desktop"

; ~\.duet (타스크·세션 파일)은 지우지 않는다. 그건 사용자의 기록이다.

[Code]
{ 설치를 시작하기 전에 Claude 창을 닫으라고 알린다. 열린 창마다 duet.exe 가
  떠 있어서 파일을 붙잡고 있다. 서재에서 실제로 이것 때문에 설치가 어정쩡하게
  끝난 적이 있다. }
function InitializeSetup(): Boolean;
begin
  Result := True;
  if MsgBox(
      '설치를 시작하기 전에' + #13#10 + #13#10 +
      'Claude Code 창과 Claude Desktop 을 모두 닫아 주세요.' + #13#10 +
      '(창마다 Duet 서버가 떠 있어서 파일을 붙잡고 있습니다)' + #13#10 + #13#10 +
      '닫으셨으면 [확인]을 누르세요.',
      mbInformation, MB_OKCANCEL) = IDCANCEL then
    Result := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedLabel.Caption :=
      'Duet 을 설치했습니다.' + #13#10 + #13#10 +
      '1. Claude Code 창을 새로 열면 Duet 이 붙습니다.' + #13#10 +
      '   평소처럼 일을 시키면 알아서 타스크에 기록됩니다.' + #13#10 + #13#10 +
      '2. Claude 창이 열려 있는 동안 보드는 늘 켜져 있습니다.' + #13#10 +
      '   브라우저에서 http://127.0.0.1:8737 을 여세요.' + #13#10 +
      '   시작 메뉴의 [Duet 보드] 를 눌러도 됩니다.' + #13#10 + #13#10 +
      '기록은 [내 사용자 폴더\.duet] 에 파일로 쌓입니다.';
  end;
end;
