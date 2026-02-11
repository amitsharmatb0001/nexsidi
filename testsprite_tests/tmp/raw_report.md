
# TestSprite AI Testing Report(MCP)

---

## 1️⃣ Document Metadata
- **Project Name:** nexsidi
- **Date:** 2026-02-11
- **Prepared by:** TestSprite AI Team

---

## 2️⃣ Requirement Validation Summary

#### Test TC001 test_root_endpoint_returns_api_information
- **Test Code:** [TC001_test_root_endpoint_returns_api_information.py](./TC001_test_root_endpoint_returns_api_information.py)
- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/e57fd341-2150-4d7c-a245-ec4fed68b5d8
- **Status:** ✅ Passed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---

#### Test TC002 test_health_check_endpoint_returns_system_status
- **Test Code:** [TC002_test_health_check_endpoint_returns_system_status.py](./TC002_test_health_check_endpoint_returns_system_status.py)
- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/4cd473b2-715b-49a1-85a0-6a7957d5302e
- **Status:** ✅ Passed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---

#### Test TC003 test_user_signup_with_valid_and_duplicate_email
- **Test Code:** [TC003_test_user_signup_with_valid_and_duplicate_email.py](./TC003_test_user_signup_with_valid_and_duplicate_email.py)
- **Test Error:** Traceback (most recent call last):
  File "/var/task/handler.py", line 258, in run_with_retry
    exec(code, exec_env)
  File "<string>", line 44, in <module>
  File "<string>", line 24, in test_user_signup_with_valid_and_duplicate_email
AssertionError: Expected 201 Created, got 422, response: {"error":"Validation Error","details":[{"type":"missing","loc":["body","full_name"],"msg":"Field required","input":{"email":"testuser_374afed8@example.com","password":"StrongPassword123!","name":"Test User"}}],"message":"Invalid request data"}

- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/4b7af063-6f14-4903-8dd5-8977817ce2cb
- **Status:** ❌ Failed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---

#### Test TC004 test_user_login_with_valid_and_invalid_credentials
- **Test Code:** [TC004_test_user_login_with_valid_and_invalid_credentials.py](./TC004_test_user_login_with_valid_and_invalid_credentials.py)
- **Test Error:** Traceback (most recent call last):
  File "/var/task/handler.py", line 258, in run_with_retry
    exec(code, exec_env)
  File "<string>", line 65, in <module>
  File "<string>", line 25, in test_user_login_with_valid_and_invalid_credentials
AssertionError: Signup failed with unexpected status code 422

- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/d04dc5c2-3900-4adc-b1ce-f8700ee3fbaa
- **Status:** ❌ Failed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---

#### Test TC005 test_create_project_with_valid_data
- **Test Code:** [TC005_test_create_project_with_valid_data.py](./TC005_test_create_project_with_valid_data.py)
- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/81b08fcf-e83e-416a-bd41-acb7674493f0
- **Status:** ✅ Passed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---

#### Test TC006 test_send_message_to_tilotma_agent_and_receive_response
- **Test Code:** [TC006_test_send_message_to_tilotma_agent_and_receive_response.py](./TC006_test_send_message_to_tilotma_agent_and_receive_response.py)
- **Test Error:** Traceback (most recent call last):
  File "<string>", line 36, in test_send_message_to_tilotma_agent_and_receive_response
  File "/var/task/requests/models.py", line 1024, in raise_for_status
    raise HTTPError(http_error_msg, response=self)
requests.exceptions.HTTPError: 422 Client Error: Unprocessable Content for url: http://localhost:8000/api/auth/signup

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/var/task/handler.py", line 258, in run_with_retry
    exec(code, exec_env)
  File "<string>", line 109, in <module>
  File "<string>", line 38, in test_send_message_to_tilotma_agent_and_receive_response
AssertionError: Signup request failed: 422 Client Error: Unprocessable Content for url: http://localhost:8000/api/auth/signup

- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/041c26c0-fdf3-4690-becf-bb483dc94b0d
- **Status:** ❌ Failed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---

#### Test TC007 test_file_upload_single_and_multiple_files
- **Test Code:** [TC007_test_file_upload_single_and_multiple_files.py](./TC007_test_file_upload_single_and_multiple_files.py)
- **Test Error:** Traceback (most recent call last):
  File "/var/task/handler.py", line 258, in run_with_retry
    exec(code, exec_env)
  File "<string>", line 130, in <module>
  File "<string>", line 124, in test_file_upload_single_and_multiple_files
AssertionError: Uploaded filenames not found in project files list

- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/5e374319-27b8-440c-99c7-c17eab4adbcc
- **Status:** ❌ Failed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---

#### Test TC008 test_deploy_project_and_check_deployment_status
- **Test Code:** [TC008_test_deploy_project_and_check_deployment_status.py](./TC008_test_deploy_project_and_check_deployment_status.py)
- **Test Error:** Traceback (most recent call last):
  File "/var/task/handler.py", line 258, in run_with_retry
    exec(code, exec_env)
  File "<string>", line 123, in <module>
  File "<string>", line 77, in test_deploy_project_and_check_deployment_status
  File "<string>", line 28, in authenticate
  File "<string>", line 25, in authenticate
  File "/var/task/requests/models.py", line 1024, in raise_for_status
    raise HTTPError(http_error_msg, response=self)
requests.exceptions.HTTPError: 422 Client Error: Unprocessable Content for url: http://localhost:8000/api/auth/signup

- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/6caacd2a-1b0a-4052-8e49-2b13d60ecd04
- **Status:** ❌ Failed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---

#### Test TC009 test_approve_reject_and_request_changes_on_checkpoints
- **Test Code:** [TC009_test_approve_reject_and_request_changes_on_checkpoints.py](./TC009_test_approve_reject_and_request_changes_on_checkpoints.py)
- **Test Error:** Traceback (most recent call last):
  File "/var/task/handler.py", line 258, in run_with_retry
    exec(code, exec_env)
  File "<string>", line 186, in <module>
  File "<string>", line 150, in test_approve_reject_and_request_changes_on_checkpoints
  File "<string>", line 30, in signup_and_login
  File "/var/task/requests/models.py", line 1024, in raise_for_status
    raise HTTPError(http_error_msg, response=self)
requests.exceptions.HTTPError: 422 Client Error: Unprocessable Content for url: http://localhost:8000/api/auth/signup

- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/af48a4e0-395a-4f61-be91-cacaa4319fb4
- **Status:** ❌ Failed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---

#### Test TC010 test_websocket_connection_for_real_time_project_updates
- **Test Code:** [TC010_test_websocket_connection_for_real_time_project_updates.py](./TC010_test_websocket_connection_for_real_time_project_updates.py)
- **Test Error:** Traceback (most recent call last):
  File "/var/task/handler.py", line 258, in run_with_retry
    exec(code, exec_env)
  File "<string>", line 2, in <module>
ModuleNotFoundError: No module named 'websocket'

- **Test Visualization and Result:** https://www.testsprite.com/dashboard/mcp/tests/f7462927-b83b-4977-aa5d-c5350c2b392c/dd5ee720-dbd9-4066-b2b2-ea65d9ebf737
- **Status:** ❌ Failed
- **Analysis / Findings:** {{TODO:AI_ANALYSIS}}.
---


## 3️⃣ Coverage & Matching Metrics

- **30.00** of tests passed

| Requirement        | Total Tests | ✅ Passed | ❌ Failed  |
|--------------------|-------------|-----------|------------|
| ...                | ...         | ...       | ...        |
---


## 4️⃣ Key Gaps / Risks
{AI_GNERATED_KET_GAPS_AND_RISKS}
---