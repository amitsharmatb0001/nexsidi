import requests
import uuid

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

# Use these credentials or generate dynamically if needed
TEST_EMAIL = "testuser.tilotma@example.com"
TEST_PASSWORD = "StrongPassword123!"

def test_send_message_to_tilotma_agent_and_receive_response():
    # Sign up (if user exists, just login)
    signup_url = f"{BASE_URL}/api/auth/signup"
    login_url = f"{BASE_URL}/api/auth/login"
    chat_new_url = f"{BASE_URL}/api/chat/new"
    chat_send_url = f"{BASE_URL}/api/chat/send"
    projects_url = f"{BASE_URL}/api/projects"

    headers = {"Content-Type": "application/json"}

    # Try signup; if fail due to duplicate, just login
    signup_payload = {
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    }
    auth_token = None
    try:
        r_signup = requests.post(signup_url, json=signup_payload, timeout=TIMEOUT)
        if r_signup.status_code == 201:
            # Signup success
            pass
        elif r_signup.status_code == 400:
            # Email already exists, proceed to login
            pass
        else:
            r_signup.raise_for_status()
    except requests.RequestException as e:
        raise AssertionError(f"Signup request failed: {e}")

    # Login to get token
    login_payload = {
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    }
    try:
        r_login = requests.post(login_url, json=login_payload, timeout=TIMEOUT)
        assert r_login.status_code == 200, f"Login failed: {r_login.text}"
        auth_token = r_login.json().get("access_token")
        assert auth_token, "No access_token returned from login"
    except requests.RequestException as e:
        raise AssertionError(f"Login request failed: {e}")

    auth_headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }

    # Create a new project to associate chat messages with
    project_payload = {
        "title": "Test Project for Tilotma Agent Message",
        "description": "Project created for testing chat message sending and AI response."
    }
    project_id = None
    try:
        r_project = requests.post(projects_url, json=project_payload, headers=auth_headers, timeout=TIMEOUT)
        assert r_project.status_code == 201, f"Project creation failed: {r_project.text}"
        project_id = r_project.json().get("id")
        assert project_id, "Project ID not returned on creation"

        # Create a new chat session
        chat_new_payload = {
            "title": "Chat Session for Tilotma Agent Test"
        }
        r_chat_new = requests.post(chat_new_url, json=chat_new_payload, headers=auth_headers, timeout=TIMEOUT)
        assert r_chat_new.status_code == 200, f"Chat session creation failed: {r_chat_new.text}"
        chat_id = r_chat_new.json().get("id")
        assert chat_id, "Chat ID not returned on chat creation"

        # Send a message to Tilotma AI agent
        message_content = "Hello, please generate a new web app project."
        chat_send_payload = {
            "content": message_content,
            "project_id": project_id,
            "chat_id": chat_id
        }
        r_chat_send = requests.post(chat_send_url, json=chat_send_payload, headers=auth_headers, timeout=TIMEOUT)
        assert r_chat_send.status_code == 200, f"Chat send failed: {r_chat_send.text}"

        resp_json = r_chat_send.json()
        # Validate presence and types of required fields in response
        assert "response" in resp_json and isinstance(resp_json["response"], str) and resp_json["response"].strip(), "Invalid or empty 'response' in AI reply"
        assert "should_create_project" in resp_json and isinstance(resp_json["should_create_project"], bool), "'should_create_project' missing or not bool"
        assert "cost" in resp_json and (isinstance(resp_json["cost"], float) or isinstance(resp_json["cost"], int)), "'cost' missing or not a number"
        # project_id in response may be string or null (optional)
        assert "project_id" in resp_json and (resp_json["project_id"] is None or isinstance(resp_json["project_id"], str)), "'project_id' missing or invalid type"

    finally:
        # Cleanup: delete the created project
        if project_id:
            delete_project_url = f"{projects_url}/{project_id}"
            try:
                r_delete = requests.delete(delete_project_url, headers=auth_headers, timeout=TIMEOUT)
                # Expect 204 No Content on success or 404 if already deleted
                assert r_delete.status_code in (204, 404), f"Unexpected status code on project delete: {r_delete.status_code}"
            except requests.RequestException:
                pass


test_send_message_to_tilotma_agent_and_receive_response()
