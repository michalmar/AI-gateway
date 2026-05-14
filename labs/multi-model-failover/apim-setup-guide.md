# APIM AI Gateway Setup Guide (AZ CLI)

This guide configures an **existing** APIM instance as an AI Gateway with PTU/PayGo load balancing on **existing** AI Foundry accounts. No Bicep required — pure `az cli` and `az rest`.

---

## Architecture Overview

```
Client → APIM (Products + Rate Limits)
           ↓ policy.xml (emit-token-metric, retry)
         Backend Pool (priority-based)
           ├─ Foundry-PTU  (priority 1) ──→ AI Foundry (Provisioned Throughput)
           └─ Foundry-PayGo (priority 2) ──→ AI Foundry (Standard Pay-as-you-go)
                  ↑ circuit breaker per backend
           ↓
         Logging → App Insights (customMetrics) + Log Analytics (LLM logs)
```

**Load Balancing Pattern — PTU + PayGo Spillover:**
- Both Foundry accounts have identically-named model deployments (e.g., `gpt-5.2`)
- PTU Foundry (priority 1): fixed-cost reserved capacity — used first
- PayGo Foundry (priority 2): variable-cost on-demand — spillover when PTU hits 429
- Models without PTU can be deployed as PayGo on both Foundries (2× rate limits)
- Client sends the same request regardless — the pool handles routing transparently

**Components configured:**
1. Managed Identity + RBAC for backend auth
2. App Insights Logger + Azure Monitor Logger
3. Backends with circuit breakers (one per Foundry)
4. Backend Pool with priority-based routing (PTU→PayGo)
5. Inference API with OpenAI spec
6. API-level policy (retry, emit-token-metric)
7. API diagnostics (azuremonitor + applicationinsights)
8. Products (finance, marketing, hr) with rate-limiting policies
9. Product-scoped subscriptions

---

## Prerequisites

```bash
# Set these variables for your environment
APIM_NAME="your-apim-name"
APIM_RG="your-resource-group"
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Your AI Foundry endpoints — PTU (priority 1) and PayGo (priority 2)
BACKEND1_NAME="foundry-ptu"
BACKEND1_URL="https://your-foundry-ptu.cognitiveservices.azure.com/openai"
BACKEND1_PRIORITY=1

BACKEND2_NAME="foundry-paygo"
BACKEND2_URL="https://your-foundry-paygo.cognitiveservices.azure.com/openai"
BACKEND2_PRIORITY=2

# Existing observability resources
APP_INSIGHTS_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$APIM_RG/providers/Microsoft.Insights/components/your-app-insights"
APP_INSIGHTS_KEY="your-instrumentation-key"
LAW_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$APIM_RG/providers/Microsoft.OperationalInsights/workspaces/your-law"

# API version for APIM REST calls (GenAI-capable)
API_VERSION="2024-06-01-preview"
APIM_BASE="https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$APIM_RG/providers/Microsoft.ApiManagement/service/$APIM_NAME"
```

---

## Step 1: Enable System-Assigned Managed Identity

APIM needs a managed identity to authenticate to AI Foundry backends using Entra ID (no API keys).

```bash
# Enable system-assigned managed identity on APIM
az apim update --name $APIM_NAME --resource-group $APIM_RG \
  --set identity.type=SystemAssigned

# Get the principal ID
APIM_PRINCIPAL_ID=$(az apim show --name $APIM_NAME --resource-group $APIM_RG \
  --query identity.principalId -o tsv)

echo "APIM Principal ID: $APIM_PRINCIPAL_ID"
```

### Assign RBAC: Cognitive Services OpenAI User

Grant APIM access to each AI Foundry resource:

```bash
# Role: Cognitive Services OpenAI User (5e0bd9bd-7b93-4f28-af87-19fc36ad61bd)
ROLE_ID="5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"

# For each AI Foundry resource (PTU + PayGo)
FOUNDRY_PTU_RESOURCE_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$APIM_RG/providers/Microsoft.CognitiveServices/accounts/your-foundry-ptu"
FOUNDRY_PAYGO_RESOURCE_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$APIM_RG/providers/Microsoft.CognitiveServices/accounts/your-foundry-paygo"

az role assignment create --assignee-object-id $APIM_PRINCIPAL_ID \
  --assignee-principal-type ServicePrincipal \
  --role $ROLE_ID --scope $FOUNDRY_PTU_RESOURCE_ID

az role assignment create --assignee-object-id $APIM_PRINCIPAL_ID \
  --assignee-principal-type ServicePrincipal \
  --role $ROLE_ID --scope $FOUNDRY_PAYGO_RESOURCE_ID
```

---

## Step 2: Create Loggers

### 2.1 Azure Monitor Logger

Used by the `azuremonitor` API diagnostic for LLM gateway logs.

```bash
az rest --method put \
  --url "$APIM_BASE/loggers/azuremonitor?api-version=$API_VERSION" \
  --body '{
    "properties": {
      "loggerType": "azureMonitor",
      "isBuffered": false
    }
  }'
```

### 2.2 Application Insights Logger

Used by the `applicationinsights` API diagnostic. **Required for `azure-openai-emit-token-metric` to work.**

```bash
az rest --method put \
  --url "$APIM_BASE/loggers/appinsights-logger?api-version=$API_VERSION" \
  --body "{
    \"properties\": {
      \"loggerType\": \"applicationInsights\",
      \"description\": \"APIM Logger for Application Insights\",
      \"isBuffered\": false,
      \"credentials\": {
        \"instrumentationKey\": \"$APP_INSIGHTS_KEY\"
      },
      \"resourceId\": \"$APP_INSIGHTS_ID\"
    }
  }"
```

> **Critical**: Without the App Insights logger + diagnostic, `azure-openai-emit-token-metric` silently does nothing.

---

## Step 3: Create Backends with Circuit Breakers

Each backend points to an AI Foundry endpoint and uses managed identity auth + circuit breaker. Deploy identical model names on both Foundries — the pool handles PTU→PayGo failover.

### 3.1 Backend: PTU Foundry (Primary)

```bash
az rest --method put \
  --url "$APIM_BASE/backends/$BACKEND1_NAME?api-version=$API_VERSION" \
  --body "{
    \"properties\": {
      \"description\": \"PTU Foundry - provisioned throughput (priority 1)\",
      \"url\": \"$BACKEND1_URL\",
      \"protocol\": \"http\",
      \"credentials\": {
        \"managedIdentity\": {
          \"resource\": \"https://cognitiveservices.azure.com\"
        }
      },
      \"circuitBreaker\": {
        \"rules\": [
          {
            \"name\": \"InferenceBreakerRule\",
            \"failureCondition\": {
              \"count\": 1,
              \"interval\": \"PT1M\",
              \"statusCodeRanges\": [
                { \"min\": 429, \"max\": 429 }
              ]
            },
            \"tripDuration\": \"PT1M\",
            \"acceptRetryAfter\": true
          }
        ]
      }
    }
  }"
```

### 3.2 Backend: PayGo Foundry (Spillover)

```bash
az rest --method put \
  --url "$APIM_BASE/backends/$BACKEND2_NAME?api-version=$API_VERSION" \
  --body "{
    \"properties\": {
      \"description\": \"PayGo Foundry - standard on-demand (priority 2, spillover)\",
      \"url\": \"$BACKEND2_URL\",
      \"protocol\": \"http\",
      \"credentials\": {
        \"managedIdentity\": {
          \"resource\": \"https://cognitiveservices.azure.com\"
        }
      },
      \"circuitBreaker\": {
        \"rules\": [
          {
            \"name\": \"InferenceBreakerRule\",
            \"failureCondition\": {
              \"count\": 1,
              \"interval\": \"PT1M\",
              \"statusCodeRanges\": [
                { \"min\": 429, \"max\": 429 }
              ]
            },
            \"tripDuration\": \"PT1M\",
            \"acceptRetryAfter\": true
          }
        ]
      }
    }
  }"
```

**Circuit Breaker Explained:**
- Trips after **1 occurrence** of HTTP 429 within a **1-minute** window
- Once tripped, backend is **bypassed for 1 minute** (requests fail over to PayGo)
- `acceptRetryAfter: true` — respects the backend's `Retry-After` header if present
- This enables the PTU→PayGo spillover pattern: PTU capacity exhausted → circuit breaks → PayGo absorbs overflow

---

## Step 4: Create Backend Pool

The pool uses **priority-based routing** for PTU/PayGo spillover: all traffic goes to PTU (priority 1); when PTU's circuit breaker trips (429), traffic spills over to PayGo (priority 2).

```bash
BACKEND1_ID="$APIM_BASE/backends/$BACKEND1_NAME"
BACKEND2_ID="$APIM_BASE/backends/$BACKEND2_NAME"

az rest --method put \
  --url "$APIM_BASE/backends/inference-backend-pool?api-version=$API_VERSION" \
  --body "{
    \"properties\": {
      \"description\": \"PTU + PayGo load balancer for inference endpoints\",
      \"type\": \"Pool\",
      \"pool\": {
        \"services\": [
          { \"id\": \"$BACKEND1_ID\", \"priority\": $BACKEND1_PRIORITY },
          { \"id\": \"$BACKEND2_ID\", \"priority\": $BACKEND2_PRIORITY }
        ]
      }
    }
  }"
```

> **Routing behavior:**
> - Different priority = **PTU→PayGo failover** (use PayGo only when PTU is unavailable)
> - Same priority = **round-robin** (weighted) — useful for models without PTU deployed on both Foundries
> - Both Foundries have identical deployment names, so the client request path works against either backend

---

## Step 5: Create the Inference API

### 5.1 Create API Definition

```bash
az rest --method put \
  --url "$APIM_BASE/apis/inference-api?api-version=$API_VERSION" \
  --body '{
    "properties": {
      "displayName": "Inference API",
      "description": "Azure OpenAI APIs for completions and search",
      "path": "inference/openai",
      "protocols": ["https"],
      "subscriptionRequired": true,
      "subscriptionKeyParameterNames": {
        "header": "api-key",
        "query": "api-key"
      },
      "type": "http",
      "format": "openapi+json",
      "value": "<OPENAPI_SPEC_JSON>"
    }
  }'
```

> **For the OpenAPI spec**: Use the spec from `modules/apim/v2/specs/AIFoundryOpenAI.json` in the repo, or import via the portal. Alternatively, create a wildcard operation:

```bash
# Alternative: Create a catch-all operation instead of importing a spec
az rest --method put \
  --url "$APIM_BASE/apis/inference-api?api-version=$API_VERSION" \
  --body '{
    "properties": {
      "displayName": "Inference API",
      "description": "Azure OpenAI inference gateway",
      "path": "inference/openai",
      "protocols": ["https"],
      "subscriptionRequired": true,
      "subscriptionKeyParameterNames": {
        "header": "api-key",
        "query": "api-key"
      }
    }
  }'

# Then add a wildcard operation for all paths
az rest --method put \
  --url "$APIM_BASE/apis/inference-api/operations/catch-all?api-version=$API_VERSION" \
  --body '{
    "properties": {
      "displayName": "Catch-All",
      "method": "POST",
      "urlTemplate": "/*",
      "templateParameters": []
    }
  }'
```

---

## Step 6: Apply API-Level Policy

This is the core policy: backend routing, retry with exponential backoff, and token metric emission.

```bash
# The policy XML (inline)
read -r -d '' POLICY_XML << 'POLICYEOF'
<policies>
    <inbound>
        <base />
        <set-backend-service backend-id="inference-backend-pool" />
        <azure-openai-emit-token-metric namespace="aiusage">
            <dimension name="Product" value="@(context.Product.Id)" />
            <dimension name="Subscription ID" />
            <dimension name="Model" value="@(context.Request.MatchedParameters.GetValueOrDefault(&quot;deployment-id&quot;, &quot;unknown&quot;))" />
        </azure-openai-emit-token-metric>
    </inbound>
    <backend>
        <retry count="2" interval="1" max-interval="10" delta="2"
               first-fast-retry="true"
               condition="@(context.Response.StatusCode == 429 || context.Response.StatusCode == 503)">
            <set-backend-service backend-id="inference-backend-pool" />
            <forward-request buffer-request-body="true" />
        </retry>
    </backend>
    <outbound>
        <base />
    </outbound>
    <on-error>
        <base />
        <choose>
            <when condition="@(context.Response.StatusCode == 429)">
                <return-response>
                    <set-status code="429" reason="Too Many Requests" />
                    <set-header name="Retry-After" exists-action="override">
                        <value>10</value>
                    </set-header>
                </return-response>
            </when>
            <when condition="@(context.Response.StatusCode == 503)">
                <return-response>
                    <set-status code="503" reason="Service Unavailable" />
                </return-response>
            </when>
        </choose>
    </on-error>
</policies>
POLICYEOF

# Apply the policy via REST API
az rest --method put \
  --url "$APIM_BASE/apis/inference-api/policies/policy?api-version=$API_VERSION" \
  --body "{
    \"properties\": {
      \"format\": \"rawxml\",
      \"value\": $(echo "$POLICY_XML" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
    }
  }"
```

**Policy breakdown:**

| Section | What it does |
|---------|-------------|
| `set-backend-service` | Routes to the backend pool |
| `azure-openai-emit-token-metric` | Emits Total/Prompt/Completion Tokens to App Insights with Product, Subscription ID, Model dimensions |
| `retry` (backend) | On 429/503: retry up to 2x with exponential backoff (1s → 2s → 4s, max 10s). Re-selects backend from pool on each retry. |
| `on-error` | Returns clean 429/503 to client without exposing backend details |

---

## Step 7: Configure API Diagnostics

### 7.1 Azure Monitor Diagnostic (LLM Logging)

This sends **LLM request/response logs** to Log Analytics (`ApiManagementGatewayLlmLog` table).

```bash
AZURE_MONITOR_LOGGER_ID="$APIM_BASE/loggers/azuremonitor"

az rest --method put \
  --url "$APIM_BASE/apis/inference-api/diagnostics/azuremonitor?api-version=$API_VERSION" \
  --body "{
    \"properties\": {
      \"alwaysLog\": \"allErrors\",
      \"verbosity\": \"verbose\",
      \"logClientIp\": true,
      \"loggerId\": \"$AZURE_MONITOR_LOGGER_ID\",
      \"sampling\": {
        \"samplingType\": \"fixed\",
        \"percentage\": 100
      },
      \"frontend\": {
        \"request\": {
          \"headers\": [\"Content-type\", \"User-agent\", \"x-ms-region\", \"x-ratelimit-remaining-tokens\", \"x-ratelimit-remaining-requests\"],
          \"body\": { \"bytes\": 0 }
        },
        \"response\": {
          \"headers\": [\"Content-type\", \"User-agent\", \"x-ms-region\", \"x-ratelimit-remaining-tokens\", \"x-ratelimit-remaining-requests\"],
          \"body\": { \"bytes\": 0 }
        }
      },
      \"backend\": {
        \"request\": {
          \"headers\": [\"Content-type\", \"User-agent\", \"x-ms-region\", \"x-ratelimit-remaining-tokens\", \"x-ratelimit-remaining-requests\"],
          \"body\": { \"bytes\": 0 }
        },
        \"response\": {
          \"headers\": [\"Content-type\", \"User-agent\", \"x-ms-region\", \"x-ratelimit-remaining-tokens\", \"x-ratelimit-remaining-requests\"],
          \"body\": { \"bytes\": 0 }
        }
      },
      \"largeLanguageModel\": {
        \"logs\": \"enabled\",
        \"requests\": {
          \"messages\": \"all\",
          \"maxSizeInBytes\": 262144
        },
        \"responses\": {
          \"messages\": \"all\",
          \"maxSizeInBytes\": 262144
        }
      }
    }
  }"
```

### 7.2 Application Insights Diagnostic (Token Metrics)

**Required for `azure-openai-emit-token-metric` to emit data.** Without this, custom metrics silently fail.

```bash
APPINSIGHTS_LOGGER_ID="$APIM_BASE/loggers/appinsights-logger"

az rest --method put \
  --url "$APIM_BASE/apis/inference-api/diagnostics/applicationinsights?api-version=$API_VERSION" \
  --body "{
    \"properties\": {
      \"alwaysLog\": \"allErrors\",
      \"verbosity\": \"verbose\",
      \"logClientIp\": true,
      \"httpCorrelationProtocol\": \"W3C\",
      \"metrics\": true,
      \"loggerId\": \"$APPINSIGHTS_LOGGER_ID\",
      \"sampling\": {
        \"samplingType\": \"fixed\",
        \"percentage\": 100
      },
      \"frontend\": {
        \"request\": {
          \"headers\": [\"Content-type\", \"User-agent\", \"x-ms-region\", \"x-ratelimit-remaining-tokens\", \"x-ratelimit-remaining-requests\"],
          \"body\": { \"bytes\": 8192 }
        },
        \"response\": {
          \"headers\": [\"Content-type\", \"User-agent\", \"x-ms-region\", \"x-ratelimit-remaining-tokens\", \"x-ratelimit-remaining-requests\"],
          \"body\": { \"bytes\": 8192 }
        }
      },
      \"backend\": {
        \"request\": {
          \"headers\": [\"Content-type\", \"User-agent\", \"x-ms-region\", \"x-ratelimit-remaining-tokens\", \"x-ratelimit-remaining-requests\"],
          \"body\": { \"bytes\": 8192 }
        },
        \"response\": {
          \"headers\": [\"Content-type\", \"User-agent\", \"x-ms-region\", \"x-ratelimit-remaining-tokens\", \"x-ratelimit-remaining-requests\"],
          \"body\": { \"bytes\": 8192 }
        }
      }
    }
  }"
```

---

## Step 8: Create Products

Each product enforces its own TPM rate limit and is associated with the inference API.

```bash
# Define products: name, displayName, tpm
declare -A PRODUCTS
PRODUCTS[finance]="Finance Product|2000"
PRODUCTS[marketing]="Marketing Product|1000"
PRODUCTS[hr]="HR Product|500"

for PRODUCT_NAME in finance marketing hr; do
  IFS='|' read -r DISPLAY_NAME TPM <<< "${PRODUCTS[$PRODUCT_NAME]}"

  # 8.1 Create the product
  az rest --method put \
    --url "$APIM_BASE/products/$PRODUCT_NAME?api-version=$API_VERSION" \
    --body "{
      \"properties\": {
        \"displayName\": \"$DISPLAY_NAME\",
        \"description\": \"$DISPLAY_NAME\",
        \"subscriptionRequired\": true,
        \"approvalRequired\": true,
        \"state\": \"published\"
      }
    }"

  # 8.2 Associate product with the inference API
  az rest --method put \
    --url "$APIM_BASE/products/$PRODUCT_NAME/apiLinks/openai-$PRODUCT_NAME?api-version=$API_VERSION" \
    --body "{
      \"properties\": {
        \"apiId\": \"$APIM_BASE/apis/inference-api\"
      }
    }"

  # 8.3 Apply product-level policy (token rate limiting)
  PRODUCT_POLICY="<policies>
    <inbound>
        <base />
        <azure-openai-token-limit
            counter-key=\"@(context.Subscription.Id)\"
            tokens-per-minute=\"$TPM\"
            estimate-prompt-tokens=\"false\">
        </azure-openai-token-limit>
    </inbound>
    <backend><base /></backend>
    <outbound><base /></outbound>
    <on-error><base /></on-error>
</policies>"

  az rest --method put \
    --url "$APIM_BASE/products/$PRODUCT_NAME/policies/policy?api-version=$API_VERSION" \
    --body "{
      \"properties\": {
        \"format\": \"rawxml\",
        \"value\": $(echo "$PRODUCT_POLICY" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
      }
    }"

  echo "✅ Product '$PRODUCT_NAME' created with ${TPM} TPM limit"
done
```

**Product-level policy explained:**
- `azure-openai-token-limit`: Counts tokens per minute, scoped by `context.Subscription.Id`
- `estimate-prompt-tokens="false"`: Uses actual token counts from the backend response, not estimates
- Returns `429 Too Many Requests` when a subscription exceeds its TPM quota

---

## Step 9: Create Product-Scoped Subscriptions

```bash
declare -A SUB_PRODUCTS
SUB_PRODUCTS[subscription-finance]="Finance Subscription|finance"
SUB_PRODUCTS[subscription-marketing]="Marketing Subscription|marketing"
SUB_PRODUCTS[subscription-hr]="HR Subscription|hr"

for SUB_NAME in subscription-finance subscription-marketing subscription-hr; do
  IFS='|' read -r DISPLAY_NAME PRODUCT <<< "${SUB_PRODUCTS[$SUB_NAME]}"

  az rest --method put \
    --url "$APIM_BASE/subscriptions/$SUB_NAME?api-version=$API_VERSION" \
    --body "{
      \"properties\": {
        \"displayName\": \"$DISPLAY_NAME\",
        \"scope\": \"$APIM_BASE/products/$PRODUCT\",
        \"state\": \"active\",
        \"allowTracing\": true
      }
    }"

  # Retrieve the subscription key
  KEY=$(az rest --method post \
    --url "$APIM_BASE/subscriptions/$SUB_NAME/listSecrets?api-version=$API_VERSION" \
    --query "primaryKey" -o tsv)

  echo "✅ Subscription '$SUB_NAME' → product '$PRODUCT' | Key: ****${KEY: -4}"
done
```

---

## Step 10: Enable APIM Diagnostic Settings (Platform-Level)

This sends APIM platform logs and metrics to Log Analytics (separate from API-level diagnostics).

```bash
az monitor diagnostic-settings create \
  --name "apimDiagnosticSettings" \
  --resource "$APIM_BASE" \
  --workspace "$LAW_ID" \
  --logs '[{"categoryGroup": "AllLogs", "enabled": true}]' \
  --metrics '[{"category": "AllMetrics", "enabled": true}]'
```

---

## Verification

### Test a request

```bash
# Get a subscription key
SUB_KEY=$(az rest --method post \
  --url "$APIM_BASE/subscriptions/subscription-finance/listSecrets?api-version=$API_VERSION" \
  --query "primaryKey" -o tsv)

GATEWAY_URL=$(az apim show --name $APIM_NAME --resource-group $APIM_RG \
  --query gatewayUrl -o tsv)

curl -s "$GATEWAY_URL/inference/openai/deployments/gpt-4.1-nano/chat/completions?api-version=2024-12-01-preview" \
  -H "api-key: $SUB_KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello"}],"max_tokens":20}' | python3 -m json.tool
```

### Verify token metrics (after ~3 min)

```bash
APP_INSIGHTS_APP_ID="your-app-insights-app-id"

az monitor app-insights query --app $APP_INSIGHTS_APP_ID --analytics-query \
  "customMetrics | where name == 'Total Tokens' | take 5 | project timestamp, valueSum, customDimensions"
```

### Verify LLM logs

```bash
LAW_CUSTOMER_ID="your-law-workspace-id"

az rest --method post \
  --url "https://api.loganalytics.io/v1/workspaces/$LAW_CUSTOMER_ID/query" \
  --body '{"query": "ApiManagementGatewayLlmLog | take 5", "timespan": "PT1H"}' \
  --headers "Content-Type=application/json"
```

---

## Quick Reference: Policy Execution Order

```
Request arrives
  │
  ▼
Product Policy (inbound)          ← azure-openai-token-limit (TPM check)
  │
  ▼
API Policy (inbound)              ← set-backend-service (pool selection)
  │                               ← azure-openai-emit-token-metric
  ▼
API Policy (backend)              ← retry with exponential backoff
  │                                  └─ re-selects from pool on each retry
  ▼
Backend Pool                      ← priority routing: PTU first, PayGo spillover
  │                                  + circuit breaker per backend
  ▼
AI Foundry endpoint               ← managed identity auth
  │
  ▼
API Policy (outbound)             ← pass-through
  │
  ▼
Product Policy (outbound)         ← pass-through
  │
  ▼
Response to client
```

---

## Key Gotchas

1. **App Insights diagnostic is REQUIRED for emit-token-metric** — the azuremonitor diagnostic alone handles LLM logs but NOT custom metrics
2. **Subscriptions default to `suspended`** on PUT — always set `"state": "active"` explicitly
3. **Circuit breaker + backend pool work together** — circuit breaker trips on PTU (429) → pool routes to PayGo → retry policy re-selects from pool
4. **Product policy runs BEFORE API policy** — TPM check happens before backend routing
5. **`estimate-prompt-tokens="false"`** requires the backend to return token counts in response headers; Azure OpenAI does this natively
6. **Use `api-version=2024-06-01-preview`** or later for GenAI features (LLM logging, circuit breaker, backend pools)
7. **Both Foundries must have identical deployment names** — the client request path (e.g., `/deployments/gpt-5.2/...`) must resolve on whichever backend the pool selects
8. **Models without PTU can use PayGo on both Foundries** — set same priority for round-robin, giving 2× the rate limits
