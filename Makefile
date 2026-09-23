.PHONY: bullseye test

test:
	python3 -m unittest discover -s tests -q

bullseye: test
	uv run --with 'playwright==1.60.0' python -m unittest discover -s tests -p keyboard_journey.py -q
	node --check serviceboard/static/app.js
	prettier --check serviceboard/static/index.html serviceboard/static/style.css serviceboard/static/app.js
