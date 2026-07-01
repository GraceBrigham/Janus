"""
File tools implementation providing list, get, and delete functionality.
"""

from typing import Dict, List, Optional, Any

from .data_store import get_collection

files: Optional[List[Dict]] = None


def _get_files() -> List[Dict]:
    global files
    if files is None:
        files = get_collection("files")
    return files


def list_files() -> str:
    """
    List available files.

    Returns:
        A formatted list of files.
    """
    global files
    files = _get_files()

    if not files:
        return "No files found."

    result = f"Found {len(files)} file(s):\n\n"
    for file in files:
        result += f"File ID: {file['id']}\n"
        result += f"Path: {file['path']}\n"
        result += "-" * 40 + "\n\n"
    return result.rstrip()


def get_file(path: Optional[str] = None, id: Optional[str] = None) -> Dict[str, Any] | str:
    """
    Get a file by path or id, returning metadata and content.

    Args:
        path: File path to fetch.
        id: File identifier to fetch.

    Returns:
        File metadata and contents.
    """
    global files
    files = _get_files()

    match = None
    if id is not None:
        match = next((file for file in files if str(file.get("id")) == str(id)), None)
    if match is None and path:
        match = next((file for file in files if file.get("path") == path), None)
    else:
        return "Provide either path or id to get a file."

    if not match:
        return "File not found."

    return {
        "id": match.get("id"),
        "path": match.get("path"),
        "content": match.get("content", "")
    }


def delete_file(id: str) -> str:
    """
    Delete a file by id.

    Args:
        id: File identifier to delete.

    Returns:
        Confirmation message.
    """
    global files
    files = _get_files()

    match = next((file for file in files if str(file.get("id")) == str(id)), None)
    if not match:
        return f"File with ID {id} not found."

    files[:] = [file for file in files if str(file.get("id")) != str(id)]
    return f"File with ID {id} deleted."
