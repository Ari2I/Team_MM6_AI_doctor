"""Probe the actual worker import path without running providers or reading requests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd() if __file__ == "<stdin>" else Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "integration.py",
    "runtime.py",
    "integration_contracts.py",
    "integration_excel.py",
    "excel_scenario.py",
    "ml2/classifier.py",
    "ml2/clinicalrecs.py",
    "ml2/official.py",
    "ml2/pipeline.py",
    "ml2/registry.py",
    "ml2/evidence_selection.py",
    "ml2/evidence_evaluation.py",
    "ml2/instructions/classification_rules.json",
)

BOOTSTRAP = r"""
import hashlib
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path
from uuid import UUID

attempts = []
def guard(event, args):
    if event in {'socket.__new__', 'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
        attempts.append(event)
        raise PermissionError('NETWORK_FORBIDDEN')
sys.addaudithook(guard)
from app.services import integration_worker as worker
from app.services.integration_protocol import IntegrationTask

class ProbeFinished(BaseException):
    pass

observed = {}
original_runner = worker.run_request
def inside_worker(payload, timeout_seconds):
    module = importlib.import_module('llm_rag.integration')
    original = module.analyze_request
    def facade_probe(request, *, verify_ssl_certs, options):
        origin = Path(original.__code__.co_filename).resolve()
        observed['integration_file'] = str(origin)
        observed['tls_verification'] = verify_ssl_certs
        observed['worker_module'] = str(Path(worker.__file__).resolve())
        observed['files'] = {
            name: hashlib.sha256((origin.parent / name).read_bytes()).hexdigest()
            for name in SOURCE_FILES
        }
        try:
            observed['gigachat_sdk'] = importlib.metadata.version('gigachat')
        except importlib.metadata.PackageNotFoundError:
            observed['gigachat_sdk'] = 'not-installed'
        raise ProbeFinished
    module.analyze_request = facade_probe
    try:
        return original_runner(payload, timeout_seconds)
    finally:
        module.analyze_request = original
worker.run_request = inside_worker
identifier = UUID(int=1)
task = IntegrationTask(
    request_id=identifier, mode='network', timeout_seconds=1.0,
    payload={
        'schema_version': 'integration-request.1', 'request_id': str(identifier),
        'diagnoses': [{'diagnosis_id': 'probe-d', 'name': 'Synthetic probe diagnosis'}],
        'purchases': [{'item_id': 'probe-i', 'name': 'Synthetic probe item'}],
        'links': [{'item_id': 'probe-i', 'diagnosis_id': 'probe-d'}],
    },
)
try:
    worker.execute(task)
except ProbeFinished:
    pass
if not observed or attempts:
    raise RuntimeError('WORKER_PROBE_FAILED')
print(json.dumps(observed, sort_keys=True))
"""


def probe() -> dict[str, object]:
    bootstrap = "SOURCE_FILES = " + repr(SOURCE_FILES) + "\n" + BOOTSTRAP
    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=True,
    )
    observed = json.loads(result.stdout)
    source = ROOT / "ai/src/llm_rag"
    expected = {
        name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in SOURCE_FILES
    }
    observed["matches_repository_source"] = observed["files"] == expected
    observed["network_attempts"] = 0
    return observed


def main() -> int:
    try:
        result = probe()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["matches_repository_source"] and result["tls_verification"] else 1
    except Exception as error:
        # Never relay child stdout/stderr, environment or SDK exception messages.
        print("AI_WORKER_PROBE_FAILED:", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
