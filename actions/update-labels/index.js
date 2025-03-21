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
  const requestedAddLabels = (core.getInput("add-labels") || "")
        .split('/\s+/')
        .map(s => s.trim())
        .filter(s => s);
  const requestedRemoveLabels = (core.getInput("remove-labels") || "")
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

    const labelsToAdd = requestedAddLabels.filter(label => !currentLabels.includes(label));
    const labelsNotToAdd = requestedAddLabels.filter(label => currentLabels.includes(label));

    const labelsToRemove = requestedRemoveLabels.filter(label => currentLabels.includes(label));
    const labelsNotToRemove = requestedRemoveLabels.filter(label => !currentLabels.includes(label));

    if (labelsNotToAdd.length > 0) {
      core.info(`gh-${issueNumber}: not adding labels because already present: `+
                labelsNotToAdd.join(', '));
    }
    if (labelsToAdd.length > 0) {
      core.info(`gh-${issueNumber}: adding labels: ` + labelsToAdd.join(', '));
      await client.rest.issues.addLabels({
        owner: github.context.repo.owner,
        repo: github.context.repo.repo,
        issue_number: issueNumber,
        labels: labelsToAdd,
      });
    }

    if (labelsNotToRemove.length > 0) {
      core.info(`gh-${issueNumber}: not removing labels because already missing: `+
                labelsNotToRemove.join(', '));
    }
    if (labelsToRemove.length > 0) {
      core.info(`gh-${issueNumber}: removing labels: ` + labelsToRemove.join(', '));
      for (const label of labelsToRemove) {
        await client.rest.issues.removeLabel({
          owner: github.context.repo.owner,
          repo: github.context.repo.repo,
          issue_number: issueNumber,
          name: label,
        });
      }
    }

    core.info(`gh-${issueNumber}: `+
              `added ${labelsToAdd.length}/${requestedAddLabels.length} label(s), `+
              `removed ${labelsToRemove.length}/${requestedRemoveLabels.length} label(s)`);

    if (labelsToAdd.length > 0 || labelsToRemove.length > 0) {
      updatedIssues.push(issueNumber);
    }
  }

  core.info(`updated issues: ${updatedIssues}`);
  core.setOutput("updated", updatedIssues);
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
