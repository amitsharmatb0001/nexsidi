import requests
import uuid

BASE_URL = "http://localhost:8000"
SIGNUP_URL = f"{BASE_URL}/api/auth/signup"
TIMEOUT = 30


def test_user_signup_with_valid_and_duplicate_email():
    headers = {"Content-Type": "application/json"}

    # Generate a unique email to avoid clash with existing users
    unique_email = f"testuser_{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPassword123!"

    signup_payload = {
        "email": unique_email,
        "password": password,
        "name": "Test User"
    }

    # 1) Attempt valid signup - expect 201 Created
    response = requests.post(SIGNUP_URL, headers=headers, json=signup_payload, timeout=TIMEOUT)
    assert response.status_code == 201, f"Expected 201 Created, got {response.status_code}, response: {response.text}"

    # 2) Attempt signup again with the same email - expect 400 Bad Request for duplicate
    duplicate_response = requests.post(SIGNUP_URL, headers=headers, json=signup_payload, timeout=TIMEOUT)
    assert duplicate_response.status_code == 400, (
        f"Expected 400 Bad Request for duplicate email, got {duplicate_response.status_code}, "
        f"response: {duplicate_response.text}"
    )

    # Optionally could check error message if returned in response JSON:
    try:
        error_data = duplicate_response.json()
        assert (
            "email" in error_data.get("detail", "") or "exists" in error_data.get("detail", "").lower()
        ), "Error detail does not indicate duplicate email"
    except (ValueError, KeyError):
        # If response is not JSON or does not contain expected fields, pass silently
        pass


test_user_signup_with_valid_and_duplicate_email()
