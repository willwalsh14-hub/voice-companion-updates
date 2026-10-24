// Owned embedded browser. CDP travels over inherited private stdin/stdout pipes.
using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

class CompanionWebView : Form {
    private readonly WebView2 view = new WebView2();
    private readonly TabControl tabs = new TabControl { Dock = DockStyle.Fill };
    private CoreWebView2Environment environment;
    private readonly Dictionary<string, WebView2> views = new Dictionary<string, WebView2>();
    private readonly Dictionary<string, string> targets = new Dictionary<string, string>();
    private readonly Dictionary<string, WebView2> children = new Dictionary<string, WebView2>();
    private readonly Dictionary<string, string> openers = new Dictionary<string, string>();
    private readonly Dictionary<string, string> navigation = new Dictionary<string, string>();
    private readonly HashSet<WebView2> noCache = new HashSet<WebView2>();
    private readonly HashSet<WebView2> intercepting = new HashSet<WebView2>();
    private readonly HashSet<string> fetchEnabled = new HashSet<string>();
    private readonly HashSet<string> attached = new HashSet<string>();
    private bool autoAttach;
    private int serial;
    // Downloads: Companion keeps the list and queue (downloads.py); this window does the transfer.
    private string downloadFolder;
    private readonly Dictionary<string, CoreWebView2DownloadOperation> downloads = new Dictionary<string, CoreWebView2DownloadOperation>();
    private int downloadSerial;
    private readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 };
    private readonly string profile;
    private readonly bool hidden;
    private readonly object outputLock = new object();
    protected override bool ShowWithoutActivation { get { return hidden; } }
    public CompanionWebView(string[] args) {
        if (args.Length < 1) throw new ArgumentException("Missing owned browser profile.");
        profile = Path.GetFullPath(args[0]); hidden = args.Length > 1 && args[1] == "--test-hidden";
        Text = "Voice Companion - Embedded Edge"; Width = 1100; Height = 760;
        if (hidden) { ShowInTaskbar = false; StartPosition = FormStartPosition.Manual; Location = new System.Drawing.Point(-20000, -20000); }
        view.Dock = DockStyle.Fill; var first = new TabPage("Page 1"); first.Controls.Add(view); tabs.TabPages.Add(first); Controls.Add(tabs); views["main-view"] = view;
        tabs.SelectedIndexChanged += (s, e) => ActiveTab(); Shown += Start;
    }
    private void Send(object message) { SendLine(json.Serialize(message)); }
    private void SendLine(string line) { lock (outputLock) { Console.WriteLine(line); Console.Out.Flush(); } }
    // Browser events and results are already JSON text. Pass them through as
    // they are: parsing and rewriting every network event and every large page
    // snapshot on this window's thread made busy pages (video sites) lag.
    // One line is one message, so text that holds a raw line break is rewritten.
    private string Compact(string raw) {
        if (String.IsNullOrEmpty(raw)) return "{}";
        return raw.IndexOf('\n') < 0 && raw.IndexOf('\r') < 0 ? raw : json.Serialize(json.DeserializeObject(raw));
    }
    private void ActiveTab() {
        foreach (var item in views) if (item.Value.Parent == tabs.SelectedTab) {
            Send(new { method = "Host.activeTab", @params = new { sessionId = item.Key } }); break;
        }
    }
    private async void Start(object sender, EventArgs args) {
        try {
            var handle = view.Handle;
            // No back/forward cache, as in the guided Chrome browser: a page shown
            // again from that cache sends no load events and hides its frames
            // from the automation driver, so Back could not read it.
            var options = new CoreWebView2EnvironmentOptions("--disable-back-forward-cache");
            environment = await CoreWebView2Environment.CreateAsync(null, profile, options);
            await view.EnsureCoreWebView2Async(environment);
            Register(view, "main-view");
            var loaded = new TaskCompletionSource<bool>();
            EventHandler<CoreWebView2NavigationCompletedEventArgs> completed = null;
            completed = (s, e) => { view.CoreWebView2.NavigationCompleted -= completed; loaded.TrySetResult(true); };
            view.CoreWebView2.NavigationCompleted += completed;
            view.CoreWebView2.Navigate("about:blank"); await loaded.Task;
            Send(new { method = "Host.ready", @params = new { version = environment.BrowserVersionString } });
            Task.Run(() => Read());
        } catch (Exception error) { Send(new { method = "Host.error", @params = new { message = error.Message } }); Close(); }
    }
    private void Register(WebView2 control, string sessionId) {
        var core = control.CoreWebView2;
            // The request filter is added only while the automation driver turns
            // the cache off (Network.setCacheDisabled). With it always on, every
            // request (each piece of a video) waited for this window's thread.
            core.WebResourceRequested += (s, request) => {
                if (noCache.Contains(control)) { request.Request.Headers.SetHeader("Cache-Control", "no-cache"); request.Request.Headers.SetHeader("Pragma", "no-cache"); }
            };
            core.PermissionRequested += (s, request) => { request.State = CoreWebView2PermissionState.Deny; };
            core.DownloadStarting += (s, request) => StartDownload(request);
            core.DocumentTitleChanged += (s, e) => { var page = control.Parent as TabPage; if (page != null) page.Text = core.DocumentTitle.Length > 60 ? core.DocumentTitle.Substring(0,60) : core.DocumentTitle; };
            core.NewWindowRequested += async (s, request) => {
                var pending = request.GetDeferral(); request.Handled = true;
                try {
                    var info = (Dictionary<string, object>)await TargetInfo(control);
                    Uri destination;
                    if (views.Count < 20 && Uri.TryCreate(request.Uri, UriKind.Absolute, out destination)
                        && (destination.Scheme == "https" || destination.Scheme == "http"))
                        await AddView(request.Uri, (string)info["targetId"]);
                } finally { pending.Complete(); }
            };
            // Register protocol events before the automation driver enables domains.
            string[] events = {
                "Target.attachedToTarget", "Target.detachedFromTarget", "Target.targetCreated", "Target.targetDestroyed", "Target.targetInfoChanged",
                "Runtime.executionContextCreated", "Runtime.executionContextDestroyed", "Runtime.executionContextsCleared", "Runtime.consoleAPICalled", "Runtime.exceptionThrown", "Runtime.bindingCalled",
                "Page.frameAttached", "Page.frameDetached", "Page.frameNavigated", "Page.navigatedWithinDocument", "Page.frameStartedLoading", "Page.frameStoppedLoading", "Page.lifecycleEvent", "Page.javascriptDialogOpening", "Page.javascriptDialogClosed", "Page.fileChooserOpened", "Page.windowOpen", "Page.domContentEventFired", "Page.loadEventFired", "Page.frameRequestedNavigation",
                "Network.requestWillBeSent", "Network.requestWillBeSentExtraInfo", "Network.responseReceived", "Network.responseReceivedExtraInfo", "Network.loadingFinished", "Network.loadingFailed", "Network.requestServedFromCache", "Network.webSocketCreated", "Network.webSocketClosed", "Network.webSocketFrameSent", "Network.webSocketFrameReceived",
                "Fetch.requestPaused", "Fetch.authRequired", "Log.entryAdded", "Inspector.detached", "Inspector.targetCrashed", "DOM.documentUpdated", "Browser.downloadWillBegin", "Browser.downloadProgress"
            };
            foreach (string name in events) {
                try {
                    string eventName = name;
                    core.GetDevToolsProtocolEventReceiver(name).DevToolsProtocolEventReceived += (s, e) => {
                        if (eventName == "Fetch.requestPaused" && !intercepting.Contains(control)) {
                            var detail = json.Deserialize<Dictionary<string, object>>(e.ParameterObjectAsJson);
                            var parameters = json.Serialize(new { requestId = (string)detail["requestId"] });
                            var continued = String.IsNullOrEmpty(e.SessionId)
                                ? core.CallDevToolsProtocolMethodAsync("Fetch.continueRequest", parameters)
                                : core.CallDevToolsProtocolMethodForSessionAsync(e.SessionId, "Fetch.continueRequest", parameters);
                            continued.ContinueWith(task => { var ignored = task.Exception; }, TaskContinuationOptions.OnlyOnFaulted);
                            return;
                        }
                        string target = !String.IsNullOrEmpty(e.SessionId) ? e.SessionId : eventName.StartsWith("Browser.") ? null : sessionId;
                        if (eventName == "Target.attachedToTarget") {
                            var detail = json.Deserialize<Dictionary<string, object>>(e.ParameterObjectAsJson);
                            if (detail.ContainsKey("sessionId")) children[(string)detail["sessionId"]] = control;
                        }
                        SendLine("{\"method\":" + json.Serialize(eventName) + ",\"params\":" + Compact(e.ParameterObjectAsJson)
                            + (target != null ? ",\"sessionId\":" + json.Serialize(target) : "") + "}");
                    };
                } catch (ArgumentException) { }
            }
    }
    private void StartDownload(CoreWebView2DownloadStartingEventArgs request) {
        var operation = request.DownloadOperation;
        string id = "edge-" + (++downloadSerial);
        try {
            string name = Path.GetFileName(request.ResultFilePath);
            if (!String.IsNullOrEmpty(downloadFolder) && !String.IsNullOrEmpty(name) && Directory.Exists(downloadFolder))
                request.ResultFilePath = FreePath(downloadFolder, name);
        } catch (Exception) { }
        // Companion says "Downloading" and keeps the list; the browser's own download box stays hidden.
        request.Handled = true;
        downloads[id] = operation;
        DateTime last = DateTime.MinValue;
        operation.BytesReceivedChanged += (s, e) => {
            // Twice a second is enough for "45 percent"; every block would flood the pipe.
            if ((DateTime.UtcNow - last).TotalMilliseconds < 500) return;
            last = DateTime.UtcNow; ReportDownload(id, operation, false, null, false);
        };
        operation.StateChanged += (s, e) => {
            ReportDownload(id, operation, false, null, false);
            if (operation.State == CoreWebView2DownloadState.Completed || (operation.State == CoreWebView2DownloadState.Interrupted && !operation.CanResume))
                downloads.Remove(id);
        };
        ReportDownload(id, operation, true, request.ResultFilePath, false);
    }
    private void ReportDownload(string id, CoreWebView2DownloadOperation operation, bool started, string path, bool refused) {
        try {
            long? total = operation.TotalBytesToReceive;
            string url = operation.Uri ?? "";
            if (url.Length > 2048) url = url.Substring(0, 2048);
            Send(new { method = "Host.download", @params = new {
                id = id, @new = started, refused = refused, path = path ?? operation.ResultFilePath, url = url,
                state = operation.State.ToString(), reason = operation.InterruptReason.ToString(),
                received = operation.BytesReceived, total = total.HasValue ? total.Value : -1L, canResume = operation.CanResume } });
        } catch (Exception) { }
    }
    private static string FreePath(string folder, string name) {
        string target = Path.Combine(folder, name);
        string stem = Path.GetFileNameWithoutExtension(name), extension = Path.GetExtension(name);
        for (int number = 1; File.Exists(target); number++) target = Path.Combine(folder, stem + " (" + number + ")" + extension);
        return target;
    }
    private async Task<object> TargetInfo(WebView2 control) {
        string info = await control.CoreWebView2.CallDevToolsProtocolMethodAsync("Target.getTargetInfo", "{}");
        return json.Deserialize<Dictionary<string, object>>(info)["targetInfo"];
    }
    private async Task Attach(string sessionId, WebView2 control) {
        if (!attached.Add(sessionId)) return;
        var info = (Dictionary<string, object>)await TargetInfo(control);
        if (openers.ContainsKey(sessionId)) { info["openerId"] = openers[sessionId]; info["canAccessOpener"] = true; }
        targets[(string)info["targetId"]] = sessionId;
        Send(new { method = "Target.attachedToTarget", @params = new { sessionId = sessionId, targetInfo = info, waitingForDebugger = true } });
    }
    private async Task<WebView2> AddView(string url, string opener = null) {
        string sessionId = "view-" + (++serial);
        var control = new WebView2 { Dock = DockStyle.Fill };
        var page = new TabPage("New page"); page.Controls.Add(control); tabs.TabPages.Add(page); tabs.SelectedTab = page;
        var handle = control.Handle; await control.EnsureCoreWebView2Async(environment);
        views[sessionId] = control; if (opener != null) openers[sessionId] = opener; Register(control, sessionId);
        if (url != "about:blank") navigation[sessionId] = url;
        if (autoAttach) await Attach(sessionId, control);
        else if (url != "about:blank") { navigation.Remove(sessionId); control.CoreWebView2.Navigate(url); }
        ActiveTab(); return control;
    }
    private void Reply(Dictionary<string, object> request, object result) {
        var response = new Dictionary<string, object> { { "id", request["id"] }, { "result", result } };
        if (request.ContainsKey("sessionId")) response["sessionId"] = request["sessionId"];
        Send(response);
    }
    private void ReplyRaw(Dictionary<string, object> request, string result) {
        SendLine("{\"id\":" + json.Serialize(request["id"]) + ",\"result\":" + Compact(result)
            + (request.ContainsKey("sessionId") ? ",\"sessionId\":" + json.Serialize(request["sessionId"]) : "") + "}");
    }
    private void Read() {
        try {
            string line;
            while ((line = Console.ReadLine()) != null) {
                if (line.Length > 16 * 1024 * 1024) break;
                var message = json.Deserialize<Dictionary<string, object>>(line);
                BeginInvoke(new Action(() => Dispatch(message)));
            }
        } catch (Exception) { }
        try { BeginInvoke(new Action(Close)); } catch (InvalidOperationException) { }
    }
    private async void Dispatch(Dictionary<string, object> message) {
        object id = message.ContainsKey("id") ? message["id"] : 0;
        try {
            string method = (string)message["method"];
            if (method == "Host.close") { Send(new { id = id, result = new { } }); Close(); return; }
            if (method == "Host.focus") { WindowState = FormWindowState.Normal; Activate(); Send(new { id = id, result = new { } }); return; }
            var parameters = message.ContainsKey("params") ? (Dictionary<string, object>)message["params"] : new Dictionary<string, object>();
            string sessionId = message.ContainsKey("sessionId") ? (string)message["sessionId"] : null;
            if (method == "Host.downloadFolder") {
                downloadFolder = parameters.ContainsKey("path") ? parameters["path"] as string : null;
                Reply(message, new { }); return;
            }
            if (method == "Host.downloadControl") {
                string target = parameters.ContainsKey("id") ? parameters["id"] as string : null;
                string action = parameters.ContainsKey("action") ? parameters["action"] as string : null;
                CoreWebView2DownloadOperation operation;
                if (target == null || !downloads.TryGetValue(target, out operation)) throw new ArgumentException("This download is no longer available.");
                try {
                    if (action == "pause") operation.Pause();
                    else if (action == "resume") operation.Resume();
                    else if (action == "cancel") operation.Cancel();
                    else throw new ArgumentException("Unknown download action.");
                } catch (Exception) {
                    // Tell Companion at once what really happened, so its list is not left guessing.
                    ReportDownload(target, operation, false, null, true); throw;
                }
                Reply(message, new { }); return;
            }
            if (method == "Target.createTarget") {
                var created = await AddView(parameters.ContainsKey("url") ? (string)parameters["url"] : "about:blank");
                var info = (Dictionary<string, object>)await TargetInfo(created);
                Reply(message, new { targetId = (string)info["targetId"] }); return;
            }
            if (method == "Target.closeTarget" || method == "Target.activateTarget") {
                string target = (string)parameters["targetId"];
                if (!targets.ContainsKey(target)) throw new ArgumentException("This tab is no longer available.");
                string selected = targets[target]; var control = views[selected]; var page = (TabPage)control.Parent;
                if (method == "Target.activateTarget") tabs.SelectedTab = page;
                else {
                    Send(new { method = "Target.detachedFromTarget", @params = new { sessionId = selected, targetId = target } });
                    Send(new { method = "Target.targetDestroyed", @params = new { targetId = target } });
                    views.Remove(selected); targets.Remove(target); attached.Remove(selected);
                    noCache.Remove(control); intercepting.Remove(control); fetchEnabled.Remove(selected);
                    navigation.Remove(selected); openers.Remove(selected);
                    foreach (var child in new Dictionary<string, WebView2>(children))
                        if (child.Value == control) { children.Remove(child.Key); fetchEnabled.Remove(child.Key); }
                    tabs.TabPages.Remove(page); control.Dispose(); page.Dispose();
                }
                Reply(message, new { success = true }); return;
            }
            WebView2 owner = view;
            if (sessionId != null) {
                if (views.ContainsKey(sessionId)) owner = views[sessionId];
                else if (children.ContainsKey(sessionId)) owner = children[sessionId];
                else throw new ArgumentException("This tab is no longer available.");
            } else if (!views.ContainsValue(owner)) {
                foreach (var live in views.Values) { owner = live; break; }
            }
            if (method == "Network.setCacheDisabled") {
                if (parameters.ContainsKey("cacheDisabled") && (bool)parameters["cacheDisabled"]) {
                    if (noCache.Add(owner)) owner.CoreWebView2.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
                } else if (noCache.Remove(owner)) owner.CoreWebView2.RemoveWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
                Reply(message, new { }); return;
            }
            if (method == "Fetch.enable") {
                intercepting.Add(owner);
                // Keep native interception enabled once per session. Playwright
                // filters its routes itself; toggling an idle WebView's Fetch
                // domain can leave the native API's completion task unresolved.
                if (!fetchEnabled.Add(sessionId ?? "main-view")) { Reply(message, new { }); return; }
            }
            if (method == "Fetch.disable") {
                // Some WebView2 versions do not complete this call for idle
                // controls. Disable routing immediately; any residual paused
                // request is continued by the native receiver above.
                intercepting.Remove(owner);
                Reply(message, new { }); return;
            }
            if (method == "Page.bringToFront" && owner.Parent is TabPage) tabs.SelectedTab = (TabPage)owner.Parent;
            string result = sessionId != null && !views.ContainsKey(sessionId)
                ? await owner.CoreWebView2.CallDevToolsProtocolMethodForSessionAsync(sessionId, method, json.Serialize(parameters))
                : await owner.CoreWebView2.CallDevToolsProtocolMethodAsync(method, json.Serialize(parameters));
            if (method == "Target.setAutoAttach" && sessionId == null) {
                autoAttach = parameters.ContainsKey("autoAttach") && (bool)parameters["autoAttach"];
                if (autoAttach) foreach (var item in new Dictionary<string, WebView2>(views)) await Attach(item.Key, item.Value);
            }
            ReplyRaw(message, result);
            if (method == "Runtime.runIfWaitingForDebugger" && sessionId != null && navigation.ContainsKey(sessionId)) {
                string destination = navigation[sessionId]; navigation.Remove(sessionId); owner.CoreWebView2.Navigate(destination);
            }
        } catch (Exception error) {
            var response = new Dictionary<string, object> { { "id", id }, { "error", new { code = -32000, message = error.Message } } };
            if (message.ContainsKey("sessionId")) response["sessionId"] = message["sessionId"];
            Send(response);
        }
    }
    [STAThread] static void Main(string[] args) {
        Console.InputEncoding = new System.Text.UTF8Encoding(false);
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new CompanionWebView(args));
    }
}
