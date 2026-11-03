const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

async function setup({https=false, workerError=null}={}) {
  const elements=new Map(), events={}, registrations=[];
  const element=selector => {
    if (!elements.has(selector)) elements.set(selector,{hidden:selector==='#install-app',textContent:'',classList:{toggle(){}},addEventListener(type,callback){this[type]=callback;}});
    return elements.get(selector);
  };
  const location={hostname:'192.168.1.20',protocol:https?'https:':'http:',replace(){throw Error('Unexpected redirect');}};
  const context=vm.createContext({location,document:{querySelector:element},window:{isSecureContext:https,addEventListener(type,callback){events[type]=callback;}},
    navigator:{serviceWorker:{async register(url){registrations.push(url);if(workerError)throw Error(workerError);},ready:Promise.resolve({active:true})}},
    fetch:async url => ({ok:true,json:async()=>({name:'Image Studio Home (studio-test)',filename:'ubuntu-123.crt',fingerprint_sha256:'12:34'})}),
    setTimeout,clearTimeout});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/setup.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  return {elements,events,registrations};
}

test('HTTP reachability does not claim trust or redirect away from certificate instructions',async()=>{
  const {elements,registrations}=await setup();
  assert.match(elements.get('#setup-message').textContent,/reachable/);
  assert.doesNotMatch(elements.get('#setup-message').textContent,/trusts|passed/);
  assert.equal(elements.get('#certificate-file').textContent,'ubuntu-123.crt');
  assert.equal(elements.get('#check-secure').href,'https://192.168.1.20:7443/setup');
  assert.equal(registrations.length,0);
});

test('an HTTPS certificate exception that blocks service workers is shown as an installation failure',async()=>{
  const {elements}=await setup({https:true,workerError:'An SSL certificate error occurred'});
  assert.match(elements.get('#setup-message').textContent,/blocked.*SSL certificate/);
  assert.doesNotMatch(elements.get('#setup-message').textContent,/passed/);
  assert.equal(elements.get('#install-app').hidden,true);
});

test('trusted HTTPS registers the worker and shows the browser installation action',async()=>{
  const {elements,events,registrations}=await setup({https:true});
  assert.deepEqual(registrations,['/sw.js']);
  assert.match(elements.get('#setup-message').textContent,/checks passed/);
  let prompted=0;
  events.beforeinstallprompt({preventDefault(){},async prompt(){prompted++;}});
  assert.equal(elements.get('#install-app').hidden,false);
  await elements.get('#install-app').click();
  assert.equal(prompted,1);
});
