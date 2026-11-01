; Instalador do onVLC (Inno Setup, grátis). Gerado por tools\build_installer.py, que passa:
;   /DAppVersion=1.0.N   /DSrc=<pasta dist\onVLC>   /DRoot=<raiz do projeto>
; Instala só para o usuário (sem administrador) em %LOCALAPPDATA%\Programs\onVLC. A única confirmação do Windows é
; para liberar a porta da TV no firewall (opcional, marcada).

[Setup]
AppId={{6C2B1E7A-8F3D-4B5E-9C1A-0F4E7D2B9A31}
AppName=onVLC
AppVersion={#AppVersion}
AppVerName=onVLC {#AppVersion}
AppPublisher=onVLC
DefaultDirName={autopf}\onVLC
DisableProgramGroupPage=yes
DisableDirPage=auto
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Root}\dist
OutputBaseFilename=instalar-onVLC
SetupIconFile={#Root}\ui\icon.ico
UninstallDisplayIcon={app}\onVLC.exe
UninstallDisplayName=onVLC
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
Name: "firewall"; Description: "Liberar a TV para falar com este PC (o Windows pede confirmação)"
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"

[Files]
Source: "{#Src}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\onVLC"; Filename: "{app}\onVLC.exe"
Name: "{autodesktop}\onVLC"; Filename: "{app}\onVLC.exe"; Tasks: desktopicon

[Run]
; "Abrir com", a tarefa que mantém a biblioteca aberta e a biblioteca já rodando
Filename: "{app}\onVLC.exe"; Parameters: "--registrar"; StatusMsg: "Preparando a biblioteca…"; Flags: runhidden waituntilterminated
; a TV precisa chegar na biblioteca (porta 8780) pela rede de casa
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""onVLC biblioteca"" dir=in action=allow program=""{app}\onvlc-biblioteca.exe"" profile=private enable=yes"; StatusMsg: "Liberando a TV no firewall…"; Flags: runhidden shellexec waituntilterminated; Verb: runas; Tasks: firewall
Filename: "{app}\onVLC.exe"; Description: "Abrir o onVLC e configurar a TV"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\onVLC.exe"; Parameters: "--desregistrar"; Flags: runhidden waituntilterminated; RunOnceId: "desregistrar"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""onVLC biblioteca"""; Flags: runhidden shellexec waituntilterminated; Verb: runas; RunOnceId: "firewall"; Tasks: firewall

[Code]
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
  Result := True;
end;
