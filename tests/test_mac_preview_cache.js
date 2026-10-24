// Local API mocks only: no network calls, token use, or remote cache mutation.
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const cache = require('../.github/scripts/mac_preview_cache.js');
const GiB = 1024 ** 3, reserve = 128 * 1024 ** 2;
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'helium-cache-test-'));
const original = process.cwd();
process.chdir(directory);
fs.mkdirSync('preview-checkpoint');
const checkpoint = 'preview-checkpoint/build.tar.zst';
fs.writeFileSync(checkpoint, 'not-an-actual-build');
const own = {id:1,key:'helium-preview-123-1-1',size_in_bytes:9*GiB};
const unrelated = {id:2,key:'other-work',size_in_bytes:1000};
function mock(initial=[], options={}) {
  let caches = structuredClone(initial);
  const deleted = [], pages = [], warnings = [];
  const github = {
    request: async () => {throw new Error('Settings endpoint must never be requested');},
    rest: {repos: {get: async () => ({data: {private: options.private ?? false}})}, actions: {
      getActionsCacheList: async args => {
        assert.strictEqual(args.per_page,100);
        assert.strictEqual(args.key,undefined); assert.strictEqual(args.ref,undefined);
        pages.push(args.page);
        if (options.refuse) throw new Error('403 Resource not accessible by integration');
        if (options.response) return options.response(args,caches,deleted);
        return {data:{total_count:caches.length,actions_caches:structuredClone(caches.slice((args.page-1)*100,args.page*100))}};
      },
      deleteActionsCacheById: async ({cache_id}) => {
        deleted.push(cache_id);
        if (!options.keepDeleted) caches=caches.filter(c=>c.id!==cache_id);
        if (options.afterDelete) caches=options.afterDelete(caches);
      },
    }},
  };
  return {args: {github, context: {repo: {owner:'iamrajjoshi',repo:'helium-macos'}, runId:123}, core:{info:()=>{},warning:message=>warnings.push(message)}}, deleted,pages,warnings};
}
let passed=0;
async function test(name, body) {await body(); passed++; console.log(`PASS ${name}`);}
async function denied(m,op='check') {await assert.rejects(cache(m.args,op)); assert.deepStrictEqual(m.deleted,[]);}
(async () => {
  await test('valid empty inventory, no settings request',async()=>{await cache(mock().args,'check');});
  await test('only exact-run predecessor deleted',async()=>{
    const otherRun={...own,id:3,key:'helium-preview-1234-1-1',size_in_bytes:1};
    const m=mock([own,unrelated,otherRun]); await cache(m.args,'prepare-save');
    assert.deepStrictEqual(m.deleted,[1]);
  });
  await test('all inventory pages counted',async()=>{
    const m=mock(Array.from({length:101},(_,i)=>({...unrelated,id:i+1})));
    await cache(m.args,'check'); assert.deepStrictEqual(m.pages,[1,2]);
  });
  await test('existing total above fixed cap denied',()=>denied(mock([{...unrelated,size_in_bytes:10*GiB+1}])));
  await test('exact fixed cap accepted for read-only check',async()=>{await cache(mock([{...unrelated,size_in_bytes:10*GiB}]).args,'check');});
  await test('insufficient aggregate budget leaves all caches',()=>denied(mock([{...own,size_in_bytes:1000},{...unrelated,size_in_bytes:10*GiB-1000}]),'prepare-save'));
  await test('unknown repository privacy denied',async()=>{const m=mock();m.args.github.rest.repos.get=async()=>({data:{}});await denied(m);});
  await test('private repository denied',()=>denied(mock([],{private:true})));
  for(const runId of [undefined,0,-1,NaN,Infinity,'123','',Number.MAX_SAFE_INTEGER+1]) {
    await test(`invalid run ID ${String(runId)} denied`,async()=>{const m=mock([own]);m.args.context.runId=runId;await denied(m,'cleanup');});
  }
  await test('refused inventory fails closed',()=>denied(mock([],{refuse:true})));
  for(const data of [undefined,{}, {total_count:0}, {total_count:NaN,actions_caches:[]},
      {total_count:-1,actions_caches:[]},{total_count:1,actions_caches:[]},
      {total_count:0,actions_caches:[unrelated]}, {total_count:2,actions_caches:[unrelated,unrelated]}]) {
    await test('malformed or truncated inventory denied',()=>denied(mock([],{response:()=>({data})})));
  }
  for(const size of [undefined,-1,NaN,Infinity,'1000',0.5,Number.MAX_SAFE_INTEGER+1]) {
    await test(`invalid cache size ${String(size)} denied`,()=>denied(mock([{...unrelated,size_in_bytes:size}])));
  }
  await test('missing key denied',()=>denied(mock([{id:2,size_in_bytes:0}])));
  await test('missing ID denied',()=>denied(mock([{key:'other',size_in_bytes:0}])));
  await test('changing pagination count denied',()=>{
    const rows=Array.from({length:100},(_,i)=>({...unrelated,id:i+1}));
    return denied(mock([],{response:args=>({data:{total_count:args.page===1?101:102,actions_caches:args.page===1?rows:[{...unrelated,id:101}]}})}));
  });
  await test('unconfirmed deletion blocks upload',async()=>{
    const m=mock([own,unrelated],{keepDeleted:true}); await assert.rejects(cache(m.args,'prepare-save'));assert.deepStrictEqual(m.deleted,[1]);
  });
  await test('concurrent unrelated change blocks upload even below cap',async()=>{
    const m=mock([own,unrelated],{afterDelete:rows=>rows.map(c=>({...c,size_in_bytes:c.size_in_bytes+1}))});
    await assert.rejects(cache(m.args,'prepare-save'));assert.deepStrictEqual(m.deleted,[1]);
  });
  await test('checkpoint size zero rejected',async()=>{fs.truncateSync(checkpoint,0);await denied(mock(),'prepare-save');});
  await test('checkpoint >9 GiB rejected without allocation',async()=>{fs.truncateSync(checkpoint,9*GiB+1);await denied(mock(),'prepare-save');});
  await test('9 GiB plus unrelated plus reserve exactly at cap accepted',async()=>{
    fs.truncateSync(checkpoint,9*GiB);await cache(mock([{...unrelated,size_in_bytes:GiB-reserve}]).args,'prepare-save');
  });
  await test('one byte over envelope budget denied',()=>denied(mock([{...unrelated,size_in_bytes:GiB-reserve+1}]),'prepare-save'));
  fs.truncateSync(checkpoint,19);
  await test('expected exact saved key required',async()=>{process.env.CHECKPOINT_KEY='helium-preview-123-2-1';await denied(mock([own]),'verify-save');});
  await test('different run key denied',async()=>{process.env.CHECKPOINT_KEY='helium-preview-1234-1-1';await denied(mock([own]),'verify-save');});
  await test('post-save aggregate overshoot detected',async()=>{
    process.env.CHECKPOINT_KEY=own.key;await denied(mock([own,{...unrelated,size_in_bytes:GiB+1}]),'verify-save');
  });
  await test('verify saved exact key and cleanup own run only',async()=>{
    const m=mock([own,unrelated]);process.env.CHECKPOINT_KEY=own.key;
    await cache(m.args,'verify-save');await cache(m.args,'cleanup');assert.deepStrictEqual(m.deleted,[1]);
  });

  const bootstrapKey='helium-preview-40051201468-1-1';
  const bootstrap={id:4,key:bootstrapKey,size_in_bytes:3*GiB};
  const similar={id:5,key:bootstrapKey+'0',size_in_bytes:1000};
  const evidence={checkpoint_key:bootstrapKey,
    archive_sha256:'b2169963676226e1580379471ffee31b0ad531d26f4a4eb72e615e0e7c38608c',
    saved_provenance_sha256:'0b85d0d5f225daaae3744c750eebbc426ee278a76c8f519f20b308c4975e5c99',
    saved_packaging_sha:'1f1cc956e4c094f43dad5f3b1fd6e211ca3cb004',
    prior_recovery_origin_sha256:'4458ead84e320cef30263461afcd18c3be72fba0835e33fa39f303dbdd842d24',
    unchanged_build_tree_sha256:'af6bed91b8c6bffcea50a4d10768b23f34e9a518ec2955f9094fc3db51979fa9',
    new_packaging_sha:'b'.repeat(40)};
  fs.mkdirSync('preview-logs');
  const evidencePath='preview-logs/recovery-origin.json';
  const writeEvidence=(value=evidence)=>fs.writeFileSync(evidencePath,JSON.stringify(value));
  writeEvidence();
  Object.assign(process.env,{RECOVER_PHASE_ONE:'true',PHASE:'1',RESTORE_KEY:bootstrapKey,
    SOURCE_SHA:'0b1af54b49c343484035c0977a85c4cc8642595a',GITHUB_SHA:'b'.repeat(40)});
  await test('bootstrap retains old until successor is verified when room allows',async()=>{
    const m=mock([bootstrap,unrelated,similar]);await cache(m.args,'prepare-save');assert.deepStrictEqual(m.deleted,[]);
    const saved=mock([bootstrap,unrelated,similar,{...own,size_in_bytes:GiB}]);process.env.CHECKPOINT_KEY=own.key;
    await cache(saved.args,'verify-save');assert.deepStrictEqual(saved.deleted,[4]);
  });
  await test('bootstrap consumes only exact old key when both would exceed cap',async()=>{
    fs.truncateSync(checkpoint,4*GiB);
    const m=mock([{...bootstrap,size_in_bytes:4*GiB},{...unrelated,size_in_bytes:3*GiB},similar]);
    await cache(m.args,'prepare-save');assert.deepStrictEqual(m.deleted,[4]);assert.strictEqual(m.warnings.length,1);
    fs.truncateSync(checkpoint,19);
  });
  await test('insufficient budget even after old removal never deletes anything',async()=>{
    fs.truncateSync(checkpoint,8*GiB);
    await denied(mock([bootstrap,{...unrelated,size_in_bytes:3*GiB}]),'prepare-save');fs.truncateSync(checkpoint,19);
  });
  await test('bootstrap missing successor does not delete old',async()=>{
    process.env.CHECKPOINT_KEY=own.key;await denied(mock([bootstrap,unrelated]),'verify-save');
  });
  await test('bootstrap post-save overshoot does not delete old',async()=>{
    await denied(mock([bootstrap,{...own,size_in_bytes:8*GiB}]),'verify-save');
  });
  await test('bootstrap requires bridge evidence before any deletion',async()=>{
    fs.unlinkSync(evidencePath);await denied(mock([bootstrap]),'prepare-save');writeEvidence();
  });
  for(const field of Object.keys(evidence)) {
    await test(`bootstrap rejects mismatched evidence ${field}`,async()=>{
      writeEvidence({...evidence,[field]:'wrong'});await denied(mock([bootstrap]),'prepare-save');writeEvidence();
    });
  }
  for(const [field,value] of [['PHASE','2'],['RESTORE_KEY',bootstrapKey+'0'],['SOURCE_SHA','a'.repeat(40)],['GITHUB_SHA','c'.repeat(40)]]) {
    await test(`bootstrap rejects mismatched context ${field}`,async()=>{
      const old=process.env[field];process.env[field]=value;await denied(mock([bootstrap]),'prepare-save');process.env[field]=old;
    });
  }
  await test('bootstrap disabled leaves bootstrap untouched',async()=>{
    process.env.RECOVER_PHASE_ONE='false';const m=mock([bootstrap]);await cache(m.args,'prepare-save');assert.deepStrictEqual(m.deleted,[]);process.env.RECOVER_PHASE_ONE='true';
  });
  await test('former schema1 recovery key is unrelated and never deleted',async()=>{
    const former={id:7,key:'helium-preview-40019797612-1-1',size_in_bytes:GiB};
    const m=mock([bootstrap,former,{...own,size_in_bytes:GiB}]);process.env.CHECKPOINT_KEY=own.key;
    await cache(m.args,'verify-save');assert.deepStrictEqual(m.deleted,[4]);
  });
  await test('ambiguous exact old key fails closed',async()=>{
    await denied(mock([bootstrap,{...bootstrap,id:6}]),'prepare-save');
  });
  await test('old cache deletion must be verified after successful successor',async()=>{
    const m=mock([bootstrap,{...own,size_in_bytes:GiB}],{keepDeleted:true});
    await assert.rejects(cache(m.args,'verify-save'));assert.deepStrictEqual(m.deleted,[4]);
  });
  await test('ordinary cleanup never expands to bootstrap prefix',async()=>{
    const m=mock([bootstrap,similar,{...own,size_in_bytes:GiB}]);await cache(m.args,'cleanup');assert.deepStrictEqual(m.deleted,[1]);
  });
  console.log(`${passed} cache guard scenarios passed.`);
})().finally(()=>{process.chdir(original);fs.rmSync(directory,{recursive:true});});
