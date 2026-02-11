import requests

def test_root_endpoint_returns_api_information():
    base_url = "http://localhost:8000"
    timeout = 30
    try:
        response = requests.get(f"{base_url}/", timeout=timeout)
        assert response.status_code == 200, f"Expected status code 200 but got {response.status_code}"
        json_response = response.json()
        # Validate expected keys in response
        expected_keys = {"message", "version", "status", "docs", "description"}
        assert expected_keys.issubset(json_response.keys()), f"Response JSON keys missing. Expected at least {expected_keys}, got {json_response.keys()}"
        # Validate types
        assert isinstance(json_response["message"], str), "Field 'message' should be a string"
        assert isinstance(json_response["version"], str), "Field 'version' should be a string"
        assert isinstance(json_response["status"], str), "Field 'status' should be a string"
        assert isinstance(json_response["docs"], str), "Field 'docs' should be a string"
        assert isinstance(json_response["description"], str), "Field 'description' should be a string"
        # Additional sanity checks (optional)
        assert len(json_response["message"]) > 0, "Message should not be empty"
        assert len(json_response["version"]) > 0, "Version should not be empty"
        assert len(json_response["docs"]) > 0, "Docs URL should not be empty"
        assert len(json_response["description"]) > 0, "Description should not be empty"
    except requests.RequestException as e:
        assert False, f"Request to root endpoint failed: {e}"

test_root_endpoint_returns_api_information()