import requests

def test_health_check_endpoint_returns_system_status():
    url = "http://localhost:8000/health"
    timeout = 30
    headers = {
        "Accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        assert False, f"Request to /health endpoint failed: {e}"

    assert response.status_code == 200, f"Expected status code 200 but got {response.status_code}"

    try:
        data = response.json()
    except ValueError:
        assert False, "Response is not valid JSON"

    # Validate that response contains required keys with correct data types
    assert "status" in data, "Response JSON missing 'status' key"
    assert isinstance(data["status"], str), "'status' should be a string"

    assert "queue_size" in data, "Response JSON missing 'queue_size' key"
    assert isinstance(data["queue_size"], int), "'queue_size' should be an integer"

    assert "alerts" in data, "Response JSON missing 'alerts' key"
    assert isinstance(data["alerts"], list), "'alerts' should be a list"

test_health_check_endpoint_returns_system_status()