; Instalador Windows do Chess PDF Editor (Sprint 9.21, §49).
;
; Rode pelo script, que descobre o compilador e passa a versão e a variante:
;
;     python scripts/build_exe.py --light --installer
;
; Ou diretamente, se o ISCC estiver no PATH:
;
;     ISCC.exe packaging/installer.iss /DAppVersion=0.1.0 /DVariant=full
;
; Os `#define` abaixo são só os padrões de quem compila à mão. Quem manda é o
; `build_exe.py`, que os sobrescreve com `/D` — a versão sai do `pyproject.toml`,
; e há teste conferindo que o padrão daqui não fica para trás dele (§49.3).

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef Variant
  #define Variant "full"
#endif

#define AppName "ChessPdfEditor"
#define AppDisplayName "Chess PDF Editor"
#define AppExeName "ChessPdfEditor.exe"

; Cada variante empacota a pasta que o `build_exe.py` gerou para ela. Os nomes
; espelham `dist_dir()`; se um dos dois mudar sem o outro, o teste acusa.
#if Variant == "light"
  #define DistName "ChessPdfEditor-lite"
  #define SetupSuffix "lite"
#else
  #define DistName "ChessPdfEditor"
  #define SetupSuffix "full"
#endif

; `AddBackslash(...)` e não `"..\dist\" + DistName`: um literal terminado em
; contrabarra depende de como o pré-processador trata a barra antes das aspas, e
; isto aqui não pode ser compilado nesta máquina para tirar a dúvida. A função
; embutida não tem essa ambiguidade.
#ifndef SourceDir
  #define SourceDir AddBackslash("..\dist") + DistName
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
; O AppId é o mesmo nas duas variantes de propósito: é o mesmo aplicativo, e quem
; baixou a completa e depois a leve deve **trocar** de instalação, não ficar com
; duas. Ver `[InstallDelete]` para o que isso exige.
AppId={{7C4B1E8A-2F63-4D51-9E07-5A8D3B6C1F42}
AppName={#AppDisplayName}
AppVersion={#AppVersion}
AppVerName={#AppDisplayName} {#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppDisplayName}
DefaultGroupName={#AppDisplayName}
UninstallDisplayName={#AppDisplayName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-{#AppVersion}-{#SetupSuffix}-setup
; O build é x64 e leva binários nativos (PyMuPDF, Qt, e torch na completa).
; `x64` e não `x64compatible`: o segundo só existe a partir do Inno Setup 6.3, e num
; 6.0–6.2 seria erro de compilação. O `x64` é aceito em todo o 6.x — nas versões
; novas com aviso de obsolescência, que é o modo certo de errar quando não se pode
; compilar aqui para conferir qual versão a máquina de release terá.
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
; Sem assinatura de código o SmartScreen já avisa; exigir administrador por cima
; disso é um segundo obstáculo para quem só quer abrir um livro. O padrão é
; instalação por usuário, e quem quiser para a máquina toda escolhe no diálogo.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; A armadilha que este bloco existe para fechar: instalar a variante **leve** por
; cima da **completa** deixaria o torch da anterior em `_internal`, porque o Inno
; não remove o que não está na lista de arquivos novos. O marcador do bundle diria
; "light" com o motor local ainda importável ao lado — exatamente o contrato que a
; §44.4 criou o auto-teste para garantir, quebrado pela instalação.
;
; Apaga só o `_internal` do próprio bundle, e não `{app}` inteiro: se alguém
; instalou numa pasta compartilhada, varrer tudo destruiria o que não é nosso.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppDisplayName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppDisplayName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppDisplayName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppDisplayName}}"; Flags: nowait postinstall skipifsilent
