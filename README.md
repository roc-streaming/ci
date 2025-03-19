# Continuous integration scripts

This repo contains:

- reusable GitHub workflows (`.github/workflows`)
- custom GitHub actions (`actions`)
- Digital Ocean serverless functions (`packages/functions`)
- scripts for managing issues and pull requests (`scripts`)

Typical event handling call stack, e.g. for `rocd` repo:

1. GitHub generates event in `rocd` (e.g. `pull_request_review.submitted`).

2. GitHub invokes webhook, implemented by a digital ocean function in `ci` (`packages/functions/webhook`).

3. Webhook translates event to GitHub repository dispatch call in `rocd` (e.g. to `pull_request_review_submitted`).

4. Repository dispatch triggers some workflow in `rocd` (e.g. to `pr_reviewed.yml`).

5. The workflow in `rocd` may call reusable workflows from `ci` to do the actual job (e.g. `roc-streaming/ci/.github/workflows/pr_handle_reviewed.yml`).

6. The workflow from `ci` may, among other things, call custom GitHub actions from `ci` (e.g. `actions/post-comment`).

Some explanations:

- Webhook approach allows to untie automation from pull request checks, run it with different permissions, in different security context and on different branch.

- Reusable workflows allow to reduce duplication of github actions stuff across different repositories (e.g. `roc-toolkit` and `rocd`).

- Custom actions are primarily used to avoid (or reduce) sharing access tokens with third-party actions. Ideally, for all operations with non-default token, we want to use only official actions by GitHub and custom actions from `ci` repo.

- Helper scripts in `scripts` directory are used both by github actions and by maintainers locally, e.g. to merge pull requests.

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
doctl serverless functions get functions/webhook --url
```

Send request:

```
echo '{"action": "submitted", "repository": {"full_name": "roc-streaming/rocd"}, "pull_request": {"number": 123}}' | http POST <url> x-github-event:pull_request_review
```
