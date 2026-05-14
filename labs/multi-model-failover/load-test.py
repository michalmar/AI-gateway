#!/usr/bin/env python3
"""
AI Gateway Load Test Script
Generates significant concurrent load across all APIM products and OpenAI models
to test load balancing, department access, and monitoring scenarios.

Usage:
    python3 load-test.py                      # auto-discover from Azure deployment
    python3 load-test.py --runs 200           # custom run count
    python3 load-test.py --concurrency 10     # custom concurrency
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests as req

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEPLOYMENT_NAME = "multi-model-failover"
RESOURCE_GROUP = f"lab-{DEPLOYMENT_NAME}"
INFERENCE_API_PATH = "inference"
API_VERSION = "2025-03-01-preview"

MODELS = ["gpt-4.1-nano", "gpt-4.1"]

# Per-product TPM limits as configured in the deployment (products-policy.xml)
PRODUCT_TPM_LIMITS = {
    "hr": 500,
    "marketing": 1000,
    "finance": 2000,
}

PROMPTS = [
    "What is the capital of France?",
    "Explain quantum computing in one sentence.",
    "What is 42 * 17?",
    "Name three primary colors.",
    "What year did the first moon landing occur?",
    "Define machine learning briefly.",
    "What is the speed of light?",
    "Name the largest ocean on Earth.",
    "What is photosynthesis?",
    "Translate 'hello' to Spanish.",
]


def get_deployment_outputs():
    """Fetch deployment outputs from Azure, or fall back to APIM REST API."""
    print("🔍 Discovering deployment outputs from Azure...")

    # Try deployment outputs first
    result = subprocess.run(
        ["az", "deployment", "group", "show",
         "--name", DEPLOYMENT_NAME, "-g", RESOURCE_GROUP, "-o", "json"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        try:
            deployment = json.loads(result.stdout)
            outputs = deployment.get("properties", {}).get("outputs", {})
            if outputs and "apimResourceGatewayURL" in outputs:
                gateway_url = outputs["apimResourceGatewayURL"]["value"]
                subscriptions = outputs["apimSubscriptions"]["value"]
                app_insights = outputs["appInsightsName"]["value"]
                print(f"✅ Gateway: {gateway_url}")
                print(f"✅ App Insights: {app_insights}")
                print(f"✅ Subscriptions: {', '.join(s['name'] for s in subscriptions)}")
                return gateway_url, subscriptions, app_insights
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: discover APIM service name from resource group
    print("  ⚠️  Deployment outputs not available, discovering via APIM REST API...")
    apim_name = None

    result = subprocess.run(
        ["az", "resource", "list", "-g", RESOURCE_GROUP,
         "--resource-type", "Microsoft.ApiManagement/service", "--query", "[0].name", "-o", "tsv"],
        capture_output=True, text=True
    )
    apim_name = result.stdout.strip()

    if not apim_name:
        # Deployment might be in progress; try to find APIM via the subscription
        sub_id = subprocess.run(
            ["az", "account", "show", "--query", "id", "-o", "tsv"],
            capture_output=True, text=True
        ).stdout.strip()
        result = subprocess.run(
            ["az", "rest", "--method", "get",
             "--url", f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.ApiManagement/service?api-version=2024-05-01"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            try:
                services = json.loads(result.stdout).get("value", [])
                if services:
                    apim_name = services[0]["name"]
            except (json.JSONDecodeError, KeyError):
                pass

    if not apim_name:
        print("❌ Could not discover APIM service. Ensure the deployment has completed.")
        sys.exit(1)

    gateway_url = f"https://{apim_name}.azure-api.net"
    sub_id = subprocess.run(
        ["az", "account", "show", "--query", "id", "-o", "tsv"],
        capture_output=True, text=True
    ).stdout.strip()
    apim_id = f"/subscriptions/{sub_id}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.ApiManagement/service/{apim_name}"

    # Get subscription keys
    result = subprocess.run(
        ["az", "rest", "--method", "get",
         "--url", f"https://management.azure.com{apim_id}/subscriptions?api-version=2024-05-01"],
        capture_output=True, text=True
    )
    subs_data = json.loads(result.stdout)
    subscriptions = []
    for sub in subs_data.get("value", []):
        name = sub["name"]
        if name == "master":
            continue
        result2 = subprocess.run(
            ["az", "rest", "--method", "post",
             "--url", f"https://management.azure.com{sub['id']}/listSecrets?api-version=2024-05-01"],
            capture_output=True, text=True
        )
        secrets = json.loads(result2.stdout)
        subscriptions.append({
            "name": name,
            "displayName": sub["properties"]["displayName"],
            "key": secrets["primaryKey"],
        })

    # Get App Insights name
    result = subprocess.run(
        ["az", "resource", "list", "-g", RESOURCE_GROUP,
         "--resource-type", "Microsoft.Insights/components", "--query", "[0].name", "-o", "tsv"],
        capture_output=True, text=True
    )
    app_insights = result.stdout.strip()

    print(f"✅ Gateway: {gateway_url}")
    print(f"✅ App Insights: {app_insights}")
    print(f"✅ Subscriptions: {', '.join(s['name'] for s in subscriptions)}")
    return gateway_url, subscriptions, app_insights


def send_request(run_id, gateway_url, subscription, model, prompt):
    """Send a single chat completion request and return metrics."""
    start = time.time()
    url = f"{gateway_url}/{INFERENCE_API_PATH}/openai/deployments/{model}/chat/completions?api-version={API_VERSION}"
    headers = {
        "api-key": subscription["key"],
        "Content-Type": "application/json",
        "x-user-id": subscription["name"].replace("subscription-", ""),
    }
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 50,
    }
    try:
        resp = req.post(url, json=body, headers=headers, timeout=30)
        elapsed = time.time() - start
        # Capture backend/region info from response headers
        resp_headers = dict(resp.headers)
        region = resp_headers.get("x-ms-region", "")
        remaining_tokens = resp_headers.get("x-ratelimit-remaining-tokens", "")
        remaining_requests = resp_headers.get("x-ratelimit-remaining-requests", "")

        if resp.status_code == 200:
            data = resp.json()
            usage = data.get("usage", {})
            return {
                "run_id": run_id,
                "subscription": subscription["name"],
                "product": subscription["name"].replace("subscription-", ""),
                "model": data.get("model", model),
                "status": "success",
                "latency_ms": round(elapsed * 1000),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "response_preview": data.get("choices", [{}])[0].get("message", {}).get("content", "")[:60],
                "region": region,
                "remaining_tokens": remaining_tokens,
                "remaining_requests": remaining_requests,
            }
        elif resp.status_code == 429:
            return {
                "run_id": run_id,
                "subscription": subscription["name"],
                "product": subscription["name"].replace("subscription-", ""),
                "model": model,
                "status": "rate_limited",
                "latency_ms": round(elapsed * 1000),
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "response_preview": resp.text[:100],
                "region": region,
                "remaining_tokens": remaining_tokens,
                "remaining_requests": remaining_requests,
            }
        else:
            return {
                "run_id": run_id,
                "subscription": subscription["name"],
                "product": subscription["name"].replace("subscription-", ""),
                "model": model,
                "status": "error",
                "latency_ms": round(elapsed * 1000),
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "response_preview": f"HTTP {resp.status_code}: {resp.text[:80]}",
                "region": region,
                "remaining_tokens": remaining_tokens,
                "remaining_requests": remaining_requests,
            }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "run_id": run_id,
            "subscription": subscription["name"],
            "product": subscription["name"].replace("subscription-", ""),
            "model": model,
            "status": "error",
            "latency_ms": round(elapsed * 1000),
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "response_preview": str(e)[:100],
            "region": "", "remaining_tokens": "", "remaining_requests": "",
        }


def print_progress(results, total):
    """Print real-time progress."""
    success = sum(1 for r in results if r["status"] == "success")
    rate_limited = sum(1 for r in results if r["status"] == "rate_limited")
    errors = sum(1 for r in results if r["status"] == "error")
    pct = len(results) / total * 100
    print(f"\r  ⏳ Progress: {len(results)}/{total} ({pct:.0f}%) | ✅ {success} | ⚡ {rate_limited} rate-limited | ❌ {errors} errors", end="", flush=True)


def run_load_test(gateway_url, subscriptions, total_runs, concurrency, distribution):
    """Execute the load test with concurrent requests."""
    print(f"\n🚀 Starting load test: {total_runs} requests, concurrency={concurrency}")
    print(f"   Distribution: {json.dumps(distribution)}")
    print(f"   Models: {MODELS}")
    print(f"   Start time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

    # Build request queue based on distribution
    requests_queue = []
    sub_map = {s["name"].replace("subscription-", ""): s for s in subscriptions}

    for product, pct in distribution.items():
        count = int(total_runs * pct / 100)
        sub = sub_map[product]
        for i in range(count):
            model = random.choice(MODELS)
            prompt = random.choice(PROMPTS)
            requests_queue.append((sub, model, prompt))

    # Pad to exact total if rounding left us short
    while len(requests_queue) < total_runs:
        product = random.choice(list(distribution.keys()))
        sub = sub_map[product]
        requests_queue.append((sub, random.choice(MODELS), random.choice(PROMPTS)))

    random.shuffle(requests_queue)

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {}
        for i, (sub, model, prompt) in enumerate(requests_queue):
            future = executor.submit(send_request, i + 1, gateway_url, sub, model, prompt)
            futures[future] = i

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print_progress(results, total_runs)

    print()  # newline after progress
    return results


def print_summary(results):
    """Print a summary table of the load test results."""
    print("\n" + "=" * 80)
    print("📊 LOAD TEST SUMMARY")
    print("=" * 80)

    # Overall stats
    total = len(results)
    success = [r for r in results if r["status"] == "success"]
    rate_limited = [r for r in results if r["status"] == "rate_limited"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"\n  Total requests:  {total}")
    print(f"  Successful:      {len(success)} ({len(success)/total*100:.1f}%)")
    print(f"  Rate-limited:    {len(rate_limited)} ({len(rate_limited)/total*100:.1f}%)")
    print(f"  Errors:          {len(errors)} ({len(errors)/total*100:.1f}%)")

    if success:
        latencies = [r["latency_ms"] for r in success]
        print(f"\n  Avg latency:     {sum(latencies)/len(latencies):.0f} ms")
        print(f"  Min latency:     {min(latencies)} ms")
        print(f"  Max latency:     {max(latencies)} ms")
        total_tokens = sum(r["total_tokens"] for r in success)
        print(f"  Total tokens:    {total_tokens:,}")

    # Per-product breakdown
    print("\n  📦 Per-Product Breakdown:")
    print(f"  {'Product':<12} {'Requests':>10} {'Success':>10} {'RateLimited':>12} {'Tokens':>10}")
    print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*12} {'-'*10}")

    by_product = defaultdict(list)
    for r in results:
        by_product[r["product"]].append(r)

    for product in sorted(by_product):
        pr = by_product[product]
        ps = [r for r in pr if r["status"] == "success"]
        prl = [r for r in pr if r["status"] == "rate_limited"]
        tokens = sum(r["total_tokens"] for r in ps)
        print(f"  {product:<12} {len(pr):>10} {len(ps):>10} {len(prl):>12} {tokens:>10,}")

    # Per-model breakdown
    print("\n  🤖 Per-Model Breakdown:")
    print(f"  {'Model':<20} {'Requests':>10} {'Success':>10} {'RateLimited':>12} {'Tokens':>10}")
    print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*12} {'-'*10}")

    by_model = defaultdict(list)
    for r in results:
        by_model[r["model"]].append(r)

    for model in sorted(by_model):
        mr = by_model[model]
        ms = [r for r in mr if r["status"] == "success"]
        mrl = [r for r in mr if r["status"] == "rate_limited"]
        tokens = sum(r["total_tokens"] for r in ms)
        print(f"  {model:<20} {len(mr):>10} {len(ms):>10} {len(mrl):>12} {tokens:>10,}")

    # Rate-limit info (from headers)
    if rate_limited:
        print(f"\n  ⚡ Rate Limiting Details:")
        print(f"  {len(rate_limited)} requests received HTTP 429 — APIM retry policy")
        print(f"  attempted failover to secondary backend(s).")
        print(f"  Check scenario-report.md for backend distribution from logs.")

    print("\n" + "=" * 80)
    return results


def save_results(results, filename="load-test-results.json", extra_meta=None):
    """Save detailed results to JSON for the verification script."""
    filepath = os.path.join(os.path.dirname(__file__), filename)
    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "total_requests": len(results),
        "results": results,
    }
    if extra_meta:
        output.update(extra_meta)
    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n💾 Results saved to {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Quota Test Mode
# ---------------------------------------------------------------------------

def run_quota_test(gateway_url, subscriptions, concurrency):
    """Test per-product TPM limits by sending enough traffic to trigger 429s.

    For each product, sends requests until the TPM budget is exhausted (~2x the
    limit) so that 429 responses are clearly visible. Products are tested one
    at a time to isolate each product's quota enforcement.
    """
    print("\n" + "=" * 80)
    print("🔒 QUOTA ENFORCEMENT TEST")
    print("=" * 80)
    print(f"   Testing per-product TPM limits via azure-openai-token-limit policy")
    print(f"   Products: {json.dumps(PRODUCT_TPM_LIMITS)}")
    print(f"   Start time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")

    sub_map = {s["name"].replace("subscription-", ""): s for s in subscriptions}
    all_results = []
    product_summaries = {}
    est_tokens_per_req = 40  # conservative estimate

    # Test products in order of ascending TPM limit (easiest to hit first)
    for product in sorted(PRODUCT_TPM_LIMITS, key=lambda p: PRODUCT_TPM_LIMITS[p]):
        tpm_limit = PRODUCT_TPM_LIMITS[product]
        sub = sub_map[product]
        # Send enough requests to exceed the TPM by ~2.5x
        target_requests = max(40, int(tpm_limit / est_tokens_per_req * 2.5))

        print(f"  ┌─────────────────────────────────────────────")
        print(f"  │ 📊 Product: {product.upper()} (TPM limit: {tpm_limit})")
        print(f"  │    Sending {target_requests} requests, concurrency={concurrency}")
        print(f"  └─────────────────────────────────────────────")

        results = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {}
            for i in range(target_requests):
                model = random.choice(MODELS)
                prompt = random.choice(PROMPTS)
                future = executor.submit(
                    send_request, len(all_results) + i + 1,
                    gateway_url, sub, model, prompt,
                )
                futures[future] = i

            for future in as_completed(futures):
                result = future.result()
                result["test_phase"] = product
                result["tpm_limit"] = tpm_limit
                results.append(result)
                # Live progress
                s = sum(1 for r in results if r["status"] == "success")
                rl = sum(1 for r in results if r["status"] == "rate_limited")
                err = sum(1 for r in results if r["status"] == "error")
                tok = sum(r["total_tokens"] for r in results if r["status"] == "success")
                pct = len(results) / target_requests * 100
                print(f"\r  ⏳ {len(results)}/{target_requests} ({pct:.0f}%) "
                      f"| ✅ {s} | ⚡ {rl} 429s | ❌ {err} | tokens={tok}/{tpm_limit}",
                      end="", flush=True)

        print()  # newline after progress

        # Sort results by run_id to preserve temporal ordering
        results.sort(key=lambda r: r["run_id"])

        # Compute summary
        success = [r for r in results if r["status"] == "success"]
        rate_limited = [r for r in results if r["status"] == "rate_limited"]
        errors = [r for r in results if r["status"] == "error"]
        tokens_before_429 = 0
        first_429_idx = None
        cumul_tokens = 0
        for idx, r in enumerate(results):
            if r["status"] == "success":
                cumul_tokens += r["total_tokens"]
            if r["status"] == "rate_limited" and first_429_idx is None:
                first_429_idx = idx + 1
                tokens_before_429 = cumul_tokens

        total_tokens = sum(r["total_tokens"] for r in success)
        summary = {
            "product": product,
            "tpm_limit": tpm_limit,
            "total_requests": len(results),
            "success": len(success),
            "rate_limited": len(rate_limited),
            "errors": len(errors),
            "total_tokens": total_tokens,
            "tokens_before_first_429": tokens_before_429,
            "first_429_at_request": first_429_idx,
        }
        product_summaries[product] = summary

        limit_hit = "YES ✅" if rate_limited else "NO ❌"
        print(f"     Limit hit: {limit_hit}")
        print(f"     Success: {len(success)}, Rate-limited (429): {len(rate_limited)}, Errors: {len(errors)}")
        print(f"     Tokens consumed: {total_tokens:,} / {tpm_limit} TPM")
        if first_429_idx:
            print(f"     First 429 after request #{first_429_idx} ({tokens_before_429} tokens consumed)")
        print()

        all_results.extend(results)

    # Print overall summary
    print("=" * 80)
    print("📊 QUOTA TEST SUMMARY")
    print("=" * 80)
    print(f"\n  {'Product':<12} {'TPM Limit':>10} {'Success':>8} {'429s':>6} {'Tokens':>8} {'Limit Hit':>10}")
    print(f"  {'-'*12} {'-'*10} {'-'*8} {'-'*6} {'-'*8} {'-'*10}")
    for product in sorted(PRODUCT_TPM_LIMITS, key=lambda p: PRODUCT_TPM_LIMITS[p]):
        s = product_summaries[product]
        hit = "✅ YES" if s["rate_limited"] > 0 else "❌ NO"
        print(f"  {product:<12} {s['tpm_limit']:>10,} {s['success']:>8} {s['rate_limited']:>6} "
              f"{s['total_tokens']:>8,} {hit:>10}")
    print()

    all_hit = all(s["rate_limited"] > 0 for s in product_summaries.values())
    if all_hit:
        print("  🎯 All product TPM limits were successfully enforced!")
    else:
        missed = [p for p, s in product_summaries.items() if s["rate_limited"] == 0]
        print(f"  ⚠️  Some products did NOT hit their limit: {', '.join(missed)}")
        print(f"     Consider running with higher concurrency or more requests.")

    print("\n" + "=" * 80)
    return all_results, product_summaries


def main():
    parser = argparse.ArgumentParser(description="AI Gateway Load Test")
    parser.add_argument("--mode", choices=["standard", "quota"], default="standard",
                        help="Test mode: 'standard' = balanced load, 'quota' = hit per-product TPM limits (default: standard)")
    parser.add_argument("--runs", type=int, default=90,
                        help="Total number of requests for standard mode (default: 90)")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Max concurrent requests (default: 5)")
    parser.add_argument("--finance-pct", type=int, default=50,
                        help="Percent of requests for Finance in standard mode (default: 50)")
    parser.add_argument("--marketing-pct", type=int, default=30,
                        help="Percent of requests for Marketing in standard mode (default: 30)")
    parser.add_argument("--hr-pct", type=int, default=20,
                        help="Percent of requests for HR in standard mode (default: 20)")
    args = parser.parse_args()

    gateway_url, subscriptions, app_insights = get_deployment_outputs()

    if args.mode == "quota":
        results, product_summaries = run_quota_test(
            gateway_url, subscriptions, args.concurrency,
        )
        save_results(results, extra_meta={
            "test_mode": "quota",
            "product_tpm_limits": PRODUCT_TPM_LIMITS,
            "product_summaries": product_summaries,
        })
    else:
        distribution = {
            "finance": args.finance_pct,
            "marketing": args.marketing_pct,
            "hr": args.hr_pct,
        }
        assert sum(distribution.values()) == 100, "Distribution must sum to 100%"
        results = run_load_test(gateway_url, subscriptions, args.runs, args.concurrency, distribution)
        print_summary(results)
        save_results(results, extra_meta={"test_mode": "standard"})

    print(f"\n⏳ Wait 2-5 minutes for metrics to propagate to App Insights, then run:")
    print(f"   python3 verify-scenarios.py")


if __name__ == "__main__":
    main()
