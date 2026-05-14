

@description('Configuration array for the model deployments')
param modelsConfig array = []

param cognitiveServiceName string

resource cognitiveService 'Microsoft.CognitiveServices/accounts@2026-03-15-preview' existing = {
  name: cognitiveServiceName
}

@batchSize(1)
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2026-03-15-preview' = [for (model, i) in modelsConfig: if(model.?aiservice == null || contains(cognitiveService.name, model.?aiservice)) {
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
