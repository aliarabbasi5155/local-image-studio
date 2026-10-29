const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function harness() {
  const nodes = new Map();
  const element = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {style:{}, hidden:false, disabled:false, textContent:'', remove() { nodes.delete(selector); }});
    return nodes.get(selector);
  };
  for (const id of ['lb-crop', 'lb-crop-layer', 'closeup-size', 'closeup-send', 'closeup-find', 'closeup-subject', 'closeup-status', 'closeup-error']) element(`#${id}`);
  const context = vm.createContext({
    $:selector => nodes.get(selector), connection:{offline:false},
    viewer:{project:{id:'project'}, quality:'fast', tool:'closeup'},
    state:{options:{qualities:{fast:{sizes:{square:[512,512],portrait:[512,640],landscape:[640,512]}}, high:{sizes:{square:[1024,1024],portrait:[832,1216],landscape:[1216,832]}}}}},
    studioSettings:{edit:{variations:2}},
    editSourceFor:async () => ({id:'source'}), loadAutomaticMask:async row => row,
    renderTools() {}, fitPhoto() {}, resetZoom() {}, setComparing() {}, setPeek() {},
    refresh:async () => {}, selectedImage:() => ({id:'photo', width:1000, height:800}),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/closeup.js'), 'utf8'), context);
  const run = code => vm.runInContext(code, context);
  run("Object.assign(closeup, {item:{id:'photo', width:1000, height:800}, frame:defaultFrame(), subject:'face'})");
  return {context, run, nodes, element};
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const plain = value => JSON.parse(JSON.stringify(value));

test('a pending finder cannot change a new frame or clear its busy state', async () => {
  const {context, run, nodes} = harness(), pending = deferred();
  context.send = () => pending.promise;
  const operation = run('findCloseUp()');
  await new Promise(resolve => setImmediate(resolve));
  run("closeCloseUp(); Object.assign(closeup, {item:{id:'second'}, busy:true})");
  nodes.delete('#closeup-status');
  pending.reject(new Error('Finder failed after leaving the photo'));
  await operation;
  assert.equal(run('closeup.busy'), true);
  assert.equal(run('closeup.item.id'), 'second');
  assert.deepEqual(plain(run('closeup.matches')), []);
});

test('leaving while masks decode ignores the old result', async () => {
  const {context, run} = harness(), decoding = deferred();
  context.send = async () => ({candidates:[{}]});
  context.loadAutomaticMask = () => decoding.promise;
  const operation = run('findCloseUp()');
  await new Promise(resolve => setImmediate(resolve));
  run('closeCloseUp()');
  decoding.resolve({naturalWidth:100, naturalHeight:100});
  await operation;
  assert.equal(run('closeup.item'), null);
  assert.deepEqual(plain(run('closeup.matches')), []);
});

test('submission snapshots quality and settings before preparing the crop', async () => {
  const {context, run, nodes} = harness(), preparing = deferred(), requests = [];
  context.send = (url, body) => {
    requests.push({url, body:plain(body)});
    return requests.length === 1 ? preparing.promise : Promise.resolve({jobs:['job']});
  };
  const operation = run('submitCloseUp({preventDefault(){}})');
  assert.equal(nodes.get('#closeup-find').disabled, true);
  await run('findCloseUp()'); // Enter/Find must not submit a second request while busy.
  run("viewer.quality = 'high'; closeup.detail = 'detailed'; closeup.subject = 'hand'; closeup.size = '4k'");
  preparing.resolve({id:'crop'});
  await operation;
  assert.equal(requests.length, 2);
  assert.equal(requests[0].body.quality, 'fast');
  assert.equal(requests[1].body.quality, 'fast');
  assert.deepEqual(requests[1].body.close_up, {detail:'faithful', subject:'face'});
  assert.equal(context.viewer.autoSelect.id, 'job');
});

test('leaving during crop preparation does not queue a close-up', async () => {
  const {context, run} = harness(), preparing = deferred(), requests = [];
  context.send = (url, body) => { requests.push(url); return preparing.promise; };
  const operation = run('submitCloseUp({preventDefault(){}})');
  run('closeCloseUp()');
  context.viewer.project = {id:'another-project'};
  preparing.resolve({id:'crop'});
  await operation;
  assert.equal(requests.length, 1);
  assert.equal(context.viewer.autoSelect, undefined);
});

test('a queued result does not switch tools in a different photo', async () => {
  const {context, run} = harness(), queued = deferred();
  context.send = async url => url === '/api/generate' ? queued.promise : {id:'crop'};
  const operation = run('submitCloseUp({preventDefault(){}})');
  await new Promise(resolve => setImmediate(resolve));
  run("closeCloseUp(); Object.assign(closeup, {item:{id:'second'}, busy:true})");
  queued.resolve({jobs:['old-job']});
  await operation;
  assert.equal(context.viewer.autoSelect, undefined);
  assert.equal(context.viewer.tool, 'closeup');
  assert.equal(run('closeup.busy'), true);
});

test('size hints disclose shrinking and track the rounded quality budget', () => {
  const {run, nodes} = harness();
  run('closeup.frame = {x:0, y:0, w:1, h:1}; renderFrame()');
  assert.match(nodes.get('#closeup-size').textContent, /smaller output/);
  const fast = run('enlargement(400, 320)');
  run("viewer.quality = 'high'");
  assert.ok(run('enlargement(400, 320)') > fast);
});

test('reusing a close-up restores its frame and seed on the parent', () => {
  const {context, run} = harness();
  run('renderCloseUpPanel = () => {}; renderTools = () => {}');
  context.recipe = {seed:42, close_up:{from:[100, 200, 300, 400], detail:'detailed', size:'2k', subject:'hand'}};
  run('openCloseUp(recipe)');
  assert.deepEqual(plain(run('framePixels()')), [100, 200, 300, 400]);
  assert.equal(run('closeup.seed'), 42);
  assert.equal(run('closeup.detail'), 'detailed');
  assert.equal(run('closeup.subject'), 'hand');
});
