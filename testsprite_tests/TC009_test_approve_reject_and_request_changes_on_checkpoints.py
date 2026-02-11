import requests
import uuid
import time

BASE_URL = "http://localhost:8000"
TIMEOUT = 30
EMAIL = "testuser_tc009@example.com"
PASSWORD = "TestPass123!"


def test_approve_reject_and_request_changes_on_checkpoints():
    headers = {"Content-Type": "application/json"}
    token = None
    project_id = None
    checkpoint_id = None

    def signup_and_login():
        signup_data = {
            "email": EMAIL,
            "password": PASSWORD,
            "name": "Test User TC009"
        }
        resp = requests.post(
            f"{BASE_URL}/api/auth/signup",
            json=signup_data,
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT
        )
        if resp.status_code not in (201, 400):
            resp.raise_for_status()
        # If 400 and email exists, proceed to login
        login_data = {
            "email": EMAIL,
            "password": PASSWORD
        }
        login_resp = requests.post(
            f"{BASE_URL}/api/auth/login",
            json=login_data,
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT
        )
        login_resp.raise_for_status()
        access_token = login_resp.json().get("access_token")
        assert access_token, "Login did not return access_token"
        return access_token

    def create_project(token):
        project_data = {
            "title": "TC009 Test Project",
            "description": "Project for testing checkpoint approval workflow"
        }
        resp = requests.post(
            f"{BASE_URL}/api/projects",
            json=project_data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        assert resp.status_code == 201
        project = resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") else {}
        if "id" in project:
            return project["id"]
        # If no id in response, get projects and find the one with the title
        list_resp = requests.get(
            f"{BASE_URL}/api/projects",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT
        )
        list_resp.raise_for_status()
        for proj in list_resp.json():
            if proj.get("title") == project_data["title"]:
                return proj["id"]
        raise Exception("Created project ID not found")

    def get_checkpoints(project_id, token):
        # Checkpoints are not explicitly documented; assume /api/projects/{project_id}/checkpoints returns them
        resp = requests.get(
            f"{BASE_URL}/api/projects/{project_id}/checkpoints",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT
        )
        if resp.status_code == 404:  # No checkpoints endpoint or no checkpoints
            return []
        resp.raise_for_status()
        return resp.json() if resp.headers.get("Content-Type","").startswith("application/json") else []

    def create_dummy_checkpoint(project_id, token):
        # If no endpoint documented for creating checkpoint, create by sending a chat message with should_create_project = True
        chat_data = {
            "content": "Create checkpoint for testing.",
            "project_id": project_id
        }
        resp = requests.post(
            f"{BASE_URL}/api/chat/send",
            json=chat_data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        # After this action, maybe checkpoint created; then we re-fetch checkpoints
        time.sleep(1)  # Wait a bit for checkpoint creation
        checkpoints = get_checkpoints(project_id, token)
        if checkpoints:
            return checkpoints[0]
        raise Exception("Unable to create or find checkpoint for test")

    def approve_checkpoint(proj_id, chkpt_id, token):
        resp = requests.post(
            f"{BASE_URL}/api/approvals/{proj_id}/approve/{chkpt_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        assert resp.status_code == 200
        return resp.json() if resp.headers.get("Content-Type","").startswith("application/json") else {}

    def reject_checkpoint(proj_id, chkpt_id, token, feedback):
        reject_data = {"feedback": feedback}
        resp = requests.post(
            f"{BASE_URL}/api/approvals/{proj_id}/reject/{chkpt_id}",
            json=reject_data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        assert resp.status_code == 200
        return resp.json() if resp.headers.get("Content-Type","").startswith("application/json") else {}

    def request_changes_checkpoint(proj_id, chkpt_id, token, feedback):
        req_changes_data = {"feedback": feedback}
        resp = requests.post(
            f"{BASE_URL}/api/approvals/{proj_id}/request-changes/{chkpt_id}",
            json=req_changes_data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        assert resp.status_code == 200
        return resp.json() if resp.headers.get("Content-Type","").startswith("application/json") else {}

    def delete_project(proj_id, token):
        resp = requests.delete(
            f"{BASE_URL}/api/projects/{proj_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT
        )
        if resp.status_code not in (204, 404):
            resp.raise_for_status()

    token = signup_and_login()
    headers["Authorization"] = f"Bearer {token}"

    try:
        project_id = create_project(token)

        # Try to get checkpoints - no explicit doc, attempt assumed endpoint
        checkpoints = get_checkpoints(project_id, token)
        if not checkpoints:
            # If none found, try to create one (via chat interaction or other means)
            checkpoint = create_dummy_checkpoint(project_id, token)
        else:
            checkpoint = checkpoints[0]

        checkpoint_id = checkpoint.get("id") if isinstance(checkpoint, dict) else None
        assert checkpoint_id, "Checkpoint ID should exist"

        # Approve checkpoint
        approve_resp = approve_checkpoint(project_id, checkpoint_id, token)
        assert isinstance(approve_resp, dict)

        # Reject checkpoint with feedback
        reject_feedback = "The checkpoint does not meet requirements."
        reject_resp = reject_checkpoint(project_id, checkpoint_id, token, reject_feedback)
        assert isinstance(reject_resp, dict)

        # Request changes with required feedback
        changes_feedback = "Please update the design as per new guidelines."
        req_changes_resp = request_changes_checkpoint(project_id, checkpoint_id, token, changes_feedback)
        assert isinstance(req_changes_resp, dict)

    finally:
        if project_id and token:
            delete_project(project_id, token)


test_approve_reject_and_request_changes_on_checkpoints()
