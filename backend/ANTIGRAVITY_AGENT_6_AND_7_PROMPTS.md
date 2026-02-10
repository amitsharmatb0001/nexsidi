# AGENT 6: USER APPROVAL FLOW (Visual Preview System)
**Agent Name:** "Approval System Builder"  
**Priority:** HIGH  
**Time Estimate:** 3-4 hours  
**Dependencies:** Requires Vikram (architect) to be built first

---

## TASK DESCRIPTION

Build a user approval system with **VISUAL PREVIEWS** (not code!) where non-technical users can:
1. See a mockup of their app before we build it
2. Test the real working app before final delivery
3. Approve or request changes at each stage

**CRITICAL:** Users are NOT developers - show them the actual app, not technical details!

---

## WHAT TO BUILD

### File 1: `app/services/approval_service.py`

Create a service that manages approval checkpoints:

```python
"""
Approval Service - Visual Preview & User Approval System
"""
from typing import Dict, Optional
from enum import Enum
from datetime import datetime
import asyncio


class ApprovalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class ApprovalService:
    """
    Manages user approval checkpoints with visual previews
    """
    
    def __init__(self, project_id: str):
        self.project_id = project_id
    
    async def create_design_preview_checkpoint(
        self, 
        blueprint: Dict,
        vanya_design: Dict
    ) -> Dict:
        """
        CHECKPOINT 1: After Vikram designs architecture
        
        Shows user:
        - Interactive mockup (from Vanya)
        - Visual preview of their app
        - Price and timeline
        - Approve/Reject buttons
        
        Args:
            blueprint: Vikram's architecture blueprint
            vanya_design: Vanya's design system (colors, components, mockup)
        
        Returns:
            {
                "checkpoint_id": "cp-123",
                "type": "design_preview",
                "status": "pending",
                "preview_data": {
                    "mockup_html": "<html>...</html>",  # Interactive preview
                    "mockup_url": "https://preview.nexsidi.com/proj-123",
                    "design_system": {...},
                    "features_included": ["Login", "Posts", "Comments"],
                    "cost_inr": 45000,
                    "timeline_hours": 12
                }
            }
        """
        
        # Generate interactive mockup URL
        mockup_url = await self._generate_mockup_preview(
            blueprint, 
            vanya_design
        )
        
        # Create checkpoint
        checkpoint = {
            "checkpoint_id": f"cp-{self.project_id}-design",
            "type": "design_preview",
            "status": ApprovalStatus.PENDING.value,
            "created_at": datetime.now().isoformat(),
            "preview_data": {
                "mockup_url": mockup_url,
                "mockup_html": vanya_design.get("preview_html"),
                "features_included": self._extract_features(blueprint),
                "cost_inr": blueprint.get("cost", 45000),
                "timeline_hours": blueprint.get("timeline_hours", 12),
                "design_preview": {
                    "primary_color": vanya_design.get("colors", {}).get("primary"),
                    "font": vanya_design.get("typography", {}).get("font_family"),
                    "layout": vanya_design.get("layout_preview")
                }
            },
            "expires_at": None  # No expiry - wait for user
        }
        
        # Store in database
        await self._store_checkpoint(checkpoint)
        
        # Send to frontend via WebSocket
        await self._notify_user_approval_needed(checkpoint)
        
        return checkpoint
    
    async def create_testing_checkpoint(
        self,
        deployment_urls: Dict,
        test_credentials: Dict
    ) -> Dict:
        """
        CHECKPOINT 2: After code is built and deployed to test environment
        
        Shows user:
        - Live working app they can test
        - Test login credentials
        - Instructions to try features
        - Approve/Request Changes buttons
        
        Args:
            deployment_urls: {
                "backend": "https://test-api-xyz.run.app",
                "frontend": "https://test-app-xyz.run.app"
            }
            test_credentials: {
                "admin_email": "admin@test.com",
                "admin_password": "test123"
            }
        
        Returns:
            {
                "checkpoint_id": "cp-456",
                "type": "testing",
                "status": "pending",
                "test_data": {
                    "app_url": "https://test-app-xyz.run.app",
                    "login": {...},
                    "test_instructions": [...]
                }
            }
        """
        
        checkpoint = {
            "checkpoint_id": f"cp-{self.project_id}-testing",
            "type": "testing",
            "status": ApprovalStatus.PENDING.value,
            "created_at": datetime.now().isoformat(),
            "test_data": {
                "app_url": deployment_urls["frontend"],
                "api_url": deployment_urls["backend"],
                "test_credentials": test_credentials,
                "test_instructions": [
                    "1. Click the link above to open your app",
                    "2. Log in using the test credentials",
                    "3. Try creating a blog post",
                    "4. Add a comment to test that feature",
                    "5. Check the admin dashboard",
                    "6. Test on your phone (responsive design)"
                ],
                "features_to_test": self._get_features_checklist()
            }
        }
        
        await self._store_checkpoint(checkpoint)
        await self._notify_user_approval_needed(checkpoint)
        
        return checkpoint
    
    async def wait_for_approval(self, checkpoint_id: str) -> Dict:
        """
        Wait for user to approve or reject
        
        This is a blocking call that waits until user clicks:
        - [Approve] button
        - [Reject] button
        - [Request Changes] button
        
        Returns:
            {
                "status": "approved" | "rejected" | "changes_requested",
                "user_feedback": "Optional text from user",
                "timestamp": "..."
            }
        """
        
        # Poll database for approval status
        while True:
            checkpoint = await self._get_checkpoint(checkpoint_id)
            
            if checkpoint["status"] != ApprovalStatus.PENDING.value:
                return {
                    "status": checkpoint["status"],
                    "user_feedback": checkpoint.get("user_feedback"),
                    "timestamp": checkpoint.get("updated_at")
                }
            
            # Wait 2 seconds before checking again
            await asyncio.sleep(2)
    
    async def _generate_mockup_preview(
        self, 
        blueprint: Dict, 
        design: Dict
    ) -> str:
        """
        Generate interactive HTML mockup
        
        This creates a clickable preview that looks like the real app
        but doesn't have backend functionality yet
        """
        
        # Use Vanya's design to create HTML preview
        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>{project_name} - Preview</title>
            <style>
                {css_from_design_system}
            </style>
        </head>
        <body>
            {mockup_components}
            <div class="preview-badge">
                ⚠️ This is a preview - Click Approve to build the real app
            </div>
        </body>
        </html>
        """
        
        # Save to temporary hosting
        preview_url = await self._upload_preview_html(html_template)
        
        return preview_url
    
    async def _notify_user_approval_needed(self, checkpoint: Dict):
        """
        Send notification to user via WebSocket
        """
        from app.api.websocket import manager
        
        await manager.broadcast_to_project(
            self.project_id,
            {
                "type": "approval_required",
                "checkpoint": checkpoint
            }
        )
    
    async def _extract_features(self, blueprint: Dict) -> list:
        """Extract user-friendly feature list from blueprint"""
        features = []
        
        # Convert technical endpoints to user-friendly features
        # Example: "POST /api/posts" → "Write blog posts"
        
        return features
    
    async def _get_features_checklist(self) -> list:
        """Get checklist of features for user to test"""
        return [
            {"feature": "User Login", "tested": False},
            {"feature": "Create Post", "tested": False},
            {"feature": "Add Comment", "tested": False},
            {"feature": "Admin Dashboard", "tested": False}
        ]
```

---

### File 2: Update `app/agents/arjun.py`

Add approval checkpoints to the pipeline:

```python
# In arjun.py, modify execute_pipeline method

async def execute_pipeline(self, requirements: Dict) -> PipelineResult:
    """
    Execute pipeline WITH user approval checkpoints
    """
    
    from app.services.approval_service import ApprovalService
    
    approval_service = ApprovalService(self.project_id)
    
    # ... (existing code: Saanvi analyzes requirements)
    
    # NEW: After Vikram designs, get user approval
    self.logger.info("📋 Vikram creating architecture blueprint...")
    vikram_blueprint = await vikram.design_architecture(saanvi_output)
    
    self.logger.info("🎨 Vanya creating design system...")
    vanya_design = await vanya.create_design_system(vikram_blueprint)
    
    # CHECKPOINT 1: Show user the mockup
    self.logger.info("⏸️ Waiting for user approval on design...")
    checkpoint_1 = await approval_service.create_design_preview_checkpoint(
        vikram_blueprint,
        vanya_design
    )
    
    # WAIT for user to approve
    approval_1 = await approval_service.wait_for_approval(
        checkpoint_1["checkpoint_id"]
    )
    
    if approval_1["status"] == "rejected":
        self.logger.info("❌ User rejected design - stopping pipeline")
        return PipelineResult(success=False, reason="user_rejected")
    
    if approval_1["status"] == "changes_requested":
        # User wants changes - loop back to Vikram
        user_feedback = approval_1["user_feedback"]
        # ... handle redesign ...
    
    self.logger.info("✅ User approved design - starting development...")
    
    # ... (existing code: Shubham, Aanya generate code)
    
    # Deploy to TEST environment first
    self.logger.info("🧪 Deploying to test environment...")
    test_urls = await pranav.deploy_to_test_environment(
        backend_path,
        frontend_path
    )
    
    # CHECKPOINT 2: Let user test the real app
    self.logger.info("⏸️ Waiting for user to test the app...")
    checkpoint_2 = await approval_service.create_testing_checkpoint(
        test_urls,
        {"admin_email": "admin@test.com", "admin_password": "test123"}
    )
    
    # WAIT for user to test and approve
    approval_2 = await approval_service.wait_for_approval(
        checkpoint_2["checkpoint_id"]
    )
    
    if approval_2["status"] == "approved":
        self.logger.info("✅ User approved app - deploying to production...")
        production_urls = await pranav.deploy_to_production(
            backend_path,
            frontend_path
        )
        return PipelineResult(success=True, urls=production_urls)
    else:
        # User wants changes
        change_requests = approval_2["user_feedback"]
        # ... handle revisions ...
```

---

### File 3: Frontend Component `frontend/app/components/ApprovalCheckpoint.tsx`

Create React component that shows approval UI:

```typescript
/**
 * Approval Checkpoint Component
 * Shows visual preview and approval buttons to user
 */
import { useState } from 'react';

interface ApprovalCheckpointProps {
  checkpoint: {
    type: 'design_preview' | 'testing';
    preview_data?: any;
    test_data?: any;
  };
  onApprove: () => void;
  onReject: () => void;
  onRequestChanges: (feedback: string) => void;
}

export function ApprovalCheckpoint({ checkpoint, onApprove, onReject, onRequestChanges }: ApprovalCheckpointProps) {
  const [feedback, setFeedback] = useState('');
  const [showFeedback, setShowFeedback] = useState(false);
  
  if (checkpoint.type === 'design_preview') {
    return (
      <div className="approval-card">
        <h2>Your App Design Preview</h2>
        
        {/* Interactive mockup */}
        <div className="mockup-preview">
          <iframe 
            src={checkpoint.preview_data.mockup_url}
            width="100%"
            height="600px"
            title="App Preview"
          />
        </div>
        
        {/* Feature list */}
        <div className="features">
          <h3>Features Included:</h3>
          <ul>
            {checkpoint.preview_data.features_included.map(f => (
              <li key={f}>✅ {f}</li>
            ))}
          </ul>
        </div>
        
        {/* Pricing */}
        <div className="pricing">
          <p><strong>Price:</strong> ₹{checkpoint.preview_data.cost_inr.toLocaleString()}</p>
          <p><strong>Timeline:</strong> {checkpoint.preview_data.timeline_hours} hours</p>
        </div>
        
        {/* Approval buttons */}
        <div className="actions">
          <button 
            onClick={onApprove}
            className="btn-approve"
          >
            ✅ Looks Perfect! Start Building
          </button>
          
          <button 
            onClick={() => setShowFeedback(true)}
            className="btn-changes"
          >
            🔧 Request Changes
          </button>
          
          {showFeedback && (
            <div className="feedback-box">
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="What would you like to change? (e.g., 'I want blue color instead of red')"
              />
              <button onClick={() => onRequestChanges(feedback)}>
                Submit Changes
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }
  
  if (checkpoint.type === 'testing') {
    return (
      <div className="testing-card">
        <h2>Your App is Ready - Test It Now!</h2>
        
        {/* Live app link */}
        <div className="live-preview">
          <a 
            href={checkpoint.test_data.app_url}
            target="_blank"
            className="test-link"
          >
            🌐 Open Your App →
          </a>
        </div>
        
        {/* Test credentials */}
        <div className="test-info">
          <h3>Test Login:</h3>
          <p>Email: {checkpoint.test_data.test_credentials.admin_email}</p>
          <p>Password: {checkpoint.test_data.test_credentials.admin_password}</p>
        </div>
        
        {/* Test instructions */}
        <div className="instructions">
          <h3>Please Test These Features:</h3>
          <ol>
            {checkpoint.test_data.test_instructions.map((instruction, i) => (
              <li key={i}>{instruction}</li>
            ))}
          </ol>
        </div>
        
        {/* Approval */}
        <div className="actions">
          <button 
            onClick={onApprove}
            className="btn-approve"
          >
            ✅ Everything Works! Go Live
          </button>
          
          <button 
            onClick={() => setShowFeedback(true)}
            className="btn-changes"
          >
            🔧 Needs Some Fixes
          </button>
        </div>
      </div>
    );
  }
  
  return null;
}
```

---

## API ENDPOINTS NEEDED

Add these to `app/api/approvals.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from app.services.approval_service import ApprovalService

router = APIRouter()

@router.post("/projects/{project_id}/approve/{checkpoint_id}")
async def approve_checkpoint(
    project_id: str,
    checkpoint_id: str,
    current_user = Depends(get_current_user)
):
    """User clicked [Approve] button"""
    approval_service = ApprovalService(project_id)
    await approval_service.approve_checkpoint(checkpoint_id)
    return {"status": "approved"}

@router.post("/projects/{project_id}/reject/{checkpoint_id}")
async def reject_checkpoint(
    project_id: str,
    checkpoint_id: str,
    feedback: str = None,
    current_user = Depends(get_current_user)
):
    """User clicked [Reject] or [Request Changes]"""
    approval_service = ApprovalService(project_id)
    await approval_service.reject_checkpoint(checkpoint_id, feedback)
    return {"status": "rejected", "feedback": feedback}
```

---

## TESTING CHECKLIST

After implementation, verify:

- [ ] User sees mockup preview (not code!)
- [ ] User can click "Approve" or "Reject"
- [ ] Pipeline waits for user approval (doesn't continue automatically)
- [ ] After approval, pipeline continues
- [ ] User can test real working app before final delivery
- [ ] User can request changes at any stage

---

## NOTES FOR ANTIGRAVITY

- Focus on **visual preview** - users should see their app, not technical details
- Make approval buttons big and obvious
- Use simple language (no "deployment", "API", "database" - say "your app", "features", "test it")
- Ensure the mockup actually looks like their app will look
- Test environment must be fully functional (user can click around)

---

**END OF AGENT 6 TASK**

---
---
---

# AGENT 7: EMAIL NOTIFICATION SYSTEM
**Agent Name:** "Email Service Builder"  
**Priority:** MEDIUM  
**Time Estimate:** 2-3 hours  
**Dependencies:** Needs Gmail API credentials from user

---

## TASK DESCRIPTION

Build an automated email notification system that sends:
1. **SDD Document** after user approves design
2. **Progress Updates** (optional) during development
3. **Completion Email** with live app URLs and login credentials

**Use Gmail API** (not SMTP) for better deliverability.

---

## WHAT TO BUILD

### File 1: `app/services/email_service.py`

```python
"""
Email Notification Service
Sends automated emails at key project milestones
"""
import os
from typing import Dict, List, Optional
from datetime import datetime
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import logging


class EmailService:
    """
    Send professional emails using Gmail API
    """
    
    def __init__(self):
        self.logger = logging.getLogger("email_service")
        self._setup_gmail_api()
    
    def _setup_gmail_api(self):
        """
        Setup Gmail API client
        
        Requires environment variables:
        - GMAIL_CLIENT_ID
        - GMAIL_CLIENT_SECRET
        - GMAIL_REFRESH_TOKEN
        
        Or: GMAIL_CREDENTIALS_JSON (path to credentials file)
        """
        
        creds_path = os.getenv("GMAIL_CREDENTIALS_JSON")
        
        if creds_path and os.path.exists(creds_path):
            # Load from file
            self.creds = Credentials.from_authorized_user_file(creds_path)
        else:
            # Load from environment variables
            self.creds = Credentials(
                token=None,
                refresh_token=os.getenv("GMAIL_REFRESH_TOKEN"),
                client_id=os.getenv("GMAIL_CLIENT_ID"),
                client_secret=os.getenv("GMAIL_CLIENT_SECRET"),
                token_uri="https://oauth2.googleapis.com/token"
            )
        
        self.service = build('gmail', 'v1', credentials=self.creds)
        self.sender_email = os.getenv("GMAIL_SENDER_EMAIL", "noreply@nexsidi.com")
    
    async def send_sdd_email(
        self,
        to_email: str,
        project_name: str,
        sdd_pdf_path: str,
        project_details: Dict
    ):
        """
        EMAIL 1: Send SDD document after user approves design
        
        Args:
            to_email: User's email
            project_name: "Blog App", "E-commerce Store", etc.
            sdd_pdf_path: Path to generated SDD PDF file
            project_details: {
                "cost": 45000,
                "timeline_hours": 12,
                "features": ["Login", "Posts", "Comments"]
            }
        """
        
        subject = f"✅ {project_name} - Project Started!"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center; color: white;">
                <h1>🎉 Your Project Has Started!</h1>
            </div>
            
            <div style="padding: 30px; background: #f9fafb;">
                <h2>Hi there!</h2>
                
                <p>Great news! Your <strong>{project_name}</strong> is now being built by our AI development team.</p>
                
                <div style="background: white; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3>📊 Project Details</h3>
                    <p><strong>Features:</strong></p>
                    <ul>
                        {''.join(f'<li>{feature}</li>' for feature in project_details['features'])}
                    </ul>
                    <p><strong>Investment:</strong> ₹{project_details['cost']:,}</p>
                    <p><strong>Expected Delivery:</strong> {project_details['timeline_hours']} hours</p>
                </div>
                
                <div style="background: #fef3c7; padding: 15px; border-left: 4px solid #f59e0b; margin: 20px 0;">
                    <p><strong>📄 Attached:</strong> Software Design Document (SDD)</p>
                    <p>This document contains the complete technical blueprint of your application.</p>
                </div>
                
                <h3>What Happens Next?</h3>
                <ol>
                    <li>Our AI agents are currently building your app</li>
                    <li>You'll receive progress updates (optional)</li>
                    <li>We'll email you when it's ready to test</li>
                    <li>After your approval, it goes live!</li>
                </ol>
                
                <div style="text-align: center; margin-top: 30px;">
                    <a href="https://nexsidi.com/projects/{project_details.get('project_id')}" 
                       style="background: #667eea; color: white; padding: 12px 30px; text-decoration: none; border-radius: 6px; display: inline-block;">
                        Track Progress →
                    </a>
                </div>
                
                <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">
                    Questions? Reply to this email or contact support@nexsidi.com
                </p>
            </div>
            
            <div style="background: #1f2937; color: #9ca3af; padding: 20px; text-align: center; font-size: 12px;">
                <p>NexSidi - AI-Powered Software Development</p>
                <p>Bhilwara, Rajasthan, India</p>
            </div>
        </body>
        </html>
        """
        
        # Create message with attachment
        message = MIMEMultipart()
        message['to'] = to_email
        message['from'] = self.sender_email
        message['subject'] = subject
        
        # Add HTML body
        message.attach(MIMEText(html_body, 'html'))
        
        # Attach SDD PDF
        if os.path.exists(sdd_pdf_path):
            with open(sdd_pdf_path, 'rb') as f:
                pdf_attachment = MIMEApplication(f.read(), _subtype='pdf')
                pdf_attachment.add_header(
                    'Content-Disposition', 
                    'attachment', 
                    filename=f'{project_name}_SDD.pdf'
                )
                message.attach(pdf_attachment)
        
        # Send via Gmail API
        await self._send_gmail_message(message)
        
        self.logger.info(f"✅ Sent SDD email to {to_email}")
    
    async def send_progress_email(
        self,
        to_email: str,
        project_name: str,
        progress_percentage: int,
        current_phase: str
    ):
        """
        EMAIL 2 (Optional): Send progress update during development
        
        Args:
            to_email: User's email
            project_name: "Blog App"
            progress_percentage: 45
            current_phase: "Backend development in progress"
        """
        
        subject = f"🔄 {project_name} - {progress_percentage}% Complete"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="padding: 30px; background: #f9fafb;">
                <h2>Progress Update: {project_name}</h2>
                
                <div style="background: white; padding: 20px; border-radius: 8px;">
                    <h3>Current Status</h3>
                    <p><strong>{current_phase}</strong></p>
                    
                    <!-- Progress bar -->
                    <div style="background: #e5e7eb; height: 30px; border-radius: 15px; overflow: hidden; margin: 20px 0;">
                        <div style="background: linear-gradient(90deg, #667eea 0%, #764ba2 100%); height: 100%; width: {progress_percentage}%; text-align: center; line-height: 30px; color: white; font-weight: bold;">
                            {progress_percentage}%
                        </div>
                    </div>
                    
                    <p style="color: #6b7280;">Your app is being built. We'll notify you when it's ready to test!</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        message = MIMEText(html_body, 'html')
        message['to'] = to_email
        message['from'] = self.sender_email
        message['subject'] = subject
        
        await self._send_gmail_message(message)
        
        self.logger.info(f"✅ Sent progress email to {to_email}")
    
    async def send_completion_email(
        self,
        to_email: str,
        project_name: str,
        app_urls: Dict,
        login_credentials: Dict
    ):
        """
        EMAIL 3: Send completion notification with live URLs
        
        Args:
            to_email: User's email
            project_name: "Blog App"
            app_urls: {
                "frontend": "https://myblog-xyz.run.app",
                "backend": "https://myblog-api-xyz.run.app"
            }
            login_credentials: {
                "admin_email": "admin@myblog.com",
                "admin_password": "secure_password_123"
            }
        """
        
        subject = f"🎉 {project_name} is Ready!"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 40px; text-align: center; color: white;">
                <h1>🎊 Your App is Live!</h1>
                <p style="font-size: 18px; margin-top: 10px;">Your {project_name} is ready to use</p>
            </div>
            
            <div style="padding: 30px; background: #f9fafb;">
                <h2>Congratulations!</h2>
                <p>Your application has been built, tested, and deployed. It's now live and ready for your users!</p>
                
                <!-- App URL -->
                <div style="background: white; padding: 25px; border-radius: 8px; margin: 20px 0; text-align: center;">
                    <h3>🌐 Your Live App</h3>
                    <a href="{app_urls['frontend']}" 
                       style="background: #10b981; color: white; padding: 15px 40px; text-decoration: none; border-radius: 8px; display: inline-block; font-size: 18px; margin: 10px 0;">
                        Open Your App →
                    </a>
                    <p style="color: #6b7280; font-size: 14px; margin-top: 10px;">
                        {app_urls['frontend']}
                    </p>
                </div>
                
                <!-- Login Credentials -->
                <div style="background: #fef3c7; padding: 20px; border-radius: 8px; border-left: 4px solid #f59e0b; margin: 20px 0;">
                    <h3>🔐 Your Admin Login</h3>
                    <p><strong>Email:</strong> {login_credentials['admin_email']}</p>
                    <p><strong>Password:</strong> {login_credentials['admin_password']}</p>
                    <p style="color: #92400e; font-size: 14px; margin-top: 10px;">
                        ⚠️ Please change this password after your first login!
                    </p>
                </div>
                
                <!-- What's Included -->
                <div style="background: white; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3>✨ What's Included</h3>
                    <ul style="line-height: 1.8;">
                        <li>✅ Fully functional application</li>
                        <li>✅ Admin dashboard access</li>
                        <li>✅ Secure authentication</li>
                        <li>✅ Mobile responsive design</li>
                        <li>✅ Database setup complete</li>
                        <li>✅ Hosted on Google Cloud</li>
                    </ul>
                </div>
                
                <!-- Support -->
                <div style="background: #e0e7ff; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3>💬 Need Changes?</h3>
                    <p>You have <strong>11 minor changes</strong> and <strong>5 major changes</strong> included in your package.</p>
                    <p>Just reply to this email with what you'd like to modify!</p>
                </div>
                
                <!-- API Info (if user is technical) -->
                <details style="margin: 20px 0;">
                    <summary style="cursor: pointer; color: #667eea; font-weight: bold;">🔧 For Developers: API Information</summary>
                    <div style="background: #f3f4f6; padding: 15px; border-radius: 6px; margin-top: 10px;">
                        <p><strong>API Base URL:</strong></p>
                        <code style="background: #1f2937; color: #10b981; padding: 8px; display: block; border-radius: 4px; margin: 5px 0;">
                            {app_urls['backend']}
                        </code>
                        <p style="margin-top: 15px;"><strong>API Documentation:</strong></p>
                        <a href="{app_urls['backend']}/docs" style="color: #667eea;">
                            {app_urls['backend']}/docs
                        </a>
                    </div>
                </details>
                
                <div style="text-align: center; margin-top: 40px;">
                    <p style="font-size: 18px; color: #059669; font-weight: bold;">Thank you for choosing NexSidi!</p>
                    <p style="color: #6b7280;">We'd love to hear your feedback.</p>
                </div>
            </div>
            
            <div style="background: #1f2937; color: #9ca3af; padding: 20px; text-align: center; font-size: 12px;">
                <p>NexSidi - AI-Powered Software Development</p>
                <p>Bhilwara, Rajasthan, India</p>
                <p style="margin-top: 10px;">
                    <a href="mailto:support@nexsidi.com" style="color: #667eea; text-decoration: none;">support@nexsidi.com</a> |
                    <a href="https://nexsidi.com" style="color: #667eea; text-decoration: none;">nexsidi.com</a>
                </p>
            </div>
        </body>
        </html>
        """
        
        message = MIMEText(html_body, 'html')
        message['to'] = to_email
        message['from'] = self.sender_email
        message['subject'] = subject
        
        await self._send_gmail_message(message)
        
        self.logger.info(f"✅ Sent completion email to {to_email}")
    
    async def _send_gmail_message(self, message):
        """
        Send email via Gmail API
        """
        try:
            # Encode message
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            
            # Send
            self.service.users().messages().send(
                userId='me',
                body={'raw': raw}
            ).execute()
            
        except HttpError as error:
            self.logger.error(f"Gmail API error: {error}")
            raise


# Global instance
email_service = EmailService()
```

---

### File 2: Update `app/agents/arjun.py` to Send Emails

```python
# In arjun.py

from app.services.email_service import email_service

async def execute_pipeline(self, requirements: Dict):
    # ... existing code ...
    
    # After user approves design checkpoint
    if approval_1["status"] == "approved":
        # Send SDD email
        await email_service.send_sdd_email(
            to_email=requirements["user_email"],
            project_name=requirements["project_name"],
            sdd_pdf_path=f"/workspace/projects/{self.project_id}/docs/SDD.pdf",
            project_details={
                "cost": 45000,
                "timeline_hours": 12,
                "features": ["Login", "Posts", "Comments"],
                "project_id": self.project_id
            }
        )
    
    # (Optional) Send progress updates during development
    await self._update_progress(PipelinePhase.DEVELOPMENT, 50)
    await email_service.send_progress_email(
        to_email=requirements["user_email"],
        project_name=requirements["project_name"],
        progress_percentage=50,
        current_phase="Backend development in progress"
    )
    
    # After deployment completes
    await email_service.send_completion_email(
        to_email=requirements["user_email"],
        project_name=requirements["project_name"],
        app_urls={
            "frontend": production_urls["frontend"],
            "backend": production_urls["backend"]
        },
        login_credentials={
            "admin_email": "admin@example.com",
            "admin_password": generated_password
        }
    )
```

---

## ENVIRONMENT VARIABLES NEEDED

User must provide these in `.env` file:

```bash
# Gmail API Configuration
GMAIL_CLIENT_ID=your-client-id-here
GMAIL_CLIENT_SECRET=your-client-secret-here
GMAIL_REFRESH_TOKEN=your-refresh-token-here
GMAIL_SENDER_EMAIL=noreply@nexsidi.com

# Alternative: Path to credentials JSON file
GMAIL_CREDENTIALS_JSON=/path/to/gmail-credentials.json
```

---

## HOW TO GET GMAIL API CREDENTIALS

### Step 1: Enable Gmail API
1. Go to: https://console.cloud.google.com/
2. Create new project (or select existing)
3. Enable "Gmail API"

### Step 2: Create OAuth Credentials
1. Go to: APIs & Services → Credentials
2. Click "Create Credentials" → "OAuth 2.0 Client ID"
3. Application type: "Web application"
4. Add redirect URI: `http://localhost:8080`
5. Download credentials JSON

### Step 3: Get Refresh Token
Run this Python script once:

```python
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/gmail.send']

flow = InstalledAppFlow.from_client_secrets_file(
    'credentials.json', SCOPES)
creds = flow.run_local_server(port=8080)

print(f"Refresh Token: {creds.refresh_token}")
```

Copy the refresh token to `.env` file.

---

## TESTING CHECKLIST

- [ ] User receives SDD email after approving design
- [ ] SDD PDF is attached to email
- [ ] Email renders correctly in Gmail/Outlook
- [ ] User receives completion email with live URLs
- [ ] URLs in email are clickable
- [ ] Login credentials are correct
- [ ] Progress emails work (if enabled)
- [ ] Emails don't go to spam folder

---

## NOTES FOR ANTIGRAVITY

- Test emails using a real Gmail account
- Make sure HTML renders properly (test in Gmail and Outlook)
- Use professional email templates (already provided above)
- Keep language simple and non-technical
- Include clear call-to-action buttons
- Always test that attachments work

---

**END OF AGENT 7 TASK**
