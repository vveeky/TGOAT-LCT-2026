"""Deployment-level mixed read load from independent authenticated clients."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    offset = (len(ordered) - 1) * fraction
    lower = math.floor(offset)
    upper = math.ceil(offset)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (offset - lower)


def request(base: str, path: str, *, token: str | None = None,
            method: str = "GET", payload: dict | None = None, timeout: float = 20):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    query = urllib.request.Request(base.rstrip("/") + path, data=body,
                                   headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(query, timeout=timeout) as response:
            raw = response.read()
            code = response.status
    except urllib.error.HTTPError as error:
        raw, code = error.read(), error.code
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        data = json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        data = raw.decode("utf-8", errors="replace")
    return code, data, elapsed_ms


def system_identity() -> dict:
    result = {"os": platform.platform(), "architecture": platform.machine(),
              "logical_cpu_count": os.cpu_count(), "python": platform.python_version()}
    if platform.system() == "Darwin":
        for name, key in (("hw.memsize", "physical_memory_bytes"),
                          ("machdep.cpu.brand_string", "cpu_model")):
            try:
                value = subprocess.check_output(["sysctl", "-n", name], text=True).strip()
                result[key] = int(value) if key.endswith("_bytes") else value
            except (OSError, subprocess.CalledProcessError, ValueError):
                pass
    return result


def process_sample(pid: int) -> tuple[float | None, float | None]:
    try:
        output = subprocess.check_output(["ps", "-o", "%cpu=,rss=", "-p", str(pid)], text=True).strip()
        cpu, rss_kb = output.split()
        return float(cpu), float(rss_kb) / 1024
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None, None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002/api/v1")
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--duration-seconds", type=int, default=180)
    parser.add_argument("--server-pid", type=int, required=True)
    args = parser.parse_args()

    code, login, _ = request(args.base_url, "/auth/login", method="POST",
                             payload={"username": "central", "password": "central-demo"})
    if code != 200:
        raise SystemExit(f"load-smoke login failed: HTTP {code}: {login}")
    token = login["access_token"]
    code, objects, _ = request(args.base_url, "/objects?limit=1000", token=token)
    if code != 200:
        raise SystemExit(f"load-smoke object list failed: HTTP {code}")
    object_ids = [int(item["id"]) for item in objects.get("items", objects if isinstance(objects, list) else [])]
    if not object_ids:
        raise SystemExit("load-smoke received no operational objects")
    code, journal, _ = request(args.base_url, "/operational/predictions?limit=500", token=token)
    candidates = [item for item in journal.get("items", [])
                  if item.get("prediction_status") == "available" and item.get("risk_probability") is not None]
    review_identity = candidates[0]["identity"] if candidates else None

    latencies: list[float] = []
    statuses: dict[str, int] = {}
    error_samples: list[str] = []
    lock_errors = 0
    requests_lock = threading.Lock()
    stop_at = time.monotonic() + args.duration_seconds
    start_at = time.monotonic()
    cpu_samples: list[float] = []
    memory_samples: list[float] = []
    sample_stop = threading.Event()

    def sample_process() -> None:
        while not sample_stop.wait(1.0):
            cpu, memory = process_sample(args.server_pid)
            if cpu is not None:
                cpu_samples.append(cpu)
            if memory is not None:
                memory_samples.append(memory)

    sampler = threading.Thread(target=sample_process, daemon=True)
    sampler.start()

    def record(path: str, *, method="GET", payload=None, action_count: int):
        nonlocal lock_errors
        status, data, elapsed = request(args.base_url, path, token=token,
                                        method=method, payload=payload)
        with requests_lock:
            latencies.append(elapsed)
            statuses[str(status)] = statuses.get(str(status), 0) + 1
            if status >= 400:
                message = json.dumps(data, ensure_ascii=False)[:400]
                error_samples.append(f"HTTP {status} {path}: {message}")
                if "locked" in message.lower() or "busy" in message.lower():
                    lock_errors += 1

    def user_session(index: int) -> None:
        rng = random.Random(8900 + index)
        action_count = 0
        while time.monotonic() < stop_at:
            object_id = object_ids[(index + rng.randrange(len(object_ids))) % len(object_ids)]
            operation = rng.choices(
                ["dashboard", "objects", "detail", "journal", "notifications"],
                weights=[30, 15, 25, 20, 10], k=1,
            )[0]
            action_count += 1
            if operation == "dashboard":
                record("/operational/dashboard", action_count=action_count)
            elif operation == "objects":
                record("/objects?limit=1000", action_count=action_count)
            elif operation == "detail":
                record(f"/operational/objects/{object_id}", action_count=action_count)
                record(f"/objects/{object_id}/risk/history?limit=10", action_count=action_count)
                record(f"/objects/{object_id}/risk/cards", action_count=action_count)
                record(f"/objects/{object_id}/risk/recommendations", action_count=action_count)
            elif operation == "journal":
                record("/operational/predictions?limit=20", action_count=action_count)
            else:
                record("/notifications?limit=100", action_count=action_count)
            # A small dispatcher-write share supplements the predominantly read workload.
            if review_identity and action_count % 250 == index:
                record(f"/operational/predictions/{urllib.parse.quote(review_identity, safe='')}/review",
                       method="POST", payload={"decision": "monitoring", "reason_code": "load_smoke",
                           "comment": None, "idempotency_key": f"load-{index}-{action_count}"},
                       action_count=action_count)

    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [pool.submit(user_session, index) for index in range(args.users)]
        for future in futures:
            future.result()
    sample_stop.set()
    sampler.join(timeout=2)
    duration = time.monotonic() - start_at

    # A post-run complete journal scan detects duplicate/corrupt persisted identities.
    unique_identities: set[str] = set()
    total = 0
    offset = 0
    while True:
        code, page, _ = request(args.base_url,
            f"/operational/predictions?limit=500&offset={offset}", token=token)
        if code != 200:
            error_samples.append(f"post-load journal verification: HTTP {code}")
            break
        total = page["total"]
        unique_identities.update(item["identity"] for item in page["items"])
        offset += len(page["items"])
        if offset >= total or not page["items"]:
            break
    _, replay_status, _ = request(args.base_url, "/replay/status", token=token)
    result = {
        "environment": system_identity(),
        "server": {"uvicorn_workers": 1, "runtime_mode": "replay", "sqlite": "one replay-session DB"},
        "concurrent_sessions": args.users,
        "duration_seconds": round(duration, 2),
        "request_count": len(latencies),
        "throughput_requests_per_second": round(len(latencies) / duration, 2) if duration else 0,
        "latency_ms": {"p50": percentile(latencies, .50), "p95": percentile(latencies, .95),
                       "p99": percentile(latencies, .99), "max": max(latencies) if latencies else None},
        "http_status_counts": statuses,
        "error_rate": round(sum(count for key, count in statuses.items() if int(key) >= 400) /
                             max(1, len(latencies)), 6),
        "sqlite_locked_or_busy_errors": lock_errors,
        "error_samples": error_samples[:10],
        "server_process_cpu_percent_ps_average_peak": max(cpu_samples, default=None),
        "server_process_rss_mib_peak": max(memory_samples, default=None),
        "journal_integrity": {"total_rows": total, "unique_identities": len(unique_identities),
                              "duplicates": total - len(unique_identities)},
        "replay_state_at_end": replay_status.get("state") if isinstance(replay_status, dict) else None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
