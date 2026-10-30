'use strict';
const secureURL = `https://${location.hostname}:7443/`;
const status = document.querySelector('#setup-status');
const message = document.querySelector('#setup-message');
const install = document.querySelector('#install-app');
let checking = false, installPrompt = null;
document.querySelector('#open-secure').href = secureURL;
document.querySelector('#check-secure').href = secureURL + 'setup';

function showStatus(text, waiting = true) {
  message.textContent = text;
  status.classList.toggle('waiting', waiting);
}

async function certificate() {
  try {
    const response = await fetch('/setup/certificate.json', {cache:'no-store'});
    if (!response.ok) return;
    const details = await response.json();
    document.querySelector('#certificate-name').textContent = details.name;
    document.querySelector('#certificate-file').textContent = details.filename;
    document.querySelector('#certificate-fingerprint').textContent = details.fingerprint_sha256;
    document.querySelector('#download-certificate').download = details.filename;
  } catch { /* The download link still works when the details cannot be read. */ }
}

async function check() {
  if (checking) return;
  checking = true;
  try {
    if (location.protocol !== 'https:') {
      // Reachability can follow a browser certificate exception; it does not prove trust.
      await fetch(secureURL + 'api/health', {mode:'no-cors', cache:'no-store'});
      showStatus('The secure server is reachable. Choose Check installation below to verify app support.');
      return;
    }
    if (!window.isSecureContext || !('serviceWorker' in navigator)) {
      showStatus('App installation needs trusted HTTPS in a supported browser. Use Chrome and install this computer’s CA certificate.');
      return;
    }
    await navigator.serviceWorker.register('/sw.js');
    let timer;
    try {
      await Promise.race([navigator.serviceWorker.ready, new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error('Offline app setup did not finish. Tap Check again.')), 15000);
      })]);
    } finally { clearTimeout(timer); }
    showStatus('Secure app checks passed. Use Install app, or Chrome’s ⋮ menu → Add to Home screen → Install.', false);
  } catch (error) {
    showStatus(location.protocol === 'https:'
      ? 'App setup is blocked: ' + (error.message || 'certificate or connection error') + ' Install the matching CA certificate, close the studio tabs, restart Chrome, then open this address again.'
      : 'The secure connection is not trusted or reachable yet. Install the matching CA certificate below, then choose Check installation.');
  } finally { checking = false; }
}

window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault(); installPrompt = event; install.hidden = false;
});
install.addEventListener('click', async () => {
  if (!installPrompt) return;
  await installPrompt.prompt();
  installPrompt = null; install.hidden = true;
});
document.querySelector('#retry-check').addEventListener('click', check);
certificate();
check();
