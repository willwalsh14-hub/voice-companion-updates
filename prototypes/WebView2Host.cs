// Feasibility host only; not enabled or bundled in the released application.
using System;
using System.IO;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

class WebView2Host : Form {
    private readonly WebView2 view = new WebView2();
    private readonly string profile;
    private readonly int port;
    private readonly string destination;
    public WebView2Host(string[] args) {
        if (args.Length != 3 || !Int32.TryParse(args[1], out port) || port < 1024 || port > 65535)
            throw new ArgumentException("Expected isolated profile folder, local test port and test URL.");
        profile = Path.GetFullPath(args[0]);
        destination = args[2];
        Uri uri = new Uri(destination);
        if (uri.Scheme != "http" || !uri.IsLoopback) throw new ArgumentException("Prototype accepts loopback test fixtures only.");
        Text = "Voice Companion WebView2 feasibility test";
        Width = 1000; Height = 700; ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        Location = new System.Drawing.Point(-20000, -20000);
        view.Dock = DockStyle.Fill; Controls.Add(view);
        Shown += Start;
    }
    private async void Start(object sender, EventArgs e) {
        try {
            var handle = view.Handle;
            var options = new CoreWebView2EnvironmentOptions();
            options.AdditionalBrowserArguments = "--remote-debugging-address=127.0.0.1 --remote-debugging-port=" + port;
            var environment = await CoreWebView2Environment.CreateAsync(null, profile, options);
            await view.EnsureCoreWebView2Async(environment);
            view.CoreWebView2.NewWindowRequested += (s, request) => { request.Handled = true; };
            view.CoreWebView2.PermissionRequested += (s, request) => { request.State = CoreWebView2PermissionState.Deny; };
            view.CoreWebView2.DownloadStarting += (s, request) => { request.Cancel = true; };
            view.CoreWebView2.Navigate(destination);
        } catch (Exception error) {
            File.WriteAllText(Path.Combine(profile, "host-error.txt"), error.ToString()); Close();
        }
    }
    [STAThread]
    static void Main(string[] args) {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new WebView2Host(args));
    }
}
