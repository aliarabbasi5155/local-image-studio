'use strict';
const endpoint = document.querySelector('#shortcut-endpoint');
endpoint.value = location.origin + '/api/share-upload';
endpoint.onclick = () => endpoint.select();
if (location.protocol !== 'https:') document.querySelector('#shortcut-address-note').textContent =
  'Open this guide from the secure studio on your phone to get its HTTPS address. Do not use localhost or the computer’s HTTP address in your phone Shortcut.';
