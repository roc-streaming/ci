// -*- mode: js; js-indent-level: 2 -*-
const core = require('@actions/core');
const github = require('@actions/github');

async function main() {
  const githubToken = core.getInput("github-token", { required: true });
  const issueNumber = parseInt(core.getInput("number", { required: true }), 10);
  const projectNumber = parseInt(core.getInput("project", { required: true }));
  const status = (core.getInput("status") || "").trim();

  const client = github.getOctokit(token);

  const findIssueResponse = await client.rest.issues.get({
    owner: github.context.repo.owner,
    repo: github.context.repo.repo,
    issue_number: issueNumber,
  });

  const contentId = findIssueResponse.data.node_id;

  core.info(`detected content id: ${contentId}`);

  const findProjectQuery = `
      query($owner: String!, $number: Int!) {
        organization(login: $owner) {
          projectV2(number: $number) {
            id
          }
        }
      }
    `;

  const findProjectResponse = await client.graphql(findProjectQuery, {
    owner,
    number: projectNumber
  });

  const projectId = findProjectResponse.organization.projectV2.id;

  core.info(`detected project id: ${projectId}`);

  const addItemMutation = `
      mutation($project: ID!, $content: ID!) {
        addProjectV2ItemById(input: {
          projectId: $project,
          contentId: $content
        }) {
          item {
            id
          }
        }
      }
    `;

  const addItemResponse = await client.graphql(addItemMutation, {
    project: projectId,
    content: contentId
  });

  const itemId = addItemResponse.addProjectV2ItemById.item.id;

  core.info(`successfully added project item: item id ${itemId}`);

  if (status) {
    const findFieldsQuery = `
        query($project: ID!) {
          node(id: $project) {
            ... on ProjectV2 {
              fields(first: 20) {
                nodes {
                  ... on ProjectV2Field {
                    id
                    name
                  }
                  ... on ProjectV2SingleSelectField {
                    id
                    name
                    options {
                      id
                      name
                    }
                  }
                }
              }
            }
          }
        }
      `;

    const findFieldsResponse = await client.graphql(findFieldsQuery, {
      project: projectId
    });

    const statusField = findFieldsResponse.node.fields.nodes.find(field => field.name === "Status");
    if (!statusField) {
      throw new Error("can't find 'Status' field in project");
    }

    core.info(`detected status field id: ${statusField.id}`);

    const statusOption = statusField.options.find(option => option.name === status);
    if (!statusOption) {
      throw new Error(`can't find 'Status' option '${status}' in project`);
    }

    core.info(`detected status option id: ${statusOption.id}`);

    const updateStatusMutation = `
        mutation($project: ID!, $item: ID!, $field: ID!, $value: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $project,
            itemId: $item,
            fieldId: $field,
            value: {
              singleSelectOptionId: $value
            }
          }) {
            projectV2Item {
              id
            }
          }
        }
      `;

    await client.graphql(updateStatusMutation, {
      project: projectId,
      item: itemId,
      field: statusField.id,
      value: statusOption.id
    });

    core.info(`successfully updated item status`);
  }
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
