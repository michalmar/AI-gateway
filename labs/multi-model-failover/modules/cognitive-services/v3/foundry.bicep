/**
 * @module openai-v2
 * @description This module defines the Azure Cognitive Services OpenAI resources using Bicep.
 * This is version 3 (v3) of the AI Foundry Bicep module.
 */

// ------------------
//    PARAMETERS
// ------------------


@description('Configuration array for AI Foundry resources')
param aiServicesConfig array = []

@description('Configuration array for the model deployments')
param modelsConfig array = []

@description('Log Analytics Workspace Id')
param lawId string = ''

@description('APIM Pricipal Id')
param  apimPrincipalId string

@description('Optional legacy prefix for AI Foundry project names. Leave empty to use the standard naming convention.')
param foundryProjectName string = ''

@description('Project segment used in resource names.')
param projectName string = 'ict-apim'

@description('Subproject segment used in resource names.')
param subprojectName string = 'multi-model-failover'

@description('Primary three-digit instance number segment used in resource names.')
param resourceNumber string = '001'

@description('Secondary three-digit instance number segment used when two resources of the same type are created.')
param secondaryResourceNumber string = '002'

@description('Tenant segment used in resource names.')
param tenantName string = 'mpsvcrtest'

@description('The instrumentation key for Application Insights')
@secure()
param appInsightsInstrumentationKey string = ''

@description('The resource ID for Application Insights')
param appInsightsId string = ''


// ------------------
//    VARIABLES
// ------------------

var azureRoles = loadJsonContent('../../azure-roles.json')
var cognitiveServicesOpenAIUserRoleDefinitionID = resourceId('Microsoft.Authorization/roleDefinitions', azureRoles.CognitiveServicesOpenAIUser)
var foundryInstanceNumbers = [for (config, i) in aiServicesConfig: config.?resourceNumber ?? (i == 0 ? resourceNumber : i == 1 ? secondaryResourceNumber : padLeft(string(i + 1), 3, '0'))]
var foundryAccountNames = [for (config, i) in aiServicesConfig: config.?resourceName ?? 'aif-${projectName}-${subprojectName}-${foundryInstanceNumbers[i]}-${tenantName}']
var foundryProjectNames = [for (config, i) in aiServicesConfig: config.?projectResourceName ?? (empty(foundryProjectName) ? 'proj-${projectName}-${subprojectName}-${foundryInstanceNumbers[i]}-${tenantName}' : '${foundryProjectName}-${config.name}')]


// ------------------
//    RESOURCES
// ------------------

resource cognitiveServices 'Microsoft.CognitiveServices/accounts@2026-03-15-preview' = [for (config, i) in aiServicesConfig: {
  name: foundryAccountNames[i]
  location: config.location
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  kind: 'AIServices'
  properties: {
    // required to work in AI Foundry
    allowProjectManagement: true 

    customSubDomainName: toLower(config.?customSubDomainName ?? foundryAccountNames[i])

    disableLocalAuth: false

    publicNetworkAccess: 'Enabled'
  }  
}]

resource aiProject 'Microsoft.CognitiveServices/accounts/projects@2026-03-15-preview' = [for (config, i) in aiServicesConfig: {  
  #disable-next-line BCP334
  name: foundryProjectNames[i]
  parent: cognitiveServices[i]
  location: config.location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}]


var aiProjectManagerRoleDefinitionID = 'eadc314b-1a2d-4efa-be10-5d325db5065e' 
resource aiProjectManagerRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (config, i) in aiServicesConfig: {
    scope: cognitiveServices[i]
    name: guid(subscription().id, resourceGroup().id, config.name, aiProjectManagerRoleDefinitionID)
    properties: {
      roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', aiProjectManagerRoleDefinitionID)
      principalId: deployer().objectId
    }
}]


// https://learn.microsoft.com/azure/templates/microsoft.insights/diagnosticsettings
resource diagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = [for (config, i) in aiServicesConfig: if (lawId != '') {
  name: 'diag-${foundryInstanceNumbers[i]}'
  scope: cognitiveServices[i]
  properties: {
    workspaceId: lawId != '' ? lawId : null
    logs: []
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}]

resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/connections@2026-03-15-preview' = [for (config, i) in aiServicesConfig: if (length(appInsightsId) > 0 && length(appInsightsInstrumentationKey) > 0) {
  parent: cognitiveServices[i]
  name: 'appi-${projectName}-${subprojectName}-${foundryInstanceNumbers[i]}-${tenantName}'
  properties: {
    authType: 'ApiKey'
    category: 'AppInsights'
    target: appInsightsId
    useWorkspaceManagedIdentity: false
    isSharedToAll: false
    sharedUserList: []
    peRequirement: 'NotRequired'
    peStatus: 'NotApplicable'
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsightsId
    }
    credentials: {
      key: appInsightsInstrumentationKey
    }    
  }
}]

resource roleAssignmentCognitiveServicesUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (config, i) in aiServicesConfig: {
  scope: cognitiveServices[i]
  name: guid(subscription().id, resourceGroup().id, config.name, cognitiveServicesOpenAIUserRoleDefinitionID)
    properties: {
        roleDefinitionId: cognitiveServicesOpenAIUserRoleDefinitionID
        principalId: apimPrincipalId
        principalType: 'ServicePrincipal'
    }
}]

module modelDeployments 'deployments.bicep' = [for (config, i) in aiServicesConfig: {
  name: take('models-${cognitiveServices[i].name}', 64)
  params: {
    cognitiveServiceName: cognitiveServices[i].name
    modelsConfig: filter(modelsConfig, model => model.?aiservice == null || model.?aiservice == config.name || model.?aiservice == foundryAccountNames[i])
  }
}]


// ------------------
//    OUTPUTS
// ------------------

output extendedAIServicesConfig array = [for (config, i) in aiServicesConfig: {
  // Original openAIConfig properties
  name: config.name
  location: config.location
  priority: config.?priority
  weight: config.?weight
  // Additional properties
  cognitiveService: cognitiveServices[i]
  cognitiveServiceName: cognitiveServices[i].name
  cognitiveServicesId: cognitiveServices[i].id
  endpoint: cognitiveServices[i].properties.endpoint
  foundryProjectEndpoint: 'https://${cognitiveServices[i].name}.services.ai.azure.com/api/projects/${aiProject[i].name}'
}]
