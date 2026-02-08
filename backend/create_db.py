from sqlalchemy import create_engine, text
from sqlalchemy_utils import database_exists, create_database
from dotenv import load_dotenv
import os
from pathlib import Path

# Load environment variables
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv('DATABASE_URL')

print(f'Target database: {DATABASE_URL}')
print('=' * 50)

try:
    # Check if database exists
    if database_exists(DATABASE_URL):
        print('? Database already exists')
    else:
        print('?? Database does not exist. Creating...')
        create_database(DATABASE_URL)
        print('? Database created successfully!')
    
    # Now connect to it
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        result = conn.execute(text('SELECT current_database();'))
        db_name = result.fetchone()[0]
        print(f'? Connected to database: {db_name}')
        
except Exception as e:
    print(f'? Error: {e}')
