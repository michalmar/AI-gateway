// ------------------
//    PARAMETERS
// ------------------

@description('Name of existing APIM instance (must have system-assigned managed identity enabled)')
param apimName string = 'apim-${uniqueString(subscription().id, resourceGroup().id)}'

@description('Name of the Log Analytics Workspace to create')
param lawName string = 'workspace-${uniqueString(subscription().id, resourceGroup().id)}'

@description('Location of the Log Analytics Workspace')
param lawLocation string = resourceGroup().location

@description('Name of the Application Insights instance to create')
param appInsightsName string = 'insights-${uniqueString(subscription().id, resourceGroup().id)}'

@description('Location of the Application Insights instance')
param appInsightsLocation string = resourceGroup().location

@description('AI Foundry accounts to create. Each entry: name (APIM backend id), location, priority. Optional: resourceName (Azure resource name, defaults to name-suffix).')
param aiServicesConfig array = [
  {
    name: 'foundry1'
    location: 'swedencentral'
    priority: 1
  }
  {
    name: 'foundry2'
    location: 'eastus2'
    priority: 2
  }
]

@description('Model deployments config with per-Foundry targeting via aiservice field.')
param modelsConfig array = [
  {
    name: 'gpt-4.1-nano'
    aiservice: 'foundry1'
    publisher: 'OpenAI'
    version: '2025-04-14'
    sku: 'GlobalStandard'
    capacity: 200
    inputTokensMeterSku: 'gpt 4.1 nano Inp glbl'
    outputTokensMeterSku: 'gpt 4.1 nano Outp glbl'
  }
  {
    name: 'gpt-4.1-nano'
    aiservice: 'foundry2'
    publisher: 'OpenAI'
    version: '2025-04-14'
    sku: 'GlobalStandard'
    capacity: 200
    inputTokensMeterSku: 'gpt 4.1 nano Inp glbl'
    outputTokensMeterSku: 'gpt 4.1 nano Outp glbl'
  }
  {
    name: 'gpt-5.2'
    aiservice: 'foundry1'
    publisher: 'OpenAI'
    version: '2025-12-11'
    sku: 'GlobalStandard'
    capacity: 200
    inputTokensMeterSku: 'gpt 5.2 Inp glbl'
    outputTokensMeterSku: 'gpt 5.2 Outp glbl'
  }
  {
    name: 'gpt-5.2'
    aiservice: 'foundry2'
    publisher: 'OpenAI'
    version: '2025-12-11'
    sku: 'GlobalStandard'
    capacity: 200
    inputTokensMeterSku: 'gpt 5.2 Inp glbl'
    outputTokensMeterSku: 'gpt 5.2 Outp glbl'
  }
  {
    name: 'gpt-4.1'
    aiservice: 'foundry1'
    publisher: 'OpenAI'
    version: '2025-04-14'
    sku: 'GlobalStandard'
    capacity: 200
    inputTokensMeterSku: 'gpt 4.1 Inp glbl'
    outputTokensMeterSku: 'gpt 4.1 Outp glbl'
  }
  {
    name: 'gpt-4.1'
    aiservice: 'foundry2'
    publisher: 'OpenAI'
    version: '2025-04-14'
    sku: 'GlobalStandard'
    capacity: 200
    inputTokensMeterSku: 'gpt 4.1 Inp glbl'
    outputTokensMeterSku: 'gpt 4.1 Outp glbl'
  }
]

param apimSubscriptionsConfig array = [
  {
    name: 'subscription-finance'
    displayName: 'Finance Subscription'
    product: 'finance'
  }
  {
    name: 'subscription-marketing'
    displayName: 'Marketing Subscription'
    product: 'marketing'
  }
  {
    name: 'subscription-hr'
    displayName: 'HR Subscription'
    product: 'hr'
  }
]

param apimProductsConfig array = [
  {
    name: 'finance'
    displayName: 'Finance Product'
    tpm: 2000
    tokenQuota: 1500000
    tokenQuotaPeriod: 'Monthly'
    costQuota: 20
  }
  {
    name: 'marketing'
    displayName: 'Marketing Product'
    tpm: 1000
    tokenQuota: 1000000
    tokenQuotaPeriod: 'Monthly'
    costQuota: 10
  }
  {
    name: 'hr'
    displayName: 'HR Product'
    tpm: 500
    tokenQuota: 500000
    tokenQuotaPeriod: 'Monthly'
    costQuota: 5
  }
]
param inferenceAPIType string = 'AzureOpenAI'
param inferenceAPIPath string = 'inference'
param foundryProjectName string = 'multi-model-failover'

// ------------------
//    VARIABLES
// ------------------
var resourceSuffix = uniqueString(subscription().id, resourceGroup().id)

// ------------------
//    EXISTING RESOURCES
// ------------------
// Prerequisite: APIM with system-assigned managed identity.

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

// ------------------
//    OBSERVABILITY RESOURCES
// ------------------

module lawModule './modules/operational-insights/v1/workspaces.bicep' = {
  name: 'lawModule'
  params: {
    logAnalyticsName: lawName
    logAnalyticsLocation: lawLocation
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: lawName
  dependsOn: [
    lawModule
  ]
}

module appInsightsModule './modules/monitor/v1/appinsights.bicep' = {
  name: 'appInsightsModule'
  params: {
    applicationInsightsName: appInsightsName
    applicationInsightsLocation: appInsightsLocation
    lawId: lawModule.outputs.id
    customMetricsOptedInType: 'WithDimensions'
  }
}

// ------------------
//    AI FOUNDRY RESOURCES
// ------------------
// Creates Foundry accounts, Foundry projects, model deployments, Foundry diagnostics,
// Application Insights connections, and APIM RBAC assignments.

module foundryModule './modules/cognitive-services/v3/foundry.bicep' = {
  name: 'foundryModule'
  params: {
    aiServicesConfig: aiServicesConfig
    modelsConfig: modelsConfig
    lawId: lawModule.outputs.id
    apimPrincipalId: apim.identity.principalId
    foundryProjectName: foundryProjectName
    appInsightsId: appInsightsModule.outputs.id
    appInsightsInstrumentationKey: appInsightsModule.outputs.instrumentationKey
  }
}

// ------------------
//    APIM LOGGERS
// ------------------

resource azureMonitorLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' = {
  name: 'azuremonitor'
  parent: apim
  properties: {
    loggerType: 'azureMonitor'
    isBuffered: false
  }
}

resource appInsightsLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' = {
  name: 'appinsights-logger'
  parent: apim
  properties: {
    loggerType: 'applicationInsights'
    description: 'APIM Logger for Application Insights'
    isBuffered: false
    credentials: {
      instrumentationKey: appInsightsModule.outputs.instrumentationKey
    }
    resourceId: appInsightsModule.outputs.id
  }
}

// ------------------
//    APIM DIAGNOSTIC SETTINGS (platform-level)
// ------------------

resource apimDiagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'apimDiagnosticSettings'
  scope: apim
  properties: {
    workspaceId: lawModule.outputs.id
    logs: [
      {
        categoryGroup: 'AllLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// ------------------
//    APIM INFERENCE API
// ------------------
// Configure backends, backend pool, API, and diagnostics on existing APIM.
// The pool routes PTU-first (priority 1) with PayGo spillover (priority 2).

module inferenceAPIModule './modules/apim/v2/inference-api.bicep' = {
  name: 'inferenceAPIModule'
  dependsOn: [
    appInsightsLogger
  ]
  params: {
    apiManagementName: apimName
    policyXml: loadTextContent('policy.xml')
    apimLoggerId: azureMonitorLogger.id
    aiServicesConfig: foundryModule.outputs.extendedAIServicesConfig
    inferenceAPIType: inferenceAPIType
    inferenceAPIPath: inferenceAPIPath
    configureCircuitBreaker: true
    appInsightsId: appInsightsModule.outputs.id
    appInsightsInstrumentationKey: appInsightsModule.outputs.instrumentationKey
  }
}


// ------------------
//    FINOPS RESOURCES
// ------------------

// Pricing custom table in Log Analytics
resource pricingTable 'Microsoft.OperationalInsights/workspaces/tables@2023-09-01' = {
  parent: logAnalytics
  name: 'PRICING_CL'
  properties: {
    totalRetentionInDays: 4383
    plan: 'Analytics'
    schema: {
      name: 'PRICING_CL'
      description: 'OpenAI models pricing table for ${logAnalytics.properties.customerId}'
      columns: [
        {
          name: 'TimeGenerated'
          type: 'datetime'
        }
        {
          name: 'Model'
          type: 'string'
        }
        {
          name: 'InputTokensPrice'
          type: 'real'
        }
        {
          name: 'OutputTokensPrice'
          type: 'real'
        }
      ]
    }
    retentionInDays: 730
  }
}

// Data Collection Rule for pricing data ingestion
resource pricingDCR 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: 'dcr-pricing-${resourceSuffix}'
  location: resourceGroup().location
  kind: 'Direct'
  properties: {
    streamDeclarations: {
      'Custom-Json-${pricingTable.name}': {
        columns: [
          {
            name: 'TimeGenerated'
            type: 'datetime'
          }
          {
            name: 'Model'
            type: 'string'
          }
          {
            name: 'InputTokensPrice'
            type: 'real'
          }
          {
            name: 'OutputTokensPrice'
            type: 'real'
          }
        ]
      }
    }
    destinations: {
      logAnalytics: [
        {
          workspaceResourceId: logAnalytics.id
          name: logAnalytics.name
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          'Custom-Json-${pricingTable.name}'
        ]
        destinations: [
          logAnalytics.name
        ]
        transformKql: 'source'
        outputStream: 'Custom-${pricingTable.name}'
      }
    ]
  }
}

var monitoringMetricsPublisherRoleDefinitionID = resourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
resource pricingDCRRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: pricingDCR
  name: guid(subscription().id, resourceGroup().id, pricingDCR.name, monitoringMetricsPublisherRoleDefinitionID)
    properties: {
        roleDefinitionId: monitoringMetricsPublisherRoleDefinitionID
        principalId: deployer().objectId
        principalType: 'User'
    }
}

// Subscription quota custom table in Log Analytics
resource subscriptionQuotaTable 'Microsoft.OperationalInsights/workspaces/tables@2023-09-01' = {
  parent: logAnalytics
  name: 'SUBSCRIPTION_QUOTA_CL'
  properties: {
    totalRetentionInDays: 4383
    plan: 'Analytics'
    schema: {
      name: 'SUBSCRIPTION_QUOTA_CL'
      description: 'APIM subscriptions quota table for ${logAnalytics.properties.customerId}'
      columns: [
        {
          name: 'TimeGenerated'
          type: 'datetime'
        }
        {
          name: 'Subscription'
          type: 'string'
        }
        {
          name: 'CostQuota'
          type: 'real'
        }
      ]
    }
    retentionInDays: 730
  }
}

// Data Collection Rule for subscription quota data ingestion
resource subscriptionQuotaDCR 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: 'dcr-quota-${resourceSuffix}'
  location: resourceGroup().location
  kind: 'Direct'
  properties: {
    streamDeclarations: {
      'Custom-Json-${subscriptionQuotaTable.name}': {
        columns: [
          {
            name: 'TimeGenerated'
            type: 'datetime'
          }
          {
            name: 'Subscription'
            type: 'string'
          }
          {
            name: 'CostQuota'
            type: 'real'
          }
        ]
      }
    }
    destinations: {
      logAnalytics: [
        {
          workspaceResourceId: logAnalytics.id
          name: logAnalytics.name
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          'Custom-Json-${subscriptionQuotaTable.name}'
        ]
        destinations: [
          logAnalytics.name
        ]
        transformKql: 'source'
        outputStream: 'Custom-${subscriptionQuotaTable.name}'
      }
    ]
  }
}

resource subscriptionQuotaDCRRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: subscriptionQuotaDCR
  name: guid(subscription().id, resourceGroup().id, subscriptionQuotaDCR.name, monitoringMetricsPublisherRoleDefinitionID)
    properties: {
        roleDefinitionId: monitoringMetricsPublisherRoleDefinitionID
        principalId: deployer().objectId
        principalType: 'User'
    }
}

// Azure Monitor Workbook - Cost Analysis
resource openAIUsageWorkbook 'Microsoft.Insights/workbooks@2022-04-01' = {
  name: guid(resourceGroup().id, resourceSuffix, 'costAnalysis')
  location: resourceGroup().location
  kind: 'shared'
  properties: {
    displayName: 'Cost Analysis'
    serializedData: replace(loadTextContent('workbooks/cost-analysis.json'), '{workspace-id}', logAnalytics.id)
    sourceId: logAnalytics.id
    category: 'workbook'
  }
}

// ------------------
//    APIM PRODUCTS
// ------------------

@batchSize(1)
resource apimProduct 'Microsoft.ApiManagement/service/products@2024-06-01-preview' = [for product in apimProductsConfig: if(length(apimProductsConfig) > 0) {
  name: product.name
  parent: apim
  properties: {
    approvalRequired: true
    description: product.displayName
    displayName: product.displayName
    subscriptionRequired: true
    state: 'published'
  }
}]

@batchSize(1)
resource apimProductInferenceAPI 'Microsoft.ApiManagement/service/products/apiLinks@2024-06-01-preview' = [for (product, i) in apimProductsConfig: if(length(apimProductsConfig) > 0) {
  parent: apimProduct[i]
  name: 'openai-${apimProduct[i].name}'
  properties: {
    apiId: inferenceAPIModule.outputs.apiId
  }
}]

@batchSize(1)
resource productPolicy 'Microsoft.ApiManagement/service/products/policies@2024-06-01-preview' = [for (product, i) in apimProductsConfig: if(length(apimProductsConfig) > 0) {
  name: 'policy'
  parent: apimProduct[i]
  properties: {
    format: 'rawxml'
    value: replace(loadTextContent('products-policy.xml'), '{tokens-per-minute}', '${product.tpm}')
  }
}]

// ------------------
//    APIM SUBSCRIPTIONS (Product-scoped)
// ------------------

@batchSize(1)
resource apimSubscriptions 'Microsoft.ApiManagement/service/subscriptions@2024-06-01-preview' = [for subscription in apimSubscriptionsConfig: if(length(apimSubscriptionsConfig) > 0) {
  name: subscription.name
  parent: apim
  properties: {
    allowTracing: true
    displayName: '${subscription.displayName}'
    scope: '/products/${subscription.product}'
    state: 'active'
  }
  dependsOn: [
    apimProduct
    productPolicy
  ]
}]

// ------------------
//    ALERTS - Auto-suspend/activate subscriptions based on cost quotas
// ------------------

resource updateSubscriptionWorkflow 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-update-sub-${resourceSuffix}'
  location: resourceGroup().location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        '$connections': {
          defaultValue: {}
          type: 'Object'
        }
      }
      triggers: {
        When_an_Alert_is_Received: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'object'
              properties: {
                schemaId: {
                  type: 'string'
                }
                data: {
                  type: 'object'
                  properties: {
                    essentials: {
                      type: 'object'
                      properties: {
                        alertRule: { type: 'string' }
                      }
                    }
                    alertContext: {
                      type: 'object'
                      properties: {
                        condition: {
                          type: 'object'
                          properties: {
                            allOf: {
                              type: 'array'
                              items: {
                                type: 'object'
                                properties: {
                                  dimensions: {
                                    type: 'array'
                                    items: {
                                      type: 'object'
                                      properties: {
                                        name: { type: 'string' }
                                        value: { type: 'string' }
                                      }
                                    }
                                  }
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
      actions: {
        Update_APIM_Subscription_Status: {
          runAfter: {}
          type: 'Http'
          inputs: {
            uri: 'https://management.azure.com/subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.ApiManagement/service/${apim.name}/subscriptions/@{triggerBody()?[\'data\']?[\'alertContext\']?[\'condition\']?[\'allOf\']?[0]?[\'dimensions\']?[0]?[\'value\']}?api-version=2024-06-01-preview'
            method: 'PATCH'
            headers: {
              'Content-Type': 'application/json'
            }
            body: {
              properties: {
                state: '@if(contains(triggerBody()?[\'data\']?[\'essentials\']?[\'alertRule\'],\'suspend\'),\'suspended\',\'active\')'
              }
            }
            authentication: {
              type: 'ManagedServiceIdentity'
              audience: 'https://management.azure.com/'
            }
          }
          runtimeConfiguration: {
            contentTransfer: {
              transferMode: 'Chunked'
            }
          }
        }
      }
      outputs: {}
    }
    parameters: {
      '$connections': {
        value: {}
      }
    }
  }
}

resource workflowDiagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: updateSubscriptionWorkflow
  name: 'workflowDiagnosticSettings'
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      {
        categoryGroup: 'AllLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

var apimServiceContributorRoleDefinitionID = resourceId('Microsoft.Authorization/roleDefinitions', '312a565d-c81f-4fd8-895a-4e21e48d571c')
resource apimRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: apim
  name: guid(subscription().id, resourceGroup().id, updateSubscriptionWorkflow.name, apimServiceContributorRoleDefinitionID)
    properties: {
        roleDefinitionId: apimServiceContributorRoleDefinitionID
        principalId: updateSubscriptionWorkflow.identity.principalId
        principalType: 'ServicePrincipal'
    }
}

resource actionGroupUpdateSub 'microsoft.insights/actionGroups@2024-10-01-preview' = {
  name: 'actiongroup-update-sub-${resourceSuffix}'
  location: 'Global'
  properties: {
    groupShortName: 'Update Sub'
    enabled: true
    emailReceivers: []
    smsReceivers: []
    webhookReceivers: []
    eventHubReceivers: []
    itsmReceivers: []
    azureAppPushReceivers: []
    automationRunbookReceivers: []
    voiceReceivers: []
    logicAppReceivers: [
      {
        name: 'update-subscription-state'
        resourceId: updateSubscriptionWorkflow.id
        callbackUrl: '${updateSubscriptionWorkflow.listCallbackUrl().basePath}/triggers/When_an_Alert_is_Received/paths/invoke?api-version=${updateSubscriptionWorkflow.listCallbackUrl().queries['api-version']}&sp=${updateSubscriptionWorkflow.listCallbackUrl().queries.sp}&sv=${updateSubscriptionWorkflow.listCallbackUrl().queries.sv}&sig=${updateSubscriptionWorkflow.listCallbackUrl().queries.sig}'
        useCommonAlertSchema: true
      }
    ]
    azureFunctionReceivers: []
    armRoleReceivers: []
  }
}

resource ruleSuspendSub 'microsoft.insights/scheduledqueryrules@2025-01-01-preview' = {
  name: 'alert-suspend-sub-${resourceSuffix}'
  location: 'westeurope'
  kind: 'LogAlert'
  properties: {
    displayName: 'alert-suspend-subscriptions'
    severity: 3
    enabled: true
    evaluationFrequency: 'PT5M'
    scopes: [
      logAnalytics.id
    ]
    targetResourceTypes: [
      'Microsoft.OperationalInsights/workspaces'
    ]
    windowSize: 'PT5M'
    overrideQueryTimeRange: 'P2D'
    criteria: {
      allOf: [
        {
          query: 'let llmHeaderLogs = ApiManagementGatewayLlmLog\n    | where TimeGenerated >= startofmonth(now()) and TimeGenerated <= endofmonth(now())\n    | where DeploymentName != \'\';\nlet llmLogsWithSubscriptionId = llmHeaderLogs\n    | join kind=leftouter ApiManagementGatewayLogs on CorrelationId\n    | project\n        SubscriptionName = ApimSubscriptionId,\n        DeploymentName,\n        PromptTokens,\n        CompletionTokens,\n        TotalTokens;\nllmLogsWithSubscriptionId\n| join kind=inner (\n    PRICING_CL\n    | summarize arg_max(TimeGenerated, *) by Model\n    | project Model, InputTokensPrice, OutputTokensPrice\n    )\n    on $left.DeploymentName == $right.Model\n| extend InputCost = PromptTokens * InputTokensPrice\n| extend OutputCost = CompletionTokens * OutputTokensPrice\n| summarize\n    InputCost = sum(InputCost),\n    OutputCost = sum(OutputCost)\n    by SubscriptionName\n| extend TotalCost = (InputCost + OutputCost) / 1000\n| join kind=inner (\n    SUBSCRIPTION_QUOTA_CL\n    | summarize arg_max(TimeGenerated, *) by Subscription\n    | project Subscription, CostQuota\n    )\n    on $left.SubscriptionName == $right.Subscription\n| project SubscriptionName, CostQuota, TotalCost\n| where TotalCost > CostQuota\n'
          timeAggregation: 'Count'
          dimensions: [
            {
              name: 'SubscriptionName'
              operator: 'Exclude'
              values: [
                'null'
              ]
            }
          ]
          operator: 'GreaterThan'
          threshold: json('0')
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: false
    actions: {
      actionGroups: [
        actionGroupUpdateSub.id
      ]
      customProperties: {}
      actionProperties: {}
    }
  }
}

resource ruleActivateSub 'microsoft.insights/scheduledqueryrules@2025-01-01-preview' = {
  name: 'alert-activate-sub-${resourceSuffix}'
  location: 'westeurope'
  kind: 'LogAlert'
  properties: {
    displayName: 'alert-activate-subscriptions'
    severity: 3
    enabled: true
    evaluationFrequency: 'PT5M'
    scopes: [
      logAnalytics.id
    ]
    targetResourceTypes: [
      'Microsoft.OperationalInsights/workspaces'
    ]
    windowSize: 'PT5M'
    overrideQueryTimeRange: 'P2D'
    criteria: {
      allOf: [
        {
          query: 'let llmHeaderLogs = ApiManagementGatewayLlmLog\n    | where TimeGenerated >= startofmonth(now()) and TimeGenerated <= endofmonth(now())\n    | where DeploymentName != \'\';\nlet llmLogsWithSubscriptionId = llmHeaderLogs\n    | join kind=leftouter ApiManagementGatewayLogs on CorrelationId\n    | project\n        SubscriptionName = ApimSubscriptionId,\n        DeploymentName,\n        PromptTokens,\n        CompletionTokens,\n        TotalTokens;\nllmLogsWithSubscriptionId\n| join kind=inner (\n    PRICING_CL\n    | summarize arg_max(TimeGenerated, *) by Model\n    | project Model, InputTokensPrice, OutputTokensPrice\n    )\n    on $left.DeploymentName == $right.Model\n| extend InputCost = PromptTokens * InputTokensPrice\n| extend OutputCost = CompletionTokens * OutputTokensPrice\n| summarize\n    InputCost = sum(InputCost),\n    OutputCost = sum(OutputCost)\n    by SubscriptionName\n| extend TotalCost = (InputCost + OutputCost) / 1000\n| join kind=inner (\n    SUBSCRIPTION_QUOTA_CL\n    | summarize arg_max(TimeGenerated, *) by Subscription\n    | project Subscription, CostQuota\n    )\n    on $left.SubscriptionName == $right.Subscription\n| project SubscriptionName, CostQuota, TotalCost\n| where TotalCost <= CostQuota\n'
          timeAggregation: 'Count'
          dimensions: [
            {
              name: 'SubscriptionName'
              operator: 'Exclude'
              values: [
                'null'
              ]
            }
          ]
          operator: 'GreaterThan'
          threshold: json('0')
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: false
    actions: {
      actionGroups: [
        actionGroupUpdateSub.id
      ]
      customProperties: {}
      actionProperties: {}
    }
  }
}

// ------------------
//    OUTPUTS
// ------------------

output logAnalyticsWorkspaceId string = lawModule.outputs.customerId
output apimServiceId string = apim.id
output apimResourceGatewayURL string = apim.properties.gatewayUrl
output appInsightsName string = appInsightsModule.outputs.name

#disable-next-line outputs-should-not-contain-secrets
output apimSubscriptions array = [for (subscription, i) in apimSubscriptionsConfig: {
  name: subscription.name
  displayName: subscription.displayName
  key: apimSubscriptions[i].listSecrets().primaryKey
}]

output foundryProjectEndpoint string = foundryModule.outputs.extendedAIServicesConfig[0].foundryProjectEndpoint

output pricingDCREndpoint string = pricingDCR.properties.endpoints.logsIngestion
output pricingDCRImmutableId string = pricingDCR.properties.immutableId
output pricingDCRStream string = pricingDCR.properties.dataFlows[0].streams[0]
output subscriptionQuotaDCREndpoint string = subscriptionQuotaDCR.properties.endpoints.logsIngestion
output subscriptionQuotaDCRImmutableId string = subscriptionQuotaDCR.properties.immutableId
output subscriptionQuotaDCRStream string = subscriptionQuotaDCR.properties.dataFlows[0].streams[0]
