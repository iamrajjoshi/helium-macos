// Only final distribution assets, never build checkpoints, go into a release.
const fs = require('fs');
const crypto = require('crypto');
module.exports = async ({github, context, core}) => {
  const {owner, repo} = context.repo;
  if (`${owner}/${repo}` !== 'iamrajjoshi/helium-macos') throw new Error('Wrong repository');
  const repoInfo = await github.rest.repos.get({owner, repo});
  if (repoInfo.data.private) throw new Error('Only the personal public fork is allowed');
  const directory = 'preview-dist';
  const files = fs.readdirSync(directory).sort();
  if (files.filter(f => f.endsWith('.dmg')).length !== 1 || !files.includes('SHA256SUMS')) {
    throw new Error('Missing unique DMG or hashes');
  }
  let total = 0;
  for (const file of files) {
    const stat = fs.statSync(`${directory}/${file}`);
    if (!stat.isFile() || stat.size <= 0 || stat.size >= 2 * 1024 ** 3) throw new Error('Invalid or oversized draft asset');
    total += stat.size;
  }
  if (total > 2 * 1024 ** 3) throw new Error('Combined draft assets exceed 2 GiB hard cap');
  const tag = `arc-preview-${process.env.SOURCE_SHA.slice(0,12)}-run-${context.runId}`;
  const releases = await github.paginate(github.rest.repos.listReleases, {owner, repo, per_page: 100});
  let release = releases.find(r => r.tag_name === tag);
  if (release) {
    if (!release.draft || release.target_commitish !== context.sha) throw new Error('Existing release is not this exact draft');
  } else {
    release = (await github.rest.repos.createRelease({owner, repo, tag_name: tag,
      target_commitish: context.sha, draft: true, prerelease: true,
      name: 'Helium Arc Preview ARM64 candidate (interactive QA pending)',
      body: fs.readFileSync(`${directory}/README.txt`, 'utf8')})).data;
  }
  // On an interrupted retry, retain same-name assets only if size and digest match.
  let assets = await github.paginate(github.rest.repos.listReleaseAssets, {owner, repo, release_id: release.id, per_page: 100});
  for (const name of files) {
    const data = fs.readFileSync(`${directory}/${name}`);
    const digest = 'sha256:' + crypto.createHash('sha256').update(data).digest('hex');
    const existing = assets.find(a => a.name === name);
    if (existing) {
      if (existing.size !== data.length || existing.digest !== digest) throw new Error(`Existing asset mismatch: ${name}; not overwritten`);
      continue;
    }
    const asset = (await github.rest.repos.uploadReleaseAsset({owner, repo, release_id: release.id,
      name, data, headers: {'content-type': 'application/octet-stream', 'content-length': data.length}})).data;
    if (asset.size !== data.length || asset.digest !== digest) throw new Error(`Uploaded asset not verified: ${name}`);
  }
  assets = await github.paginate(github.rest.repos.listReleaseAssets, {owner, repo, release_id: release.id, per_page: 100});
  if (!files.every(f => assets.some(a => a.name === f))) throw new Error('Final draft missing assets');
  const verified = (await github.rest.repos.getRelease({owner, repo, release_id: release.id})).data;
  if (!verified.draft) throw new Error('Release must remain unpublished');
  core.setOutput('url', verified.html_url);
  await core.summary.addHeading('Draft candidate stored, interactive QA still required')
    .addLink('Open unpublished draft (repository owner access required)', verified.html_url)
    .addRaw('\nNo release publication, notarization, or daily-use readiness is claimed.\n').write();
};
