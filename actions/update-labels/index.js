// -*- mode: js; js-indent-level: 2 -*-
const core = require('@actions/core');
const github = require('@actions/github');

async function main() {
  const githubToken = core.getInput("github-token", { required: true });
  const issueNumberList = core.getInput("number", { required: true })
        .split(/\s+/)
        .map(n => n.trim())
        .map(n => parseInt(n, 10))
        .filter(n => n > 0);
  const addLabels = (core.getInput("add-labels") || "")
        .split('/\s+/')
        .map(s => s.trim())
        .filter(s => s);
  const removeLabels = (core.getInput("remove-labels") || "")
        .split('/\s+/')
        .map(s => s.trim())
        .filter(s => s);

  const client = github.getOctokit(githubToken);

  let updatedIssues = [];

  for (const issueNumber of issueNumberList) {
    const currentLabelsResult = await client.rest.issues.listLabelsOnIssue({
      owner: github.context.repo.owner,
      repo: github.context.repo.repo,
      issue_number: issueNumber,
    });

    const currentLabels = currentLabelsResult.data.map(label => label.name);
    const labelsToAdd = addLabels.filter(label => !currentLabels.includes(label));
    const labelsToRemove = removeLabels.filter(label => currentLabels.includes(label));

    if (labelsToAdd.length > 0) {
      core.info(`gh-${issueNumber}: adding labels: ${labelsToAdd.join(', ')}`);
      await client.rest.issues.addLabels({
        owner: github.context.repo.owner,
        repo: github.context.repo.repo,
        issue_number: issueNumber,
        labels: labelsToAdd,
      });
    }

    if (labelsToRemove.length > 0) {
      for (const label of labelsToRemove) {
        core.info(`gh-${issueNumber}: removing label: ${label}`);
        await client.rest.issues.removeLabel({
          owner: github.context.repo.owner,
          repo: github.context.repo.repo,
          issue_number: issueNumber,
          name: label,
        });
      }
    }

    if (labelsToAdd.length > 0 || labelsToRemove.length > 0) {
      updatedIssues.push(issueNumber);
    }
  }

  core.info(`updatedIssues: ${updatedIssues}`);
  core.setOutput("updated", updatedIssues);
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
