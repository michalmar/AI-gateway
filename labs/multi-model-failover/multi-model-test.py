import os, sys, json
# sys.path.insert(1, '../../shared')  # add the shared directory to the Python path
# import utils

# deployment_name = os.path.basename(os.path.dirname(globals()['__vsc_ipynb_file__']))
deployment_name = "multi-model-failover"
resource_group_name = f"lab-{deployment_name}"
resource_group_location = "westeurope"

# AI Services - two regions for failover diversity
aiservices_config = [{"name": "foundry1", "location": "swedencentral", "priority": 1},
                     {"name": "foundry2", "location": "eastus2", "priority": 2}]

# Models - three models with priority-based failover
# gpt-4.1-nano (primary) -> gpt-5.2 (secondary) -> gpt-4.1 (tertiary)
models_config = [
    {"name": "gpt-4.1-nano", "publisher": "OpenAI", "version": "2025-04-14", "sku": "GlobalStandard", "capacity": 200,
     "inputTokensMeterSku": "gpt 4.1 nano Inp glbl", "outputTokensMeterSku": "gpt 4.1 nano Outp glbl"},
    {"name": "gpt-5.2", "publisher": "OpenAI", "version": "2025-06-01", "sku": "GlobalStandard", "capacity": 200,
     "inputTokensMeterSku": "gpt 5.2 Inp glbl", "outputTokensMeterSku": "gpt 5.2 Outp glbl"},
    {"name": "gpt-4.1", "publisher": "OpenAI", "version": "2025-04-14", "sku": "GlobalStandard", "capacity": 200,
     "inputTokensMeterSku": "gpt 4.1 Inp glbl", "outputTokensMeterSku": "gpt 4.1 Outp glbl"}
]

apim_sku = 'Basicv2'

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

# utils.print_ok('Notebook initialized')
print('Notebook initialized')
apim_resource_gateway_url = 'https://***.azure-api.net'
import time, random
from openai import AzureOpenAI

runs = 10
sleep_time_ms = 100

apim_subscriptions = [{'displayName': 'Finance Subscription',
  'key': '***',
  'name': 'subscription-finance'},
 {'displayName': 'Marketing Subscription',
  'key': '***',
  'name': 'subscription-marketing'},
 {'displayName': 'HR Subscription',
  'key': '***',
  'name': 'subscription-hr'}]

for i in range(runs):
    apim_subscription = random.choice(apim_subscriptions)
    openai_model = random.choice(models_config)
    client = AzureOpenAI(
        azure_endpoint = f"{apim_resource_gateway_url}/{inference_api_path}",
        api_key = apim_subscription.get("key"),
        api_version = inference_api_version
    )
    try:
        response = client.chat.completions.create(
            model = str(openai_model.get('name')),
            messages = [
                {"role": "user", "content": "Can you tell me the time, please?"}
            ],
            extra_headers = {"x-user-id": "alex"}
        )
        print(f"▶️ Run {i+1}/{runs}: [{apim_subscription.get('name')} w/ {openai_model.get('name')}] 💬 {response.choices[0].message.content}")
    except Exception as e:
        print(f"❌ Run {i+1}/{runs}: [{apim_subscription.get('name')} w/ {openai_model.get('name')}] Error: {e}")
    time.sleep(sleep_time_ms/1000)