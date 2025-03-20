all: build_actions build_functions

build_actions:
	cd actions/detect-conflicts && ncc -qs build index.js
	cd actions/post-comment && ncc -qs build index.js
	cd actions/update-labels && ncc -qs build index.js
	cd actions/update-project && ncc -qs build index.js

build_functions:
	cd packages/functions/redispatch && go build -o /dev/null -tags=build_main .

deploy_functions:
	doctl sls deploy .
