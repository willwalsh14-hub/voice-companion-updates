for (const button of document.querySelectorAll('button')) {
  button.addEventListener('click', async () => {
    const status = document.getElementById('status');
    status.textContent = 'Working.';
    try {
      const result = await chrome.runtime.sendMessage({command: button.id});
      status.textContent = result.ok ? result.message : result.error;
    } catch (_) {
      status.textContent = 'The browser connection stopped. Open Voice Companion; the browser will reconnect automatically.';
    }
  });
}

// If the worker cannot answer, say so; never stay on "Checking" or read "undefined".
chrome.runtime.sendMessage({command:"status"}).then(
  result=>{document.getElementById("status").textContent=result?.ok===false?result.error:result?.message||'Open Voice Companion. The browser will reconnect automatically.';},
  ()=>{document.getElementById("status").textContent='The browser connection stopped. Open Voice Companion; the browser will reconnect automatically.';});
