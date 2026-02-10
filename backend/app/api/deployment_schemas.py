# Deployment Request/Response Schemas
class DeploymentRequest(BaseModel):
    """Request to deploy a project"""
    platform: str = "gcp_cloud_run"
    region: Optional[str] = "us-central1"
    service_name: Optional[str] = None
    env_vars: Optional[dict] = None

class DeploymentResponse(BaseModel):
    """Deployment status response"""
    deployment_id: str
    status: str
    url: Optional[str] = None
    deployed_at: Optional[datetime] = None
    logs: Optional[str] = None
    message: str
