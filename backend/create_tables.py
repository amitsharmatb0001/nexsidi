from app.database import engine, Base
from app.models import User, Project, Conversation, ChangeRequest, AgentTask, Deployment, CodeFile
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

print('Creating all tables...')
Base.metadata.create_all(bind=engine)
print('? All tables created successfully!')

# List tables
from sqlalchemy import inspect
inspector = inspect(engine)
tables = inspector.get_table_names()
print(f'\nTables in database: {tables}')
