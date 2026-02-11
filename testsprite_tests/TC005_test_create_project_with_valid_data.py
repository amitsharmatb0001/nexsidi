import requests
import uuid

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

def test_create_project_with_valid_data():
    # Unique user credentials for signup
    unique_email = f"testuser_{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPassw0rd!"
    full_name = "Test User"

    signup_url = f"{BASE_URL}/api/auth/signup"
    login_url = f"{BASE_URL}/api/auth/login"
    create_project_url = f"{BASE_URL}/api/projects"

    # Signup payload with corrected 'full_name' field
    signup_data = {
        "email": unique_email,
        "password": password,
        "full_name": full_name
    }

    access_token = None
    created_project_id = None

    try:
        # Sign up user
        signup_resp = requests.post(signup_url, json=signup_data, timeout=TIMEOUT)
        assert signup_resp.status_code == 201, f"Signup failed with status {signup_resp.status_code}: {signup_resp.text}"

        # Login user to get access token
        login_data = {
            "email": unique_email,
            "password": password
        }
        login_resp = requests.post(login_url, json=login_data, timeout=TIMEOUT)
        assert login_resp.status_code == 200, f"Login failed with status {login_resp.status_code}: {login_resp.text}"
        token_json = login_resp.json()
        assert "access_token" in token_json, "No access_token in login response"
        access_token = token_json["access_token"]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # Create a new project with valid title and description
        project_title = "Test Project Title"
        project_description = "This is a test project description for TC005."
        project_payload = {
            "title": project_title,
            "description": project_description
        }

        create_resp = requests.post(create_project_url, json=project_payload, headers=headers, timeout=TIMEOUT)

        # Validate response is 201 Created
        assert create_resp.status_code == 201, f"Project creation failed with status {create_resp.status_code}: {create_resp.text}"

        # Response might contain project details with project ID
        project_resp_json = create_resp.json()
        assert isinstance(project_resp_json, dict), "Project creation response is not a JSON object"

        # We expect at least an 'id' or similar unique project id in response (according to API schemas format)
        # The PRD does not explicitly show response body for project creation, but usually APIs return project data post creation
        # So try to extract 'id' or fallback to no id; if no id, attempt to retrieve project list to find the project
        created_project_id = project_resp_json.get("id")
        if not created_project_id:
            # If no project id in response, retrieve project list to find created project id
            list_resp = requests.get(create_project_url, headers=headers, timeout=TIMEOUT)
            assert list_resp.status_code == 200, f"Failed to list projects with status {list_resp.status_code}: {list_resp.text}"
            projects = list_resp.json()
            assert isinstance(projects, list), "Projects list response is not a list"
            matching_projects = [p for p in projects if p.get("title") == project_title and p.get("description") == project_description]
            assert len(matching_projects) == 1, "Created project not found in project list or multiple matches found"
            created_project_id = matching_projects[0].get("id")
            assert created_project_id, "Could not determine project ID from project list"

        # Further validate the created project details via GET /api/projects/{project_id}
        get_project_url = f"{create_project_url}/{created_project_id}"
        get_resp = requests.get(get_project_url, headers=headers, timeout=TIMEOUT)
        assert get_resp.status_code == 200, f"Failed to get created project details with status {get_resp.status_code}: {get_resp.text}"
        project_details = get_resp.json()
        assert project_details.get("title") == project_title, "Project title mismatch in retrieved project details"
        assert project_details.get("description") == project_description, "Project description mismatch in retrieved project details"

    finally:
        # Cleanup: delete the created project if it exists and token is available
        if access_token and created_project_id:
            headers = {"Authorization": f"Bearer {access_token}"}
            delete_url = f"{create_project_url}/{created_project_id}"
            try:
                delete_resp = requests.delete(delete_url, headers=headers, timeout=TIMEOUT)
                assert delete_resp.status_code == 204, f"Failed to delete project with status {delete_resp.status_code}: {delete_resp.text}"
            except Exception:
                # Ignore cleanup errors but do not suppress assertion failures from main test flow
                pass

test_create_project_with_valid_data()