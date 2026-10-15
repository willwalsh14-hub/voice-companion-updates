for (const button of document.querySelectorAll('button')) {
  button.addEventListener('click', async () => {
    const status = document.getElementById('status');
    status.textContent = 'Working.';
    try {
      const result = await chrome.runtime.sendMessage({command: button.id});
      status.textContent = result.ok ? result.message : result.error;
    } catch (_) {
      status.textContent = 'The browser connection stopped. Connect this tab again.';
    }
  });
}
