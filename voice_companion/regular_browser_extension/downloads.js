// Mirrors Chrome's own downloads to Voice Companion. Chrome does the transfer
// (it has the page's sign-in, cookies and safety checks); Companion keeps one
// list and queue for every browser and asks Chrome to pause, resume or cancel.
// Only download progress and file names are sent. Addresses are cut short and
// data: addresses are not sent at all.
class CompanionDownloads {
  constructor(api, emit) {
    this.api = api; this.emit = emit; this.watching = new Set(); this.timer = null;
  }
  item(raw) {
    const address = String(raw.finalUrl || raw.url || '');
    return {id:raw.id, filename:String(raw.filename || ''), url:/^https?:/i.test(address) ? address.slice(0, 2048) : '',
            state:raw.state, paused:!!raw.paused, canResume:!!raw.canResume, error:String(raw.error || ''),
            received:Number(raw.bytesReceived) || 0, total:Number(raw.totalBytes) > 0 ? Number(raw.totalBytes) : 0,
            danger:String(raw.danger || 'safe'), mime:String(raw.mime || '')};
  }
  send(raw, extra = {}) {
    if (!raw || !Number.isInteger(raw.id)) return;
    this.emit({method:'VoiceCompanion.download', params:{...this.item(raw), ...extra}});
    if (raw.state === 'in_progress') this.watching.add(raw.id); else this.watching.delete(raw.id);
    this.schedule();
  }
  created(raw) { this.send(raw, {new:true}); }
  async refresh(id, extra = {}) {
    const [raw] = await this.api.downloads.search({id});
    if (raw) this.send(raw, extra); else this.watching.delete(id);
  }
  async changed(delta) { if (Number.isInteger(delta?.id)) await this.refresh(delta.id); }
  async control(params) {
    const id = Number(params?.id), action = params?.action;
    if (!Number.isInteger(id) || !['pause', 'resume', 'cancel'].includes(action)) return;
    try { await this.api.downloads[action](id); await this.refresh(id); }
    // Chrome said no (the file is gone, or the site cannot resume): report the real state.
    catch { await this.refresh(id, {refused:true}).catch(()=>{}); }
  }
  // Chrome has no progress event. While a download runs, read its progress each second.
  async poll() {
    for (const id of [...this.watching]) await this.refresh(id).catch(()=>this.watching.delete(id));
  }
  schedule() {
    if (this.watching.size && !this.timer) this.timer = setInterval(()=>{ void this.poll(); }, 1000);
    if (!this.watching.size && this.timer) { clearInterval(this.timer); this.timer = null; }
  }
  // After linking: report what is still running, so Companion's list is current.
  async sync() {
    const items = await this.api.downloads.search({orderBy:['-startTime'], limit:50});
    for (const raw of items.reverse()) this.send(raw);
  }
  stop() { this.watching.clear(); this.schedule(); }
}
globalThis.CompanionDownloads = CompanionDownloads;
