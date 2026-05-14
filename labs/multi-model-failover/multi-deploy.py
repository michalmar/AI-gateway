# %% [markdown]
# # APIM ❤️ AI Foundry
# 
# ## Multi-Model Failover lab
# 
# This lab demonstrates automatic failover between different AI models using Azure API Management with priority-based routing, retry policies with exponential backoff, circuit breaker patterns, built-in LLM logging, FinOps cost controls, and Microsoft Agent Framework (MAF) agent testing.
# 
# ### Key Features
# - **Backend pool** with priority-based routing across gpt-4.1-nano (primary), gpt-5.2 (secondary), and gpt-4.1 (tertiary)
# - **Retry policy** with exponential backoff for 429/503 errors
# - **Circuit breaker** to temporarily remove unhealthy backends
# - **Built-in LLM logging** to track usage across all backends
# - **FinOps framework** with per-product token rate limiting and cost quotas
# - **MAF agent testing** through the APIM gateway
# - **Three APIM products** (Finance, Marketing, HR) with dedicated subscriptions
# 
# ### Prerequisites
# 
# - [Python 3.12 or later version](https://www.python.org/) installed
# - [VS Code](https://code.visualstudio.com/) installed with the [Jupyter notebook extension](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.jupyter) enabled
# - [Python environment](https://code.visualstudio.com/docs/python/environments#_creating-environments) with the [requirements.txt](../../../requirements.txt) or run `pip install -r requirements.txt` in your terminal
# - [An Azure Subscription](https://azure.microsoft.com/free/) with [Contributor](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged#contributor) + [RBAC Administrator](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged#role-based-access-control-administrator) or [Owner](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged#owner) roles
# - [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) installed and [Signed into your Azure subscription](https://learn.microsoft.com/cli/azure/authenticate-azure-cli-interactively)
# 
# ▶️ Click `Run All` to execute all steps sequentially, or execute them `Step by Step`...

# %% [markdown]
# <a id='0'></a>
# ### 0️⃣ Initialize notebook variables
# 
# - Resources will be suffixed by a unique string based on your subscription id.
# - Adjust the location parameters according your preferences and on the [product availability by Azure region.](https://azure.microsoft.com/explore/global-infrastructure/products-by-region/?cdn=disable&products=cognitive-services,api-management) 
# - Adjust the OpenAI model and version according the [availability by region.](https://learn.microsoft.com/azure/ai-services/openai/concepts/models)

# %%
import os, sys, json
sys.path.insert(1, '../../shared')  # add the shared directory to the Python path
import utils

deployment_name = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
resource_group_name = f"lab-{deployment_name}"
resource_group_location = "westeurope"

# Existing APIM instance (must have system-assigned managed identity enabled)
apim_name = os.environ.get("APIM_NAME", "")

# AI Services - two Foundry accounts created by Bicep for failover diversity
aiservices_config = [{"name": "foundry1", "location": "swedencentral", "priority": 1},
                     {"name": "foundry2", "location": "eastus2", "priority": 2}]

# Models - deployed on both Foundries with aiservice targeting
# For PTU: change sku to "ProvisionedManaged" on the PTU foundry entries
models_config = [
    {"name": "gpt-4.1-nano", "aiservice": "foundry1", "publisher": "OpenAI", "version": "2025-04-14", "sku": "GlobalStandard", "capacity": 200,
     "inputTokensMeterSku": "gpt 4.1 nano Inp glbl", "outputTokensMeterSku": "gpt 4.1 nano Outp glbl"},
    {"name": "gpt-4.1-nano", "aiservice": "foundry2", "publisher": "OpenAI", "version": "2025-04-14", "sku": "GlobalStandard", "capacity": 200,
     "inputTokensMeterSku": "gpt 4.1 nano Inp glbl", "outputTokensMeterSku": "gpt 4.1 nano Outp glbl"},
    {"name": "gpt-5.2", "aiservice": "foundry1", "publisher": "OpenAI", "version": "2025-12-11", "sku": "GlobalStandard", "capacity": 200,
     "inputTokensMeterSku": "gpt 5 pro inp glbl", "outputTokensMeterSku": "gpt 5 pro out glbl"},
    {"name": "gpt-5.2", "aiservice": "foundry2", "publisher": "OpenAI", "version": "2025-12-11", "sku": "GlobalStandard", "capacity": 200,
     "inputTokensMeterSku": "gpt 5 pro inp glbl", "outputTokensMeterSku": "gpt 5 pro out glbl"},
    {"name": "gpt-4.1", "aiservice": "foundry1", "publisher": "OpenAI", "version": "2025-04-14", "sku": "GlobalStandard", "capacity": 200,
     "inputTokensMeterSku": "gpt 4.1 Inp glbl", "outputTokensMeterSku": "gpt 4.1 Outp glbl"},
    {"name": "gpt-4.1", "aiservice": "foundry2", "publisher": "OpenAI", "version": "2025-04-14", "sku": "GlobalStandard", "capacity": 200,
     "inputTokensMeterSku": "gpt 4.1 Inp glbl", "outputTokensMeterSku": "gpt 4.1 Outp glbl"}
]

# Products with per-product token rate limits and cost quotas
apim_products_config = [
    {"name": "finance", "displayName": "Finance Product", "tpm": 2000, "tokenQuota": 1500000, "tokenQuotaPeriod": "Monthly", "costQuota": 20},
    {"name": "marketing", "displayName": "Marketing Product", "tpm": 1000, "tokenQuota": 1000000, "tokenQuotaPeriod": "Monthly", "costQuota": 10},
    {"name": "hr", "displayName": "HR Product", "tpm": 500, "tokenQuota": 500000, "tokenQuotaPeriod": "Monthly", "costQuota": 5}
]

# Product-scoped subscriptions
apim_subscriptions_config = [
    {"name": "subscription-finance", "displayName": "Finance Subscription", "product": "finance"},
    {"name": "subscription-marketing", "displayName": "Marketing Subscription", "product": "marketing"},
    {"name": "subscription-hr", "displayName": "HR Subscription", "product": "hr"}
]

inference_api_path = "inference"
inference_api_type = "AzureOpenAI"
inference_api_version = "2025-03-01-preview"
foundry_project_name = deployment_name

currency_code = 'USD'

utils.print_ok('Notebook initialized')

DEPLOY = False
if (DEPLOY):

    print(f"Deployment Name: {deployment_name}")
    output = utils.run("az account show", "Retrieved az account", "Failed to get the current az account")

    if output.success and output.json_data:
        current_user = output.json_data['user']['name']
        tenant_id = output.json_data['tenantId']
        subscription_id = output.json_data['id']

        utils.print_info(f"Current user: {current_user}")
        utils.print_info(f"Tenant ID: {tenant_id}")
        utils.print_info(f"Subscription ID: {subscription_id}")

    utils.create_resource_group(resource_group_name, resource_group_location)

    if not apim_name:
        utils.print_error("Set APIM_NAME to the existing APIM instance name before deployment.")
        sys.exit(1)

    # Define the Bicep parameters
    bicep_parameters = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "apimName": { "value": apim_name },
            "aiServicesConfig": { "value": aiservices_config },
            "modelsConfig": { "value": models_config },
            "apimSubscriptionsConfig": { "value": apim_subscriptions_config },
            "apimProductsConfig": { "value": apim_products_config },
            "inferenceAPIPath": { "value": inference_api_path },
            "inferenceAPIType": { "value": inference_api_type },
            "foundryProjectName": { "value": foundry_project_name }
        }
    }

    # Write the parameters to the params.json file
    with open('params.json', 'w') as bicep_parameters_file:
        bicep_parameters_file.write(json.dumps(bicep_parameters))

    # Run the deployment
    output = utils.run(f"az deployment group create --name {deployment_name} --resource-group {resource_group_name} --template-file main.bicep --parameters params.json",
        f"Deployment '{deployment_name}' succeeded", f"Deployment '{deployment_name}' failed")

    # Obtain all of the outputs from the deployment
    output = utils.run(f"az deployment group show --name {deployment_name} -g {resource_group_name}", f"Retrieved deployment: {deployment_name}", f"Failed to retrieve deployment: {deployment_name}")

    if output.success and output.json_data:
        apim_resource_gateway_url = utils.get_deployment_output(output, 'apimResourceGatewayURL', 'APIM API Gateway URL')
        app_insights_name = utils.get_deployment_output(output, 'appInsightsName', 'App Insights Name')
        foundry_project_endpoint = utils.get_deployment_output(output, 'foundryProjectEndpoint', 'Foundry Project Endpoint')
        pricing_dcr_endpoint = utils.get_deployment_output(output, 'pricingDCREndpoint', 'Pricing DCR Endpoint')
        pricing_dcr_immutable_id = utils.get_deployment_output(output, 'pricingDCRImmutableId', 'Pricing DCR ImmutableId')
        pricing_dcr_stream = utils.get_deployment_output(output, 'pricingDCRStream', 'Pricing DCR Stream')
        subscription_quota_dcr_endpoint = utils.get_deployment_output(output, 'subscriptionQuotaDCREndpoint', 'Subscription Quota DCR Endpoint')
        subscription_quota_dcr_immutable_id = utils.get_deployment_output(output, 'subscriptionQuotaDCRImmutableId', 'Subscription Quota DCR ImmutableId')
        subscription_quota_dcr_stream = utils.get_deployment_output(output, 'subscriptionQuotaDCRStream', 'Subscription Quota DCR Stream')

        apim_subscriptions = json.loads(utils.get_deployment_output(output, 'apimSubscriptions').replace("'", "\""))
        for subscription in apim_subscriptions:
            subscription_name = subscription['name']
            subscription_key = subscription['key']
            utils.print_info(f"Subscription Name: {subscription_name}")
            utils.print_info(f"Subscription Key: ****{subscription_key[-4:]}")




app_insights_name = "insights-g3mjvytixvcoc"

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
output = utils.run(f'az monitor app-insights query --app {app_insights_name} -g {resource_group_name} --analytics-query "{query}"',
    f"App Insights query succeeded", f"App Insights query failed")

if output.success and output.json_data:
    table = output.json_data['tables'][0]
    df = pd.DataFrame(table.get("rows"), columns = [col.get("name") for col in table.get('columns')])
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime('%H:%M')
    df.head()

    # import matplotlib.pyplot as plt
    # import matplotlib as mpl
    # mpl.rcParams['figure.figsize'] = [15, 7]
    # if df.empty:
    #     print("No data to plot")
    # else:
    #     df_pivot = df.pivot(index='timestamp', columns='apimSubscription', values='TotalValue')
    #     ax = df_pivot.plot(kind='bar', stacked=True)
    #     plt.title('Total token usage over time by APIM Subscription')
    #     plt.xlabel('Time')
    #     plt.ylabel('Tokens')
    #     plt.legend(title='APIM Subscription')
    #     plt.show()

# %% [markdown]
# ### 📊 Query Azure Monitor for cost logs
#
# This query joins LLM gateway logs with the pricing table and subscription quota table
# to calculate per-subscription costs over time — matching the Cost Analysis workbook (query-1).
# Uses the Log Analytics REST API directly for reliable query execution.

# %%
log_analytics_workspace_id = "f8839ea4-23f3-41cf-a7c3-c8512d2463e6"

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

print("\n📊 Running cost analysis query against Log Analytics...")
import subprocess
rest_cmd = (
    f'az rest --method post '
    f'--url "https://api.loganalytics.io/v1/workspaces/{log_analytics_workspace_id}/query" '
    f'--headers "Content-Type=application/json" '
    f'--resource "https://api.loganalytics.io" '
    f'--body @-'
)

query_body = json.dumps({"query": cost_query, "timespan": "P30D"})
result = subprocess.run(rest_cmd, shell=True, input=query_body, capture_output=True, text=True)

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
