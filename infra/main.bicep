// Infrastructure for the interactive fare map (web/public).
//
// Provisions a single Azure Static Web App (Free tier) to host the fully static,
// offline app. Content is deployed out of band — by the GitHub Actions workflow
// in .github/workflows/deploy-web.yml, or manually with the Static Web Apps CLI
// using the deployment token — so this template intentionally does NOT link a
// source repository/branch.
//
// Deploy (resource-group scoped):
//   az group create -n rg-flights-fare-map -l westus2
//   az deployment group create -g rg-flights-fare-map \
//     -f infra/main.bicep -p infra/main.bicepparam

targetScope = 'resourceGroup'

@description('Name of the Static Web App resource (unique within the resource group).')
param name string = 'flights-fare-map'

@description('Region for the Static Web App. Must be a Static Web Apps supported region.')
@allowed([
  'westus2'
  'centralus'
  'eastus2'
  'westeurope'
  'eastasia'
])
param location string = 'westus2'

@description('SKU tier. Free is sufficient for this static, API-less app.')
@allowed([
  'Free'
  'Standard'
])
param sku string = 'Free'

@description('Tags applied to every resource.')
param tags object = {
  app: 'flights-fare-map'
  managedBy: 'bicep'
}

resource site 'Microsoft.Web/staticSites@2023-01-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: sku
    tier: sku
  }
  properties: {
    // Content is uploaded via the SWA CLI / GitHub Action rather than a linked
    // repo build, so no repositoryUrl/branch/buildProperties here.
    allowConfigFileUpdates: true
  }
}

@description('The Static Web App resource name.')
output staticWebAppName string = site.name

@description('Auto-generated default hostname, e.g. <name>-<hash>.azurestaticapps.net.')
output defaultHostname string = site.properties.defaultHostname

@description('Public URL of the deployed app.')
output url string = 'https://${site.properties.defaultHostname}'
