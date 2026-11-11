using System;
using System.Diagnostics;
using System.IO;
using System.Text;

// Small console bridge. Reuse the desktop payload instead of packaging Python twice.
class QingdanCLI {
    static string Quote(string value) {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append(c); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"');
        return result.ToString();
    }
    static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        string resultPath = Path.GetTempFileName();
        try {
            var command = new StringBuilder("--output " + Quote(resultPath));
            foreach (string arg in args) command.Append(" ").Append(Quote(arg));
            var start = new ProcessStartInfo(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Qingdan.exe"), command.ToString());
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            using (var process = Process.Start(start)) {
                process.WaitForExit();
                string result = File.ReadAllText(resultPath, Encoding.UTF8);
                if (process.ExitCode == 0) Console.Write(result);
                else Console.Error.Write(result.Length > 0 ? result : "Qingdan CLI failed before producing a result.\n");
                return process.ExitCode;
            }
        } catch (Exception error) {
            Console.Error.WriteLine(error.Message);
            return 3;
        } finally {
            try { File.Delete(resultPath); } catch (IOException) { }
        }
    }
}
