#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import random
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.latencies = []
        self.statuses = Counter()

    def add(self, status, latency):
        with self.lock:
            self.statuses[status] += 1
            self.latencies.append(latency)


def request_json(base, method, path, metrics, body=None, timeout=120):
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "GROUP2-production-load-test/1.0"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode())
            metrics.add(response.status, time.perf_counter() - started)
            return response.status, data
    except urllib.error.HTTPError as exc:
        metrics.add(exc.code, time.perf_counter() - started)
        try:
            detail = json.loads(exc.read().decode())
        except Exception:
            detail = {"detail": str(exc)}
        return exc.code, detail
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        metrics.add(0, time.perf_counter() - started)
        return 0, {"detail": f"{type(exc).__name__}: {exc}"}


def percentile(values, percentage):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentage)))
    return round(ordered[index] * 1000, 1)


def run_form(index, base, manifest, metrics):
    tag = f"LOADTEST-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{index:03d}"
    client_sid=str(uuid.uuid4())
    status, created = request_json(base, "POST", "/api/sessions", metrics, {"id":client_sid,"name": tag, "consent": True,"started_at":datetime.now(timezone.utc).isoformat(),"manifest_version":manifest.get("version") if manifest else None})
    if status != 200:
        return {"index": index, "ok": False, "stage": "start", "status": status, "detail": created}
    sid = created["id"]
    revision=1
    status, current = request_json(base, "GET", f"/api/sessions/{sid}/next", metrics)
    if status != 200:
        return {"index": index, "id": sid, "ok": False, "stage": "next", "status": status, "detail": current}

    ordered = manifest["questions"] if manifest else []
    by_id = {question["id"]: position for position, question in enumerate(ordered)}
    answered = 0
    guard = 0
    themes = ["A", "B", "C", "D"]
    max_questions = len(ordered) if ordered else 60
    while not current.get("done") and guard < max_questions * 3:
        guard += 1
        question = current["question"]
        if manifest is None:
            options = question.get("options") or []
            if not options:
                return {"index": index, "id": sid, "ok": False, "stage": "no-options", "question": question["id"]}
            option = options[index % len(options)]
            if question["id"] == "P01c":
                option = next((item for item in options if item["id"] == themes[index % 4]), option)
            payload = {"question_id": question["id"], "option_id": option["id"], "value": tag if question["id"] == "P00" else None, "duration_ms": random.randint(700, 6500)}
            status, result = request_json(base, "POST", f"/api/sessions/{sid}/answers", metrics, payload)
            if status != 200:
                return {"index": index, "id": sid, "ok": False, "stage": "answer-http", "status": status, "question": question["id"], "detail": result}
            answered += 1
            current = result.get("next") or {}
            continue
        position = by_id.get(question["id"])
        if position is None:
            return {"index": index, "id": sid, "ok": False, "stage": "manifest", "question": question["id"]}
        batch = []
        for candidate in ordered[position:position + 50]:
            options = candidate.get("options") or []
            if not options:
                break
            option = options[(index + len(batch)) % len(options)]
            if candidate["id"] == "P01c":
                option = next((item for item in options if item["id"] == themes[index % 4]), option)
            value = tag if candidate["id"] == "P00" else None
            batch.append({"question_id": candidate["id"], "option_id": option["id"], "value": value, "duration_ms": random.randint(700, 6500)})
        status, result = request_json(base, "POST", f"/api/sessions/{sid}/answers/batch", metrics, {"revision":revision,"idempotency_key":f"{sid}:{revision}:loadtest","answers": batch})
        if status != 200:
            return {"index": index, "id": sid, "ok": False, "stage": "batch-http", "status": status, "detail": result}
        accepted = int(result.get("accepted", 0))
        revision=int(result.get("revision",revision))+1
        answered += accepted
        current = result.get("next") or {}
        if accepted == 0 and not current.get("done"):
            authoritative = current.get("question") or {}
            options = authoritative.get("options") or []
            if not options:
                return {"index": index, "id": sid, "ok": False, "stage": "stalled", "detail": result}
            option = options[index % len(options)]
            one = {"question_id": authoritative["id"], "option_id": option["id"], "value": tag if authoritative["id"] == "P00" else None, "duration_ms": 1200}
            status, result = request_json(base, "POST", f"/api/sessions/{sid}/answers/batch", metrics, {"revision":revision,"idempotency_key":f"{sid}:{revision}:recovery","answers": [one]})
            if status != 200 or result.get("accepted") != 1:
                return {"index": index, "id": sid, "ok": False, "stage": "recovery", "status": status, "detail": result}
            answered += 1
            revision=int(result.get("revision",revision))+1
            current = result.get("next") or {}

    if not current.get("done"):
        return {"index": index, "id": sid, "ok": False, "stage": "guard", "answered": answered}
    result = current.get("result") or {}
    required = {"profile", "traits", "advice", "theme"}
    if required - set(result):
        return {"index": index, "id": sid, "ok": False, "stage": "result", "missing": sorted(required - set(result))}
    return {"index": index, "id": sid, "ok": True, "answered": answered, "theme": result.get("theme")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://botieuchi.onrender.com")
    parser.add_argument("--forms", type=int, default=200)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--start-interval", type=float, default=1.08)
    args = parser.parse_args()
    metrics = Metrics()
    status, health = request_json(args.base, "GET", "/api/health", metrics)
    if status != 200 or not health.get("ok"):
        raise SystemExit(f"Backend unhealthy: HTTP {status} {health}")

    # Manifest needs a real session, so use one separately and include it in cleanup/reporting.
    status, seed = request_json(args.base, "POST", "/api/sessions", metrics, {"name": "LOADTEST-MANIFEST", "consent": True})
    if status != 200:
        raise SystemExit(f"Cannot create manifest session: HTTP {status} {seed}")
    status, manifest = request_json(args.base, "GET", f'/api/sessions/{seed["id"]}/manifest', metrics)
    mode = "batch-manifest"
    if status == 404:
        manifest = None
        mode = "legacy-next-answer"
    elif status != 200:
        raise SystemExit(f"Cannot load manifest: HTTP {status} {manifest}")

    started = time.perf_counter()
    futures = []
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for index in range(args.forms):
            futures.append(pool.submit(run_form, index, args.base, manifest, metrics))
            time.sleep(args.start_interval)
        for completed_count,future in enumerate(concurrent.futures.as_completed(futures),1):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"index":None,"ok":False,"stage":"client-exception","detail":f"{type(exc).__name__}: {exc}"})
            if completed_count % 25 == 0 or completed_count == args.forms:
                print(f"progress={completed_count}/{args.forms}",flush=True)
    elapsed = time.perf_counter() - started
    success = [item for item in results if item["ok"]]
    failed = [item for item in results if not item["ok"]]
    report = {
        "target": args.base,
        "mode": mode,
        "forms_requested": args.forms,
        "forms_completed": len(success),
        "forms_failed": len(failed),
        "success_rate_percent": round(len(success) / args.forms * 100, 2) if args.forms else 0,
        "elapsed_seconds": round(elapsed, 2),
        "forms_per_minute": round(len(success) / elapsed * 60, 2) if elapsed else 0,
        "requests": sum(metrics.statuses.values()),
        "http_statuses": dict(metrics.statuses),
        "latency_ms": {
            "mean": round(statistics.mean(metrics.latencies) * 1000, 1) if metrics.latencies else None,
            "p50": percentile(metrics.latencies, .50),
            "p95": percentile(metrics.latencies, .95),
            "p99": percentile(metrics.latencies, .99),
            "max": round(max(metrics.latencies) * 1000, 1) if metrics.latencies else None,
        },
        "themes": dict(Counter(item.get("theme") for item in success)),
        "answer_counts": dict(Counter(item.get("answered") for item in success)),
        "failures": failed[:20],
        "manifest_session_id": seed["id"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not failed else 1)


if __name__ == "__main__":
    main()
