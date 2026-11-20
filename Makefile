PYTHON ?= python3
export PYTHONPATH := src

.PHONY: test status doctor help

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

status:
	$(PYTHON) -m netfix.cli status

doctor:
	$(PYTHON) -m netfix.cli doctor

help:
	$(PYTHON) -m netfix.cli --help
