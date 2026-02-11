import requests
import websocket
import threading
import time
import json

BASE_URL = "http://localhost:8000"
WS_BASE_URL = "ws://localhost:8000"
TIMEOUT = 30

# Replace these with valid credentials for your test environment
TEST_USER_EMAIL = "testuser_tc010@example.com"
TEST_USER_PASSWORD = "TestPassword123!"

def test_websocket_connection_for_real_time_project_updates():
    # Helper: Signup user (ignore if exists)
    def signup():
        url = f"{BASE_URL}/api/auth/signup"
        payload = {"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}
        try:
            r = requests.post(url, json=payload, timeout=TIMEOUT)
            if r.status_code not in (201, 400):
                r.raise_for_status()
        except requests.RequestException:
            pass

    # Helper: Login user and get access token
    def login():
        url = f"{BASE_URL}/api/auth/login"
        payload = {"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        assert "access_token" in data and data["token_type"] == "bearer"
        return data["access_token"]

    # Helper: Create a project, return project_id
    def create_project(token):
        url = f"{BASE_URL}/api/projects"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"title": "Test Project TC010", "description": "Test project for websocket connection."}
        r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        assert r.status_code == 201
        return r.json().get("id") or r.json().get("project_id") or r.json().get("uuid") or r.json().get("id") or r.headers.get('location') or r.json().get("id")

    # Helper: Delete the project
    def delete_project(token, project_id):
        url = f"{BASE_URL}/api/projects/{project_id}"
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.delete(url, headers=headers, timeout=TIMEOUT)
        if r.status_code not in (204, 404):
            r.raise_for_status()

    signup()
    token = login()
    headers = {"Authorization": f"Bearer {token}"}
    project_id = None
    ws_url = None

    try:
        # Create project
        project_id = create_project(token)
        assert project_id is not None and len(project_id) > 0

        # Construct WebSocket URL for project
        ws_url = f"{WS_BASE_URL}/api/ws/projects/{project_id}"

        # Prepare to capture messages received via websocket
        received_messages = []
        error_in_ws = []

        def on_message(ws, message):
            received_messages.append(message)

        def on_error(ws, error):
            error_in_ws.append(error)

        def on_open(ws):
            pass

        def on_close(ws, close_status_code, close_msg):
            pass

        # Create websocket app with timeout
        ws = websocket.WebSocketApp(
            ws_url,
            header=[f"Authorization: Bearer {token}"],
            on_message=on_message,
            on_error=on_error,
            on_open=on_open,
            on_close=on_close,
        )

        # Run websocket in background thread to allow timeout
        wst = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 5, "ping_timeout": 3})
        wst.daemon = True
        wst.start()

        # Wait some time to receive messages or connection events
        timeout = 15
        start_time = time.time()
        while time.time() - start_time < timeout:
            if error_in_ws:
                break
            # Wait until at least one message is received or timeout
            if received_messages:
                break
            time.sleep(0.1)

        # Close websocket connection gracefully
        ws.close()

        # Assertions
        assert not error_in_ws, f"WebSocket error occurred: {error_in_ws}"
        # Confirm at least one message received or connection established (status 101 handled internally)
        assert received_messages or wst.is_alive() is False

        # Optionally validate JSON format of received messages if any
        for msg in received_messages:
            try:
                json.loads(msg)
            except Exception as e:
                assert False, f"Received message is not valid JSON: {msg}"

    finally:
        # Cleanup project if created
        if project_id:
            delete_project(token, project_id)

test_websocket_connection_for_real_time_project_updates()