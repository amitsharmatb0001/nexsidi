from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database import Base
import uuid

# Table 1: Users (authentication & profile)
class User(Base):
    __tablename__ = 'users'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)  # Login via email
    phone = Column(String(20), unique=True, nullable=True)    # Optional phone login
    hashed_password = Column(String(255), nullable=False)     # Never store plain passwords!
    full_name = Column(String(255))
    is_active = Column(Boolean, default=True)                 # Soft delete users
    is_admin = Column(Boolean, default=False)                # Admin access
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Table 2: Chats (grouping conversations)
class Chat(Base):
    __tablename__ = 'chats'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'))
    title = Column(String(255), default="New Chat")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

# Table 2: Projects (user's app requests)
class Project(Base):
    __tablename__ = 'projects'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'))
    
    # What they want to build
    title = Column(String(500))
    description = Column(Text)
    tech_stack = Column(JSON)          # {frontend: 'React', backend: 'FastAPI', db: 'PostgreSQL'}
    complexity_score = Column(Integer)  # 1-10 (affects pricing)
    
    # Where in the pipeline?
    status = Column(String(50), default='requirements_gathering')
    current_agent = Column(String(50))  # Which agent is working on it now
    
    # Money tracking
    quoted_price = Column(Float)
    final_price = Column(Float)
    paid_amount = Column(Float, default=0)
    
    # Requirements lock (prevents scope creep after approval)
    requirements_locked = Column(Boolean, default=False)
    requirements_specification = Column(JSON)  # Final approved requirements
    email_otp_verified = Column(Boolean, default=False)
    phone_otp_verified = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

# Table 3: Conversations (chat history with agents)
class Conversation(Base):
    __tablename__ = 'conversations'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # NEW: Link to user (required)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    
    # NEW: Link to chat (grouping)
    chat_id = Column(UUID(as_uuid=True), ForeignKey('chats.id', ondelete='CASCADE'), nullable=True)
    
    # CHANGED: Link to project (optional - might not exist yet!)
    project_id = Column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'), nullable=True)
    
    agent_name = Column(String(50))
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    meta_info = Column(JSON)
    
    # Cost tracking for chat
    tokens_used = Column(Integer, default=0)
    cost = Column(Float, default=0.0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Table 4: Change Requests (iteration tracking - 5 mid + 11 small allowed)
class ChangeRequest(Base):
    __tablename__ = 'change_requests'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'))
    
    change_type = Column(String(20))        # 'mid' or 'small'
    description = Column(Text, nullable=False)
    status = Column(String(50), default='pending')  # pending ? approved ? completed
    
    approved_by = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Table 5: Agent Tasks (what each agent is doing)
class AgentTask(Base):
    __tablename__ = 'agent_tasks'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'))
    
    agent_name = Column(String(50), nullable=False)   # Which agent
    task_type = Column(String(50), nullable=False)    # What it's doing
    status = Column(String(50), default='pending')    # pending ? running ? completed/failed
    
    input_data = Column(JSON)   # What the agent received
    output_data = Column(JSON)  # What the agent produced
    
    # Cost tracking (important for profitability!)
    model_used = Column(String(50))      # 'claude-sonnet', 'gemini-pro', etc
    tokens_used = Column(Integer)        # How many tokens consumed
    cost_inr = Column(Float)             # Actual cost in rupees
    
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Table 6: Deployments (where the app is hosted)
class Deployment(Base):
    __tablename__ = 'deployments'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'))
    
    deployment_type = Column(String(50))    # 'vercel', 'railway', 'godaddy', 'user_ssh'
    deployment_url = Column(String(500))    # Live URL where app is running
    status = Column(String(50), default='pending')
    
    config = Column(JSON)  # Deployment settings
    logs = Column(Text)    # Deployment output (for debugging)
    
    deployed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# Table 7: Code Files (generated code storage)
class CodeFile(Base):
    __tablename__ = 'code_files'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'))
    
    file_path = Column(String(500), nullable=False)  # 'src/App.tsx', 'main.py', etc
    file_content = Column(Text)                      # Actual code
    file_type = Column(String(50))                   # 'py', 'js', 'tsx', 'html', 'css'
    
    created_by_agent = Column(String(50))  # Which agent created this file
    version = Column(Integer, default=1)   # Track file versions
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

# Table 8: Uploaded Files (user file uploads)
class UploadedFile(Base):
    __tablename__ = 'uploaded_files'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'), nullable=True)
    
    filename = Column(String(500), nullable=False)
    unique_filename = Column(String(500), nullable=False)   # ADD
    file_path = Column(String(1000), nullable=False)
    category = Column(String(50), nullable=False)            # ADD
    size = Column(Integer, nullable=False)                   # RENAME from file_size
    mime_type = Column(String(100))                          # RENAME from file_type
    
    description = Column(Text)
    is_public = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

