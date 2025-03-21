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
  const projectNumber = parseInt(core.getInput("project", { required: true }));
  const status = (core.getInput("status") || "").trim();

  const client = github.getOctokit(token);

  const findProjectQuery = `
    query($owner: String!, $number: Int!) {
      organization(login: $owner) {
        projectV2(number: $number) {
          id
        }
      }
    }
  `;

  const findProjectResult = await client.graphql(findProjectQuery, {
    owner,
    number: projectNumber
  });

  const projectId = findProjectResult.organization.projectV2.id;

  core.info(`found project id: ${projectId}`);

  let projectStatusField = null;
  let projectStatusOption = null;

  if (status) {
    const findProjectFieldsQuery = `
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

    const findProjectFieldsResult = await client.graphql(findProjectFieldsQuery, {
      project: projectId,
    });

    projectStatusField = findProjectFieldsResult.node.fields.nodes
          .find(field => field.name === "Status");
    if (!projectStatusField) {
      throw new Error("can't find 'Status' field in project");
    }

    core.info(`found project status field id: ${projectStatusField.id}`);

    projectStatusOption = projectStatusField.options
      .find(option => option.name === status);
    if (!projectStatusOption) {
      throw new Error(`can't find 'Status' option '${status}' in project`);
    }

    core.info(`found project status option id: ${projectStatusOption.id}`);
  }

  let updatedIssues = [];

  for (const issueNumber of issueNumberList) {
    const findIssueResult = await client.rest.issues.get({
      owner: github.context.repo.owner,
      repo: github.context.repo.repo,
      issue_number: issueNumber,
    });

    const contentId = findIssueResult.data.node_id;

    core.info(`gh-${issueNumber}: found issue content id: ${contentId}`);

    const checkItemQuery = `
      query($project: ID!, $content: ID!) {
        node(id: $project) {
          ... on ProjectV2 {
            items(first: 1, filter: {contentId: $content}) {
              nodes {
                id
                fieldValues(first: 1, filter: {field: {id: $field}}) {
                  nodes {
                    ... on ProjectV2ItemFieldSingleSelectValue {
                      field {
                        ... on ProjectV2SingleSelectField {
                          id
                        }
                      }
                      optionId
                    }
                  }
                }
              }
            }
          }
        }
      }
    `;

    const checkItemResult = await client.graphql(checkItemQuery, {
      project: projectId,
      content: contentId,
      field: projectStatusField.id,
    });

    let itemId = null;
    let optionId = null;

    if (checkItemResult.node.items.nodes.length > 0) {
      itemId = checkItemResult.node.items.nodes[0].id;
      optionId = checkItemResult.node.items.nodes[0].optionId;
    }

    if (itemId) {
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

      const addItemResult = await client.graphql(addItemMutation, {
        project: projectId,
        content: contentId,
      });

      itemId = addItemResult.addProjectV2ItemById.item.id;

      core.info(`gh-${issueNumber}: added project item: item id ${itemId}`);

      if (!updatedIssues.includes(issueNumber)) {
        updatedIssues.push(issueNumber);
      }
    } else {
      core.info(`gh-${issueNumber}: project item already exists: item id ${itemId}`);
    }

    if (status) {
      if (optionId != projectStatusOption.id) {
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
          field: projectStatusField.id,
          value: projectStatusOption.id
        });

        core.info(`gh-${issueNumber}: updated project item status to '${status}'`);

        if (!updatedIssues.includes(issueNumber)) {
          updatedIssues.push(issueNumber);
        }
      } else {
        core.info(`gh-${issueNumber}: project item status is already '${status}'`);
      }
    }
  }

  core.info(`updatedIssues: ${updatedIssues}`);
  core.setOutput("updated", updatedIssues);
}

main().catch((error) => {
  core.error(String(error));
  core.setFailed(String(error.message));
});
