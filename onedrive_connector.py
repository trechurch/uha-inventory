"""
OneDrive Connector — STUBBED for prototype
Azure app registration pending IT approval.
All functions return safe no-op values so app runs without OneDrive.
"""

def get_access_token():
    return None

def start_device_auth():
    return {"user_code": "", "device_code": "", "interval": 5, "expires_in": 0,
            "verification_uri": "https://microsoft.com/devicelogin"}

def poll_device_auth(device_code, interval=5):
    return None

def list_import_files():
    return []

def download_import_file(filename):
    return None

def archive_file(filename, content, subfolder=""):
    return False

def load_gl_files_from_onedrive():
    return []

def _secrets(key, default=""):
    return default
