// -*- mode: js; js-indent-level: 2 -*-
const core = require('@actions/core');
const github = require('@actions/github');

async function main() {
  const githubToken = core.getInput("github-token", { required: true });
  const [owner, repo] = core.getInput("repo", { required: true }).split("/");

  const client = github.getOctokit(githubToken);

  const retryAfter = 10;
  const retryMax = 10;

  let statuses = {};
  let cursor = null;
  let morePages = true;

  while (morePages) {
    const query = `
        query openPullRequests($owner: String!, $repo: String!, $after: String) {
          repository(owner:$owner, name: $repo) {
            pullRequests(first: 100, after: $after, states: OPEN) {
              nodes {
                number
                mergeable
              }
              pageInfo {
                endCursor
                hasNextPage
              }
            }
          }
        }`;

    const result = await client.graphql(query, {
      owner: owner,
      repo: repo,
      after: cursor,
    });

    const pullRequests = result.repository.pullRequests.nodes;
    const pageInfo = result.repository.pullRequests.pageInfo;

    if (pullRequests.length == 0) {
      break;
    }

    for (const pullRequest of pullRequests) {
      let prNumber = pullRequest.number;
      let prStatus = pullRequest.mergeable;

      core.info(`gh-${prNumber} state: ${prStatus}`);

      let retryCount = 0;

      while (prStatus == "UNKNOWN" && retryCount < retryMax) {
        core.info(`gh-${prNumber}: retrying after ${retryAfter} seconds`);

        await new Promise(resolve => setTimeout(resolve, retryAfter * 1000));

        const query = `
            query pullRequestStatus($owner: String!, $repo: String!, $number: Int!) {
              repository(owner:$owner, name: $repo) {
                pullRequest(number: $number) {
                  number
                  mergeable
                }
              }
            }`;

        const result = await client.graphql(query, {
          owner: owner,
          repo: repo,
          number: parseInt(prNumber, 10),
        });

        prStatus = result.repository.pullRequest.mergeable;
        retryCount++;

        core.info(`gh-${prNumber} state: ${prStatus}`);
      }

      statuses[prNumber] = prStatus;
      continue;
    }

    morePages = pageInfo.hasNextPage;
    cursor = pageInfo.endCursor;
  }

  const withConflicts = Object.keys(statuses)
        .filter(prNumber => statuses[prNumber] === "CONFLICTING");

  const withoutConflicts = Object.keys(statuses)
        .filter(prNumber => statuses[prNumber] === "MERGEABLE");

  core.info(`pull requests with conflicts: `+
            `${withConflicts.length ? withConflicts : "none"}`);
  core.info(`pull requests without conflicts: `+
            `${withoutConflicts.length ? withoutConflicts : "none"}`);

  core.setOutput("with-conflicts", withConflicts);
  core.setOutput("without-conflicts", withoutConflicts);
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
