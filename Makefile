all: build_actions build_functions

build_actions:
	cd actions/detect-conflicts && ncc -qs build index.js
	cd actions/post-comment && ncc -qs build index.js
	cd actions/setup-ccache && ncc -qs build main.js -o dist/main
	cd actions/setup-ccache && ncc -qs build post.js -o dist/post
	cd actions/update-labels && ncc -qs build index.js
	cd actions/update-project && ncc -qs build index.js

build_functions:
	cd packages/functions/keepalive && go build -o stub -tags=stub .
	cd packages/functions/redispatch && go build -o stub -tags=stub .

deploy_functions: build_functions
	doctl sls deploy .
