# Continuous integration scripts

This repo contains:

- reusable GitHub workflows (`.github/workflows`)
- custom GitHub actions (`actions`)
- Digital Ocean serverless functions (`packages/functions`)
- scripts for managing issues and pull requests (`scripts`)

Typical event handling call stack, e.g. for `rocd` repo:

1. GitHub generates event in `rocd` (e.g. `pull_request_review.submitted`).

2. GitHub invokes webhook, implemented by a digital ocean function in `ci` (`packages/functions/redispatch`).

3. Webhook translates event to GitHub repository dispatch call in `rocd` (e.g. to `pull_request_review_submitted`).

4. Repository dispatch triggers some workflow in `rocd` (e.g. to `pr_reviewed.yml`).

5. The workflow in `rocd` may call reusable workflows from `ci` to do the actual job (e.g. `roc-streaming/ci/.github/workflows/pr_handle_reviewed.yml`).

6. The workflow from `ci` may, among other things, call custom GitHub actions from `ci` (e.g. `actions/post-comment`).

Some explanations:

- Webhook approach allows to untie automation from pull request checks, run it with different permissions, in different security context and on different branch.

- Reusable workflows allow to reduce duplication of github actions stuff across different repositories (e.g. `roc-toolkit` and `rocd`).

- Custom actions are primarily used to avoid (or reduce) sharing access tokens with third-party actions. Ideally, for all operations with non-default token, we want to use only official actions by GitHub and custom actions from `ci` repo.

- Helper scripts in `scripts` directory are used both by github actions and by maintainers locally, e.g. to merge pull requests.

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
