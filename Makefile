all: build_actions

build_actions:
	cd actions/detect-conflicts && ncc -qs build index.js
	cd actions/post-comment && ncc -qs build index.js
	cd actions/update-labels && ncc -qs build index.js

deploy_webhooks:
	doctl serverless deploy .
