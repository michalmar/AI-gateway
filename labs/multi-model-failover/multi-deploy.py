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
if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12 or later is required. Run: python3.12 multi-deploy.py")

sys.path.insert(1, '../../shared')  # add the shared directory to the Python path
import utils


def print_deployment_failure_details(deployment_name, resource_group_name):
    failed_operations_query = "[?properties.provisioningState=='Failed'].{resource:properties.targetResource.resourceName,type:properties.targetResource.resourceType,state:properties.provisioningState,error:properties.statusMessage.error.message}"
    utils.run(
        f'az deployment operation group list --resource-group {resource_group_name} --name {deployment_name} --query "{failed_operations_query}" -o table',
        "Retrieved failed deployment operations",
        "Failed to retrieve deployment operation details",
        print_output=True
    )


def ensure_deployment_has_outputs(output, deployment_name, resource_group_name):
    if not output.success or not isinstance(output.json_data, dict) or not output.json_data:
        utils.print_error(f"Deployment '{deployment_name}' could not be retrieved.")
        sys.exit(1)

    properties = output.json_data.get('properties')
    if not isinstance(properties, dict):
        utils.print_error(f"Deployment '{deployment_name}' response does not contain properties.")
        sys.exit(1)

    provisioning_state = properties.get('provisioningState', 'Unknown')
    if provisioning_state != 'Succeeded':
        utils.print_error(f"Deployment '{deployment_name}' is '{provisioning_state}', not 'Succeeded'.")
        print_deployment_failure_details(deployment_name, resource_group_name)
        sys.exit(1)

    outputs = properties.get('outputs')
    if not isinstance(outputs, dict) or not outputs:
        utils.print_error(f"Deployment '{deployment_name}' succeeded but returned no outputs.")
        print_deployment_failure_details(deployment_name, resource_group_name)
        sys.exit(1)


deployment_name = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
project_name = os.environ.get("PROJECT_NAME", "ict-apim")
subproject_name = os.environ.get("SUBPROJECT_NAME", deployment_name)
dcr_subproject_name = os.environ.get("DCR_SUBPROJECT_NAME", "mf")
resource_number = os.environ.get("RESOURCE_NUMBER", "001")
secondary_resource_number = os.environ.get("SECONDARY_RESOURCE_NUMBER", "002")
tenant_name = os.environ.get("TENANT_NAME", "mpsvcrtest")



resource_group_name = "rg-aig-mpsv" # f"rg-{project_name}-{subproject_name}-{resource_number}-{tenant_name}"
resource_group_location = "westeurope"

# Existing APIM instance (must have system-assigned managed identity enabled)
apim_name = os.environ.get("APIM_NAME", f"apim-aig-mpsv-test1")

# AI Services - two Foundry accounts created by Bicep for failover diversity
aiservices_config = [{"name": "foundry1", "location": "westeurope", "priority": 1, "resourceNumber": resource_number},
                     {"name": "foundry2", "location": "swedencentral", "priority": 2, "resourceNumber": secondary_resource_number}]

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

currency_code = 'USD'

utils.print_ok('Notebook initialized')

DEPLOY = True
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
            "projectName": { "value": project_name },
            "subprojectName": { "value": subproject_name },
            "dcrSubprojectName": { "value": dcr_subproject_name },
            "resourceNumber": { "value": resource_number },
            "secondaryResourceNumber": { "value": secondary_resource_number },
            "tenantName": { "value": tenant_name },
            "apimName": { "value": apim_name },
            "aiServicesConfig": { "value": aiservices_config },
            "modelsConfig": { "value": models_config },
            "apimSubscriptionsConfig": { "value": apim_subscriptions_config },
            "apimProductsConfig": { "value": apim_products_config },
            "inferenceAPIPath": { "value": inference_api_path },
            "inferenceAPIType": { "value": inference_api_type }
        }
    }

    # Write the parameters to the params.json file
    with open('params.json', 'w') as bicep_parameters_file:
        bicep_parameters_file.write(json.dumps(bicep_parameters))

    # Run the deployment
    output = utils.run(f"az deployment group create --name {deployment_name} --resource-group {resource_group_name} --template-file main.bicep --parameters params.json",
        f"Deployment '{deployment_name}' succeeded", f"Deployment '{deployment_name}' failed")
    if not output.success:
        print_deployment_failure_details(deployment_name, resource_group_name)
        sys.exit(1)

    # Obtain all of the outputs from the deployment
    output = utils.run(f"az deployment group show --name {deployment_name} -g {resource_group_name}", f"Retrieved deployment: {deployment_name}", f"Failed to retrieve deployment: {deployment_name}")
    ensure_deployment_has_outputs(output, deployment_name, resource_group_name)

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
