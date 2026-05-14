# %% [markdown]
# # Multi-Model Failover telemetry queries
#
# Queries Application Insights and Log Analytics after the lab deployment.

# %%
import json
import os
import subprocess
import sys

if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12 or later is required. Run: python3.12 multi-model-test-query.py")

sys.path.insert(1, '../../shared')  # add the shared directory to the Python path
import utils


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


def get_output_value(outputs, name, default=''):
    output = outputs.get(name, {})
    return output.get('value', default) if isinstance(output, dict) else default


deployment_name = os.environ.get("DEPLOYMENT_NAME", os.path.basename(os.path.dirname(os.path.abspath(__file__))))
project_name = os.environ.get("PROJECT_NAME", "ict-apim")
subproject_name = os.environ.get("SUBPROJECT_NAME", deployment_name)
resource_number = os.environ.get("RESOURCE_NUMBER", "001")
tenant_name = os.environ.get("TENANT_NAME", "mpsvcrtest")

resource_group_name = os.environ.get("RESOURCE_GROUP_NAME", "rg-aig-mpsv")
app_insights_name = os.environ.get("APP_INSIGHTS_NAME", f"appi-{project_name}-{subproject_name}-{resource_number}-{tenant_name}")
log_analytics_workspace_id = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID", "")

deployment_outputs = get_deployment_outputs(deployment_name, resource_group_name)
app_insights_name = get_output_value(deployment_outputs, "appInsightsName", app_insights_name)
log_analytics_workspace_id = get_output_value(deployment_outputs, "logAnalyticsWorkspaceId", log_analytics_workspace_id)

if not log_analytics_workspace_id:
    log_analytics_name = os.environ.get("LOG_ANALYTICS_NAME", f"log-{project_name}-{subproject_name}-{resource_number}-{tenant_name}")
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

cost_query = (
    "let llmHeaderLogs = ApiManagementGatewayLlmLog "
    "| where DeploymentName != ''; "
    "let llmLogsWithSubscriptionId = llmHeaderLogs "
    "| join kind=leftouter ApiManagementGatewayLogs on CorrelationId "
    "| project "
    "    TimeGenerated, SubscriptionName = ApimSubscriptionId, DeploymentName, PromptTokens, CompletionTokens, TotalTokens; "
    "llmLogsWithSubscriptionId "
    "| join kind=inner ( "
    "    PRICING_CL "
    "    | summarize arg_max(TimeGenerated, *) by Model "
    "    | project Model, InputTokensPrice = coalesce(InputTokensPrice, 0.0), OutputTokensPrice = coalesce(OutputTokensPrice, 0.0) "
    "    ) "
    "    on $left.DeploymentName == $right.Model "
    "| extend InputCost = PromptTokens * InputTokensPrice "
    "| extend OutputCost = CompletionTokens * OutputTokensPrice "
    "| summarize "
    "    InputCost = sum(InputCost), OutputCost = sum(OutputCost) "
    "    by SubscriptionName, bin(TimeGenerated, 1m) "
    "| extend TotalCost = (InputCost + OutputCost) / 1000 "
    "| project TimeGenerated, SubscriptionName, TotalCost"
)

print("\nRunning cost analysis query against Log Analytics...")
rest_cmd = (
    f'az rest --method post '
    f'--url "https://api.loganalytics.io/v1/workspaces/{log_analytics_workspace_id}/query" '
    f'--headers "Content-Type=application/json" '
    f'--resource "https://api.loganalytics.io" '
    f'--body @-'
)

query_body = json.dumps({"query": cost_query, "timespan": "P30D"})
result = subprocess.run(
    rest_cmd,
    shell=True,
    input=query_body,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)

if result.returncode == 0:
    response_data = json.loads(result.stdout)
    table = response_data['tables'][0]
    cols = [col.get("name") for col in table.get('columns')]
    rows = table.get("rows")
    df_cost = pd.DataFrame(rows, columns=cols)
    if df_cost.empty:
        print("No cost data available yet. Ensure pricing data has been loaded and requests have been made.")
    else:
        df_cost['TimeGenerated'] = pd.to_datetime(df_cost['TimeGenerated']).dt.strftime('%Y-%m-%d %H:%M')
        print(f"\n{'Time':<20} {'Subscription':<30} {'Total Cost ($)':<15}")
        print(f"{'-'*20} {'-'*30} {'-'*15}")
        for _, row in df_cost.iterrows():
            print(f"{row['TimeGenerated']:<20} {row['SubscriptionName']:<30} ${row['TotalCost']:<14.6f}")
        print(f"\n--- Summary ---")
        summary = df_cost.groupby('SubscriptionName')['TotalCost'].sum().reset_index()
        for _, row in summary.iterrows():
            print(f"  {row['SubscriptionName']:<30} Total: ${row['TotalCost']:.6f}")
    utils.print_ok("Cost analysis query succeeded")
else:
    utils.print_error(f"Cost analysis query failed: {result.stderr}")
