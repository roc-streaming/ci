# Continuous integration scripts for Roc Toolkit

This repo contains:

- reusable github workflows
- digital ocean function for github webhook (for automation via repository dispatch)
- scripts for managing issues and pull requests

## Encrypt secret

```
echo -n <secret> | openssl enc -aes-256-cbc -a -salt -pbkdf2 -pass pass:<key> | tr -d '\n'
```

## Deploy webhook

```
doctl serverless deploy .
```

## Test webhook

Determine URL:

```
doctl serverless functions get dispatch/webhook --url
```

Send request:

```
echo '{"action": "submitted", "repository": {"full_name": "roc-streaming/rocd"}, "pull_request": {"number": 123}}' | http POST <url> x-github-event:pull_request_review
```
