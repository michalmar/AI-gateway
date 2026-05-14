# AI Gateway Scenario Verification Report

**Generated:** 2026-03-25 13:02:38 UTC  
**Resource Group:** `lab-multi-model-failover`  
**Deployment:** `multi-model-failover`

## Summary

| # | Scenario | Status |
|---|----------|--------|
| 1 | Load Balancing Across Models/Backends | ✅ PASS |
| 2 | Department Access via Product Subscriptions | ✅ PASS |
| 3 | Usage Monitoring per Product/Department/Model | ✅ PASS |
| 4 | Cost Monitoring per Product/Department/Model | ✅ PASS |
| 5 | Product TPM Quota Enforcement | ✅ PASS |

## Load Test Summary

- **Total Requests:** 227
- **Successful:** 134 (59.0%)
- **Rate Limited:** 93 (41.0%)
- **Errors:** 0 (0.0%)
- **Total Tokens:** 4,782
- **Avg Latency:** 1468 ms

---

## Scenario 1: Load Balancing Across Models/Backends

**Status:** ✅ PASS

**Backend Distribution (Load Balancing Evidence):**

> ✅ **Failover confirmed!** Requests were distributed across multiple backends.

| Backend | Requests | % | Success | Throttled (429) | Errors |
|---------|----------|---|---------|----------------|--------|
| foundry1 | 139 | 71.3% | 139 | 0 | 0 |
| foundry2 | 56 | 28.7% | 56 | 0 | 0 |

**Model Distribution:**

| Model | Requests | % of Total | Total Tokens |
|-------|----------|-----------|--------------|
| gpt-4.1 | 104 | 53.1% | 3,783 |
| gpt-4.1-nano | 92 | 46.9% | 3,181 |

**Backend × Model (Failover Detail):**

| Backend | Model | Requests | Total Tokens |
|---------|-------|----------|-------------|
| foundry2 | gpt-4.1-nano | 20 | 625 |
| foundry2 | gpt-4.1 | 36 | 1,356 |
| foundry1 | gpt-4.1-nano | 71 | 2,556 |
| foundry1 | gpt-4.1 | 68 | 2,427 |

![Load Balancing Across Models/Backends](scenario-charts/scenario1_backend_distribution.png)

![Load Balancing Across Models/Backends](scenario-charts/scenario1_backend_timeline.png)

![Load Balancing Across Models/Backends](scenario-charts/scenario1_model_distribution.png)

---

## Scenario 2: Department Access via Product Subscriptions

**Status:** ✅ PASS

**Per-Subscription Access Summary:**

| Subscription | Total | Success | Rate Limited (429) | Errors (5xx) |
|-------------|-------|---------|-------------------|-------------|
| subscription-finance | 156 | 109 | 47 | 0 |
| subscription-marketing | 80 | 53 | 27 | 0 |
| subscription-hr | 52 | 33 | 19 | 0 |
|  | 1 | 0 | 0 | 0 |

![Department Access via Product Subscriptions](scenario-charts/scenario2_department_access.png)

---

## Scenario 3: Usage Monitoring per Product/Department/Model

**Status:** ✅ PASS

**Token Usage by Subscription (Department):**

| Subscription | Requests | Prompt Tokens | Completion Tokens | Total Tokens |
|-------------|----------|--------------|------------------|-------------|
| subscription-finance | 109 | 1,496 | 2,434 | 3,930 |
| subscription-marketing | 53 | 721 | 1,192 | 1,913 |
| subscription-hr | 33 | 461 | 660 | 1,121 |
|  | 1 | 0 | 0 | 0 |

**Token Usage by Model:**

| Model | Requests | Prompt Tokens | Completion Tokens | Total Tokens |
|-------|----------|--------------|------------------|-------------|
| gpt-4.1 | 104 | 1,430 | 2,353 | 3,783 |
| gpt-4.1-nano | 92 | 1,248 | 1,933 | 3,181 |

**Token Usage by Subscription × Model:**

| Subscription | Model | Requests | Total Tokens |
|-------------|-------|----------|-------------|
| subscription-marketing | gpt-4.1-nano | 28 | 1,032 |
| subscription-marketing | gpt-4.1 | 25 | 881 |
| subscription-hr | gpt-4.1-nano | 16 | 510 |
| subscription-hr | gpt-4.1 | 17 | 611 |
| subscription-finance | gpt-4.1-nano | 47 | 1,639 |
| subscription-finance | gpt-4.1 | 62 | 2,291 |
|  | gpt-4.1-nano | 1 | 0 |

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
| subscription-marketing | $0.723800 | $4.565600 | $5.289400 |
| subscription-finance | $1.774100 | $11.887200 | $13.661300 |
| subscription-hr | $0.486900 | $3.144400 | $3.631300 |
|  | $0.000000 | $0.000000 | $0.000000 |

**Cost by Model:**

| Model | Input Cost ($) | Output Cost ($) | Total Cost ($) |
|-------|---------------|----------------|---------------|
| gpt-4.1 | $2.860000 | $18.824000 | $21.684000 |
| gpt-4.1-nano | $0.124800 | $0.773200 | $0.898000 |

**Cost by Subscription × Model:**

| Subscription | Model | Total Cost ($) |
|-------------|-------|---------------|
| subscription-marketing | gpt-4.1-nano | $0.299400 |
| subscription-marketing | gpt-4.1 | $4.990000 |
| subscription-hr | gpt-4.1-nano | $0.135300 |
| subscription-hr | gpt-4.1 | $3.496000 |
| subscription-finance | gpt-4.1-nano | $0.463300 |
| subscription-finance | gpt-4.1 | $13.198000 |
|  | gpt-4.1-nano | $0.000000 |

**Cost vs Quota:**

| Subscription | Cost ($) | Quota ($) | Usage % |
|-------------|---------|----------|---------|
| subscription-finance | $13.661300 | $20.00 | 🟢 68.3065% |
| subscription-marketing | $5.289400 | $10.00 | 🟢 52.8940% |
| subscription-hr | $3.631300 | $5.00 | 🟢 72.6260% |

![Cost Monitoring per Product/Department/Model](scenario-charts/scenario4_cost_by_subscription.png)

![Cost Monitoring per Product/Department/Model](scenario-charts/scenario4_cost_by_model.png)

![Cost Monitoring per Product/Department/Model](scenario-charts/scenario4_cost_cross.png)

![Cost Monitoring per Product/Department/Model](scenario-charts/scenario4_cost_vs_quota.png)

---

## Scenario 5: Product TPM Quota Enforcement

**Status:** ✅ PASS

**Quota Test Results (client-side):**

| Product | TPM Limit | Requests | ✅ Success | ⚡ 429 | Tokens Used | First 429 After |
|---------|-----------|----------|-----------|-------|-------------|-----------------|
| hr | 500 | 40 | 21 | 19 | 668 | Request #22 (668 tokens) |
| marketing | 1,000 | 62 | 35 | 27 | 1,283 | Request #36 (1283 tokens) |
| finance | 2,000 | 125 | 78 | 47 | 2,831 | Request #79 (2831 tokens) |

> ✅ **All product TPM limits were enforced!** Every department hit its 429 ceiling.

**Per-Product Token Accumulation:**

**HR** (TPM limit: 500)
- Requests sent: 40
- Successful: 21 (consumed 668 tokens)
- Rate-limited (429): 19
- First 429 at request #22 after 668 tokens
- Token usage at first 429: **134%** of TPM limit

**MARKETING** (TPM limit: 1,000)
- Requests sent: 62
- Successful: 35 (consumed 1,283 tokens)
- Rate-limited (429): 27
- First 429 at request #36 after 1,283 tokens
- Token usage at first 429: **128%** of TPM limit

**FINANCE** (TPM limit: 2,000)
- Requests sent: 125
- Successful: 78 (consumed 2,831 tokens)
- Rate-limited (429): 47
- First 429 at request #79 after 2,831 tokens
- Token usage at first 429: **142%** of TPM limit


![Product TPM Quota Enforcement](scenario-charts/scenario5_quota_hr.png)

![Product TPM Quota Enforcement](scenario-charts/scenario5_quota_marketing.png)

![Product TPM Quota Enforcement](scenario-charts/scenario5_quota_finance.png)

![Product TPM Quota Enforcement](scenario-charts/scenario5_quota_summary.png)

---

*Report generated by `verify-scenarios.py`*