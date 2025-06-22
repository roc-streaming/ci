// -*- mode: js; js-indent-level: 2 -*-
const core = require('@actions/core');
const exec = require('@actions/exec');
const path = require('path');
const cache = require('@actions/cache');

async function getCachePath() {
  const getOutput = await exec.getExecOutput(`ccache --get-config cache_dir`, undefined,
                                             { ignoreReturnCode: true, silent: true });
  if (getOutput.exitCode === 0) {
    const cachePath = getOutput.stdout.trim();
    if (cachePath) {
      return path.normalize(cachePath);
    }
  }
}

function getCacheKeys() {
  let os = process.env.ImageOS.toLowerCase();
  let keys = [
    `ccache-${os}`,
  ];

  if (process.env.GITHUB_HEAD_REF) {
    keys.push(process.env.GITHUB_HEAD_REF);
  }

  return {
    primaryKey: keys.join('-'),
    restoreKeys: keys.map((_, i) => keys.slice(0, i + 1).join('_'))
  };
}

async function main() {
  const cachePath = await getCachePath();
  if (!cachePath) {
    core.warning("can't determine ccache directory");
    return;
  }

  const { primaryKey, restoreKeys } = getCacheKeys();
  core.info(`restoring cache with key: "${primaryKey}"`);

  const cacheKey = await cache.restoreCache([cachePath], primaryKey, restoreKeys);
  if (cacheKey) {
    core.info(`cache found at: "${cacheKey}"`);
  } else {
    core.info("cache not found");
  }
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
