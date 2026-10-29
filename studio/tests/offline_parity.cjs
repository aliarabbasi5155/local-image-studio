// Answers test_offline.py's steps from a library copy, the way the installed app does away from home.
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const sandbox = vm.createContext({structuredClone, Response});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/offline-store.js'), 'utf8'), sandbox);
const OfflineCopy = vm.runInContext('OfflineCopy', sandbox);
const {copy, steps} = JSON.parse(fs.readFileSync(0, 'utf8'));
const pending = OfflineCopy.empty();
const results = steps.map(step => {
  const projects = OfflineCopy.view(copy, pending);
  if (step.change) {
    const {method, path: route, body} = step.change;
    return OfflineCopy.change(copy, pending, method, route, body);
  }
  if (step.kind === 'library') return OfflineCopy.library(projects, step.section, step.q, step.offset, step.limit);
  if (step.kind === 'favorites') return OfflineCopy.favorites(projects, step.q, step.offset, step.limit);
  if (step.kind === 'photos') return OfflineCopy.images(projects, step.section, step.q, step.offset, step.limit);
  return projects.find(project => project.id === step.id) || null;
});
process.stdout.write(JSON.stringify(results));
