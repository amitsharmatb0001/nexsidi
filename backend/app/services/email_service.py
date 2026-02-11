"""
Email Notification Service - ZeptoMail Implementation
Sends automated emails via Zoho ZeptoMail SMTP with Secret Manager integration.
"""
import os
import smtplib
import ssl
import logging
from typing import Dict, List, Optional
from email.message import EmailMessage
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from app.core.secret_manager import get_secret_manager

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("email_service")


class EmailService:
    """
    Send professional emails using Zoho ZeptoMail SMTP.
    Credentials are preoccupation from Secret Manager for security.
    """
    
    def __init__(self):
        self.logger = logger
        self.secret_manager = get_secret_manager()
        
        # Load configuration
        self.smtp_server = self.secret_manager.get_secret("ZEPTOMAIL_SMTP_SERVER") or "smtp.zeptomail.in"
        self.port = int(self.secret_manager.get_secret("ZEPTOMAIL_SMTP_PORT") or 587)
        self.username = self.secret_manager.get_secret("ZEPTOMAIL_USERNAME") or "emailapikey"
        self.sender_email = self.secret_manager.get_secret("ZEPTOMAIL_SENDER_EMAIL") or "noreply@yugnex.com"
        
        # Password (API Key) must be in Secret Manager
        self._password = None
    
    @property
    def password(self):
        if not self._password:
            self._password = self.secret_manager.get_secret("ZEPTOMAIL_PASSWORD")
        return self._password

    async def send_sdd_email(
        self,
        to_email: str,
        project_name: str,
        sdd_pdf_path: str,
        project_details: Dict
    ):
        """
        EMAIL 1: Send SDD document after user approves design
        """
        subject = f"[OK] {project_name} - Project Started!"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #333;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center; color: white; border-radius: 8px 8px 0 0;">
                <h1 style="margin:0;">🎉 Project Started!</h1>
            </div>
            
            <div style="padding: 30px; background: #fff; border: 1px solid #eee; border-top: none;">
                <h2>Hello!</h2>
                <p>Great news! Your project <strong>{project_name}</strong> has officially entered development.</p>
                
                <div style="background: #f8fafc; padding: 20px; border-radius: 8px; margin: 20px 0; border: 1px solid #e2e8f0;">
                    <h3 style="margin-top:0; color: #475569;">[STATS] Project Summary</h3>
                    <p><strong>Investment:</strong> ₹{project_details.get('cost', 0):,}</p>
                    <p><strong>Estimated Timeline:</strong> {project_details.get('timeline_hours', 0)} hours</p>
                </div>
                
                <div style="background: #fffbef; padding: 15px; border-left: 4px solid #f59e0b; margin: 20px 0;">
                    <p><strong>📄 Attached:</strong> Your Software Design Document (SDD)</p>
                    <p style="font-size: 14px; color: #92400e;">This contains the full technical blueprint we're building.</p>
                </div>
                
                <p>We'll keep you updated as our AI agents build out your application.</p>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="https://nexsidi.com/projects/{project_details.get('project_id')}" 
                       style="background: #667eea; color: white; padding: 12px 30px; text-decoration: none; border-radius: 6px; font-weight: bold;">
                        Track Live Progress →
                    </a>
                </div>
            </div>
            
            <div style="background: #1f2937; color: #9ca3af; padding: 20px; text-align: center; font-size: 12px; border-radius: 0 0 8px 8px;">
                <p>NexSidi - AI-Powered Autonomous Software Engineering</p>
                <p>Bhilwara, Rajasthan, India</p>
            </div>
        </body>
        </html>
        """
        
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = self.sender_email
        msg['To'] = to_email
        msg.attach(MIMEText(html_body, 'html'))
        
        if os.path.exists(sdd_pdf_path):
            try:
                with open(sdd_pdf_path, 'rb') as f:
                    pdf_attachment = MIMEApplication(f.read(), _subtype='pdf')
                    pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f'{project_name}_SDD.pdf')
                    msg.attach(pdf_attachment)
            except Exception as e:
                self.logger.error(f"Could not attach PDF: {e}")
        
        await self._send_smtp_message(msg)
    
    async def send_progress_email(
        self,
        to_email: str,
        project_name: str,
        progress_percentage: int,
        current_phase: str
    ):
        """
        EMAIL 2: Send progress update
        """
        subject = f"🔄 {project_name} - {progress_percentage}% Complete"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="padding: 30px; background: #f9fafb; border: 1px solid #eee; border-radius: 8px;">
                <h2 style="color: #4f46e5;">Progress Update: {project_name}</h2>
                <div style="background: white; padding: 25px; border-radius: 8px; border: 1px solid #e5e7eb;">
                    <h3 style="margin-top:0;">Current Phase: {current_phase}</h3>
                    
                    <div style="background: #e5e7eb; height: 12px; border-radius: 6px; overflow: hidden; margin: 20px 0;">
                        <div style="background: #4f46e5; height: 100%; width: {progress_percentage}%;"></div>
                    </div>
                    <p style="font-weight: bold; font-size: 18px; color: #4f46e5;">{progress_percentage}% Completed</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg = MIMEText(html_body, 'html')
        msg['Subject'] = subject
        msg['From'] = self.sender_email
        msg['To'] = to_email
        
        await self._send_smtp_message(msg)

    async def send_completion_email(
        self,
        to_email: str,
        project_name: str,
        app_urls: Dict,
        login_credentials: Dict
    ):
        """
        EMAIL 3: Completion email with live URLs
        """
        subject = f"🎉 {project_name} is Now Live!"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #10b981; padding: 40px; text-align: center; color: white; border-radius: 8px 8px 0 0;">
                <h1 style="margin:0;">🎊 Congratulations!</h1>
                <p style="font-size: 18px; margin-top: 10px;">Your <strong>{project_name}</strong> is live!</p>
            </div>
            
            <div style="padding: 30px; background: #fff; border: 1px solid #eee; border-top: none;">
                <div style="text-align: center; margin: 20px 0;">
                    <a href="{app_urls.get('frontend')}" 
                       style="background: #10b981; color: white; padding: 15px 40px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 18px; display: inline-block;">
                        Open Your Live App →
                    </a>
                </div>
                
                <div style="background: #fffbef; padding: 20px; border-radius: 8px; border-left: 4px solid #f59e0b; margin: 20px 0;">
                    <h3 style="margin-top:0; color: #92400e;">[SECURE] Admin Credentials</h3>
                    <p><strong>Email:</strong> {login_credentials.get('admin_email')}</p>
                    <p><strong>Password:</strong> {login_credentials.get('admin_password')}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg = MIMEText(html_body, 'html')
        msg['Subject'] = subject
        msg['From'] = self.sender_email
        msg['To'] = to_email
        
        await self._send_smtp_message(msg)
    
    async def _send_smtp_message(self, msg: EmailMessage):
        """Send via smtplib (ZeptoMail SMTP)"""
        if not self.password:
            self.logger.error("[ERROR] ZEPTOMAIL_PASSWORD not found in Secret Manager. Cannot send email.")
            return

        try:
            if self.port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.smtp_server, self.port, context=context) as server:
                    server.login(self.username, self.password)
                    server.send_message(msg)
            elif self.port == 587:
                with smtplib.SMTP(self.smtp_server, self.port) as server:
                    server.starttls()
                    server.login(self.username, self.password)
                    server.send_message(msg)
            else:
                self.logger.error(f"[ERROR] Invalid SMTP port: {self.port}")
                return
                
            self.logger.info(f"📧 Email successfully sent to {msg['To']} via ZeptoMail")
        except Exception as e:
            self.logger.error(f"[ERROR] SMTP Error: {e}")


# Global instance
email_service = EmailService()
