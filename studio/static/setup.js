'use strict';
// The secure address uses the same host this phone just reached, so it works for IP and .local addresses.
const secureURL = `https://${location.hostname}:7443/`;
const status = document.querySelector('#setup-status');
document.querySelector('#open-secure').href = secureURL;
async function check() {
  try {
    // This request only succeeds after the phone trusts the computer's certificate.
    await fetch(secureURL + 'api/health', {mode:'no-cors', cache:'no-store'});
    status.lastChild.textContent = ' This phone trusts your computer. Opening the studio…';
    location.replace(secureURL);
  } catch {
    status.lastChild.textContent = ' This phone doesn’t trust your computer yet. Follow the steps below.';
    status.classList.add('waiting');
  }
}
check();
setInterval(() => { if (!document.hidden) check(); }, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
