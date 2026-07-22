.PHONY: test run install export-manifest verify

PYTHON := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; elif [ -x ../venv/bin/python ]; then echo ../venv/bin/python; elif [ -x ../../venv/bin/python ]; then echo ../../venv/bin/python; else command -v python3; fi)

test:
	$(PYTHON) -m unittest discover -s tests -v
run:
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000
install:
	$(PYTHON) -m pip install -r requirements.txt
export-manifest:
	$(PYTHON) scripts/export_public_manifest.py
verify: export-manifest test
	cd ../khaosat-web && npm run build
