PYTHON ?= python3
VENV ?= venv
ACTIVATE = . $(VENV)/bin/activate

.PHONY: setup install run check clean

setup:
	$(PYTHON) -m venv $(VENV)
	$(ACTIVATE) && pip install -r requirements.txt

install:
	$(ACTIVATE) && pip install -r requirements.txt

run:
	$(ACTIVATE) && python -m src.main

check:
	$(ACTIVATE) && python -m compileall src

clean:
	rm -rf __pycache__ src/__pycache__ .pytest_cache .mypy_cache .ruff_cache
