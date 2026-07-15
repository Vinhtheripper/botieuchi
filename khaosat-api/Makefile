.PHONY: test run install

PYTHON := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; elif [ -x ../venv/bin/python ]; then echo ../venv/bin/python; elif [ -x ../../venv/bin/python ]; then echo ../../venv/bin/python; else command -v python3; fi)

test:
	$(PYTHON) -m unittest discover -s tests -v
run:
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000
install:
	$(PYTHON) -m pip install -r requirements.txt
