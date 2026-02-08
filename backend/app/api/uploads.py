"""
FILE UPLOAD API
===============
Location: app/api/uploads.py

Purpose: Handle file uploads (audio, video, screenshots, zip)
Users can upload files as part of requirements:
- Screenshots: UI mockups, reference designs
- Audio: Voice requirements
- Video: Screen recordings
- Zip: Existing code to modify

"""

from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import uuid4, UUID
import os
import shutil
from pathlib import Path
import mimetypes

from app.database import get_db
from app.models import User
from app.dependencies import get_current_user
from app.core.config import get_settings

router = APIRouter()
settings = get_settings()

# Allowed file types
ALLOWED_EXTENSIONS = {
    "images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg"},
    "video": {".mp4", ".webm", ".mov", ".avi"},
    "archives": {".zip", ".tar", ".gz", ".7z"},
    "documents": {".pdf", ".doc", ".docx", ".txt", ".md"}
}

# Max file sizes (in bytes)
MAX_FILE_SIZES = {
    "images": 10 * 1024 * 1024,      # 10 MB
    "audio": 50 * 1024 * 1024,       # 50 MB
    "video": 200 * 1024 * 1024,      # 200 MB
    "archives": 100 * 1024 * 1024,   # 100 MB
    "documents": 20 * 1024 * 1024    # 20 MB
}


def get_file_category(filename: str) -> str:
    """Determine file category from extension"""
    ext = Path(filename).suffix.lower()
    
    for category, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return category
    
    raise HTTPException(
        status_code=400,
        detail=f"File type {ext} not allowed"
    )


def validate_file_size(file_size: int, category: str):
    """Check if file size is within limits"""
    max_size = MAX_FILE_SIZES.get(category)
    
    if file_size > max_size:
        max_mb = max_size / (1024 * 1024)
        raise HTTPException(
            status_code=400,
            detail=f"{category.title()} files must be under {max_mb}MB"
        )


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    project_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload a file for use in project requirements.
    
    Supported types:
    - Images: UI mockups, screenshots
    - Audio: Voice requirements
    - Video: Screen recordings
    - Archives: Existing code
    - Documents: Specs, PDFs
    
    Returns:
        {
            "file_id": "uuid",
            "filename": "original.png",
            "url": "/uploads/uuid.png",
            "category": "images",
            "size": 1024000
        }
    """
    try:
        # Get category and validate
        category = get_file_category(file.filename)
        
        # Read file (needed to get size)
        contents = await file.read()
        file_size = len(contents)
        
        # Validate size
        validate_file_size(file_size, category)
        
        # Generate unique filename
        file_id = str(uuid4())
        extension = Path(file.filename).suffix
        unique_filename = f"{file_id}{extension}"
        
        # Create upload directory structure
        # uploads/user_id/category/file
        upload_dir = Path("uploads") / str(current_user.id) / category
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = upload_dir / unique_filename
        
        # Save file
        with open(file_path, "wb") as f:
            f.write(contents)
        
        # Store in database
        from app.models import UploadedFile
        
        uploaded_file = UploadedFile(
            id=uuid4(),
            user_id=current_user.id,
            project_id=project_id,
            filename=file.filename,
            unique_filename=unique_filename,
            file_path=str(file_path),
            category=category,
            size=file_size,
            mime_type=file.content_type
        )
        
        db.add(uploaded_file)
        db.commit()
        db.refresh(uploaded_file)
        
        return {
            "file_id": str(uploaded_file.id),
            "filename": file.filename,
            "url": f"/api/uploads/{uploaded_file.id}",
            "category": category,
            "size": file_size
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"File upload failed: {str(e)}"
        )


@router.post("/upload/multiple")
async def upload_multiple_files(
    files: List[UploadFile] = File(...),
    project_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload multiple files at once.
    
    Useful for:
    - Multiple screenshots
    - Design assets
    - Reference files
    """
    if len(files) > 10:
        raise HTTPException(
            status_code=400,
            detail="Maximum 10 files per upload"
        )
    
    results = []
    errors = []
    
    for file in files:
        try:
            result = await upload_file(file, project_id, current_user, db)
            results.append(result)
        except Exception as e:
            errors.append({
                "filename": file.filename,
                "error": str(e)
            })
    
    return {
        "uploaded": results,
        "errors": errors,
        "total": len(files),
        "success_count": len(results)
    }


@router.get("/{file_id}")
async def get_file(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retrieve an uploaded file.
    
    Security: Only file owner can access
    """
    try:
        from app.models import UploadedFile
        from fastapi.responses import FileResponse
        
        # Get file record
        uploaded_file = db.query(UploadedFile).filter(
            UploadedFile.id == file_id,
            UploadedFile.user_id == current_user.id
        ).first()
        
        if not uploaded_file:
            raise HTTPException(status_code=404, detail="File not found")
        
        # Check file exists on disk
        if not os.path.exists(uploaded_file.file_path):
            raise HTTPException(status_code=404, detail="File not found on disk")
        
        # Return file
        return FileResponse(
            uploaded_file.file_path,
            media_type=uploaded_file.mime_type,
            filename=uploaded_file.filename
        )
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("uploads").error(f"Error fetching file {file_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve file"
        )


@router.delete("/{file_id}")
async def delete_file(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an uploaded file"""
    try:
        from app.models import UploadedFile
        
        uploaded_file = db.query(UploadedFile).filter(
            UploadedFile.id == file_id,
            UploadedFile.user_id == current_user.id
        ).first()
        
        if not uploaded_file:
            raise HTTPException(status_code=404, detail="File not found")
        
        # Delete from disk
        try:
            if os.path.exists(uploaded_file.file_path):
                os.remove(uploaded_file.file_path)
        except Exception as e:
            pass  # Log but don't fail if file already deleted
        
        # Delete from database
        db.delete(uploaded_file)
        db.commit()
        
        return {"status": "deleted", "file_id": str(file_id)}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger("uploads").error(f"Error deleting file {file_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete file"
        )


@router.get("/project/{project_id}/files")
async def list_project_files(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List all files uploaded for a project.
    
    Returns files grouped by category.
    """
    try:
        from app.models import UploadedFile, Project
        
        # Verify project ownership
        project = db.query(Project).filter(
            Project.id == project_id,
            Project.user_id == current_user.id
        ).first()
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Get files
        files = db.query(UploadedFile).filter(
            UploadedFile.project_id == project_id
        ).all()
        
        # Group by category
        grouped = {
            "images": [],
            "audio": [],
            "video": [],
            "archives": [],
            "documents": []
        }
        
        for file in files:
            grouped[file.category].append({
                "file_id": str(file.id),
                "filename": file.filename,
                "url": f"/api/uploads/{file.id}",
                "size": file.size,
                "created_at": file.created_at.isoformat()
            })
        
        return grouped
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("uploads").error(f"Error listing files for project {project_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve project files"
        )
