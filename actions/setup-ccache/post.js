// -*- mode: js; js-indent-level: 2 -*-
const cache = require('@actions/cache');
const core = require('@actions/core');
const crypto = require('crypto');
const exec = require('@actions/exec');
const path = require('path');

async function getCachePath() {
  const getOutput = await exec.getExecOutput(`ccache --get-config cache_dir`, undefined,
                                             { ignoreReturnCode: true, silent: true });
  if (getOutput.exitCode === 0) {
    const cachePath = getOutput.stdout.trim();
    if (cachePath) {
      return path.normalize(cachePath)
    }
  }
}

function getCacheKey() {
  let os = process.env.ImageOS.toLowerCase();
  let keys = [
    `ccache-${os}`,
  ];

  if (process.env.GITHUB_HEAD_REF) {
    keys.push(process.env.GITHUB_HEAD_REF);
  }

  keys.push(crypto.randomBytes(16).toString('hex'));

  return keys.join('-');
}

async function main() {
  await exec.exec('ccache --show-stats');

  const cachePath = await getCachePath();
  if (!cachePath) {
    core.warning("can't determine ccache directory");
    return;
  }

  const key = getCacheKey();
  core.info(`saving cache with key: "${key}"`);

  try {
    await cache.saveCache([cachePath], key);
    core.info("cache saved");
  } catch (error) {
    core.warning(`failed to save cache: ${error.message}`);
  }
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
