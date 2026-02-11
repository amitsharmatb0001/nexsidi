import requests

BASE_URL = "http://localhost:8000"
LOGIN_ENDPOINT = "/api/auth/login"
SIGNUP_ENDPOINT = "/api/auth/signup"
TIMEOUT = 30

def test_user_login_with_valid_and_invalid_credentials():
    # Define valid user credentials
    valid_email = "testuser_tc004@example.com"
    valid_password = "SecurePass123!"

    # First, ensure the user exists by attempting signup (ignore if already exists)
    signup_payload = {
        "email": valid_email,
        "password": valid_password
    }
    try:
        signup_resp = requests.post(
            BASE_URL + SIGNUP_ENDPOINT,
            json=signup_payload,
            timeout=TIMEOUT
        )
        # 201 Created is expected for new user, 400 if already exists
        assert signup_resp.status_code in (201, 400), f"Signup failed with unexpected status code {signup_resp.status_code}"
    except requests.RequestException as e:
        assert False, f"Signup request failed: {e}"

    # Test login with valid credentials
    login_payload_valid = {
        "email": valid_email,
        "password": valid_password
    }
    try:
        login_resp_valid = requests.post(
            BASE_URL + LOGIN_ENDPOINT,
            json=login_payload_valid,
            timeout=TIMEOUT
        )
        assert login_resp_valid.status_code == 200, f"Valid login failed with status code {login_resp_valid.status_code}"
        login_json = login_resp_valid.json()
        assert "access_token" in login_json and isinstance(login_json["access_token"], str) and login_json["access_token"], \
            "Valid login response does not contain a valid access_token"
        assert "token_type" in login_json and login_json["token_type"].lower() == "bearer", \
            "Valid login response token_type is missing or not 'bearer'"
    except requests.RequestException as e:
        assert False, f"Valid login request failed: {e}"

    # Test login with invalid credentials (wrong password)
    login_payload_invalid = {
        "email": valid_email,
        "password": "WrongPassword123!"
    }
    try:
        login_resp_invalid = requests.post(
            BASE_URL + LOGIN_ENDPOINT,
            json=login_payload_invalid,
            timeout=TIMEOUT
        )
        assert login_resp_invalid.status_code == 401, f"Invalid login did not return 401, got {login_resp_invalid.status_code}"
    except requests.RequestException as e:
        assert False, f"Invalid login request failed: {e}"


test_user_login_with_valid_and_invalid_credentials()
