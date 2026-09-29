"""Small acceptance load test for the hackathon prototype.

Runs >=20 concurrent logical users through authenticated read-heavy workflows and
prints latency/throughput/error evidence.  It intentionally never targets
customer systems; point --base-url at the local/staging T.G.O.A.T. deployment.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import ssl


def request_json(url: str, *, method: str = "GET", body=None, token: str | None = None, timeout=15, insecure=False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    started = time.perf_counter()
    try:
        context = ssl._create_unverified_context() if insecure and url.lower().startswith("https://") else None
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=timeout, context=context) as response:
            payload = response.read()
            code = response.status
    except HTTPError as error:
        payload = error.read()
        code = error.code
    elapsed = time.perf_counter() - started
    return code, payload, elapsed


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))
    return ordered[index]


def run_user(base: str, username: str, password: str, iterations: int, insecure: bool):
    latencies, errors, status_counts = [], [], Counter()
    code, payload, elapsed = request_json(base + "/api/v1/auth/login", method="POST",
                                          body={"username": username, "password": password}, insecure=insecure)
    latencies.append(elapsed)
    status_counts[code] += 1
    if code != 200:
        return latencies, [("login", code)], status_counts
    token = json.loads(payload)["access_token"]
    paths = ["/api/v1/operational/dashboard", "/api/v1/objects?limit=20",
             "/api/v1/signals?limit=20", "/api/v1/maintenance-requests?limit=20",
             "/api/v1/predictions?limit=20", "/api/v1/integrations/status"]
    for i in range(iterations):
        path = paths[i % len(paths)]
        code, _, elapsed = request_json(base + path, token=token, insecure=insecure)
        latencies.append(elapsed)
        status_counts[code] += 1
        if code != 200:
            errors.append((path, code))
    code, _, elapsed = request_json(base + "/api/v1/auth/logout", method="POST", token=token, insecure=insecure)
    latencies.append(elapsed)
    status_counts[code] += 1
    if code not in {200, 204}:
        errors.append(("logout", code))
    return latencies, errors, status_counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://127.0.0.1:8443")
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--requests-per-user", type=int, default=12)
    parser.add_argument("--username", default="central")
    parser.add_argument("--password", default="central-demo")
    parser.add_argument("--insecure", action="store_true", help="accept self-signed TLS only for local acceptance")
    args = parser.parse_args()
    if args.users < 20:
        parser.error("TЗ acceptance requires --users >= 20")
    started = time.perf_counter()
    all_latencies, all_errors, all_status_counts = [], [], Counter()
    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [pool.submit(run_user, args.base_url.rstrip('/'), args.username, args.password,
                               args.requests_per_user, args.insecure) for _ in range(args.users)]
        for future in as_completed(futures):
            latencies, errors, status_counts = future.result()
            all_latencies.extend(latencies)
            all_errors.extend(errors)
            all_status_counts.update(status_counts)
    elapsed = time.perf_counter() - started
    evidence = {
        "concurrent_users": args.users,
        "request_samples": len(all_latencies),
        "elapsed_seconds": round(elapsed, 3),
        "throughput_requests_per_second": round(len(all_latencies) / elapsed, 3) if elapsed else None,
        "p50_seconds": round(percentile(all_latencies, .50), 4),
        "p95_seconds": round(percentile(all_latencies, .95), 4),
        "max_seconds": round(max(all_latencies), 4) if all_latencies else None,
        "error_count": len(all_errors),
        "errors": all_errors[:100],
        "http_status_distribution": {
            str(code): count for code, count in sorted(all_status_counts.items())
        },
    }
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    raise SystemExit(1 if all_errors else 0)


if __name__ == "__main__":
    main()
