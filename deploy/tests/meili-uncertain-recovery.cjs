'use strict';
// Isolated disposable Meilisearch only; never point this at production.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
if (process.env.MEILI_DISPOSABLE !== 'yes' || !process.env.MEILI_TEST_IMAGE) throw Error('disposable opt-in and local image required');
const name = `shirone-uncertain-${process.pid}`;
const key = 'synthetic-only-uncertain-swap-key';
const docker = (...args) => execFileSync('docker', args, {encoding:'utf8', stdio:['ignore','pipe','pipe']}).trim();
const wait = async (fn, label) => { for (let i=0; i<200; i++) { try { const v=await fn(); if(v) return v; } catch {} await new Promise(r=>setTimeout(r,100)); } throw Error(`timed out: ${label}`); };
const nativeFetch=global.fetch;
const path=require.resolve('../../cms/src/search/meilisearch.js');
const doc={documentId:'public001',slug:'hello',body:'真实恢复内容',publishedAt:'2026-10-01T00:00:00Z',metadata:{title:'恢复验证文章'}};
const strapi={documents:()=>({findMany:async({start,limit})=>[doc].slice(start,start+limit),findOne:async()=>doc})};
async function get(route) { return nativeFetch(process.env.MEILI_URL+route,{headers:{authorization:`Bearer ${key}`},signal:AbortSignal.timeout(5000)}); }
function start() { docker('run','-d','--name',name,'-p','127.0.0.1::7700','-e',`MEILI_MASTER_KEY=${key}`,process.env.MEILI_TEST_IMAGE); const port=docker('port',name,'7700/tcp').split(':').pop(); process.env.MEILI_URL=`http://127.0.0.1:${port}`; }
(async()=>{
  try {
    docker('image','inspect',process.env.MEILI_TEST_IMAGE);
    process.env.MEILI_ADMIN_KEY=key; process.env.MEILI_TASKS_KEY=key; process.env.MEILI_INDEX='posts';
    start(); await wait(async()=>(await get('/health')).ok,'first search process');
    let intercepted=false;
    global.fetch=async (url,opts)=>{
      const r=await nativeFetch(url,opts);
      if (!intercepted && opts?.method==='POST' && String(url).endsWith('/swap-indexes')) { intercepted=true; await r.body?.cancel(); throw Error('synthetic lost swap receipt'); }
      return r;
    };
    let search=require(path);
    await assert.rejects(search.rebuild(strapi),/synthetic lost swap receipt/);
    assert.ok(intercepted,'actual swap request was sent');
    global.fetch=nativeFetch;
    assert.equal((await get('/indexes/posts_rebuild')).status,200,'staging is retained');
    await assert.rejects(search.rebuild(strapi),/swap outcome uncertain/);
    // Fence the old writer and process, discard the rebuildable index data,
    // then start a fresh process; no pending task from the old process survives.
    docker('rm','-f',name); start(); await wait(async()=>(await get('/health')).ok,'fresh search process');
    assert.equal((await get('/indexes/posts_rebuild')).status,404);
    delete require.cache[path]; search=require(path);
    await search.rebuild(strapi);
    const result=await nativeFetch(process.env.MEILI_URL+'/indexes/posts/search',{method:'POST',headers:{authorization:`Bearer ${key}`,'content-type':'application/json'},body:JSON.stringify({q:'恢复验证'})});
    assert.ok(result.ok); const data=await result.json(); assert.ok(data.hits.some(x=>x.title==='恢复验证文章'));
    console.log('uncertain swap retained staging; empty-store process and writer restart rebuilt published Chinese content');
  } finally { global.fetch=nativeFetch; try { docker('rm','-f',name); } catch {} }
})().catch(e=>{console.error(e.message);process.exitCode=1});
