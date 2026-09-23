.PHONY: bullseye test

test:
	python3 -m unittest discover -s tests -q

bullseye: test
	node --check serviceboard/static/app.js
	prettier --check serviceboard/static/index.html serviceboard/static/style.css serviceboard/static/app.js
