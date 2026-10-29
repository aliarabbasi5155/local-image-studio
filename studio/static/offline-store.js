'use strict';
// The installed app's copy of the library, shared by the page and worker. The copy is the Mac's last
// snapshot; changes made away from home are kept beside it as wanted states and applied on top, so
// they survive reloads and are sent once the Mac is reachable. Library rules mirror studio/projects.py.
const OfflineCopy = (() => {
  const data = 'studio-offline-v1', media = 'studio-offline-media-v1';
  const active = ['queued', 'submitted', 'running'];
  const entry = name => `/__studio_offline/${name}.json`;
  const empty = () => ({archived:{}, favorites:{}, deletedProjects:{}, deletedVersions:{}, deletedVideos:{}});
  const size = pending => pending ? Object.values(pending).reduce((sum, group) => sum + Object.keys(group).length, 0) : 0;
  const descending = (key, a, b) => (b[key] - a[key]) || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0);
  const newest = (a, b) => descending('updated', a, b);

  async function read(name) {
    const response = await (await caches.open(data)).match(entry(name));
    return response ? response.json() : null;
  }
  async function write(name, value) {
    await (await caches.open(data)).put(entry(name), new Response(JSON.stringify(value), {headers:{'Content-Type':'application/json'}}));
  }
  async function erase() { await Promise.all([caches.delete(data), caches.delete(media)]); }

  function summarize(project) {
    const versions = project.versions, favorites = versions.filter(v => v.favorite && v.ready).length;
    const created = [...versions, ...project.videos].map(v => v.created);
    return Object.assign(project, {favorite:favorites > 0, favorite_count:favorites,
      updated:created.length ? Math.max(...created) : project.created,
      saved_count:versions.filter(v => v.ready).length, edits:versions.filter(v => v.sequence > 0).length});
  }
  // The library as it looks with this phone's changes applied.
  function view(copy, pending) {
    const projects = [];
    for (const original of copy.projects) {
      if (pending.deletedProjects[original.id]) continue;
      const project = structuredClone(original), deletedVideos = pending.deletedVideos || {};
      project.videos = (project.videos || []).filter(v => !deletedVideos[v.id]);
      if (project.id in pending.archived) project.archived = pending.archived[project.id];
      for (const version of project.versions) if (version.id in pending.favorites) version.favorite = pending.favorites[version.id];
      const removed = project.versions.filter(v => pending.deletedVersions[v.id]);
      project.versions = project.versions.filter(v => !pending.deletedVersions[v.id]);
      for (const version of project.versions) if (version.parent && removed.some(r => r.id === version.parent.id)) version.parent.deleted = true;
      // On the Mac the root is the original's own record. The Mac keeps a comparison copy of a deleted
      // original; until the next update the phone still has the image itself.
      const root = project.versions.find(v => v.id === project.root_id), gone = removed.find(r => r.id === project.root_id);
      if (root) project.root = root; else if (gone) project.root = {id:gone.id, url:gone.url, ready:true, recovered:true};
      if (project.versions.length) projects.push(summarize(project));
    }
    return projects;
  }

  function library(projects, section = 'library', query = '', offset = 0, limit = 48) {
    const sorted = [...projects].sort(newest), needle = query.toLowerCase();
    const counts = {library:sorted.filter(p => !p.archived).length, archive:sorted.filter(p => p.archived).length,
      favorites:sorted.reduce((sum, p) => sum + (p.archived ? 0 : p.favorite_count), 0)};
    const matches = sorted.filter(p => (section === 'archive' ? p.archived : !p.archived && (section !== 'favorites' || p.favorite)) &&
      (!needle || `${p.title} ${p.versions.map(v => v.prompt).join(' ')}`.toLowerCase().includes(needle)));
    return {total:matches.length, counts, navigation:matches.map(p => ({id:p.id, title:p.title})),
      projects:matches.slice(offset, offset + limit).map(p => {
        const cover = [...p.versions].reverse().find(v => v.ready) || null;
        return {id:p.id, created:p.created, updated:p.updated, title:p.title, favorite:p.favorite, favorite_count:p.favorite_count,
          saved_count:p.saved_count, archived:p.archived, edits:p.edits, root_id:p.root_id, cover, pending:p.versions.filter(v => !v.ready),
          animating:p.videos.filter(v => active.includes(v.status))};
      })};
  }
  function images(projects, section = 'library', query = '', offset = 0, limit = 48) {
    const live = projects.filter(p => p.archived === (section === 'archive')), needle = query.toLowerCase();
    const photos = live.flatMap(p => p.versions.filter(v => v.ready && (section !== 'favorites' || v.favorite) && (!needle || `${p.title} ${v.prompt}`.toLowerCase().includes(needle)))
      .map(v => ({...v, project_title:p.title, is_root:v.id === p.root_id})));
    photos.sort((a, b) => descending('created', a, b));
    const matching = new Set(photos.map(photo => photo.project_id));
    return {images:photos.slice(offset, offset + limit), total:photos.length, counts:library(projects).counts,
      navigation:live.sort(newest).filter(p => matching.has(p.id)).map(p => ({id:p.id, title:p.title}))};
  }
  // Offline, comparisons use the parent's own saved image, a pixel copy of the Mac's edit source. A parent
  // deleted on this phone is still saved here until the next update.
  function detail(copy, projects, id) {
    const project = projects.find(p => p.id === id);
    if (!project) return null;
    const result = structuredClone(project);
    const saved = new Map(copy.projects.flatMap(p => p.versions).filter(v => v.ready).map(v => [v.id, v.url]));
    for (const version of result.versions) if (version.parent && saved.has(version.parent.id)) version.parent.url = saved.get(version.parent.id);
    return result;
  }
  function mediaPaths(projects, videos = []) {
    const thumbs = [], images = [], extra = [];
    for (const project of [...projects].sort(newest)) {
      const ready = new Set(project.versions.filter(v => v.ready).map(v => v.id));
      for (const version of project.versions) {
        if (version.ready) { thumbs.push(version.thumbnail); images.push(version.url); }
        if (version.parent?.url && !ready.has(version.parent.id)) extra.push(version.parent.url);
      }
      if (project.root?.recovered && project.root.url) extra.push(project.root.url);
      for (const video of project.videos || []) { if (video.ready) thumbs.push(video.thumbnail); if (video.url) extra.push(video.url); }
    }
    for (const video of videos) if (video.url) extra.push(video.url);
    return [...new Set([...thumbs, ...images, ...extra])];
  }

  // Validates a library change against the phone's view like the Mac would, then records it in pending.
  function change(copy, pending, method, path, body) {
    const current = view(copy, pending), fail = (status, error) => ({status, body:{error}});
    const project = id => current.find(p => p.id === id);
    const saved = new Map(copy.projects.flatMap(p => p.versions.map(v => [v.id, v])));
    const favorite = (id, value) => { if (saved.get(id)?.favorite === value) delete pending.favorites[id]; else pending.favorites[id] = value; };
    const archive = (id, value) => { if (copy.projects.find(p => p.id === id)?.archived === value) delete pending.archived[id]; else pending.archived[id] = value; };
    const removeVersion = (version) => { pending.deletedVersions[version.id] = version.project_id; delete pending.favorites[version.id]; };
    const removeProject = id => {
      const original = copy.projects.find(p => p.id === id), known = original.versions.map(v => v.id);
      for (const versionId of known) { delete pending.favorites[versionId]; delete pending.deletedVersions[versionId]; }
      for (const video of original.videos || []) delete pending.deletedVideos[video.id];
      delete pending.archived[id]; pending.deletedProjects[id] = known;
    };
    const busy = versions => versions.some(v => active.includes(v.status));
    const done = result => ({status:200, body:result()});
    const parts = path.split('/').slice(3), target = project(parts[0]);
    pending.deletedVideos ??= {};
    if (path.startsWith('/api/videos/') && parts.length === 1 && method === 'DELETE') {
      const video = current.flatMap(p => p.videos).find(v => v.id === parts[0]);
      if (!video) return fail(404, 'Not Found');
      if (body?.confirm !== true) return fail(400, 'Confirm deletion of this video first.');
      if (busy([video])) return fail(409, 'Stop the video first, or wait for it to finish.');
      pending.deletedVideos[video.id] = video.project_id;
      return done(() => ({ok:true}));
    }
    if (path === '/api/bulk' && method === 'POST') {
      if (!body || !['projects', 'images'].includes(body.scope)) return fail(400, 'Select projects or images first.');
      if (!Array.isArray(body.ids) || body.ids.length < 1 || body.ids.length > 500 || body.ids.some(id => typeof id !== 'string')) return fail(400, 'Select between 1 and 500 entries.');
      const ids = [...new Set(body.ids)], versions = [];
      for (const id of ids) {
        if (body.scope === 'projects') {
          if (!project(id)) return fail(400, 'A selected project no longer exists. Refresh your selection.');
          versions.push(...project(id).versions);
        } else {
          const version = current.flatMap(p => p.versions).find(v => v.id === id);
          if (!version) return fail(400, 'A selected image no longer exists. Refresh your selection.');
          versions.push(version);
        }
      }
      if (body.action === 'favorite' && typeof body.favorite === 'boolean') versions.forEach(v => favorite(v.id, body.favorite));
      else if (body.action === 'delete') {
        if (body.confirm !== true) return fail(400, 'Confirm permanent deletion first.');
        if (busy(versions)) return fail(409, 'Stop selected queued or running images before deleting.');
        if (body.scope === 'projects') ids.forEach(removeProject); else versions.forEach(removeVersion);
      } else return fail(400, 'Choose favorite or delete.');
      return {status:200, body:{ok:true, entries:ids.length, images:versions.length}};
    }
    if (parts[0] === undefined || !path.startsWith('/api/projects/') || !['PATCH', 'DELETE'].includes(method)) return null;
    if (!target) return fail(404, 'Not Found');
    if (parts.length === 1 && method === 'PATCH') {
      const keys = Object.keys(body || {});
      if (!keys.length || keys.some(k => !['favorite', 'archived'].includes(k)) || keys.some(k => typeof body[k] !== 'boolean')) return fail(400, 'Choose favorite or archive as true or false.');
      if ('archived' in body) archive(target.id, body.archived);
      if ('favorite' in body) target.versions.forEach(v => favorite(v.id, body.favorite));
      return done(() => view(copy, pending).find(p => p.id === target.id));
    }
    if (parts.length === 1) {
      if (body?.confirm !== true) return fail(400, 'Confirm permanent deletion of the whole project first.');
      if (busy(target.versions)) return fail(409, 'Stop this project’s queued or running images before deleting it.');
      if (busy(target.videos)) return fail(409, 'Stop this project’s video before deleting it.');
      removeProject(target.id);
      return done(() => ({ok:true}));
    }
    const version = parts[1] === 'versions' && parts.length === 3 && target.versions.find(v => v.id === parts[2]);
    if (!version) return fail(404, 'Not Found');
    if (method === 'PATCH') {
      if (!version.ready) return fail(404, 'Not Found');
      if (Object.keys(body || {}).join() !== 'favorite' || typeof body.favorite !== 'boolean') return fail(400, 'Choose favorite as true or false.');
      favorite(version.id, body.favorite);
      return done(() => view(copy, pending).flatMap(p => p.versions).find(v => v.id === version.id));
    }
    if (busy([version])) return fail(409, 'Stop the queued image first, or wait for it to finish.');
    if (body?.confirm !== true) return fail(400, 'Confirm deletion of this image first.');
    removeVersion(version);
    return done(() => ({ok:true}));
  }

  const favorites = (projects, query, offset, limit) => images(projects, 'favorites', query, offset, limit);
  return {data, media, empty, size, read, write, erase, view, library, images, favorites, detail, mediaPaths, change};
})();
