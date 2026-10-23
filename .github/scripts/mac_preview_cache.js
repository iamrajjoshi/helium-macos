// Invoked only with the workflow's own short-lived GITHUB_TOKEN. No quota writes.
const fs = require('fs');
module.exports = async ({github, context, core}, operation) => {
  const {owner, repo} = context.repo;
  if (`${owner}/${repo}` !== 'iamrajjoshi/helium-macos') throw new Error('Wrong repository');
  const meta = await github.rest.repos.get({owner, repo});
  if (meta.data.private) throw new Error('Standard runners are free only for this public fork');
  const limit = await github.request('GET /repos/{owner}/{repo}/actions/cache/storage-limit', {owner, repo});
  const configured = limit.data.max_cache_size_gb;
  if (!Number.isFinite(configured) || configured <= 0 || configured > 10) {
    throw new Error(`Unverified or expanded cache quota (${configured}); no settings will be changed`);
  }
  const list = () => github.paginate(github.rest.actions.getActionsCacheList, {owner, repo, per_page: 100});
  const prefix = `helium-preview-${context.runId}-`;
  let caches = await list();
  if (operation === 'prepare-save') {
    const path = 'preview-checkpoint/build.tar.zst';
    const size = fs.statSync(path).size;
    const allowance = configured * 1024 ** 3;
    const reserve = 128 * 1024 ** 2; // metadata and cache envelope headroom
    if (size <= 0 || size > 9 * 1024 ** 3) throw new Error('Checkpoint exceeds hard 9 GiB cap');
    const unrelated = caches.filter(c => !c.key.startsWith(prefix));
    const unrelatedSize = unrelated.reduce((s, c) => s + c.size_in_bytes, 0);
    if (unrelatedSize + size + reserve > allowance) {
      throw new Error('Free cache allowance insufficient; unrelated caches will not be deleted');
    }
    // The new local archive has already been completed and hashed. Only now
    // remove the consumed checkpoint(s) from this run to avoid double billing.
    // If the subsequent upload fails, this run may need a fresh build.
    for (const cache of caches.filter(c => c.key.startsWith(prefix))) {
      await github.rest.actions.deleteActionsCacheById({owner, repo, cache_id: cache.id});
    }
    caches = await list();
    const remaining = caches.reduce((s, c) => s + c.size_in_bytes, 0);
    if (remaining + size + reserve > allowance) throw new Error('Cache deletion not verified, or concurrent usage appeared; upload blocked');
    core.info(`Saving ${size} bytes; existing ${remaining}; cap ${allowance}. No quota expansion.`);
  } else if (operation === 'verify-save') {
    const key = process.env.CHECKPOINT_KEY;
    if (!key || !key.startsWith(prefix)) throw new Error('Invalid checkpoint key');
    if (!caches.some(c => c.key === key)) throw new Error('Cache save did not produce the expected exact key');
    const total = caches.reduce((s, c) => s + c.size_in_bytes, 0);
    if (total > configured * 1024 ** 3) throw new Error('Cache usage exceeds free cap');
  } else if (operation === 'cleanup') {
    for (const cache of caches.filter(c => c.key.startsWith(prefix))) {
      await github.rest.actions.deleteActionsCacheById({owner, repo, cache_id: cache.id});
    }
  } else if (operation !== 'check') {
    throw new Error('Unknown cache operation');
  }
};
