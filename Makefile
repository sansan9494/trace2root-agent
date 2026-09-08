.PHONY: setup setup-embedding setup-models setup-full rebuild-index import-samples start start-all test smoke evaluate evaluate-public

setup:
	./scripts/setup.sh

setup-embedding:
	./scripts/setup.sh --with-embedding

setup-models:
	./scripts/setup.sh --with-models

setup-full:
	./scripts/setup.sh --full

rebuild-index:
	.venv/bin/python scripts/rebuild_vector_index.py

import-samples:
	.venv/bin/python scripts/import_samples.py

start:
	./scripts/start.sh

start-all:
	./scripts/start_all.sh

test:
	./scripts/test.sh

smoke:
	./scripts/smoke_test.sh

evaluate:
	.venv/bin/python scripts/evaluate_rag.py --top-k 3
	.venv/bin/python scripts/evaluate_agent.py
	.venv/bin/python scripts/evaluate_ragas.py --max-cases 3

evaluate-public:
	.venv/bin/python scripts/evaluate_rag.py --cases evaluation/public_rag_cases.json --output evaluation/results/latest_public_rag.json --top-k 3 --repeats 3
	.venv/bin/python scripts/evaluate_public_rca.py --max-cases 32 --repeats 3
	.venv/bin/python scripts/evaluate_robustness.py --repeats 3
