# Roc continuous integration scripts

This repo contains:

- GitHub workflows (`.github/workflows`)
- custom GitHub actions (`actions`)
- Digital Ocean serverless functions (`packages/functions`)
- scripts for managing issues and pull requests (`scripts`)
- ruleset templates (`rulesets`)

Typical event handling call stack, e.g. for `rocd` repo:

1. GitHub generates event in `rocd` repo (e.g. `pull_request_review.submitted`).

2. GitHub invokes webhook, implemented by a Digital Ocean function in `ci` repo (`packages/functions/redispatch`).

3. Webhook translates event to GitHub repository dispatch call in `ci` repo (e.g. to `pull_request_review_submitted`).

4. Repository dispatch triggers one or a few workflows in `ci` repo (e.g. to `auto_status.yml`).

5. The workflows in `ci` repo typically call custom GitHub actions, also from `ci` repo (e.g. `actions/update-labels`).

6. Some of the GitHub actions also use scripts from `scripts` directory in `ci` repo.

Some explanations:

- Webhook approach allows to untie automation workflows from pull requests. Such workflows typically require a token with extended privileges. Redispatch reduces the risk of exposing that token to third-party actions and malicious pull requests.

- Custom actions are primarily used to avoid sharing access tokens with third-party actions. Ideally, in all workflows with non-default token, we want to use only official actions by GitHub and custom actions from `ci` repo.

- Helper scripts in `scripts` directory are used both by GitHub actions and by maintainers locally. E.g. `rgh.py` is used on CI to gather pull request info, and by maintainers to merge pull requests.

Automation workflows (`.github/workflows/auto_xxx.yml`) perform various routine tasks, like setting labels, detecting conflicts, posting welcome messages, etc. This is configured on per-repo basis in `automation.yml` in the project root.

Digital Ocean functions are listed and configured in `project.yml` in the project root. `project.yml` file and `packages` directory are used by `doctl` command.

## Dev dependencies

- Node.js 1.20+, npm.js
- Go 1.20+
- Python 3.9+
- [ncc](https://www.npmjs.com/package/@vercel/ncc)
- [doctl](https://docs.digitalocean.com/reference/doctl/how-to/install/)

## Build actions

Build all github actions:

```
make build_actions
```

## Build functions

Build all digital ocean functions:

```
make build_functions
```

## Deploy functions

Encrypt a secret (for `.env` file):

```
echo -n <secret> | openssl enc -aes-256-cbc -a -salt -pbkdf2 -pass pass:<key> | tr -d '\n'
```

Deploy all digital ocean functions:

```
make deploy_functions
```

## Test functions

Determine function URL:

```
doctl sls fn get functions/redispatch --url
```

Send request:

```
echo '{"action": "submitted",
       "repository": {"full_name": "roc-streaming/rocd"},
       "pull_request": {"number": 123}}' \
    | http POST <url> x-github-event:pull_request_review
```

Determine function URL:

```
doctl sls fn get functions/keepalive --url
```

Send request:

```
echo '{"action": "completed",
       "repository": {"full_name": "roc-streaming/rocd"}}' \
    | http POST <url> x-github-event:workflow_run
```

## Test stubs

Emulate request:

```
printf '{"http": {
         "headers": {"x-github-event": "workflow_run"},
         "queryString": "<query>",
         "isBase64Encoded": true,
         "body": "%s"}}' \
       "$(echo '{"action": "submitted",
                 "repository": {"full_name": "roc-streaming/rocd"},
                 "pull_request": {"number": 123}}' \
           | base64 | tr -d '\n')" \
    | ./packages/functions/keepalive/stub \
    | jq -C .
```

Emulate request:

```
printf '{"http": {
         "headers": {"x-github-event": "workflow_run"},
         "queryString": "<query>",
         "isBase64Encoded": true,
         "body": "%s"}}' \
       "$(echo '{"action": "completed",
                 "repository": {"full_name": "roc-streaming/rocd"}}' \
           | base64 | tr -d '\n')" \
    | ./packages/functions/keepalive/stub \
    | jq -C .
```
