// -*- mode: js; js-indent-level: 2 -*-
const core = require('@actions/core');
const github = require('@actions/github');

async function main() {
  const githubToken = core.getInput("github-token", { required: true });
  const text = core.getInput("text", { required: true });
  const issueNumberList = core.getInput("number", { required: true })
        .split(/\s+/)
        .map(n => n.trim())
        .map(n => parseInt(n, 10))
        .filter(n => n > 0);

  const client = github.getOctokit(githubToken);

  for (const issueNumber of issueNumberList) {
    await client.rest.issues.createComment({
      owner: github.context.repo.owner,
      repo: github.context.repo.repo,
      issue_number: issueNumber,
      body: text,
    });
  }
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
