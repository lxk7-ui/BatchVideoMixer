// 单文件版启动器：程序本体（zip）嵌在本 exe 里，首次运行解压到 %LOCALAPPDATA%\BatchVideoMixer\app\<版本>，之后直接启动（秒开）。
// 由 tools/build.py 生成常量后用 Windows 自带的 csc.exe（.NET Framework 4.x）编译，无需安装任何东西。
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("批量视频混剪工具")]
[assembly: AssemblyDescription("批量视频混剪工具（单文件免安装版）")]
[assembly: AssemblyCompany("内部工具")]
[assembly: AssemblyProduct("批量视频混剪工具")]
[assembly: AssemblyCopyright("仅限处理自有或已授权素材")]
[assembly: AssemblyVersion("{VERSION}.0")]
[assembly: AssemblyFileVersion("{VERSION}.0")]

static class Program
{
    const string Version = "{VERSION}";
    const string Stamp = "{STAMP}";
    const string ExeName = "批量视频混剪工具.exe";
    const string Title = "批量视频混剪工具";

    [STAThread]
    static int Main(string[] args)
    {
        string root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                   "BatchVideoMixer", "app");
        string dir = Path.Combine(root, Version);
        string exe = Path.Combine(dir, ExeName);
        string marker = Path.Combine(dir, ".ready");
        bool wait = Array.Exists(args, a => a == "--selftest" || a == "--wait");
        try
        {
            using (var mutex = new Mutex(false, "BatchVideoMixer_Setup_" + Version))
            {
                try { mutex.WaitOne(); } catch (AbandonedMutexException) { }
                try
                {
                    if (!IsReady(exe, marker))
                    {
                        if (!Extract(root, dir, marker, wait))
                            return 1;
                        CleanupOld(root);
                    }
                }
                finally { mutex.ReleaseMutex(); }
            }
            var psi = new ProcessStartInfo(exe, JoinArgs(args));
            psi.UseShellExecute = false;
            psi.WorkingDirectory = dir;
            var p = Process.Start(psi);
            if (wait)
            {
                p.WaitForExit();
                return p.ExitCode;
            }
            return 0;
        }
        catch (Exception e)
        {
            if (!wait)
                MessageBox.Show("启动失败：" + e.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
    }

    static bool IsReady(string exe, string marker)
    {
        try { return File.Exists(exe) && File.Exists(marker) && File.ReadAllText(marker).Trim() == Stamp; }
        catch { return false; }
    }

    static string JoinArgs(string[] args)
    {
        var sb = new StringBuilder();
        foreach (var a in args)
        {
            if (sb.Length > 0) sb.Append(' ');
            if (a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) { sb.Append(a); continue; }
            sb.Append('"');
            int slashes = 0;
            foreach (char c in a)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { sb.Append('\\', slashes * 2 + 1); sb.Append('"'); slashes = 0; continue; }
                sb.Append('\\', slashes); slashes = 0; sb.Append(c);
            }
            sb.Append('\\', slashes * 2);
            sb.Append('"');
        }
        return sb.ToString();
    }

    static void CleanupOld(string root)
    {
        try
        {
            foreach (var d in Directory.GetDirectories(root))
            {
                string name = Path.GetFileName(d);
                if (name != Version && !name.EndsWith(".tmp"))
                {
                    try { Directory.Delete(d, true); } catch { }   // 旧版本正在运行时删不掉，下次再删
                }
            }
        }
        catch { }
    }

    static bool Extract(string root, string dir, string marker, bool silent)
    {
        Exception error = null;
        string tmp = dir + ".tmp";
        Form form = null;
        ProgressBar bar = null;
        Label label = null;
        if (!silent)
        {
            Application.EnableVisualStyles();
            form = new Form();
            form.Text = Title;
            form.FormBorderStyle = FormBorderStyle.FixedDialog;
            form.MaximizeBox = false;
            form.MinimizeBox = false;
            form.StartPosition = FormStartPosition.CenterScreen;
            form.ClientSize = new Size(460, 120);
            form.Font = new Font("Microsoft YaHei UI", 9.5f);
            try { form.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            label = new Label();
            label.Text = "首次启动，正在准备程序文件（只需要一次，约 10~30 秒）…";
            label.SetBounds(20, 20, 420, 24);
            bar = new ProgressBar();
            bar.SetBounds(20, 56, 420, 22);
            bar.Minimum = 0;
            bar.Maximum = 1000;
            var tip = new Label();
            tip.Text = "程序会解压到：" + dir;
            tip.ForeColor = Color.Gray;
            tip.SetBounds(20, 88, 420, 20);
            form.Controls.Add(label);
            form.Controls.Add(bar);
            form.Controls.Add(tip);
        }
        var worker = new Thread(() =>
        {
            try
            {
                if (Directory.Exists(tmp)) Directory.Delete(tmp, true);
                Directory.CreateDirectory(tmp);
                using (var res = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
                using (var zip = new ZipArchive(res, ZipArchiveMode.Read))
                {
                    long total = 0, done = 0;
                    foreach (var e in zip.Entries) total += e.Length;
                    foreach (var e in zip.Entries)
                    {
                        string target = Path.GetFullPath(Path.Combine(tmp, e.FullName));
                        if (!target.StartsWith(Path.GetFullPath(tmp), StringComparison.OrdinalIgnoreCase))
                            continue;
                        if (e.FullName.EndsWith("/") || e.FullName.EndsWith("\\"))
                        {
                            Directory.CreateDirectory(target);
                            continue;
                        }
                        Directory.CreateDirectory(Path.GetDirectoryName(target));
                        using (var src = e.Open())
                        using (var dst = File.Create(target))
                            src.CopyTo(dst, 1 << 20);
                        done += e.Length;
                        if (form != null && total > 0)
                        {
                            int v = (int)(done * 1000 / total);
                            try { form.BeginInvoke((Action)(() => bar.Value = Math.Min(1000, v))); } catch { }
                        }
                    }
                }
                File.WriteAllText(Path.Combine(tmp, ".ready"), Stamp);
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
                Directory.Move(tmp, dir);
            }
            catch (Exception ex)
            {
                error = ex;
            }
            if (form != null)
            {
                try { form.BeginInvoke((Action)(() => form.Close())); } catch { }
            }
        });
        worker.IsBackground = true;
        if (form != null)
        {
            // 窗口显示后再开始解压，保证结束时能正常关闭窗口
            form.Shown += (s, e) => worker.Start();
            Application.Run(form);
        }
        else
        {
            worker.Start();
        }
        worker.Join();
        if (error != null)
        {
            try { if (Directory.Exists(tmp)) Directory.Delete(tmp, true); } catch { }
            if (!silent)
                MessageBox.Show("准备程序文件失败：" + error.Message + "\r\n\r\n请确认 C 盘有至少 500MB 可用空间，或改用安装版。",
                                Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return false;
        }
        return true;
    }
}
