# AI Gateway Scenario Verification Report

**Generated:** 2026-03-25 12:37:06 UTC  
**Resource Group:** `lab-multi-model-failover`  
**Deployment:** `multi-model-failover`

## Summary

| # | Scenario | Status |
|---|----------|--------|
| 1 | Load Balancing Across Models/Backends | ✅ PASS |
| 2 | Department Access via Product Subscriptions | ✅ PASS |
| 3 | Usage Monitoring per Product/Department/Model | ✅ PASS |
| 4 | Cost Monitoring per Product/Department/Model | ✅ PASS |

## Load Test Summary

- **Total Requests:** 60
- **Successful:** 60 (100.0%)
- **Rate Limited:** 0 (0.0%)
- **Errors:** 0 (0.0%)
- **Total Tokens:** 2,170
- **Avg Latency:** 1512 ms

---

## Scenario 1: Load Balancing Across Models/Backends

**Status:** ✅ PASS

**Backend Distribution (Load Balancing Evidence):**

> ✅ **Failover confirmed!** Requests were distributed across multiple backends.

| Backend | Requests | % | Success | Throttled (429) | Errors |
|---------|----------|---|---------|----------------|--------|
| foundry1 | 138 | 71.1% | 138 | 0 | 0 |
| foundry2 | 56 | 28.9% | 56 | 0 | 0 |

**Model Distribution:**

| Model | Requests | % of Total | Total Tokens |
|-------|----------|-----------|--------------|
| gpt-4.1 | 136 | 55.5% | 4,320 |
| gpt-4.1-nano | 109 | 44.5% | 2,817 |

**Backend × Model (Failover Detail):**

| Backend | Model | Requests | Total Tokens |
|---------|-------|----------|-------------|
| foundry2 | gpt-4.1-nano | 20 | 625 |
| foundry2 | gpt-4.1 | 36 | 1,356 |
| foundry1 | gpt-4.1-nano | 63 | 2,192 |
| foundry1 | gpt-4.1 | 75 | 2,964 |

![Load Balancing Across Models/Backends](scenario-charts/scenario1_backend_distribution.png)

![Load Balancing Across Models/Backends](scenario-charts/scenario1_backend_timeline.png)

![Load Balancing Across Models/Backends](scenario-charts/scenario1_model_distribution.png)

---

## Scenario 2: Department Access via Product Subscriptions

**Status:** ✅ PASS

**Per-Subscription Access Summary:**

| Subscription | Total | Success | Rate Limited (429) | Errors (5xx) |
|-------------|-------|---------|-------------------|-------------|
| subscription-finance | 97 | 97 | 0 | 0 |
| subscription-marketing | 58 | 58 | 0 | 0 |
|  | 51 | 0 | 0 | 0 |
| subscription-hr | 39 | 39 | 0 | 0 |

![Department Access via Product Subscriptions](scenario-charts/scenario2_department_access.png)

---

## Scenario 3: Usage Monitoring per Product/Department/Model

**Status:** ✅ PASS

**Token Usage by Subscription (Department):**

| Subscription | Requests | Prompt Tokens | Completion Tokens | Total Tokens |
|-------------|----------|--------------|------------------|-------------|
| subscription-finance | 97 | 1,295 | 2,392 | 3,687 |
| subscription-marketing | 58 | 782 | 1,315 | 2,097 |
| subscription-hr | 39 | 530 | 823 | 1,353 |
|  | 51 | 0 | 0 | 0 |

**Token Usage by Model:**

| Model | Requests | Prompt Tokens | Completion Tokens | Total Tokens |
|-------|----------|--------------|------------------|-------------|
| gpt-4.1 | 136 | 1,503 | 2,817 | 4,320 |
| gpt-4.1-nano | 109 | 1,104 | 1,713 | 2,817 |

**Token Usage by Subscription × Model:**

| Subscription | Model | Requests | Total Tokens |
|-------------|-------|----------|-------------|
| subscription-marketing | gpt-4.1-nano | 27 | 914 |
| subscription-marketing | gpt-4.1 | 31 | 1,183 |
| subscription-hr | gpt-4.1-nano | 12 | 364 |
| subscription-hr | gpt-4.1 | 27 | 989 |
| subscription-finance | gpt-4.1-nano | 44 | 1,539 |
| subscription-finance | gpt-4.1 | 53 | 2,148 |
|  | gpt-4.1-nano | 26 | 0 |
|  | gpt-4.1 | 25 | 0 |

![Usage Monitoring per Product/Department/Model](scenario-charts/scenario3_usage_by_subscription.png)

![Usage Monitoring per Product/Department/Model](scenario-charts/scenario3_usage_by_model.png)

![Usage Monitoring per Product/Department/Model](scenario-charts/scenario3_usage_cross.png)

![Usage Monitoring per Product/Department/Model](scenario-charts/scenario3_usage_timeline.png)

---

## Scenario 4: Cost Monitoring per Product/Department/Model

**Status:** ✅ PASS

**Cost by Subscription (Department):**

| Subscription | Input Cost ($) | Output Cost ($) | Total Cost ($) |
|-------------|---------------|----------------|---------------|
|  | $0.000000 | $0.000000 | $0.000000 |
| subscription-finance | $1.482300 | $11.870400 | $13.352700 |
| subscription-marketing | $0.880000 | $6.309600 | $7.189600 |
| subscription-hr | $0.754100 | $5.041200 | $5.795300 |

**Cost by Model:**

| Model | Input Cost ($) | Output Cost ($) | Total Cost ($) |
|-------|---------------|----------------|---------------|
| gpt-4.1 | $3.006000 | $22.536000 | $25.542000 |
| gpt-4.1-nano | $0.110400 | $0.685200 | $0.795600 |

**Cost by Subscription × Model:**

| Subscription | Model | Total Cost ($) |
|-------------|-------|---------------|
| subscription-marketing | gpt-4.1-nano | $0.257600 |
| subscription-marketing | gpt-4.1 | $6.932000 |
| subscription-hr | gpt-4.1-nano | $0.097300 |
| subscription-hr | gpt-4.1 | $5.698000 |
| subscription-finance | gpt-4.1-nano | $0.440700 |
| subscription-finance | gpt-4.1 | $12.912000 |
|  | gpt-4.1-nano | $0.000000 |
|  | gpt-4.1 | $0.000000 |

**Cost vs Quota:**

| Subscription | Cost ($) | Quota ($) | Usage % |
|-------------|---------|----------|---------|
| subscription-finance | $13.352700 | $20.00 | 🟢 66.7635% |
| subscription-marketing | $7.189600 | $10.00 | 🟢 71.8960% |
| subscription-hr | $5.795300 | $5.00 | 🔴 115.9060% |

![Cost Monitoring per Product/Department/Model](scenario-charts/scenario4_cost_by_subscription.png)

![Cost Monitoring per Product/Department/Model](scenario-charts/scenario4_cost_by_model.png)

![Cost Monitoring per Product/Department/Model](scenario-charts/scenario4_cost_cross.png)

![Cost Monitoring per Product/Department/Model](scenario-charts/scenario4_cost_vs_quota.png)

---

*Report generated by `verify-scenarios.py`*