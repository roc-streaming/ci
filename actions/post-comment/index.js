// -*- mode: js; js-indent-level: 2 -*-
const core = require('@actions/core');
const github = require('@actions/github');

async function main() {
  const githubToken = core.getInput("github-token", { required: true });
  const [owner, repo] = core.getInput("repo", { required: true }).split("/");
  const issueNumberList = core.getInput("number", { required: true })
        .split(/\s+/)
        .map(n => n.trim())
        .map(n => parseInt(n, 10))
        .filter(n => n > 0);
  const text = core.getInput("text", { required: true });

  const client = github.getOctokit(githubToken);

  for (const issueNumber of issueNumberList) {
    core.info(`gh-${issueNumber}: posting comment`);

    await client.rest.issues.createComment({
      owner: owner,
      repo: repo,
      issue_number: issueNumber,
      body: text,
    });
  }
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
