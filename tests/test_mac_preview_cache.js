// Local API mocks only: no network calls, token use, or remote cache mutation.
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const cache = require('../.github/scripts/mac_preview_cache.js');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'helium-cache-test-'));
const original = process.cwd();
process.chdir(directory);
fs.mkdirSync('preview-checkpoint');
fs.writeFileSync('preview-checkpoint/build.tar.zst', 'not-an-actual-build');
function mock(initial, limit=10, isPrivate=false) {
  let caches = structuredClone(initial);
  const deleted = [];
  const github = {
    request: async () => ({data: {max_cache_size_gb: limit}}),
    paginate: async () => structuredClone(caches),
    rest: {repos: {get: async () => ({data: {private: isPrivate}})}, actions: {
      getActionsCacheList: {},
      deleteActionsCacheById: async ({cache_id}) => {deleted.push(cache_id); caches=caches.filter(c=>c.id!==cache_id);},
    }},
  };
  return {args: {github, context: {repo: {owner:'iamrajjoshi',repo:'helium-macos'}, runId:123}, core:{info:()=>{}}}, deleted};
}
(async () => {
  const own = {id:1,key:'helium-preview-123-1-1',size_in_bytes:9*1024**3};
  const unrelated = {id:2,key:'other-work',size_in_bytes:1000};
  let m=mock([own,unrelated]);
  await cache(m.args,'prepare-save');
  assert.deepStrictEqual(m.deleted,[1]);
  for (const [initial,limit,priv] of [[[],11,false],[[],10,true],[[],undefined,false],
      [[{...unrelated,size_in_bytes:10*1024**3}],10,false]]) {
    m=mock(initial,limit,priv);
    if (limit===undefined) m.args.github.request=async()=>({data:{}});
    await assert.rejects(cache(m.args,'prepare-save'));
    assert.deepStrictEqual(m.deleted,[]);
  }
  m=mock([unrelated]); process.env.CHECKPOINT_KEY='helium-preview-123-2-1';
  await assert.rejects(cache(m.args,'verify-save'));
  m=mock([own,unrelated]); process.env.CHECKPOINT_KEY=own.key;
  await cache(m.args,'verify-save');
  await cache(m.args,'cleanup'); assert.deepStrictEqual(m.deleted,[1]);
  console.log('7 cache guard scenarios passed: quota, privacy, absent settings, unrelated retention, save verification, exact-run cleanup.');
})().finally(()=>{process.chdir(original);fs.rmSync(directory,{recursive:true});});
