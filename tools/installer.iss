; 批量视频混剪工具 安装包脚本（Inno Setup 6）。由 tools/build.py 传入 SrcDir / OutDir / AppVer / IconFile 后编译。
#define AppName "批量视频混剪工具"
#define AppExe "批量视频混剪工具.exe"
#ifndef AppVer
  #define AppVer "1.1.0"
#endif
; 简体中文语言文件随仓库提供（tools/ChineseSimplified.isl，官方安装版的 Inno Setup 不自带）
#ifndef LangFile
  #define LangFile "compiler:Languages\ChineseSimplified.isl"
#endif

[Setup]
AppId={{8F3C7B52-6A3E-4C1E-9B7A-2D7E5A1C9B10}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} v{#AppVer}
AppPublisher=内部工具
AppComments=批量导入素材 · 时长上限控制 · 素材查重 · 去水印/去贴纸/去遮挡 · 滤镜贴纸花字 · 一键批量成片
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 默认装到当前用户目录，不需要管理员权限（公司电脑通常没有管理员权限）；管理员可在开始时选择“为所有用户安装”
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutDir}
OutputBaseFilename={#AppName}_安装包_v{#AppVer}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Qt 6 需要 Windows 10 1809 及以上（Win10 IoT 企业版 LTSC 21H2 = 19044，满足）
MinVersion=10.0.17763
WizardStyle=modern
ShowLanguageDialog=no
VersionInfoVersion={#AppVer}.0
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} 安装程序
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chs"; MessagesFile: "{#LangFile}"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName} 使用文档"; Filename: "{app}\使用文档.html"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\ffmpeg"

[Messages]
chs.WelcomeLabel2=即将在您的电脑上安装 [name/ver]。%n%n本工具用于对自有或已获授权的素材进行批量混剪、去水印、加包装，请遵守著作权与平台规则。%n%n建议安装前关闭其他应用程序。
