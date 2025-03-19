# Continuous integration scripts for Roc Toolkit

This repo contains:

- reusable github workflows (`.github/workflows`)
- custom github actions (`actions`)
- digital ocean functions for github webhooks (`packages`)
- scripts for managing issues and pull requests (`scripts`)

Typical call stack, e.g. for `rocd` repo:

- github generates event in `rocd` (e.g. when review is submitted)
- github invokes webhook from `ci`
- webhook translates event to repository dispatch call in `rocd`
- repository dispatch triggers some workflow in `rocd`
- the workflow may call reusable workflows from `ci` and/or github actions from `ci`
- reusable workflows from `ci` may call github actions from `ci`

Webhook is implemented as a digital ocean serverless function. It allows to untie automation from pull request checks and run automation with different permissions.

Reusable workflows allow to reduce duplication of github actions stuff across different repositories (e.g. `roc-toolkit` and `rocd`).

Custom actions are primarily used to avoid (or reduce) sharing access tokens with third-party actions.

Helper scripts in `scripts` directory are used both by github actions and by maintainers locally, e.g. to merge pull requests.

## Build actions

Build all github actions:

```
make build_actions
```

## Deploy webhooks

Encrypt secret (for `.env` file):

```
echo -n <secret> | openssl enc -aes-256-cbc -a -salt -pbkdf2 -pass pass:<key> | tr -d '\n'
```

Deploy all webhooks:

```
make deploy_webhooks
```

## Test webhook

Determine webhook URL:

```
doctl serverless functions get dispatch/webhook --url
```

Send request:

```
echo '{"action": "submitted", "repository": {"full_name": "roc-streaming/rocd"}, "pull_request": {"number": 123}}' | http POST <url> x-github-event:pull_request_review
```
