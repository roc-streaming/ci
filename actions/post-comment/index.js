// -*- mode: js; js-indent-level: 2 -*-
const core = require('@actions/core');
const github = require('@actions/github');

async function main() {
  const githubToken = core.getInput("github-token", { required: true });
  const text = core.getInput("text", { required: true });
  const issueNumber = parseInt(core.getInput("number", { required: true }), 10);

  const client = github.getOctokit(githubToken);

  await client.rest.issues.createComment({
    owner: github.context.repo.owner,
    repo: github.context.repo.repo,
    issue_number: issueNumber,
    body: text,
  });
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
