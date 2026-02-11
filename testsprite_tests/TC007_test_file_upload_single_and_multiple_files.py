import requests
import uuid
import io

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

# Replace with valid user credentials for authentication
TEST_USER_EMAIL = "testuser_fileupload@example.com"
TEST_USER_PASSWORD = "StrongPassword123!"

def authenticate_user():
    login_url = f"{BASE_URL}/api/auth/login"
    data = {"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}
    response = requests.post(login_url, json=data, timeout=TIMEOUT)
    response.raise_for_status()
    token = response.json().get("access_token")
    assert token, "Authentication failed, no access token received"
    return token

def signup_user_if_not_exists():
    signup_url = f"{BASE_URL}/api/auth/signup"
    data = {"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD, "full_name": "File Upload Test User"}
    headers = {"Content-Type": "application/json"}
    r = requests.post(signup_url, json=data, headers=headers, timeout=TIMEOUT)
    if r.status_code == 201:
        return True
    elif r.status_code == 400:
        return False
    elif r.status_code == 422:
        # Unprocessable Entity - likely validation error
        assert False, f"Signup failed with 422 Unprocessable Entity: {r.text}"
    else:
        r.raise_for_status()

def create_project(token, title="Test Project for File Upload"):
    url = f"{BASE_URL}/api/projects"
    headers = {"Authorization": f"Bearer {token}"}
    data = {"title": title, "description": "Project to test file upload endpoints."}
    response = requests.post(url, json=data, headers=headers, timeout=TIMEOUT)
    response.raise_for_status()
    assert response.status_code == 201
    project = response.json()
    project_id = project.get("id") or project.get("project_id") or None
    if not project_id and isinstance(project, dict):
        # Sometimes API returns created object with 'id'
        for k in project:
            if isinstance(project[k], str) and len(project[k]) == 36:  # uuid length
                project_id = project[k]
                break
    assert project_id, "Created project ID not returned"
    return project_id

def delete_project(token, project_id):
    url = f"{BASE_URL}/api/projects/{project_id}"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.delete(url, headers=headers, timeout=TIMEOUT)
    if response.status_code not in (204, 404):
        response.raise_for_status()

def test_file_upload_single_and_multiple_files():
    signup_user_if_not_exists()
    token = authenticate_user()
    headers = {"Authorization": f"Bearer {token}"}
    project_id = None

    # Prepare dummy file content
    file_content_1 = b"File content for single upload test"
    file_content_2 = b"File content for multi upload test file 1"
    file_content_3 = b"File content for multi upload test file 2"

    try:
        # Create project
        project_id = create_project(token)

        # Test single file upload
        single_upload_url = f"{BASE_URL}/api/uploads/upload"
        files_single = {
            "file": ("test_single.txt", io.BytesIO(file_content_1), "text/plain"),
        }
        data_single = {"project_id": project_id}
        response_single = requests.post(single_upload_url, files=files_single, data=data_single, headers=headers, timeout=TIMEOUT)
        response_single.raise_for_status()
        assert response_single.status_code == 200
        resp_json_single = response_single.json()
        assert "file_id" in resp_json_single or "id" in resp_json_single or "message" in resp_json_single or len(resp_json_single) > 0, "Single file upload response unexpected"

        # Test multiple files upload
        multi_upload_url = f"{BASE_URL}/api/uploads/upload/multiple"
        files_multi = [
            ("files", ("test_multi_1.txt", io.BytesIO(file_content_2), "text/plain")),
            ("files", ("test_multi_2.txt", io.BytesIO(file_content_3), "text/plain")),
        ]
        data_multi = [("project_id", project_id)]
        # requests requires files parameter to list tuples for multiple files of same key
        response_multi = requests.post(multi_upload_url, files=files_multi, data=data_multi, headers=headers, timeout=TIMEOUT)
        response_multi.raise_for_status()
        assert response_multi.status_code == 200
        resp_json_multi = response_multi.json()
        assert resp_json_multi and ( "files" in resp_json_multi or "uploaded" in resp_json_multi or len(resp_json_multi) > 0), "Multiple file upload response unexpected"

        # Validate uploaded files are associated with project
        list_files_url = f"{BASE_URL}/api/uploads/project/{project_id}/files"
        response_list = requests.get(list_files_url, headers=headers, timeout=TIMEOUT)
        response_list.raise_for_status()
        files_list_json = response_list.json()
        assert isinstance(files_list_json, dict), "Project files list response should be a dict"
        # Ideally check uploaded file names in returned structure
        uploaded_file_names = []
        def extract_file_names(d):
            if isinstance(d, dict):
                for key, val in d.items():
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict) and 'filename' in item:
                                uploaded_file_names.append(item['filename'])
            elif isinstance(d, list):
                for item in d:
                    if isinstance(item, dict) and 'filename' in item:
                        uploaded_file_names.append(item['filename'])
        extract_file_names(files_list_json)
        # Check that our filenames exist in the uploaded files list
        expected_filenames = {"test_single.txt", "test_multi_1.txt", "test_multi_2.txt"}
        assert expected_filenames.intersection(set(uploaded_file_names)), "Uploaded filenames not found in project files list"

    finally:
        if project_id:
            delete_project(token, project_id)

test_file_upload_single_and_multiple_files()
