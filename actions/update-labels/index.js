// -*- mode: js; js-indent-level: 2 -*-
const core = require('@actions/core');
const github = require('@actions/github');

async function main() {
  const githubToken = core.getInput("github-token", { required: true });
  const issueNumber = parseInt(core.getInput("number", { required: true }), 10);
  const addLabels = core.getInput("add-labels");
  const removeLabels = core.getInput("remove-labels");

  const client = github.getOctokit(githubToken);

  if (addLabels && addLabels.trim()) {
    const labelsToAdd = addLabels.split('\n').filter(label => label.trim());
    if (labelsToAdd.length > 0) {
      core.info(`adding label: ${labelsToAdd.join(', ')}`);
      await client.rest.issues.addLabels({
        owner: github.context.repo.owner,
        repo: github.context.repo.repo,
        issue_number: issueNumber,
        labels: labelsToAdd,
      });
    }
  }

  if (removeLabels && removeLabels.trim()) {
    const labelsToRemove = removeLabels.split('\n').filter(label => label.trim());
    for (const label of labelsToRemove) {
      core.info(`removing label: ${label}`);
      try {
        await client.rest.issues.removeLabel({
          owner: github.context.repo.owner,
          repo: github.context.repo.repo,
          issue_number: issueNumber,
          name: label,
        });
      } catch (error) {
        core.info(`not removed: ${label}`);
      }
    }
  }
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
