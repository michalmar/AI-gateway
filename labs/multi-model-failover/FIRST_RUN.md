# First run guide: Multi-Model Failover

This guide starts from a clean Azure subscription and ends with a deployed AI Gateway, populated FinOps tables, a load test, and scenario verification.

## 1. Prepare Azure

### Required permissions

Use an Azure account with these permissions on the target subscription or resource group:

- Contributor
- Role Based Access Control Administrator or Owner

The deployment creates role assignments for APIM and for the Data Collection Rules used to populate custom Log Analytics tables.

### Register resource providers

```bash
az provider register --namespace Microsoft.ApiManagement
az provider register --namespace Microsoft.CognitiveServices
az provider register --namespace Microsoft.Insights
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.Logic
```

### Choose names and regions

The lab expects an existing APIM instance in the same resource group where the lab is deployed. Set these values before running the commands below:

```bash
export RESOURCE_GROUP_NAME="rg-ict-apim-multi-model-failover-001-mpsvcrtest"
export RESOURCE_GROUP_LOCATION="westeurope"
export APIM_NAME="apim-ict-apim-multi-model-failover-001-mpsvcrtest"
```

The default model deployment regions in `multi-deploy.py` are `westeurope` and `swedencentral`. Confirm that your subscription has model availability and quota for the configured models in those regions, or adjust `aiservices_config` and `models_config` in `multi-deploy.py` before deploying.

### Create or prepare APIM

If you already have APIM, it must be in `$RESOURCE_GROUP_NAME` and have system-assigned managed identity enabled:

```bash
az apim update \
  --name "$APIM_NAME" \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --set identity.type=SystemAssigned
```

If you need a new APIM instance, create the resource group first and then create APIM. APIM provisioning can take a while.

```bash
az group create \
  --name "$RESOURCE_GROUP_NAME" \
  --location "$RESOURCE_GROUP_LOCATION"

az apim create \
  --name "$APIM_NAME" \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --location "$RESOURCE_GROUP_LOCATION" \
  --publisher-email "admin@example.com" \
  --publisher-name "AI Gateway Lab" \
  --sku-name Basicv2 \
  --sku-capacity 1

az apim update \
  --name "$APIM_NAME" \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --set identity.type=SystemAssigned
```

## 2. Prepare your local environment

From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

az login
az account set --subscription "<subscription-id-or-name>"

cd labs/multi-model-failover
```

If you are using WSL, run the Python commands inside WSL and make sure Azure CLI is authenticated in the same environment.

## 3. Deploy and populate FinOps tables

Use `multi-deploy.py` for the first deployment. It writes `params.json`, deploys `main.bicep`, retrieves deployment outputs, and populates the custom Log Analytics tables:

- `PRICING_CL`
- `SUBSCRIPTION_QUOTA_CL`

```bash
python3.12 multi-deploy.py
```

The script uses these environment variables:

| Variable | Purpose |
|----------|---------|
| `RESOURCE_GROUP_NAME` or `RESOURCE_GROUP` | Resource group for APIM and lab resources |
| `RESOURCE_GROUP_LOCATION` | Resource group location |
| `APIM_NAME` | Existing APIM instance to configure |
| `PROJECT_NAME` | Naming segment, default `ict-apim` |
| `SUBPROJECT_NAME` | Naming segment, default `multi-model-failover` |
| `TENANT_NAME` | Naming segment, default `mpsvcrtest` |
| `RESOURCE_NUMBER` | Primary number, default `001` |
| `SECONDARY_RESOURCE_NUMBER` | Secondary number, default `002` |
| `DCR_SUBPROJECT_NAME` | Short DCR name segment, default `mf` |

### If you deploy directly with Bicep

Direct Bicep deployment creates the custom tables and DCRs, but ARM cannot upload rows into Log Analytics custom tables. If you deploy this way, run `multi-model-test-query.py` once after deployment to backfill missing FinOps rows:

```bash
az deployment group create \
  --name multi-model-failover \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --template-file main.bicep \
  --parameters apimName="$APIM_NAME"

python3.12 multi-model-test-query.py \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --deployment-name multi-model-failover
```

## 4. Confirm deployment outputs and custom tables

```bash
az deployment group show \
  --name multi-model-failover \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --query "properties.outputs.{gateway:apimResourceGatewayURL.value,appInsights:appInsightsName.value,workspaceId:logAnalyticsWorkspaceId.value,pricingTable:pricingTableName.value,quotaTable:subscriptionQuotaTableName.value}" \
  -o table
```

Check that the FinOps tables have rows. It can take a few minutes for uploaded custom logs to become queryable.

```bash
WORKSPACE_ID=$(az deployment group show \
  --name multi-model-failover \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --query "properties.outputs.logAnalyticsWorkspaceId.value" \
  -o tsv)

az monitor log-analytics query \
  -w "$WORKSPACE_ID" \
  --analytics-query "PRICING_CL | summarize Rows=count(), Models=make_set(Model, 20)" \
  -o table

az monitor log-analytics query \
  -w "$WORKSPACE_ID" \
  --analytics-query "SUBSCRIPTION_QUOTA_CL | summarize Rows=count(), Subscriptions=make_set(Subscription, 20)" \
  -o table
```

Expected first-run counts are three pricing rows and three subscription quota rows.

## 5. Run a smoke test

Run a small load test first:

```bash
python3.12 load-test.py \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --deployment-name multi-model-failover \
  --apim-name "$APIM_NAME" \
  --runs 10 \
  --concurrency 2
```

Wait a few minutes for APIM, Application Insights, and Log Analytics telemetry to arrive.

Then run the telemetry query:

```bash
python3.12 multi-model-test-query.py \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --deployment-name multi-model-failover
```

You should see App Insights token metrics and Log Analytics cost rows per subscription.

## 6. Verify scenarios

Run the full scenario verifier:

```bash
python3.12 verify-scenarios.py \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --deployment-name multi-model-failover \
  --apim-name "$APIM_NAME" \
  --timespan PT1H
```

The verifier writes:

- `load-test-results.json` after load tests
- `scenario-report.md`
- charts under `scenario-charts/`

To exercise quota enforcement, run quota mode and then rerun verification:

```bash
python3.12 load-test.py \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --deployment-name multi-model-failover \
  --apim-name "$APIM_NAME" \
  --mode quota \
  --concurrency 5

python3.12 verify-scenarios.py \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --deployment-name multi-model-failover \
  --apim-name "$APIM_NAME" \
  --timespan PT1H
```

## 7. Check the Azure Monitor workbook

The deployment creates a shared Azure Monitor workbook named `Cost Analysis`. Open it from the Log Analytics workspace or Azure Monitor Workbooks. The workbook reads both dedicated APIM tables and legacy `AzureDiagnostics`, so it works before and after redeploying APIM diagnostic settings with dedicated table output.

If workbook panels are empty:

1. Confirm `PRICING_CL` and `SUBSCRIPTION_QUOTA_CL` have rows.
2. Run `load-test.py` and wait for telemetry ingestion.
3. Re-run `multi-model-test-query.py` to backfill FinOps rows if needed.
4. Confirm the workbook is scoped to the deployed Log Analytics workspace.

## 8. Common issues

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Deployment cannot find APIM | APIM is not in the deployment resource group or `APIM_NAME` is wrong | Create/move APIM into the target resource group or set `APIM_NAME` correctly |
| APIM backend gets authorization errors | APIM managed identity or Foundry RBAC is not ready yet | Confirm APIM system-assigned identity is enabled, wait for role assignment propagation, then retry |
| `PRICING_CL` or `SUBSCRIPTION_QUOTA_CL` has zero rows | Direct Bicep deployment does not upload custom log rows, or ingestion is still delayed | Run `multi-model-test-query.py` and wait a few minutes |
| No cost data | No APIM LLM logs yet, or pricing/quota tables are empty | Run `load-test.py`, wait for ingestion, then run `multi-model-test-query.py` |
| No model deployments in Foundry | Model not available or quota missing in selected region | Adjust `models_config`/`aiservices_config` and redeploy |
| Python syntax error near `match` | Python older than 3.12 | Run with `python3.12` |

