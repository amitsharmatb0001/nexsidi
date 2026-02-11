import requests
import uuid
import time

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

# Replace with valid test user credentials
TEST_USER_EMAIL = "testuser_deploy@example.com"
TEST_USER_PASSWORD = "StrongPassword123!"

TEST_USER_NAME = "Test User Deployment"

def authenticate():
    # Signup the test user (ignore if exists)
    signup_url = f"{BASE_URL}/api/auth/signup"
    signup_payload = {
        "email": TEST_USER_EMAIL,
        "password": TEST_USER_PASSWORD,
        "name": TEST_USER_NAME
    }
    try:
        resp = requests.post(signup_url, json=signup_payload, timeout=TIMEOUT)
        if resp.status_code not in (201, 400):  # 400 means user already exists
            resp.raise_for_status()
    except requests.HTTPError as e:
        if e.response is None or e.response.status_code != 400:
            raise e
    # Login to get token
    login_url = f"{BASE_URL}/api/auth/login"
    login_payload = {
        "email": TEST_USER_EMAIL,
        "password": TEST_USER_PASSWORD
    }
    resp = requests.post(login_url, json=login_payload, timeout=TIMEOUT)
    resp.raise_for_status()
    token = resp.json().get("access_token")
    assert token, "Login failed to return access token"
    return token

def create_project(token):
    url = f"{BASE_URL}/api/projects"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "title": f"Deploy Test Project {uuid.uuid4()}",
        "description": "Project created for deployment test"
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    assert resp.status_code == 201, "Project creation failed"
    data = resp.json()
    project_id = data.get("id") or data.get("project_id")
    if not project_id:
        # Attempt to get project id from location header or response body keys
        if "id" in data:
            project_id = data["id"]
        else:
            # As fallback, list projects and find the newly created by title
            list_resp = requests.get(url, headers=headers, timeout=TIMEOUT)
            list_resp.raise_for_status()
            projects = list_resp.json()
            for proj in projects:
                if proj.get("title") == payload["title"]:
                    project_id = proj.get("id")
                    break
    assert project_id, "Failed to retrieve project ID after creation"
    return project_id

def delete_project(token, project_id):
    url = f"{BASE_URL}/api/projects/{project_id}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.delete(url, headers=headers, timeout=TIMEOUT)
    # Allow 204 or 404 (if already deleted)
    assert resp.status_code in (204, 404), "Failed to delete project"

def test_deploy_project_and_check_deployment_status():
    token = authenticate()
    headers = {"Authorization": f"Bearer {token}"}

    project_id = None
    try:
        # Create project to deploy
        project_id = create_project(token)

        # Initiate deployment to GCP Cloud Run
        deploy_url = f"{BASE_URL}/api/projects/{project_id}/deploy"
        deploy_resp = requests.post(deploy_url, headers=headers, timeout=TIMEOUT)
        deploy_resp.raise_for_status()
        assert deploy_resp.status_code == 200, "Deployment initiation failed"
        deploy_data = deploy_resp.json()
        assert isinstance(deploy_data, dict), "Deployment response must be JSON object"

        # Check that deployment was initiated (response can have deployment info)
        # We expect at least some indication like deployment id or status message
        # Since schema isn't explicitly detailed, just assert returned JSON has keys or is not empty
        assert deploy_data, "Deployment initiation response is empty"

        # Allow some time for deployment status to be available, polling latest deployment status
        latest_deploy_url = f"{BASE_URL}/api/projects/{project_id}/deployments/latest"

        for attempt in range(5):
            status_resp = requests.get(latest_deploy_url, headers=headers, timeout=TIMEOUT)
            status_resp.raise_for_status()
            status_data = status_resp.json()
            # Expect status_data to contain some keys like deployment id, status, created_at etc.
            assert isinstance(status_data, dict), "Latest deployment response must be JSON object"
            # Check if deployment status is present and valid
            status = status_data.get("status") or status_data.get("deployment_status")
            if status:
                # If status is present, assert it's one of expected states (e.g., pending, running, succeeded, failed)
                assert status.lower() in ("pending", "running", "succeeded", "failed", "deploying"), \
                    f"Unexpected deployment status: {status}"
                # Once we get status, break early
                break
            time.sleep(2)  # wait before next poll
        else:
            assert False, "Deployment status not available or missing after retries"

    finally:
        if project_id:
            delete_project(token, project_id)

test_deploy_project_and_check_deployment_status()
