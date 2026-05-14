# %% [markdown]
# # Multi-Model Failover telemetry queries
#
# Queries Application Insights and Log Analytics after the lab deployment.

# %%
import json
import argparse
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12 or later is required. Run: python3.12 multi-model-test-query.py")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
default_deployment_name = os.path.basename(SCRIPT_DIR)
parser = argparse.ArgumentParser(description="Run Multi-Model Failover telemetry queries")
parser.add_argument("--deployment-name", default=os.environ.get("DEPLOYMENT_NAME", default_deployment_name),
                    help=f"Azure deployment name to read outputs from (default: {default_deployment_name})")
parser.add_argument("--resource-group", default=os.environ.get("RESOURCE_GROUP_NAME") or os.environ.get("RESOURCE_GROUP", ""),
                    help="Resource group containing the deployment/resources")
parser.add_argument("--app-insights-name", default=os.environ.get("APP_INSIGHTS_NAME", ""),
                    help="Application Insights name")
parser.add_argument("--log-analytics-workspace-id", default=os.environ.get("LOG_ANALYTICS_WORKSPACE_ID", ""),
                    help="Log Analytics workspace customer ID")
parser.add_argument("--log-analytics-name", default=os.environ.get("LOG_ANALYTICS_NAME", ""),
                    help="Log Analytics workspace resource name")
parser.add_argument("--currency-code", default=os.environ.get("CURRENCY_CODE", "USD"),
                    help="Currency code for Azure Retail Prices API (default: USD)")
args = parser.parse_args()

sys.path.insert(1, os.path.join(SCRIPT_DIR, '../../shared'))  # add the shared directory to the Python path
try:
    import utils
except ModuleNotFoundError as exc:
    raise SystemExit(
        f"Missing Python dependency: {exc.name}. Install lab dependencies with: pip install -r ../../requirements.txt"
    )


DEFAULT_AISERVICES_CONFIG = [
    {"name": "foundry1", "location": "westeurope"},
    {"name": "foundry2", "location": "swedencentral"},
]

DEFAULT_MODELS_CONFIG = [
    {"name": "gpt-4.1-nano", "aiservice": "foundry1", "inputTokensMeterSku": "gpt 4.1 nano Inp glbl", "outputTokensMeterSku": "gpt 4.1 nano Outp glbl"},
    {"name": "gpt-4.1-nano", "aiservice": "foundry2", "inputTokensMeterSku": "gpt 4.1 nano Inp glbl", "outputTokensMeterSku": "gpt 4.1 nano Outp glbl"},
    {"name": "gpt-5.2", "aiservice": "foundry1", "inputTokensMeterSku": "gpt 5 pro inp glbl", "outputTokensMeterSku": "gpt 5 pro out glbl"},
    {"name": "gpt-5.2", "aiservice": "foundry2", "inputTokensMeterSku": "gpt 5 pro inp glbl", "outputTokensMeterSku": "gpt 5 pro out glbl"},
    {"name": "gpt-4.1", "aiservice": "foundry1", "inputTokensMeterSku": "gpt 4.1 Inp glbl", "outputTokensMeterSku": "gpt 4.1 Outp glbl"},
    {"name": "gpt-4.1", "aiservice": "foundry2", "inputTokensMeterSku": "gpt 4.1 Inp glbl", "outputTokensMeterSku": "gpt 4.1 Outp glbl"},
]

DEFAULT_APIM_SUBSCRIPTIONS_CONFIG = [
    {"name": "subscription-finance", "displayName": "Finance Subscription", "product": "finance"},
    {"name": "subscription-marketing", "displayName": "Marketing Subscription", "product": "marketing"},
    {"name": "subscription-hr", "displayName": "HR Subscription", "product": "hr"},
]

DEFAULT_APIM_PRODUCTS_CONFIG = [
    {"name": "finance", "displayName": "Finance Product", "tpm": 2000, "tokenQuota": 1500000, "tokenQuotaPeriod": "Monthly", "costQuota": 20},
    {"name": "marketing", "displayName": "Marketing Product", "tpm": 1000, "tokenQuota": 1000000, "tokenQuotaPeriod": "Monthly", "costQuota": 10},
    {"name": "hr", "displayName": "HR Product", "tpm": 500, "tokenQuota": 500000, "tokenQuotaPeriod": "Monthly", "costQuota": 5},
]


def get_deployment_outputs(deployment_name, resource_group_name):
    output = utils.run(
        f"az deployment group show --name {deployment_name} -g {resource_group_name}",
        f"Retrieved deployment: {deployment_name}",
        f"Failed to retrieve deployment: {deployment_name}",
    )
    if not output.success or not isinstance(output.json_data, dict):
        return {}

    properties = output.json_data.get('properties')
    outputs = properties.get('outputs') if isinstance(properties, dict) else None
    return outputs if isinstance(outputs, dict) else {}


def discover_deployment_resource_group(deployment_name, current_resource_group):
    output = utils.run(
        'az group list --query "[].name" -o tsv',
        print_command_to_run=False,
    )
    if not output.success:
        return None

    for resource_group in output.text.splitlines():
        resource_group = resource_group.strip()
        if not resource_group or resource_group == current_resource_group:
            continue

        deployment = utils.run(
            f"az deployment group show --name {deployment_name} -g {resource_group} --query properties.provisioningState -o tsv",
            print_command_to_run=False,
        )
        if deployment.success:
            return resource_group

    return None


def get_output_value(outputs, name, default=''):
    output = outputs.get(name, {})
    return output.get('value', default) if isinstance(output, dict) else default


def load_lab_parameters():
    params_path = os.path.join(SCRIPT_DIR, "params.json")
    if not os.path.exists(params_path):
        return (
            DEFAULT_AISERVICES_CONFIG,
            DEFAULT_MODELS_CONFIG,
            DEFAULT_APIM_SUBSCRIPTIONS_CONFIG,
            DEFAULT_APIM_PRODUCTS_CONFIG,
        )

    try:
        with open(params_path, "r", encoding="utf-8") as params_file:
            params = json.load(params_file).get("parameters", {})
        aiservices_config = params.get("aiServicesConfig", {}).get("value") or DEFAULT_AISERVICES_CONFIG
        models_config = params.get("modelsConfig", {}).get("value") or DEFAULT_MODELS_CONFIG
        subscriptions_config = params.get("apimSubscriptionsConfig", {}).get("value") or DEFAULT_APIM_SUBSCRIPTIONS_CONFIG
        products_config = params.get("apimProductsConfig", {}).get("value") or DEFAULT_APIM_PRODUCTS_CONFIG
        return aiservices_config, models_config, subscriptions_config, products_config
    except (OSError, json.JSONDecodeError) as exc:
        utils.print_error(f"Failed to read params.json, using defaults: {exc}")
        return (
            DEFAULT_AISERVICES_CONFIG,
            DEFAULT_MODELS_CONFIG,
            DEFAULT_APIM_SUBSCRIPTIONS_CONFIG,
            DEFAULT_APIM_PRODUCTS_CONFIG,
        )


def run_log_analytics_query(workspace_id, query, timespan="P30D"):
    query_body = json.dumps({"query": query, "timespan": timespan})
    result = subprocess.run(
        [
            "az", "rest", "--method", "post",
            "--url", f"https://api.loganalytics.io/v1/workspaces/{workspace_id}/query",
            "--headers", "Content-Type=application/json",
            "--resource", "https://api.loganalytics.io",
            "--body", "@-",
        ],
        input=query_body,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None, result.stderr or result.stdout

    try:
        response_data = json.loads(result.stdout)
        table = response_data.get("tables", [])[0]
        cols = [col.get("name") for col in table.get("columns", [])]
        rows = table.get("rows", [])
        return pd.DataFrame(rows, columns=cols), None
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        return None, f"Failed to parse Log Analytics response: {exc}"


def get_query_count(workspace_id, query):
    df, error = run_log_analytics_query(workspace_id, query)
    if error or df is None or df.empty:
        return 0
    first_value = df.iloc[0, 0]
    try:
        return int(first_value)
    except (TypeError, ValueError):
        return 0


def fetch_retail_prices(location, currency_code):
    filter_expression = (
        "serviceName eq 'Foundry Models' "
        "and unitOfMeasure eq '1K' "
        f"and armRegionName eq '{location}'"
    )
    url = "https://prices.azure.com/api/retail/prices?" + urllib.parse.urlencode({
        "currencyCode": currency_code,
        "$filter": filter_expression,
    })
    items = []

    while url:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.load(response)
        items.extend(data.get("Items", []))
        url = data.get("NextPageLink")

    return items


def find_retail_price(items, sku_name):
    match = next((item for item in items if item.get("skuName") == sku_name), None)
    return None if match is None else match.get("retailPrice")


def seed_pricing_table(outputs, aiservices_config, models_config, currency_code):
    pricing_dcr_endpoint = get_output_value(outputs, "pricingDCREndpoint", "")
    pricing_dcr_immutable_id = get_output_value(outputs, "pricingDCRImmutableId", "")
    pricing_dcr_stream = get_output_value(outputs, "pricingDCRStream", "")

    if not pricing_dcr_endpoint or not pricing_dcr_immutable_id or not pricing_dcr_stream:
        utils.print_error("PRICING_CL is empty and deployment outputs do not include pricing DCR details.")
        return False

    try:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.ingestion import LogsIngestionClient
        from azure.core.exceptions import HttpResponseError
    except ModuleNotFoundError as exc:
        utils.print_error(
            f"Cannot seed PRICING_CL because dependency '{exc.name}' is missing. "
            "Install lab dependencies with: pip install -r ../../requirements.txt"
        )
        return False

    service_locations = {service.get("name"): service.get("location") for service in aiservices_config}
    default_location = next((service.get("location") for service in aiservices_config if service.get("location")), None)
    prices_by_location = {}
    records_by_model = {}

    for deployment in models_config:
        model_name = deployment.get("name")
        if not model_name or model_name in records_by_model:
            continue

        location = service_locations.get(deployment.get("aiservice")) or default_location
        if not location:
            utils.print_error(f"Cannot determine Azure region for model '{model_name}'.")
            continue

        if location not in prices_by_location:
            prices_by_location[location] = fetch_retail_prices(location, currency_code)

        input_price = find_retail_price(prices_by_location[location], deployment.get("inputTokensMeterSku"))
        output_price = find_retail_price(prices_by_location[location], deployment.get("outputTokensMeterSku"))
        if input_price is None or output_price is None:
            utils.print_error(
                f"Could not find retail prices for model '{model_name}' in '{location}' "
                f"({deployment.get('inputTokensMeterSku')} / {deployment.get('outputTokensMeterSku')})."
            )
            continue

        records_by_model[model_name] = {
            "TimeGenerated": datetime.now(timezone.utc).isoformat(),
            "Model": model_name,
            # Store per-million token prices. Cost queries divide by 1,000,000.
            "InputTokensPrice": input_price * 1000,
            "OutputTokensPrice": output_price * 1000,
        }

    records = list(records_by_model.values())
    if not records:
        utils.print_error("No pricing records were prepared for ingestion.")
        return False

    client = LogsIngestionClient(
        endpoint=pricing_dcr_endpoint,
        credential=DefaultAzureCredential(),
        logging_enable=False,
    )
    try:
        client.upload(rule_id=pricing_dcr_immutable_id, stream_name=pricing_dcr_stream, logs=records)
        utils.print_ok(f"Uploaded {len(records)} pricing records to PRICING_CL")
        return True
    except HttpResponseError as exc:
        utils.print_error(f"Pricing upload failed: {exc}")
        return False


def seed_subscription_quota_table(outputs, subscriptions_config, products_config):
    quota_dcr_endpoint = get_output_value(outputs, "subscriptionQuotaDCREndpoint", "")
    quota_dcr_immutable_id = get_output_value(outputs, "subscriptionQuotaDCRImmutableId", "")
    quota_dcr_stream = get_output_value(outputs, "subscriptionQuotaDCRStream", "")

    if not quota_dcr_endpoint or not quota_dcr_immutable_id or not quota_dcr_stream:
        utils.print_error("SUBSCRIPTION_QUOTA_CL is empty and deployment outputs do not include subscription quota DCR details.")
        return False

    try:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.ingestion import LogsIngestionClient
        from azure.core.exceptions import HttpResponseError
    except ModuleNotFoundError as exc:
        utils.print_error(
            f"Cannot seed SUBSCRIPTION_QUOTA_CL because dependency '{exc.name}' is missing. "
            "Install lab dependencies with: pip install -r ../../requirements.txt"
        )
        return False

    products_by_name = {product.get("name"): product for product in products_config}
    records = []
    for subscription in subscriptions_config:
        product = products_by_name.get(subscription.get("product"))
        if product is None:
            utils.print_error(f"Could not find product quota for subscription '{subscription.get('name')}'.")
            continue

        records.append({
            "TimeGenerated": datetime.now(timezone.utc).isoformat(),
            "Subscription": subscription.get("name"),
            "CostQuota": product.get("costQuota", 0),
        })

    if not records:
        utils.print_error("No subscription quota records were prepared for ingestion.")
        return False

    client = LogsIngestionClient(
        endpoint=quota_dcr_endpoint,
        credential=DefaultAzureCredential(),
        logging_enable=False,
    )
    try:
        client.upload(rule_id=quota_dcr_immutable_id, stream_name=quota_dcr_stream, logs=records)
        utils.print_ok(f"Uploaded {len(records)} subscription quota records to SUBSCRIPTION_QUOTA_CL")
        return True
    except HttpResponseError as exc:
        utils.print_error(f"Subscription quota upload failed: {exc}")
        return False


def wait_for_table_rows(workspace_id, table_name):
    for _ in range(30):
        if get_query_count(workspace_id, f"{table_name} | count") > 0:
            return True
        time.sleep(10)
    return False


def get_apim_log_source(workspace_id):
    dedicated_count = get_query_count(
        workspace_id,
        "ApiManagementGatewayLlmLog | where TimeGenerated > ago(30d) | where isnotempty(DeploymentName) and TotalTokens > 0 | count",
    )
    if dedicated_count > 0:
        return "Dedicated"

    legacy_count = get_query_count(
        workspace_id,
        "AzureDiagnostics | where TimeGenerated > ago(30d) | where Category == 'GatewayLlmLogs' | where isnotempty(deploymentName_s) and totalTokens_d > 0 | count",
    )
    if legacy_count > 0:
        return "AzureDiagnostics"

    return None


def build_cost_query(log_source):
    if log_source == "AzureDiagnostics":
        return """
let llmHeaderLogs = AzureDiagnostics
| where TimeGenerated > ago(30d)
| where Category == 'GatewayLlmLogs'
| where isnotempty(deploymentName_s) and totalTokens_d > 0
| project
    TimeGenerated,
    CorrelationId,
    DeploymentName = deploymentName_s,
    PromptTokens = toreal(promptTokens_d),
    CompletionTokens = toreal(completionTokens_d),
    TotalTokens = toreal(totalTokens_d);
let gatewayLogs = AzureDiagnostics
| where TimeGenerated > ago(30d)
| where Category == 'GatewayLogs'
| summarize SubscriptionName = take_any(apimSubscriptionId_s) by CorrelationId;
llmHeaderLogs
| join kind=leftouter gatewayLogs on CorrelationId
| project TimeGenerated, SubscriptionName = coalesce(SubscriptionName, 'unknown'), DeploymentName, PromptTokens, CompletionTokens, TotalTokens
| join kind=inner (
    PRICING_CL
    | summarize arg_max(TimeGenerated, *) by Model
    | project Model, InputTokensPrice = toreal(InputTokensPrice), OutputTokensPrice = toreal(OutputTokensPrice)
) on $left.DeploymentName == $right.Model
| extend InputCost = PromptTokens * InputTokensPrice
| extend OutputCost = CompletionTokens * OutputTokensPrice
| summarize InputCost = sum(InputCost), OutputCost = sum(OutputCost) by SubscriptionName, bin(TimeGenerated, 1m)
| extend TotalCost = (InputCost + OutputCost) / 1000000.0
| project TimeGenerated, SubscriptionName, TotalCost
| order by TimeGenerated asc
"""

    return """
let llmHeaderLogs = ApiManagementGatewayLlmLog
| where TimeGenerated > ago(30d)
| where isnotempty(DeploymentName) and TotalTokens > 0;
let gatewayLogs = ApiManagementGatewayLogs
| where TimeGenerated > ago(30d)
| summarize SubscriptionName = take_any(ApimSubscriptionId) by CorrelationId;
llmHeaderLogs
| join kind=leftouter gatewayLogs on CorrelationId
| project TimeGenerated, SubscriptionName = coalesce(SubscriptionName, 'unknown'), DeploymentName, PromptTokens, CompletionTokens, TotalTokens
| join kind=inner (
    PRICING_CL
    | summarize arg_max(TimeGenerated, *) by Model
    | project Model, InputTokensPrice = toreal(InputTokensPrice), OutputTokensPrice = toreal(OutputTokensPrice)
) on $left.DeploymentName == $right.Model
| extend InputCost = PromptTokens * InputTokensPrice
| extend OutputCost = CompletionTokens * OutputTokensPrice
| summarize InputCost = sum(InputCost), OutputCost = sum(OutputCost) by SubscriptionName, bin(TimeGenerated, 1m)
| extend TotalCost = (InputCost + OutputCost) / 1000000.0
| project TimeGenerated, SubscriptionName, TotalCost
| order by TimeGenerated asc
"""


deployment_name = args.deployment_name
project_name = os.environ.get("PROJECT_NAME", "ict-apim")
subproject_name = os.environ.get("SUBPROJECT_NAME", deployment_name)
resource_number = os.environ.get("RESOURCE_NUMBER", "001")
tenant_name = os.environ.get("TENANT_NAME", "mpsvcrtest")

resource_group_name = args.resource_group or f"rg-{project_name}-{subproject_name}-{resource_number}-{tenant_name}"
app_insights_name = args.app_insights_name or f"appi-{project_name}-{subproject_name}-{resource_number}-{tenant_name}"
log_analytics_workspace_id = args.log_analytics_workspace_id
aiservices_config, models_config, apim_subscriptions_config, apim_products_config = load_lab_parameters()

deployment_outputs = get_deployment_outputs(deployment_name, resource_group_name)
if not deployment_outputs:
    discovered_resource_group = discover_deployment_resource_group(deployment_name, resource_group_name)
    if discovered_resource_group:
        utils.print_info(f"Found deployment '{deployment_name}' in resource group '{discovered_resource_group}'")
        resource_group_name = discovered_resource_group
        deployment_outputs = get_deployment_outputs(deployment_name, resource_group_name)

app_insights_name = get_output_value(deployment_outputs, "appInsightsName", app_insights_name)
log_analytics_workspace_id = get_output_value(deployment_outputs, "logAnalyticsWorkspaceId", log_analytics_workspace_id)

if not log_analytics_workspace_id:
    log_analytics_name = args.log_analytics_name or f"log-{project_name}-{subproject_name}-{resource_number}-{tenant_name}"
    output = utils.run(
        f"az monitor log-analytics workspace show -g {resource_group_name} -n {log_analytics_name} --query customerId -o tsv",
        "Retrieved Log Analytics workspace ID",
        "Failed to retrieve Log Analytics workspace ID",
    )
    if output.success:
        log_analytics_workspace_id = output.text.strip()


# %% [markdown]
# ### Query Application Insights token metrics

# %%
import pandas as pd

query = (
    "customMetrics "
    "| where name == 'Total Tokens' "
    "| where timestamp >= ago(4h) "
    "| extend parsedCustomDimensions = parse_json(customDimensions) "
    "| extend apimSubscription = tostring(parsedCustomDimensions.['Subscription ID']) "
    "| extend agentID = tostring(parsedCustomDimensions.['Agent ID']) "
    "| summarize TotalValue = sum(value) by apimSubscription, bin(timestamp, 1m), agentID "
    "| order by timestamp asc"
)
print("Running the following Kusto query against App Insights:")
print(query)
output = utils.run(
    f'az monitor app-insights query --app {app_insights_name} -g {resource_group_name} --analytics-query "{query}"',
    "App Insights query succeeded",
    "App Insights query failed",
)

if output.success and output.json_data:
    table = output.json_data['tables'][0]
    df = pd.DataFrame(table.get("rows"), columns=[col.get("name") for col in table.get('columns')])
    if df.empty:
        print("No App Insights token metric data available yet.")
    else:
        df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime('%H:%M')
        print(df.to_string(index=False))


# %% [markdown]
# ### Query Azure Monitor for cost logs

# %%
if not log_analytics_workspace_id:
    utils.print_error("Missing Log Analytics workspace ID. Set LOG_ANALYTICS_WORKSPACE_ID or deploy the lab first.")
    sys.exit(1)

print("\nRunning cost analysis query against Log Analytics...")

pricing_rows = get_query_count(log_analytics_workspace_id, "PRICING_CL | count")
if pricing_rows == 0:
    utils.print_info("PRICING_CL is empty. Loading pricing data from Azure Retail Prices API...")
    if seed_pricing_table(deployment_outputs, aiservices_config, models_config, args.currency_code):
        if not wait_for_table_rows(log_analytics_workspace_id, "PRICING_CL"):
            utils.print_error("Pricing data was uploaded but is not queryable yet. Wait a minute and rerun the script.")
            sys.exit(1)
    else:
        sys.exit(1)

subscription_quota_rows = get_query_count(log_analytics_workspace_id, "SUBSCRIPTION_QUOTA_CL | count")
if subscription_quota_rows == 0:
    utils.print_info("SUBSCRIPTION_QUOTA_CL is empty. Loading subscription quota data from APIM product config...")
    if seed_subscription_quota_table(deployment_outputs, apim_subscriptions_config, apim_products_config):
        if not wait_for_table_rows(log_analytics_workspace_id, "SUBSCRIPTION_QUOTA_CL"):
            utils.print_error("Subscription quota data was uploaded but is not queryable yet. Wait a minute and rerun the script.")
            sys.exit(1)
    else:
        sys.exit(1)

log_source = get_apim_log_source(log_analytics_workspace_id)
if not log_source:
    utils.print_error(
        "No APIM LLM logs with token data were found in Log Analytics. "
        "Run load-test.py after deployment and wait for Azure Monitor ingestion."
    )
    sys.exit(1)

if log_source == "AzureDiagnostics":
    utils.print_info("Using AzureDiagnostics APIM logs. Redeploy main.bicep to switch future logs to dedicated APIM tables.")
else:
    utils.print_info("Using dedicated APIM Log Analytics tables.")

df_cost, error = run_log_analytics_query(log_analytics_workspace_id, build_cost_query(log_source), "P30D")
if error:
    utils.print_error(f"Cost analysis query failed: {error}")
elif df_cost is None or df_cost.empty:
    print("No cost data available yet. Ensure pricing data has been loaded and requests have been made.")
    utils.print_ok("Cost analysis query succeeded")
else:
    df_cost['TimeGenerated'] = pd.to_datetime(df_cost['TimeGenerated']).dt.strftime('%Y-%m-%d %H:%M')
    df_cost['TotalCost'] = pd.to_numeric(df_cost['TotalCost'], errors='coerce').fillna(0)
    print(f"\n{'Time':<20} {'Subscription':<30} {'Total Cost ($)':<15}")
    print(f"{'-'*20} {'-'*30} {'-'*15}")
    for _, row in df_cost.iterrows():
        print(f"{row['TimeGenerated']:<20} {row['SubscriptionName']:<30} ${row['TotalCost']:<14.8f}")
    print(f"\n--- Summary ---")
    summary = df_cost.groupby('SubscriptionName')['TotalCost'].sum().reset_index()
    for _, row in summary.iterrows():
        print(f"  {row['SubscriptionName']:<30} Total: ${row['TotalCost']:.8f}")
    utils.print_ok("Cost analysis query succeeded")
