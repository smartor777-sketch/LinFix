PYTHON ?= python3
export PYTHONPATH := src

.PHONY: test status doctor help gui deb

deb:
	sh packaging/build_deb.sh

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

status:
	$(PYTHON) -m linfix.cli status

doctor:
	$(PYTHON) -m linfix.cli doctor

gui:
	$(PYTHON) -m linfix.gtk.main

help:
	$(PYTHON) -m linfix.cli --help
