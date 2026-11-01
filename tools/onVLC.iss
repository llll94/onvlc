; Instalador do onVLC (Inno Setup, grátis). Gerado por tools\build_installer.py, que passa:
;   /DAppVersion=1.0.N   /DSrc=<pasta dist\onVLC>   /DRoot=<raiz do projeto>
; Por padrão instala só para o usuário (sem administrador) em %LOCALAPPDATA%\Programs\onVLC. Quem preferir pode
; escolher "para todos os usuários" (Arquivos de Programas): aí só um administrador consegue trocar os arquivos,
; o que protege melhor contra programas maliciosos que rodem com o usuário. A confirmação do Windows aparece também
; para liberar a porta da TV no firewall (opcional, marcada).

[Setup]
AppId={{6C2B1E7A-8F3D-4B5E-9C1A-0F4E7D2B9A31}
AppName=onVLC
AppVersion={#AppVersion}
AppVerName=onVLC {#AppVersion}
AppPublisher=onVLC
AppPublisherURL=https://github.com/llll94/onvlc
AppSupportURL=https://github.com/llll94/onvlc/issues
DefaultDirName={autopf}\onVLC
DisableProgramGroupPage=yes
DisableDirPage=auto
DisableReadyPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Root}\dist
OutputBaseFilename=instalar-onVLC
SetupIconFile={#Root}\ui\icon.ico
UninstallDisplayIcon={app}\onVLC.exe
UninstallDisplayName=onVLC
LicenseFile={#Root}\LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=no

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Messages]
pt.WelcomeLabel2=O onVLC é um player de vídeo com o motor do VLC que manda os seus filmes para a TV.%n%nDepois de instalar, ele abre um assistente que encontra a sua TV na rede e instala o onVLC nela.

[Tasks]
Name: "firewall"; Description: "Liberar a TV para falar com este PC (só a porta 8780, só na rede de casa; o Windows pede confirmação)"
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"

[Files]
Source: "{#Src}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Root}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#Root}\THIRD_PARTY.md"; DestDir: "{app}"; DestName: "TERCEIROS.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\onVLC"; Filename: "{app}\onVLC.exe"
Name: "{autodesktop}\onVLC"; Filename: "{app}\onVLC.exe"; Tasks: desktopicon

[Run]
; "Abrir com", a tarefa que mantém a biblioteca aberta e a biblioteca já rodando. runasoriginaluser: numa
; instalação "para todos", isso vale para quem está usando o PC, não para a conta de administrador da confirmação
Filename: "{app}\onVLC.exe"; Parameters: "--registrar"; StatusMsg: "Preparando a biblioteca…"; Flags: runhidden waituntilterminated runasoriginaluser
; a TV precisa chegar na biblioteca: só TCP 8780, só de endereços da rede local, só em rede Particular
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""onVLC biblioteca"" dir=in action=allow program=""{app}\onvlc-biblioteca.exe"" protocol=TCP localport=8780 remoteip=localsubnet profile=private enable=yes"; StatusMsg: "Liberando a TV no firewall…"; Flags: runhidden shellexec waituntilterminated; Verb: runas; Tasks: firewall
Filename: "{app}\onVLC.exe"; Description: "Abrir o onVLC e configurar a TV"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{app}\onVLC.exe"; Parameters: "--desregistrar"; Flags: runhidden waituntilterminated; RunOnceId: "desregistrar"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""onVLC biblioteca"""; Flags: runhidden shellexec waituntilterminated; Verb: runas; RunOnceId: "firewall"; Tasks: firewall

[Code]
var
  WipeData: Boolean;

// antes de trocar ou apagar os arquivos: a tarefa para de reabrir a biblioteca, e biblioteca e player fecham
procedure StopRunning();
var
  R: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Change /TN "onVLC biblioteca" /DISABLE', '', SW_HIDE, ewWaitUntilTerminated, R);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM onvlc-biblioteca.exe /F', '', SW_HIDE, ewWaitUntilTerminated, R);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM onVLC.exe /F', '', SW_HIDE, ewWaitUntilTerminated, R);
  Sleep(800);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunning();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunning();
  // os filmes (Vídeos\onVLC) nunca são apagados; a pergunta é sobre o que o onVLC guardou
  WipeData := SuppressibleMsgBox('Apagar também o que o onVLC guardou neste PC?' + #13#10#13#10 +
    'Posições dos filmes, capas, a TV salva, o pareamento com a TV e as senhas (TMDB e OpenSubtitles).' + #13#10 +
    'Os seus filmes na pasta Vídeos\onVLC continuam lá.' + #13#10#13#10 +
    'Na TV, para ela esquecer este PC: Configurações → Opções do desenvolvedor → Revogar autorizações de depuração USB.',
    mbConfirmation, MB_YESNO, IDNO) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  R: Integer;
begin
  // antes de apagar os arquivos (o onVLC.exe ainda existe)
  if (CurUninstallStep = usUninstall) and WipeData then
    Exec(ExpandConstant('{app}\onVLC.exe'), '--apagar-dados', '', SW_HIDE, ewWaitUntilTerminated, R);
end;
