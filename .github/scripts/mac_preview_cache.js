// Only the workflow's short-lived GITHUB_TOKEN; no settings reads or writes.
const fs = require('fs');
const FREE_CAP = 10 * 1024 ** 3; // GitHub's included per-repository allowance.
const CHECKPOINT_CAP = 9 * 1024 ** 3;
const RESERVE = 128 * 1024 ** 2; // Cache archive envelope headroom; not a concurrency guarantee.
module.exports = async ({github, context, core}, operation) => {
  if (!['check', 'prepare-save', 'verify-save', 'cleanup'].includes(operation)) throw new Error('Unknown cache operation');
  const {owner, repo} = context.repo;
  if (`${owner}/${repo}` !== 'iamrajjoshi/helium-macos') throw new Error('Wrong repository');
  if (!Number.isSafeInteger(context.runId) || context.runId <= 0) throw new Error('Invalid workflow run ID');
  const meta = await github.rest.repos.get({owner, repo});
  if (meta.data.private !== false) throw new Error('Public repository status not verified');
  const prefix = `helium-preview-${context.runId}-`;
  const legacyKey = 'helium-preview-40019797612-1-1';
  const recovering = process.env.RECOVER_PHASE_ONE === 'true';
  // Exact same-task bootstrap only, after the Python migration verified hashes,
  // unchanged build inputs and every legacy metadata field. No broad old prefix.
  const approvedLegacy = () => {
    if (!recovering) return false;
    if (process.env.PHASE !== '1' || process.env.RESTORE_KEY !== legacyKey ||
        process.env.SOURCE_SHA !== '0b1af54b49c343484035c0977a85c4cc8642595a') throw new Error('Invalid legacy cache recovery context');
    const evidence = JSON.parse(fs.readFileSync('preview-logs/recovery-origin.json', 'utf8'));
    if (evidence.checkpoint_key !== legacyKey ||
        evidence.archive_sha256 !== '4c775ddebcde7ea0be330be1f24533dc341bd67b2de2fd7672191f1c655545f1' ||
        evidence.legacy_provenance_sha256 !== '00f840bbaa377de729dd8edefda8031acfb1b1fc1a49e621e97fb0ef46290ae0' ||
        evidence.legacy_packaging_sha !== '7935eb905b333044ade4653a144b1ac3e1b0a8f1' ||
        evidence.unchanged_build_tree_sha256 !== 'af6bed91b8c6bffcea50a4d10768b23f34e9a518ec2955f9094fc3db51979fa9' ||
        !/^[0-9a-f]{40}$/.test(evidence.new_packaging_sha) ||
        evidence.new_packaging_sha !== process.env.GITHUB_SHA) throw new Error('Legacy cache migration not verified');
    return true;
  };
  const totalSize = caches => caches.reduce((s, c) => s + c.size_in_bytes, 0);
  // Enumerate ALL repository caches, without a branch/key filter. Validate the
  // raw envelope and pagination, rather than treating missing data as no usage.
  // API failures, count changes, malformed entries and incomplete pages all fail.
  const list = async () => {
    const caches = [], ids = new Set();
    let count;
    for (let page = 1; ; page++) {
      const {data} = await github.rest.actions.getActionsCacheList({
        owner, repo, per_page: 100, page, sort: 'created_at', direction: 'asc',
      });
      if (!data || !Number.isSafeInteger(data.total_count) || data.total_count < 0 ||
          !Array.isArray(data.actions_caches)) throw new Error('Unverified cache inventory');
      if (count === undefined) count = data.total_count;
      if (data.total_count !== count || data.actions_caches.length !== Math.min(100, count - caches.length)) {
        throw new Error('Incomplete or changing cache inventory');
      }
      for (const cache of data.actions_caches) {
        if (!cache || !Number.isSafeInteger(cache.id) || cache.id <= 0 || ids.has(cache.id) ||
            typeof cache.key !== 'string' || !cache.key ||
            !Number.isSafeInteger(cache.size_in_bytes) || cache.size_in_bytes < 0) {
          throw new Error('Malformed cache inventory entry');
        }
        ids.add(cache.id); caches.push(cache);
      }
      if (!Number.isSafeInteger(totalSize(caches))) throw new Error('Invalid total cache size');
      if (caches.length === count) return caches;
    }
  };
  let caches = await list();
  if (totalSize(caches) > FREE_CAP) throw new Error('Existing cache usage exceeds hard 10 GiB free cap');
  if (operation === 'prepare-save') {
    const size = fs.statSync('preview-checkpoint/build.tar.zst').size;
    if (!Number.isSafeInteger(size) || size <= 0 || size > CHECKPOINT_CAP) throw new Error('Checkpoint exceeds hard 9 GiB cap');
    const legacy = approvedLegacy() ? caches.filter(c => c.key === legacyKey) : [];
    if (legacy.length > 1) throw new Error('Ambiguous legacy cache inventory');
    const unrelated = caches.filter(c => !c.key.startsWith(prefix) && !legacy.includes(c));
    if (totalSize(unrelated) + size + RESERVE > FREE_CAP) {
      throw new Error('Free cache allowance insufficient; unrelated caches will not be deleted');
    }
    // The new local archive has already been completed and hashed. Only now
    // remove this run's consumed checkpoint(s). Upload failure can lose resume.
    const retainLegacy = totalSize(unrelated) + totalSize(legacy) + size + RESERVE <= FREE_CAP;
    const remove = caches.filter(c => c.key.startsWith(prefix)).concat(retainLegacy ? [] : legacy);
    if (!retainLegacy && legacy.length) {
      core.warning('Consuming only the pinned legacy checkpoint before upload to stay below 10 GiB. A failed successor upload will lose this resume point.');
    }
    for (const cache of remove) {
      await github.rest.actions.deleteActionsCacheById({owner, repo, cache_id: cache.id});
    }
    const expectedRemaining = unrelated.concat(retainLegacy ? legacy : []);
    caches = await list();
    const inventory = entries => JSON.stringify(entries.map(c => [c.id, c.key, c.size_in_bytes]).sort((a, b) => a[0] - b[0]));
    if (inventory(caches) !== inventory(expectedRemaining) || totalSize(caches) + size + RESERVE > FREE_CAP) {
      throw new Error('Cache deletion not verified, or concurrent usage appeared; upload blocked');
    }
    // This is a checked budget, not an atomic server-side storage reservation.
    core.info(`Saving ${size} bytes; existing ${totalSize(caches)}; fixed cap ${FREE_CAP}; reserve ${RESERVE}. No settings access.`);
  } else if (operation === 'verify-save') {
    const key = process.env.CHECKPOINT_KEY;
    if (!key || !key.startsWith(prefix)) throw new Error('Invalid checkpoint key');
    if (!caches.some(c => c.key === key)) throw new Error('Cache save did not produce the expected exact key');
    if (approvedLegacy()) {
      const legacy = caches.filter(c => c.key === legacyKey);
      if (legacy.length > 1) throw new Error('Ambiguous legacy cache inventory');
      // Prefer this order: successor first saved and verified, then consume old.
      for (const cache of legacy) {
        await github.rest.actions.deleteActionsCacheById({owner, repo, cache_id: cache.id});
      }
      if (legacy.length) {
        const remaining = await list();
        if (remaining.some(c => c.key === legacyKey) || !remaining.some(c => c.key === key) || totalSize(remaining) > FREE_CAP) {
          throw new Error('Legacy replacement cleanup or successor presence not verified');
        }
      }
    }
  } else if (operation === 'cleanup') {
    for (const cache of caches.filter(c => c.key.startsWith(prefix))) {
      await github.rest.actions.deleteActionsCacheById({owner, repo, cache_id: cache.id});
    }
  }
};
