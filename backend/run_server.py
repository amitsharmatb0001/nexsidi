import os
import sys
import uvicorn

if __name__ == "__main__":
    # Ensure the current directory is in sys.path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)
    
    # Also set PYTHONPATH environment variable for subprocesses
    # This is critical for Windows where uvicorn subprocesses might not inherit sys.path
    os.environ["PYTHONPATH"] = current_dir + os.pathsep + os.environ.get("PYTHONPATH", "")

    print("=" * 60)
    print(f"NexSidi Server Starting from: {current_dir}")
    print(f"PYTHONPATH: {os.environ['PYTHONPATH']}")
    print("=" * 60)
    
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
