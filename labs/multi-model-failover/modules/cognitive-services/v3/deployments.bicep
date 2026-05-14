

@description('Configuration array for the model deployments')
param modelsConfig array = []

param cognitiveServiceName string

resource cognitiveService 'Microsoft.CognitiveServices/accounts@2026-03-15-preview' existing = {
  name: cognitiveServiceName
}

@batchSize(1)
// The parent Foundry module pre-filters models for the target account.
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2026-03-01' = [for model in modelsConfig: {
  name: model.name
  parent: cognitiveService
  sku: {
    name: model.sku
    capacity: model.capacity
  }
  properties: {
    model: {
      format: model.publisher
      name: model.name
      version: model.version
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}]
